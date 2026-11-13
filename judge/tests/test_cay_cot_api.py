import hashlib
from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.db import connection
from django.test import Client, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from judge.models import (
    Contest,
    ContestParticipation,
    ContestProblem,
    ContestSubmission,
    ProblemType,
    Submission,
)
from judge.tests import test_grades_live_refresh


class CayCotAPITest(TestCase):
    fixtures = ["language_small"]

    @classmethod
    def setUpTestData(cls):
        test_grades_live_refresh.GradesLiveRefreshTest.setUpTestData.__func__(cls)
        cls.problem.types.add(ProblemType.objects.create(name="math", full_name="Toán"))
        cls.public = cls.make_submission()
        cls.wa = cls.make_submission(result="WA", case_points=30)
        cls.pending = cls.make_submission(status="QU")
        cls.pretest = cls.make_submission(is_pretested=True)
        cls.partial = cls.make_submission(case_points=80)
        cls.other_submission = cls.make_submission(user=cls.other)

    @classmethod
    def make_submission(cls, **kwargs):
        fields = {
            "user": cls.student,
            "problem": cls.problem,
            "language": cls.lang,
            "status": "D",
            "result": "AC",
            "case_points": 100,
            "case_total": 100,
        }
        fields.update(kwargs)
        return Submission.objects.create(**fields)

    def setUp(self):
        cache.clear()
        self.token = "qa-test-only_" * 4
        self.scope = {"profile_ids": [self.student.pk], "include_public_problems": True}
        key = hashlib.sha256(self.token.encode()).hexdigest()
        config = override_settings(
            CAY_COT_SCOPES={key: self.scope},
            CAY_COT_PROBLEM_RATINGS={self.problem.pk: 1200},
            CAY_COT_SITE_URL="https://hncode.edu.vn",
        )
        config.enable()
        self.addCleanup(config.disable)
        self.http = Client(enforce_csrf_checks=True)

    def fetch(self, endpoint, id=None, params=None, **kwargs):
        url = reverse("cay_cot_" + endpoint, args=[] if id is None else [id])
        return self.http.get(
            url, params, HTTP_AUTHORIZATION="Bearer " + self.token, **kwargs
        )

    def test_token_is_required_and_cannot_write(self):
        url = reverse("cay_cot_submissions")
        self.http.force_login(self.student.user)
        self.assertEqual(self.http.get(url).status_code, 401)
        self.assertEqual(self.http.get(url, {"token": self.token}).status_code, 401)
        for method in ("post", "patch", "delete"):
            r = getattr(self.http, method)(
                url, HTTP_AUTHORIZATION="Bearer " + self.token
            )
            self.assertEqual(r.status_code, 405)
            self.assertEqual(r.json(), {"error": "method_not_allowed"})
        with override_settings(CAY_COT_SCOPES={}):
            self.assertEqual(self.fetch("submission", self.public.pk).status_code, 401)
        self.http.logout()
        source = self.http.get(
            reverse("submission_source_raw", args=[self.public.pk]),
            HTTP_AUTHORIZATION="Bearer " + self.token,
        )
        self.assertIn(source.status_code, (302, 403, 404))

    def test_exact_account_and_id_scope(self):
        self.assertEqual(
            self.fetch("find_user", params={"username": self.student.username}).json(),
            {
                "id": str(self.student.pk),
                "username": self.student.username,
                "is_active": True,
            },
        )
        for username in (self.student.username.upper(), self.other.username):
            self.assertEqual(
                self.fetch("find_user", params={"username": username}).status_code, 404
            )
        self.student.user.username = "caycot_renamed"
        self.student.user.save(update_fields=["username"])
        self.assertEqual(
            self.fetch("user", self.student.pk).json()["username"], "caycot_renamed"
        )
        self.assertEqual(self.fetch("user", self.other.pk).status_code, 404)
        self.assertEqual(
            self.fetch("submission", self.other_submission.pk).status_code, 404
        )

    def test_every_page_has_stable_order_and_verdict_semantics(self):
        records = []
        for offset, has_more in [(0, True), (2, True), (4, False)]:
            r = self.fetch(
                "submissions",
                params={"user_id": self.student.pk, "limit": 2, "offset": offset},
            )
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r["Cache-Control"], "private, no-store")
            data = r.json()
            self.assertEqual(data["pagination"]["has_more"], has_more)
            records.extend(data["items"])
        self.assertEqual(
            [r["id"] for r in records],
            [
                str(s.pk)
                for s in (
                    self.partial,
                    self.pretest,
                    self.pending,
                    self.wa,
                    self.public,
                )
            ],
        )
        self.assertEqual(
            [r["is_full_accept"] for r in records], [False, False, False, False, True]
        )
        self.assertIsNone(records[2]["verdict"])
        self.assertIsNone(records[2]["score"])
        self.assertEqual((records[3]["score"], records[3]["max_score"]), (30, 100))
        self.assertEqual(records[-1]["problem"]["difficulty_rating"], 1200)
        self.assertEqual(records[-1]["problem"]["tags"], ["math"])
        self.assertTrue(records[-1]["submitted_at"].endswith("Z"))
        self.assertNotIn("source", records[-1])
        self.assertNotIn("email", records[-1])
        for params in ({"limit": 101}, {"limit": 0}, {"offset": -1}, {"offset": "x"}):
            self.assertEqual(
                self.fetch(
                    "submissions", params={"user_id": self.student.pk, **params}
                ).status_code,
                400,
            )

    def test_private_scope_and_hidden_contest_results(self):
        self.problem.is_public = False
        self.problem.save(update_fields=["is_public"])
        self.assertEqual(self.fetch("submission", self.public.pk).status_code, 404)
        self.scope["problem_ids"] = [self.problem.pk]
        self.assertEqual(self.fetch("submission", self.public.pk).status_code, 200)
        cp = ContestProblem.objects.create(
            contest=self.contest,
            problem=self.problem,
            order=0,
            points=100,
            is_result_hidden=True,
        )
        participation = ContestParticipation.objects.create(
            contest=self.contest, user=self.student
        )
        contest_sub = self.make_submission(contest_object=self.contest)
        ContestSubmission.objects.create(
            submission=contest_sub, problem=cp, participation=participation
        )
        self.assertEqual(self.fetch("submission", contest_sub.pk).status_code, 404)
        self.scope["contest_ids"] = [self.contest.pk]
        data = self.fetch("submission", contest_sub.pk).json()
        self.assertTrue(data["result_hidden"])
        for key in (
            "status",
            "verdict",
            "is_final",
            "is_full_accept",
            "is_pretest",
            "score",
            "max_score",
        ):
            self.assertIsNone(data[key], key)
        Contest.objects.filter(pk=self.contest.pk).update(
            end_time=timezone.now() + timedelta(hours=1)
        )
        self.assertEqual(self.fetch("submission", contest_sub.pk).status_code, 404)

    def test_rejudge_and_metadata_are_current(self):
        before = self.fetch("submission", self.public.pk).json()
        Submission.objects.filter(pk=self.public.pk).update(result="WA", case_points=0)
        self.problem.name = "Tên đã đổi"
        self.problem.save(update_fields=["name"])
        after = self.fetch("submission", self.public.pk).json()
        self.assertEqual(
            (before["id"], before["submitted_at"]), (after["id"], after["submitted_at"])
        )
        self.assertEqual(after["verdict"], "WA")
        self.assertEqual(after["problem"]["title"], "Tên đã đổi")
        Submission.objects.filter(pk=self.public.pk).update(
            result="AC", case_points=100
        )
        self.assertTrue(
            self.fetch("submission", self.public.pk).json()["is_full_accept"]
        )
        self.public.delete()
        self.assertEqual(self.fetch("submission", before["id"]).status_code, 404)

    @patch("judge.views.api.cay_cot.REQUESTS_PER_MINUTE", 2)
    def test_rate_limit(self):
        self.assertEqual(self.fetch("user", self.student.pk).status_code, 200)
        self.assertEqual(self.fetch("user", self.student.pk).status_code, 200)
        r = self.fetch("user", self.student.pk)
        self.assertEqual(r.status_code, 429)
        self.assertGreater(int(r["Retry-After"]), 0)

    def test_page_queries_do_not_grow_with_submission_count(self):
        with CaptureQueriesContext(connection) as single:
            self.assertEqual(
                self.fetch(
                    "submissions", params={"user_id": self.student.pk, "limit": 1}
                ).status_code,
                200,
            )
        for _ in range(20):
            self.make_submission()
        with CaptureQueriesContext(connection) as many:
            data = self.fetch(
                "submissions", params={"user_id": self.student.pk, "limit": 100}
            ).json()
        self.assertEqual(len(data["items"]), 25)
        self.assertLessEqual(len(many), len(single) + 1)

    def test_database_failure_returns_json_without_details(self):
        with patch(
            "judge.views.api.cay_cot.allowed_profiles",
            side_effect=RuntimeError("private detail"),
        ):
            response = self.fetch("user", self.student.pk)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"error": "temporarily_unavailable"})
