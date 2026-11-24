from datetime import timedelta
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth.models import AnonymousUser, User
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from judge.models import (
    Contest,
    ContestProblem,
    Course,
    CourseLesson,
    CourseLessonProblem,
    CourseRole,
    ExamDivision,
    ExamPaper,
    ExamSeries,
    Organization,
    Problem,
    Profile,
)
from judge.tests.strict_helpers import make_group, make_language
from judge.utils.exam_covers import COVER_WIDTH, cover_thumbnail_url
from judge.utils.exam_library import LibraryAccess
from judge.views.exam_library import fold


class ExamCatalogTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    @classmethod
    def setUpTestData(cls):
        cls.language = make_language()
        cls.user = User.objects.create_user("catalog_student", password="pass")
        cls.profile, _ = Profile.objects.get_or_create(
            user=cls.user, defaults={"language": cls.language}
        )
        cls.course = Course.objects.create(
            name="Kho đề", slug="catalog-course", is_public=True
        )
        CourseRole.objects.create(course=cls.course, user=cls.profile, role="ST")
        cls.problem = cls.make_problem("catalog_sum", "Tổng hai số", is_public=True)
        cls.thcs = ExamSeries.objects.create(
            title="Học sinh giỏi THCS",
            slug="catalog-thcs",
            category="HSG",
            level="THCS",
            region="Hà Nội",
            is_published=True,
        )
        cls.tre = ExamSeries.objects.create(
            title="Tin học trẻ Toàn quốc",
            slug="catalog-tre",
            category="Tin học trẻ",
            level="Phổ thông",
            region="Toàn quốc",
            is_published=True,
        )
        cls.paper = cls.make_paper(cls.thcs, "Đề năm 2025", "thcs-2025", 2025)
        CourseLessonProblem.objects.create(
            lesson=cls.paper.lesson, problem=cls.problem, score=100
        )
        cls.north = cls.make_paper(
            cls.tre,
            "Tin học trẻ 2024 - Bảng C2",
            "tre-north",
            2024,
            round_name="Khu vực miền Bắc",
        )
        cls.north_upper = cls.make_paper(
            cls.tre,
            "Tin học trẻ 2023 - Bảng C2",
            "tre-north-2023",
            2023,
            round_name="Khu vực Miền Bắc",
        )
        c2 = ExamDivision.objects.create(name="Bảng C2")
        cls.north.divisions.add(c2)
        cls.north_upper.divisions.add(c2)

    @classmethod
    def make_problem(cls, code, name, **fields):
        return Problem.objects.create(
            code=code,
            name=name,
            group=make_group(),
            description="Đề bài.",
            time_limit=1,
            memory_limit=65536,
            points=100,
            **fields,
        )

    @classmethod
    def make_paper(cls, series, title, slug, year, **fields):
        lesson = CourseLesson.objects.create(course=cls.course, title=title, points=100)
        return ExamPaper.objects.create(
            series=series,
            title=title,
            slug=slug,
            year=year,
            lesson=lesson,
            is_published=True,
            list_metadata=True,
            **fields,
        )

    def catalog(self, **params):
        return self.client.get(reverse("exam_library"), params)

    def test_fold_ignores_case_accents_and_extra_spaces(self):
        self.assertEqual(fold("  Tin  HỌC trẻ Đà Nẵng "), "tin hoc tre da nang")

    def test_search_ignores_accents_and_word_order(self):
        for query in ("tin hoc tre", "TRẺ 2024", "c2 tre mien bac 2024"):
            with self.subTest(query=query):
                response = self.catalog(q=query)
                self.assertContains(response, self.north.title)
                self.assertNotContains(response, self.paper.title)

    def test_search_combines_metadata_and_problem_names(self):
        self.client.force_login(self.user)
        response = self.catalog(q="2025 tong hai")
        self.assertContains(response, self.paper.title)
        self.assertContains(response, "Bài khớp: Tổng hai số (catalog_sum)")
        self.assertNotContains(self.catalog(q="2024 tong hai"), self.paper.title)

    def test_guest_search_does_not_reveal_problem_names(self):
        self.assertNotContains(self.catalog(q="tong hai"), self.paper.title)
        self.assertContains(self.catalog(), "Tên kỳ thi, năm, vòng hoặc bảng")

    def test_filter_values_that_differ_by_case_share_one_option(self):
        response = self.catalog(round="khu vuc mien bac")
        self.assertContains(response, self.north.title)
        self.assertContains(response, self.north_upper.title)
        rounds = next(
            values
            for name, _, values, _ in response.context["options"]
            if name == "round"
        )
        self.assertEqual(
            len([value for value in rounds if fold(value) == "khu vuc mien bac"]), 1
        )

    def test_filter_options_follow_other_selected_filters(self):
        options = {
            name: values
            for name, _, values, _ in self.catalog(level="THCS").context["options"]
        }
        self.assertNotIn("Bảng C2", options["division"])
        self.assertEqual(options["year"], ["2025"])
        # The active filter keeps every value so the choice can be changed.
        self.assertIn("Phổ thông", options["level"])

    def test_results_from_one_series_stay_together(self):
        # Older than both papers of the other series, so year order alone splits it.
        self.make_paper(self.thcs, "Đề năm 2022", "thcs-2022", 2022)
        response = self.catalog(q="20")
        series = [paper.series_id for paper in response.context["page"].object_list]
        self.assertEqual(series, [self.thcs.pk, self.thcs.pk, self.tre.pk, self.tre.pk])

    def test_series_without_cover_gets_a_generated_cover(self):
        response = self.catalog()
        self.assertContains(
            response, 'class="exam-cover exam-cover-generated"', count=2
        )
        self.assertContains(response, '<meta name="description"')

    def test_series_and_paper_pages_describe_themselves(self):
        self.thcs.summary = "Tổng hợp đề HSG THCS."
        self.thcs.save(update_fields=["summary"])
        response = self.client.get(self.thcs.get_absolute_url())
        self.assertContains(response, 'content="Tổng hợp đề HSG THCS."')
        self.assertContains(response, 'property="og:title"')
        response = self.client.get(self.paper.get_absolute_url())
        self.assertContains(response, "luyện tập và thi thử trên HNCode")


class ProblemAccessPrimingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        language = make_language()
        admin = User.objects.create_superuser("catalog_owner", password="pass")
        cls.owner, _ = Profile.objects.get_or_create(
            user=admin, defaults={"language": language}
        )
        cls.user = User.objects.create_user("catalog_viewer", password="pass")
        cls.profile, _ = Profile.objects.get_or_create(
            user=cls.user, defaults={"language": language}
        )
        cls.org = Organization.objects.create(
            name="Catalog Org",
            slug="catalog-org",
            short_name="CO",
            about="x",
            registrant=cls.owner,
            is_open=True,
        )
        cls.other_org = Organization.objects.create(
            name="Other Org",
            slug="catalog-other-org",
            short_name="OO",
            about="x",
            registrant=cls.owner,
            is_open=True,
        )
        cls.profile.organizations.add(cls.org)
        course = Course.objects.create(
            name="Lớp", slug="catalog-access", is_public=True
        )
        CourseRole.objects.create(course=course, user=cls.profile, role="ST")
        hidden_course = Course.objects.create(
            name="Lớp khác", slug="catalog-hidden", is_public=False
        )
        lesson = CourseLesson.objects.create(course=course, title="Bài", points=1)
        hidden_lesson = CourseLesson.objects.create(
            course=hidden_course, title="Bài", points=1
        )

        def problem(code, **fields):
            return ExamCatalogTests.make_problem(code, code, **fields)

        cls.problems = [
            problem("pa_public", is_public=True),
            problem("pa_org_member", is_public=True, is_organization_private=True),
            problem("pa_org_other", is_public=True, is_organization_private=True),
            problem("pa_private"),
            problem("pa_author"),
            problem("pa_curator"),
            problem("pa_tester"),
            problem("pa_course"),
            problem("pa_hidden_course"),
        ]
        by_code = {item.code: item for item in cls.problems}
        by_code["pa_org_member"].organizations.add(cls.org)
        by_code["pa_org_other"].organizations.add(cls.other_org)
        by_code["pa_author"].authors.add(cls.profile)
        by_code["pa_curator"].curators.add(cls.profile)
        by_code["pa_tester"].testers.add(cls.profile)
        CourseLessonProblem.objects.create(lesson=lesson, problem=by_code["pa_course"])
        CourseLessonProblem.objects.create(
            lesson=lesson, problem=by_code["pa_org_other"]
        )
        contest = Contest.objects.create(
            key="catalog_access",
            name="Kỳ thi",
            start_time=timezone.now() - timedelta(days=2),
            end_time=timezone.now() - timedelta(days=1),
            is_visible=True,
        )
        for order, code in enumerate(("pa_public", "pa_org_member", "pa_private"), 1):
            ContestProblem.objects.create(
                contest=contest, problem=by_code[code], points=100, order=order
            )
        series = ExamSeries.objects.create(
            title="Kỳ thi", slug="catalog-access", category="HSG", is_published=True
        )
        for index, source in enumerate(({"lesson": lesson}, {"contest": contest})):
            ExamPaper.objects.create(
                series=series,
                title=f"Đề {index}",
                slug=f"de-{index}",
                year=2024,
                is_published=True,
                **source,
            )
        CourseLessonProblem.objects.create(
            lesson=hidden_lesson, problem=by_code["pa_hidden_course"]
        )

    def access_for(self, user, profile):
        request = SimpleNamespace(user=user, profile=profile)
        access = LibraryAccess(request)
        access.prime_problem_access(self.problems)
        return access

    def test_bulk_access_matches_problem_rules(self):
        cases = (
            (self.user, self.profile),
            (User.objects.get(pk=self.owner.user_id), self.owner),
            (AnonymousUser(), None),
        )
        for user, profile in cases:
            access = self.access_for(user, profile)
            for item in self.problems:
                with self.subTest(user=str(user), problem=item.code):
                    self.assertEqual(
                        access.problem_access[item.pk],
                        item.is_accessible_by(user, in_contest=False),
                    )

    def test_catalog_does_not_check_problems_one_by_one(self):
        self.client.force_login(self.user)
        with patch.object(
            Problem, "is_accessible_by", side_effect=AssertionError("not primed")
        ):
            response = self.client.get(reverse("exam_library"))
        (card,) = response.context["cards"]
        # pa_course and pa_org_other come from the course, pa_private stays hidden.
        self.assertEqual((card["papers"], card["problems"]), (2, 4))

    def catalog_queries(self):
        cache.clear()
        with CaptureQueriesContext(connection) as queries:
            self.assertEqual(self.client.get(reverse("exam_library")).status_code, 200)
        return [query["sql"] for query in queries]

    def test_catalog_queries_do_not_grow_with_problems(self):
        lesson = CourseLesson.objects.get(title="Bài", course__slug="catalog-access")
        for user in (self.user, self.owner.user):
            self.client.force_login(user)
            self.catalog_queries()
            before = len(self.catalog_queries())
            for index in range(10):
                CourseLessonProblem.objects.create(
                    lesson=lesson,
                    problem=ExamCatalogTests.make_problem(
                        f"pa_extra_{user.pk}_{index}", "Bài thêm"
                    ),
                )
            with self.subTest(user=user.username):
                self.assertEqual(len(self.catalog_queries()), before)

    def test_catalog_does_not_load_items_without_source_access(self):
        ExamPaper.objects.filter(contest__isnull=False).delete()
        ExamPaper.objects.update(list_metadata=True)
        response = self.client.get(reverse("exam_library"))
        self.assertEqual([card["problems"] for card in response.context["cards"]], [0])
        self.assertFalse(
            [sql for sql in self.catalog_queries() if "FROM `judge_problem`" in sql]
        )


class ExamCoverTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def test_media_cover_is_resized_to_webp(self):
        from PIL import Image

        source = BytesIO()
        Image.new("RGB", (1600, 800), "#2158a6").save(source, "PNG")
        name = default_storage.save(
            "catalog-tests/cover.png", ContentFile(source.getvalue())
        )
        self.addCleanup(default_storage.delete, name)
        url = cover_thumbnail_url(settings.MEDIA_URL + name)
        self.assertTrue(url.endswith(".webp"))
        thumb_name = url[len(settings.MEDIA_URL) :]
        self.addCleanup(default_storage.delete, thumb_name)
        with default_storage.open(thumb_name, "rb") as thumb:
            self.assertEqual(Image.open(thumb).size, (COVER_WIDTH, 360))
        self.assertEqual(cover_thumbnail_url(settings.MEDIA_URL + name), url)

    def test_other_covers_are_left_unchanged(self):
        for url in (
            "https://images.example.org/media/cover.png",
            settings.MEDIA_URL + "missing/cover.png",
            settings.MEDIA_URL + "../secret.png",
        ):
            with self.subTest(url=url):
                self.assertEqual(cover_thumbnail_url(url), url)
