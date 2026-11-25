from importlib import import_module

from django.apps import apps
from django.contrib.auth.models import User
from django.core.cache import cache
from django.db import connection
from django.test import TestCase
from django.urls import reverse

from judge.models import (
    Course,
    CourseLesson,
    CourseLessonProblem,
    CourseRole,
    ExamDivision,
    ExamGroup,
    ExamPaper,
    ExamSeries,
    Problem,
    Profile,
)
from judge.tests.strict_helpers import make_group, make_language

SEED = import_module("judge.migrations.0296_seed_exam_groups")


class ExamGroupTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("group_student", password="pass")
        cls.profile, _ = Profile.objects.get_or_create(
            user=cls.user, defaults={"language": make_language()}
        )
        cls.course = Course.objects.create(
            name="Kho đề", slug="group-course", is_public=True
        )
        CourseRole.objects.create(course=cls.course, user=cls.profile, role="ST")
        cls.series = {
            slug: ExamSeries.objects.create(
                title=slug,
                slug=slug,
                category="Tin học trẻ",
                region="Toàn quốc",
                level="Phổ thông",
                is_published=True,
            )
            for slug in (*SEED.GROUPS, "hsg")
        }
        cls.youth = cls.series["tin-hoc-tre-toan-quoc"]
        cls.students = cls.series["olympic-tin-hoc-sinh-vien-toan-quoc"]
        cls.a = cls.make_paper(cls.youth, "De A 2025", "a-2025", 2025, "Bảng A")
        cls.b = cls.make_paper(cls.youth, "De B 2025", "b-2025", 2025, "Bảng B")
        cls.c2 = cls.make_paper(cls.youth, "De C2 2024", "c2-2024", 2024, "Bảng C2")
        cls.shared = cls.make_paper(
            cls.students, "De chung", "shared", 2023, "Không chuyên", "Cao đẳng"
        )
        cls.hsg = cls.make_paper(cls.series["hsg"], "De HSG", "hsg-2025", 2025)
        cls.problem = Problem.objects.create(
            code="group_sum",
            name="Tổng",
            group=make_group(),
            description="",
            time_limit=1,
            memory_limit=65536,
            points=100,
            is_public=True,
        )
        CourseLessonProblem.objects.create(
            lesson=cls.a.lesson, problem=cls.problem, score=100
        )
        SEED.create_groups(apps, connection.schema_editor(atomic=False))

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    @classmethod
    def make_paper(cls, series, title, slug, year, *divisions):
        paper = ExamPaper.objects.create(
            series=series,
            title=title,
            slug=slug,
            year=year,
            lesson=CourseLesson.objects.create(
                course=cls.course, title=title, points=100
            ),
            is_published=True,
            list_metadata=True,
        )
        paper.divisions.set(
            [ExamDivision.objects.get_or_create(name=name)[0] for name in divisions]
        )
        return paper

    def group(self, series, slug):
        return self.client.get(reverse("exam_group", args=[series.slug, slug]))

    def test_seed_creates_the_requested_groups_once(self):
        def titles(slug):
            return [group.title for group in self.series[slug].groups.all()]

        youth = ["Bảng A", "Bảng B & C", "Bảng D1", "Bảng M1", "Bảng M2"]
        self.assertEqual(titles("tin-hoc-tre-ha-noi"), youth)
        self.assertEqual(titles("tin-hoc-tre-toan-quoc"), youth)
        self.assertEqual(
            titles("olympic-tin-hoc-sinh-vien-toan-quoc"),
            [
                "Khối Cao đẳng",
                "Khối Không Chuyên Tin",
                "Khối Chuyên Tin",
                "Khối Siêu Cup",
            ],
        )
        self.assertEqual(titles("hsg"), [])
        count = ExamGroup.objects.count()
        SEED.create_groups(apps, connection.schema_editor(atomic=False))
        self.assertEqual(ExamGroup.objects.count(), count)

    def test_series_page_shows_groups_instead_of_papers(self):
        response = self.client.get(self.youth.get_absolute_url())
        counts = [card["papers"] for card in response.context["group_cards"]]
        self.assertEqual(counts, [1, 2, 0, 0, 0])
        self.assertNotContains(response, self.a.title)

    def test_group_card_counts_problems_of_its_own_papers(self):
        self.client.force_login(self.user)
        response = self.client.get(self.youth.get_absolute_url())
        counts = [card["problems"] for card in response.context["group_cards"]]
        self.assertEqual(counts, [1, 0, 0, 0, 0])

    def test_group_page_lists_only_its_divisions(self):
        response = self.group(self.youth, "bang-b-c")
        self.assertContains(response, self.b.title)
        self.assertContains(response, self.c2.title)
        self.assertNotContains(response, self.a.title)
        # Papers of one group only keep a single address.
        self.assertNotContains(response, "?group=")

    def test_empty_and_unknown_groups(self):
        self.assertEqual(self.group(self.youth, "bang-m1").status_code, 200)
        self.assertEqual(self.group(self.youth, "cao-dang").status_code, 404)
        ExamSeries.objects.filter(pk=self.youth.pk).update(is_published=False)
        self.assertEqual(self.group(self.youth, "bang-a").status_code, 404)

    def test_paper_shared_by_two_groups_goes_back_to_the_one_used(self):
        for slug in ("cao-dang", "khong-chuyen-tin"):
            with self.subTest(slug=slug):
                url = f"{self.shared.get_absolute_url()}?group={slug}"
                self.assertContains(self.group(self.students, slug), url)
                back = self.client.get(url).context["back_group"]
                self.assertEqual(back.slug, slug)
        response = self.client.get(self.shared.get_absolute_url())
        self.assertIsNone(response.context["back_group"])
        response = self.client.get(self.a.get_absolute_url())
        self.assertEqual(response.context["back_group"].slug, "bang-a")

    def test_search_all_view_and_ungrouped_series_list_papers(self):
        url = self.youth.get_absolute_url()
        for params in ({"q": "2025"}, {"view": "all"}, {"year": "2025"}):
            with self.subTest(params=params):
                response = self.client.get(url, params)
                self.assertContains(response, self.a.title)
                self.assertContains(response, self.b.title)
        response = self.client.get(
            reverse("exam_group", args=[self.youth.slug, "bang-a"]), {"q": "2025"}
        )
        self.assertNotContains(response, self.b.title)
        response = self.client.get(self.series["hsg"].get_absolute_url())
        self.assertContains(response, self.hsg.title)

    def test_admin_edits_groups_inside_the_series(self):
        admin = User.objects.create_superuser("group_admin", password="pass")
        Profile.objects.get_or_create(
            user=admin, defaults={"language": make_language()}
        )
        self.client.force_login(admin)
        response = self.client.get(
            reverse("admin:judge_examseries_change", args=[self.youth.pk])
        )
        self.assertContains(response, "groups-0-title")
        self.assertContains(response, "Bảng B &amp; C")
