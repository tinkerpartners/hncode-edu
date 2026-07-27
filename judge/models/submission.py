import hashlib
import hmac

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db import models
from django.db.models import (
    Case,
    Count,
    ExpressionWrapper,
    F,
    FloatField,
    IntegerField,
    Value,
    When,
)
from django.db.models.fields import DateField
from django.db.models.functions import Cast
from django.urls import reverse
from django.utils.functional import cached_property
from django.utils.translation import gettext_lazy as _

from judge.caching import cache_wrapper
from judge.judgeapi import abort_submission, judge_submission
from judge.models.problem import Problem
from judge.models.profile import Profile
from judge.models.runtime import Language
from judge.utils.unicode import utf8bytes

__all__ = [
    "SUBMISSION_RESULT",
    "Submission",
    "SubmissionSource",
    "SubmissionTestCase",
    "BestSubmission",
    "best_submission_order_annotations",
    "BEST_SUBMISSION_ORDER",
    "get_user_submission_dates",
    "get_user_min_submission_year",
]

SUBMISSION_RESULT = (
    ("AC", _("Accepted")),
    ("WA", _("Wrong Answer")),
    ("TLE", _("Time Limit Exceeded")),
    ("MLE", _("Memory Limit Exceeded")),
    ("OLE", _("Output Limit Exceeded")),
    ("IR", _("Invalid Return")),
    ("RTE", _("Runtime Error")),
    ("CE", _("Compile Error")),
    ("IE", _("Internal Error")),
    ("SC", _("Short circuit")),
    ("AB", _("Aborted")),
)


class Submission(models.Model):
    STATUS = (
        ("QU", _("Queued")),
        ("P", _("Processing")),
        ("G", _("Grading")),
        ("D", _("Completed")),
        ("IE", _("Internal Error")),
        ("CE", _("Compile Error")),
        ("AB", _("Aborted")),
    )
    IN_PROGRESS_GRADING_STATUS = ("QU", "P", "G")
    RESULT = SUBMISSION_RESULT
    USER_DISPLAY_CODES = {
        "AC": _("Accepted"),
        "WA": _("Wrong Answer"),
        "SC": "Short Circuited",
        "TLE": _("Time Limit Exceeded"),
        "MLE": _("Memory Limit Exceeded"),
        "OLE": _("Output Limit Exceeded"),
        "IR": _("Invalid Return"),
        "RTE": _("Runtime Error"),
        "CE": _("Compile Error"),
        "IE": _("Internal Error (judging server error)"),
        "QU": _("Queued"),
        "P": _("Processing"),
        "G": _("Grading"),
        "D": _("Completed"),
        "AB": _("Aborted"),
    }

    user = models.ForeignKey(Profile, on_delete=models.CASCADE)
    problem = models.ForeignKey(Problem, on_delete=models.CASCADE)
    date = models.DateTimeField(
        verbose_name=_("submission time"), auto_now_add=True, db_index=True
    )
    time = models.FloatField(verbose_name=_("execution time"), null=True, db_index=True)
    memory = models.FloatField(verbose_name=_("memory usage"), null=True)
    points = models.FloatField(
        verbose_name=_("points granted"), null=True, db_index=True
    )
    language = models.ForeignKey(
        Language, verbose_name=_("submission language"), on_delete=models.CASCADE
    )
    status = models.CharField(
        verbose_name=_("status"),
        max_length=2,
        choices=STATUS,
        default="QU",
        db_index=True,
    )
    result = models.CharField(
        verbose_name=_("result"),
        max_length=3,
        choices=SUBMISSION_RESULT,
        default=None,
        null=True,
        blank=True,
        db_index=True,
    )
    error = models.TextField(verbose_name=_("compile errors"), null=True, blank=True)
    current_testcase = models.IntegerField(default=0)
    batch = models.BooleanField(verbose_name=_("batched cases"), default=False)
    case_points = models.FloatField(verbose_name=_("test case points"), default=0)
    case_total = models.FloatField(verbose_name=_("test case total points"), default=0)
    judged_on = models.ForeignKey(
        "Judge",
        verbose_name=_("judged on"),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    judged_date = models.DateTimeField(
        verbose_name=_("submission judge time"), default=None, null=True
    )
    was_rejudged = models.BooleanField(
        verbose_name=_("was rejudged by admin"), default=False
    )
    is_pretested = models.BooleanField(
        verbose_name=_("was ran on pretests only"), default=False
    )
    contest_object = models.ForeignKey(
        "Contest",
        verbose_name=_("contest"),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    @classmethod
    def result_class_from_code(cls, result, case_points, case_total):
        if result == "AC":
            if case_points == case_total:
                return "AC"
            return "_AC"
        return result

    @property
    def result_class(self):
        # This exists to save all these conditionals from being executed (slowly) in each row.jade template
        if self.status in ("IE", "CE"):
            return self.status
        return Submission.result_class_from_code(
            self.result, self.case_points, self.case_total
        )

    @property
    def memory_bytes(self):
        return self.memory * 1024 if self.memory is not None else 0

    @property
    def short_status(self):
        return self.result or self.status

    @property
    def long_status(self):
        return Submission.USER_DISPLAY_CODES.get(self.short_status, "")

    def judge(self, *args, **kwargs):
        judge_submission(self, *args, **kwargs)

    judge.alters_data = True

    def abort(self):
        abort_submission(self)

    abort.alters_data = True

    def update_contest(self):
        try:
            contest = self.contest
        except AttributeError:
            return

        contest_problem = contest.problem
        contest.points = round(
            (
                self.case_points / self.case_total * contest_problem.points
                if self.case_total > 0
                else 0
            ),
            3,
        )
        if not contest_problem.partial and contest.points != contest_problem.points:
            contest.points = 0
        contest.save()
        contest.participation.recompute_results()

    update_contest.alters_data = True

    @property
    def is_graded(self):
        return self.status not in ("QU", "P", "G")

    @cached_property
    def contest_key(self):
        if hasattr(self, "contest"):
            return self.contest_object.key

    def __str__(self):
        return "Submission %d of %s by %s" % (
            self.id,
            self.problem,
            self.user.user.username,
        )

    def get_absolute_url(self):
        return reverse("submission_status", args=(self.id,))

    @cached_property
    def contest_or_none(self):
        try:
            return self.contest
        except ObjectDoesNotExist:
            return None

    @classmethod
    def get_id_secret(cls, sub_id):
        return (
            hmac.new(
                utf8bytes(settings.EVENT_DAEMON_SUBMISSION_KEY),
                b"%d" % sub_id,
                hashlib.sha512,
            ).hexdigest()[:16]
            + "%08x" % sub_id
        )

    @cached_property
    def id_secret(self):
        return self.get_id_secret(self.id)

    def is_accessible_by(self, profile, check_contest=True):
        if not profile:
            return False

        problem_id = self.problem_id
        user = profile.user

        if profile.id == self.user_id:
            return True

        if user.has_perm("judge.change_submission"):
            return True

        if user.has_perm("judge.view_all_submission"):
            return True

        if self.problem.is_public and user.has_perm("judge.view_public_submission"):
            return True

        if check_contest:
            contest = self.contest_object
            if contest and contest.is_editable_by(user):
                return True

        from judge.utils.problems import (
            user_completed_ids,
            user_tester_ids,
            user_editable_ids,
        )

        if problem_id in user_editable_ids(profile):
            return True

        if self.problem_id in user_completed_ids(profile):
            if self.problem.is_public:
                return True
            if problem_id in user_tester_ids(profile):
                return True

        return False

    def save(self, *args, **kwargs):
        """Override to invalidate caches on save"""
        super().save(*args, **kwargs)
        # Invalidate submission date caches
        get_user_submission_dates.dirty(self.user_id)
        get_user_min_submission_year.dirty(self.user_id)

    save.alters_data = True

    def delete(self, *args, **kwargs):
        """Override to invalidate caches on delete"""
        user_id = self.user_id
        super().delete(*args, **kwargs)
        # Invalidate submission date caches
        get_user_submission_dates.dirty(user_id)
        get_user_min_submission_year.dirty(user_id)

    delete.alters_data = True

    class Meta:
        permissions = (
            ("abort_any_submission", "Abort any submission"),
            ("rejudge_submission", "Rejudge the submission"),
            ("rejudge_submission_lot", "Rejudge a lot of submissions"),
            ("spam_submission", "Submit without limit"),
            ("view_all_submission", "View all submission"),
            ("resubmit_other", "Resubmit others' submission"),
            ("view_public_submission", "View public submissions"),
        )
        verbose_name = _("submission")
        verbose_name_plural = _("submissions")

        indexes = [
            models.Index(fields=["problem", "user", "-points"]),
            models.Index(fields=["contest_object", "problem", "user", "-points"]),
            models.Index(fields=["language", "result"]),
            models.Index(fields=["problem", "result", "points"]),
        ]


class SubmissionSource(models.Model):
    submission = models.OneToOneField(
        Submission,
        on_delete=models.CASCADE,
        verbose_name=_("associated submission"),
        related_name="source",
    )
    source = models.TextField(verbose_name=_("source code"), max_length=65536)

    def __str__(self):
        return "Source of %s" % self.submission


class SubmissionTestCase(models.Model):
    RESULT = SUBMISSION_RESULT

    submission = models.ForeignKey(
        Submission,
        verbose_name=_("associated submission"),
        related_name="test_cases",
        on_delete=models.CASCADE,
    )
    case = models.IntegerField(verbose_name=_("test case ID"))
    status = models.CharField(
        max_length=3, verbose_name=_("status flag"), choices=SUBMISSION_RESULT
    )
    time = models.FloatField(verbose_name=_("execution time"), null=True)
    memory = models.FloatField(verbose_name=_("memory usage"), null=True)
    points = models.FloatField(verbose_name=_("points granted"), null=True)
    total = models.FloatField(verbose_name=_("points possible"), null=True)
    batch = models.IntegerField(verbose_name=_("batch number"), null=True)
    feedback = models.CharField(
        max_length=50, verbose_name=_("judging feedback"), blank=True
    )
    extended_feedback = models.TextField(
        verbose_name=_("extended judging feedback"), blank=True
    )
    output = models.TextField(verbose_name=_("program output"), blank=True)

    @property
    def long_status(self):
        return Submission.USER_DISPLAY_CODES.get(self.status, "")

    class Meta:
        unique_together = ("submission", "case")
        verbose_name = _("submission test case")
        verbose_name_plural = _("submission test cases")


@cache_wrapper(prefix="SUB_dates", expected_type=dict)
def get_user_submission_dates(user_id):
    """
    Get submission dates and counts for a user.

    Args:
        user_id: The ID of the user profile

    Returns:
        A dictionary mapping ISO-formatted dates to submission counts
    """
    submissions = (
        Submission.objects.filter(user_id=user_id)
        .annotate(date_only=Cast("date", DateField()))
        .values("date_only")
        .annotate(cnt=Count("id"))
    )

    return {
        date_counts["date_only"].isoformat(): date_counts["cnt"]
        for date_counts in submissions
    }


@cache_wrapper(prefix="SUB_min_year", expected_type=int)
def get_user_min_submission_year(user_id):
    """
    Get the earliest year a user made a submission.

    Args:
        user_id: The ID of the user profile

    Returns:
        The minimum year as an integer, or None if no submissions exist
    """
    date_counts = get_user_submission_dates(user_id)
    if not date_counts:
        return None

    # Extract years from ISO-formatted dates (YYYY-MM-DD) and find the minimum
    years = [int(date.split("-")[0]) for date in date_counts.keys()]
    return min(years) if years else None


def best_submission_order_annotations():
    """
    Annotation kwargs for ranking Submissions the way BestSubmission does.

    Use together with `BEST_SUBMISSION_ORDER`. Kept in one place so that every
    site that has to pick "the best submission for a user/problem" agrees; see
    `BestSubmission.recalculate_for_user_problem` for why the keys are what they
    are, and `judge.views.course.bulk_max_case_points_per_problem` for the other
    caller.
    """
    return {
        "case_ratio": Case(
            When(
                case_total__gt=0,
                then=ExpressionWrapper(
                    F("case_points") / F("case_total"), output_field=FloatField()
                ),
            ),
            default=Value(0.0),
            output_field=FloatField(),
        ),
        "has_cases": Case(
            When(case_total__gt=0, then=Value(1)),
            default=Value(0),
            output_field=IntegerField(),
        ),
    }


BEST_SUBMISSION_ORDER = ("-case_ratio", "-has_cases", "-points", "-date", "-id")


class BestSubmission(models.Model):
    """
    Caches the best submission for each user/problem pair.
    Updated when a new submission is judged and has a better score.
    """

    user = models.ForeignKey(
        Profile,
        on_delete=models.CASCADE,
        related_name="best_submissions",
        verbose_name=_("user"),
    )
    problem = models.ForeignKey(
        Problem,
        on_delete=models.CASCADE,
        related_name="best_submissions",
        verbose_name=_("problem"),
    )
    submission = models.ForeignKey(
        "Submission",
        on_delete=models.CASCADE,
        related_name="+",
        verbose_name=_("best submission"),
    )
    points = models.FloatField(
        default=0,
        verbose_name=_("points"),
        help_text=_("Best score achieved (case_points)"),
    )
    case_total = models.FloatField(
        default=0,
        verbose_name=_("case total"),
        help_text=_("Total possible points for this problem"),
    )

    class Meta:
        unique_together = ("user", "problem")
        verbose_name = _("Best Submission")
        verbose_name_plural = _("Best Submissions")
        indexes = [
            models.Index(fields=["user", "problem"]),
        ]

    def __str__(self):
        return f"{self.user.user.username} - {self.problem.code}: {self.points}/{self.case_total}"

    def save(self, *args, **kwargs):
        # Track if the score changed for triggering lesson grade updates.
        # Watch `case_total` as well as `points`: grades are computed from the
        # ratio `points / case_total`, so a submission that changes only
        # case_total (e.g. the best run moves to one judged on rescaled test
        # data) still changes every lesson grade that includes this problem.
        old_points = 0
        old_case_total = 0
        if self.pk:
            try:
                old_instance = BestSubmission.objects.get(pk=self.pk)
                old_points = old_instance.points
                old_case_total = old_instance.case_total
            except BestSubmission.DoesNotExist:
                pass

        super().save(*args, **kwargs)

        # If the score changed, trigger lesson grade update for related lessons
        if (
            abs(self.points - old_points) > 0.001
            or abs(self.case_total - old_case_total) > 0.001
        ):
            self._update_related_lesson_grades()

    def _update_related_lesson_grades(self):
        """Update lesson grades for lessons containing this problem."""
        from judge.models.course import CourseLessonProblem, CourseRole
        from judge.utils.course_prerequisites import update_lesson_grade

        # Find all lessons containing this problem
        lesson_problems = CourseLessonProblem.objects.filter(
            problem=self.problem
        ).select_related("lesson__course")

        for lesson_problem in lesson_problems:
            lesson = lesson_problem.lesson
            course = lesson.course

            # Check if user is enrolled in this course
            if CourseRole.objects.filter(course=course, user=self.user).exists():
                update_lesson_grade(self.user, lesson)

    @classmethod
    def update_from_submission(cls, submission):
        """
        Recalculate best submission for a user/problem after a submission is judged.

        Args:
            submission: Submission object that was just judged

        Returns:
            BestSubmission object if updated/created, None if no valid submissions
        """
        if submission.status != "D":  # Only consider completed submissions
            return None

        return cls.recalculate_for_user_problem(
            submission.user_id, submission.problem_id
        )

    @classmethod
    def recalculate_for_user_problem(cls, user_id, problem_id):
        """
        Recalculate best submission for a user/problem pair.
        Called after a submission is judged or deleted.

        Args:
            user_id: Profile ID of the user
            problem_id: Problem ID
        """
        # Rank candidates by TEST-CASE RATIO, because that is the metric every
        # reader of this table consumes: course lesson grades render
        # `points / case_total * lesson_problem.score`
        # (judge.views.course.bulk_max_case_points_per_problem) and
        # `user_completed_ids` (judge.utils.problems) tests `points >= case_total`.
        #
        # Do NOT rank by `Submission.points`. It is the normalized *problem*
        # score, and it disagrees with the ratio in two ways:
        #   * on a non-partial problem every non-AC submission has points=0, so
        #     "-points, -date" degenerates into "the most recent submission" and
        #     a student's cached grade DROPS when they resubmit something worse;
        #   * `points` is frozen at judge time, so editing `Problem.points`
        #     afterwards leaves older rows carrying an inflated score that
        #     outranks a later, genuinely better submission.
        #
        # The ratio is also what keeps rescaled test data safe -- the reason
        # "-points" was originally chosen here. When test data is replaced and
        # case_total goes 1000 -> 12, an un-rejudged WA (750/1000 = 0.75) still
        # loses to a fresh AC (12/12 = 1.00), because both are compared as
        # fractions of their own scale rather than as raw case_points.
        #
        # Tie-breaks, in order: prefer a submission that actually ran test cases
        # (a case_total=0 row must never displace a graded run -- it is filtered
        # out by `case_total__gt=0` downstream, which would erase the pair from
        # the grades page), then the normalized score (so a true AC beats a
        # 100%-of-cases non-AC on a non-partial problem), then the most recent,
        # then the highest id so the result is deterministic on equal dates.
        best_submission = (
            Submission.objects.filter(
                user_id=user_id,
                problem_id=problem_id,
                status="D",
            )
            .annotate(**best_submission_order_annotations())
            .order_by(*BEST_SUBMISSION_ORDER)
            .first()
        )

        if best_submission:
            # Update or create best submission record
            best_sub, created = cls.objects.update_or_create(
                user_id=user_id,
                problem_id=problem_id,
                defaults={
                    "submission": best_submission,
                    "points": best_submission.case_points or 0,
                    "case_total": best_submission.case_total or 0,
                },
            )
            return best_sub
        else:
            # No submissions left, delete the best submission record if it exists
            cls.objects.filter(user_id=user_id, problem_id=problem_id).delete()
            return None
