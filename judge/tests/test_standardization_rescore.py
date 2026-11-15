import json
from io import StringIO
from unittest import mock

from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings

from judge.models import Language, Problem, ProblemGroup, Profile, Submission
from judge.models.standardization import StandardizationRescoreJob
from judge.standardization.enums import RescoreStatus
from judge.standardization.rescore import schedule_rescore
from judge.tasks.standardization import DRAIN_LOCK_KEY, drain_rescore_jobs


class RescoreDrainTest(TestCase):
    fixtures = ["language_small"]

    @classmethod
    def setUpTestData(cls):
        cls.group = ProblemGroup.objects.create(name="stdr", full_name="std rescore")
        cls.lang = Language.get_python3()
        cls.user = Profile.objects.create(
            user=User.objects.create_user("stdrescore", "r@x.com", "x"),
            language=cls.lang,
        )

    def make_problem(self, code, partial):
        p = Problem.objects.create(
            code=code,
            name=code,
            description="x",
            group=self.group,
            time_limit=1,
            memory_limit=262144,
            points=100,
            partial=partial,
            is_public=True,
        )
        Submission.objects.create(
            problem=p,
            user=self.user,
            language=self.lang,
            points=0,
            case_points=1,
            case_total=2,
            status="D",
            result="WA",
        )
        return p

    def setUp(self):
        cache.delete(DRAIN_LOCK_KEY)

    def test_schedule_is_idempotent_per_target_state(self):
        p = self.make_problem("stdr1", partial=False)
        job = schedule_rescore(p, reason="partial")
        again = schedule_rescore(p, reason="partial")
        self.assertEqual(job.pk, again.pk)
        self.assertEqual(StandardizationRescoreJob.objects.count(), 1)
        p.partial = True
        p.save()
        other = schedule_rescore(p, reason="partial")
        self.assertNotEqual(job.pk, other.pk)

    def test_schedule_skips_problems_without_submissions(self):
        p = Problem.objects.create(
            code="stdr0",
            name="n",
            description="x",
            group=self.group,
            time_limit=1,
            memory_limit=262144,
            points=100,
            is_public=True,
        )
        self.assertIsNone(schedule_rescore(p, reason="points"))

    def test_drain_runs_jobs_and_fixes_submission_points(self):
        p = self.make_problem("stdr2", partial=False)
        # flip partial the way the config pass does, then drain
        p.partial = True
        p.save()
        job = schedule_rescore(p, reason="partial")
        result = drain_rescore_jobs(limit=10, reschedule=False, sync=True)
        self.assertEqual(result["claimed"], 1)
        self.assertEqual(result["remaining"], 0)
        job.refresh_from_db()
        self.assertEqual(job.status, RescoreStatus.DONE)
        self.assertEqual(job.attempts, 1)
        self.assertIsNotNone(job.finished_at)
        sub = Submission.objects.get(problem=p)
        self.assertEqual(sub.points, 50.0)  # 1/2 * 100, partial now counts
        self.assertIsNone(cache.get(DRAIN_LOCK_KEY))

    def test_drain_respects_limit_and_reports_remaining(self):
        for i in range(3):
            schedule_rescore(
                self.make_problem(f"stdr3{i}", partial=True), reason="points"
            )
        result = drain_rescore_jobs(limit=2, reschedule=False, sync=True)
        self.assertEqual(result["claimed"], 2)
        self.assertEqual(result["remaining"], 1)
        self.assertEqual(
            StandardizationRescoreJob.objects.filter(status=RescoreStatus.DONE).count(),
            2,
        )

    def test_drain_is_skipped_while_locked(self):
        schedule_rescore(self.make_problem("stdr4", partial=True), reason="points")
        cache.add(DRAIN_LOCK_KEY, "x", 60)
        result = drain_rescore_jobs(limit=5, reschedule=False, sync=True)
        self.assertEqual(result, {"claimed": 0, "skipped": "locked"})
        self.assertEqual(
            StandardizationRescoreJob.objects.filter(
                status=RescoreStatus.PENDING
            ).count(),
            1,
        )

    def test_failed_job_is_recorded_and_retryable(self):
        job = schedule_rescore(
            self.make_problem("stdr5", partial=True), reason="points"
        )
        failing = mock.Mock()
        failing.apply.side_effect = RuntimeError("boom")
        with mock.patch("judge.tasks.standardization.rescore_problem", failing):
            drain_rescore_jobs(limit=5, reschedule=False, sync=True)
        job.refresh_from_db()
        self.assertEqual(job.status, RescoreStatus.FAILED)
        self.assertIn("boom", job.error)
        out = StringIO()
        call_command("standardize_rescore", "--retry-failed", stdout=out)
        job.refresh_from_db()
        self.assertEqual(job.status, RescoreStatus.PENDING)
        self.assertIn("re-queued 1", out.getvalue())
        summary = json.loads(out.getvalue().splitlines()[-1])
        self.assertEqual(summary["jobs"], {"pending": 1})

    @override_settings(STANDARDIZATION_RESCORE_PER_MINUTE=1)
    def test_default_limit_from_settings(self):
        for i in range(2):
            schedule_rescore(
                self.make_problem(f"stdr7{i}", partial=True), reason="points"
            )
        result = drain_rescore_jobs(reschedule=False, sync=True)
        self.assertEqual(result["claimed"], 1)
