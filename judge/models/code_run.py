import hashlib
import hmac

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from judge.models.problem import Problem
from judge.models.profile import Profile
from judge.models.runtime import Language
from judge.utils.unicode import utf8bytes

__all__ = ["CodeRun"]


class CodeRun(models.Model):
    """One "Run" from the IDE: the user's code against the user's own inputs.

    Deliberately not a Submission. It never touches points, rankings, best
    submissions or contest scores, and it never sees the problem's test data --
    the judge is handed a throwaway one-case problem holding only the input the
    user typed (see judge/utils/code_run.py). The problem is kept for its limits
    and its file I/O names, and so a run can be tied back to where it came from.

    Rows are short-lived; old ones are swept when new runs are created.
    """

    STATUS = (
        ("QU", _("Queued")),
        ("P", _("Processing")),
        ("G", _("Running")),
        ("D", _("Completed")),
        ("CE", _("Compile Error")),
        ("IE", _("Internal Error")),
        ("AB", _("Aborted")),
    )
    IN_PROGRESS_STATUS = ("QU", "P", "G")
    RESULT = (
        ("AC", _("Accepted")),
        ("WA", _("Wrong Answer")),
        ("TLE", _("Time Limit Exceeded")),
        ("MLE", _("Memory Limit Exceeded")),
        ("OLE", _("Output Limit Exceeded")),
        ("IR", _("Invalid Return")),
        ("RTE", _("Runtime Error")),
    )

    user = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name="+")
    problem = models.ForeignKey(Problem, on_delete=models.CASCADE, related_name="+")
    language = models.ForeignKey(Language, on_delete=models.CASCADE, related_name="+")
    source = models.TextField(max_length=65536)
    # [{"input": str, "expected": str or None}, ...] -- one compile, every case
    # run, like LeetCode's Run. When a case has an expected output it is
    # checked with the standard checker, so a sample case can say AC/WA.
    cases = models.JSONField(default=list)
    # Blank means stdin/stdout.
    fileio_input = models.CharField(max_length=64, blank=True)
    fileio_output = models.CharField(max_length=64, blank=True)
    time_limit = models.FloatField()
    memory_limit = models.PositiveIntegerField()  # KB

    status = models.CharField(max_length=2, choices=STATUS, default="QU", db_index=True)
    # [{"result", "time", "memory", "output", "feedback",
    #   "extended_feedback"}, ...] in case order, filled in by the bridge.
    results = models.JSONField(default=list)
    error = models.TextField(blank=True)
    judged_on = models.CharField(max_length=50, blank=True)

    created = models.DateTimeField(auto_now_add=True)
    finished = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = _("code run")
        verbose_name_plural = _("code runs")
        indexes = [
            models.Index(fields=["user", "-created"]),
            models.Index(fields=["created"]),
        ]

    def __str__(self):
        return "Run %d by %s on %s" % (self.id, self.user_id, self.problem_id)

    @property
    def is_finished(self):
        return self.status not in self.IN_PROGRESS_STATUS

    # The bridge and the judge carry runs in the same "submission-id" field as
    # real submissions, as negative numbers, so the two id spaces never meet.
    @property
    def judge_id(self):
        return -self.id

    @staticmethod
    def from_judge_id(judge_id):
        return -judge_id

    @staticmethod
    def is_run_judge_id(judge_id):
        return isinstance(judge_id, int) and judge_id < 0

    @property
    def problem_dir(self):
        """Name of the throwaway problem directory under the problem root.

        One level deep, because the judge builds temporary file names from the
        problem id (a slash breaks them). The hmac makes the name
        unpredictable to anyone who can see an id but not the DB.
        """
        return "__run_%d_%s" % (self.id, self.secret[:12])

    @property
    def secret(self):
        return hmac.new(
            utf8bytes(settings.EVENT_DAEMON_SUBMISSION_KEY),
            b"run%d" % self.id,
            hashlib.sha256,
        ).hexdigest()
