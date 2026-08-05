from django.test import TestCase

from judge.models import Problem, ProblemGroup


class ProblemUpdateStatsPointsTestCase(TestCase):
    """update_stats() must not clobber fields it did not compute.

    Regression test: the non-public points cap in Problem.save() used to be
    triggered by the update_problem_stats celery task (queued by the bridge
    after every graded submission), silently resetting a superuser-set points
    value back to 1 on any non-public problem.
    """

    @classmethod
    def setUpTestData(cls):
        cls.problem_group, _ = ProblemGroup.objects.get_or_create(
            name="test",
            defaults={"full_name": "Test Group"},
        )

    def _create_problem(self, code, **kwargs):
        return Problem.objects.create(
            code=code,
            name=f"Test Problem {code}",
            group=self.problem_group,
            time_limit=1.0,
            memory_limit=262144,
            points=1.0,
            **kwargs,
        )

    def test_update_stats_preserves_points_on_non_public_problem(self):
        problem = self._create_problem("statsprob1", is_public=False)

        # Superuser path: points above the cap on a non-public problem
        problem.points = 100
        problem._bypass_points_cap = True
        problem.save()
        problem.refresh_from_db()
        self.assertEqual(problem.points, 100)

        # Fresh instance, as loaded by the update_problem_stats task
        fresh = Problem.objects.get(code="statsprob1")
        fresh._updating_stats_only = True
        fresh.update_stats()

        fresh.refresh_from_db()
        self.assertEqual(fresh.points, 100)

    def test_update_stats_computes_stats(self):
        problem = self._create_problem("statsprob2", is_public=True)
        problem.update_stats()
        problem.refresh_from_db()
        self.assertEqual(problem.user_count, 0)
        self.assertEqual(problem.ac_rate, 0)

    def test_save_still_caps_points_on_non_public_problem(self):
        problem = self._create_problem("statsprob3", is_public=False)
        problem.points = 100
        problem.save()
        problem.refresh_from_db()
        self.assertEqual(problem.points, 1)
