from io import BytesIO
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from openpyxl import load_workbook

from judge.models import (
    Contest,
    ContestParticipation,
    ContestProblem,
    Problem,
    ProblemGroup,
    Quiz,
)
from judge.tests.test_grades_live_refresh import make_profile


class ContestRankingExportTests(TestCase):
    fixtures = ["language_small"]

    @classmethod
    def setUpTestData(cls):
        cls.manager = make_profile("xlsx_manager")
        cls.student = make_profile(
            "xlsx_student", first_name="Nguyễn Thị Ánh", last_name="Trường Hà Nội"
        )
        cls.other = make_profile("xlsx_other")
        cls.blank = make_profile("xlsx_blank")
        cls.virtual = make_profile("xlsx_virtual")
        cls.admin = make_profile("xlsx_admin", is_staff=True, is_superuser=True)
        now = timezone.now()
        cls.contest = Contest.objects.create(
            key="xlsx_ranking",
            name="Kỳ thi có dấu",
            is_visible=True,
            start_time=now - timezone.timedelta(hours=2),
            end_time=now - timezone.timedelta(hours=1),
        )
        cls.contest.authors.add(cls.manager)
        group = ProblemGroup.objects.create(name="xlsx", full_name="Excel")
        problem = Problem.objects.create(
            code="xlsx_p",
            name="Bài một",
            group=group,
            time_limit=1,
            memory_limit=65536,
            points=100,
            is_public=True,
        )
        cls.cp = ContestProblem.objects.create(
            contest=cls.contest,
            problem=problem,
            points=100,
            order=0,
            is_result_hidden=True,
        )
        quiz = Quiz.objects.create(code="xlsx_quiz", title="Trắc nghiệm có dấu")
        cls.cq = ContestProblem.objects.create(
            contest=cls.contest, quiz=quiz, points=50, order=1
        )
        for profile, score in (
            (cls.student, 50),
            (cls.other, 50),
            (cls.blank, 0),
            (cls.virtual, 70),
        ):
            ContestParticipation.objects.create(
                contest=cls.contest,
                user=profile,
                virtual=1 if profile == cls.virtual else 0,
                score=score,
                score_final=score + 25,
                format_data=(
                    {}
                    if profile == cls.blank
                    else {
                        str(cls.cp.id): {"points": 25, "time": 0},
                        f"quiz_{cls.cq.id}": {"points": 50, "time": 0},
                    }
                ),
            )

    def setUp(self):
        cache.clear()
        self.client.force_login(self.manager.user)

    def url(self, final=False):
        return reverse(
            "contest_final_ranking" if final else "contest_ranking",
            args=[self.contest.key],
        )

    def sheet(self, final=False, **params):
        response = self.client.get(
            self.url(final), {"format": "xlsx", "virtual": "0", **params}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertIn(".xlsx", response["Content-Disposition"])
        return load_workbook(BytesIO(response.content)).active

    def test_xlsx_unicode_totals_ties_hidden_and_quiz_points(self):
        sheet = self.sheet()
        rows = list(sheet.values)
        self.assertEqual(len(rows), 4)
        self.assertEqual(
            rows[1][:6],
            (1, "xlsx_student", "Nguyễn Thị Ánh", "Trường Hà Nội", 50, "Hợp lệ"),
        )
        self.assertEqual(rows[1][6:], ("?", 50))
        self.assertEqual(rows[2][0], 1)
        self.assertEqual(rows[3][0], 3)
        self.assertEqual(rows[3][6:], (None, None))
        self.assertIn("Trắc nghiệm có dấu", rows[0][7])
        self.assertEqual(sheet["E2"].data_type, "n")

    def test_unpaginated_export_keeps_search_favorites_friends_and_virtual(self):
        with patch("judge.views.contests.RANKING_PAGE_SIZE", 1):
            self.assertEqual(self.sheet(page=999).max_row, 4)
        self.assertEqual(self.sheet(search="Nguyễn").max_row, 2)
        self.assertEqual(self.sheet(search="no_match").max_row, 1)
        self.assertEqual(
            self.sheet(favorites=str(self.other.id))["B2"].value, self.other.username
        )
        with patch(
            "judge.models.Profile.get_following_ids", return_value=[self.student.id]
        ):
            self.assertEqual(self.sheet(friend="1").max_row, 2)
        self.assertEqual(self.sheet(virtual="1").max_row, 5)

    def test_only_managers_and_admin_can_export(self):
        for profile in (self.student, self.other):
            self.client.force_login(profile.user)
            self.assertEqual(
                self.client.get(self.url(), {"format": "xlsx"}).status_code, 403
            )
            self.assertNotContains(self.client.get(self.url()), 'id="ranking-export"')
        self.client.logout()
        self.assertEqual(
            self.client.get(self.url(), {"format": "xlsx"}).status_code, 403
        )
        self.client.force_login(self.admin.user)
        self.assertEqual(self.sheet().max_row, 4)

    def test_final_export_requires_editor_and_hidden_results(self):
        rows = list(self.sheet(final=True).values)
        self.assertEqual(rows[1][4], 75)
        self.assertEqual(rows[1][6], 25)
        self.client.force_login(self.student.user)
        for fmt in ("xlsx", "csv"):
            response = self.client.get(self.url(True), {"format": fmt})
            # ContestMixin renders its existing "No such contest" HTML for 404s.
            self.assertNotIn("Content-Disposition", response)
            self.assertTrue(response["Content-Type"].startswith("text/html"))
            self.assertTemplateUsed(response, "generic-message.html")
        self.client.force_login(self.manager.user)
        ContestProblem.objects.filter(pk=self.cp.id).update(is_result_hidden=False)
        cache.clear()
        for fmt in ("xlsx", "csv"):
            response = self.client.get(self.url(True), {"format": fmt})
            self.assertNotIn("Content-Disposition", response)
            self.assertTrue(response["Content-Type"].startswith("text/html"))

    def test_private_contest_cannot_be_exported_by_outsider(self):
        Contest.objects.filter(pk=self.contest.id).update(
            is_private=True, public_scoreboard=False
        )
        cache.clear()
        self.client.force_login(self.other.user)
        self.assertEqual(
            self.client.get(self.url(), {"format": "xlsx"}).status_code, 403
        )
