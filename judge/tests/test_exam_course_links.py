from importlib import import_module

from django.apps import apps
from django.contrib.auth.models import User
from django.core.cache import cache
from django.db import connection
from django.test import TestCase
from django.urls import reverse
from django.utils.translation import gettext

from judge.models import (
    Course,
    CourseRole,
    ExamGroup,
    ExamSeries,
    Profile,
)
from judge.tests.strict_helpers import make_language

GROUPS = import_module("judge.migrations.0296_seed_exam_groups")
LINKS = import_module("judge.migrations.0298_seed_exam_course_links")


class ExamCourseLinkTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("link_student", password="pass")
        cls.profile, _ = Profile.objects.get_or_create(
            user=cls.user, defaults={"language": make_language()}
        )
        cls.youth = ExamSeries.objects.create(
            title="Tin học trẻ Toàn quốc",
            slug="tin-hoc-tre-toan-quoc",
            category="Tin học trẻ",
            region="Toàn quốc",
            is_published=True,
        )
        cls.national = ExamSeries.objects.create(
            title="HSG Tin học Quốc gia",
            slug="hsg-tin-hoc-quoc-gia",
            category="HSG",
            region="Toàn quốc",
            is_published=True,
        )
        GROUPS.create_groups(apps, connection.schema_editor(atomic=False))
        cls.open_course = Course.objects.create(
            name="Tin học trẻ - Bảng A",
            slug="thd_tinhoctre_tq_a",
            about="",
            is_public=True,
        )
        cls.hidden_course = Course.objects.create(
            name="Tin học trẻ - Bảng B",
            slug="thd_tinhoctre",
            about="",
            is_public=False,
        )
        cls.national_course = Course.objects.create(
            name="HSG Quốc gia", slug="thd_hsgqg_v1", about="", is_public=True
        )
        LINKS.link_courses(apps, connection.schema_editor(atomic=False))
        cls.group_a = cls.youth.groups.get(slug="bang-a")
        cls.group_b = cls.youth.groups.get(slug="bang-b")

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def course_link(self, course):
        return f'href="{course.get_absolute_url()}"'

    def test_migration_links_existing_courses_and_skips_the_rest(self):
        self.assertEqual(self.group_a.course, self.open_course)
        self.assertEqual(self.group_b.course, self.hidden_course)
        # No course with this slug exists here.
        self.assertIsNone(self.youth.groups.get(slug="bang-c1").course)
        self.national.refresh_from_db()
        self.assertEqual(self.national.course, self.national_course)

    def test_migration_keeps_a_course_set_by_hand(self):
        other = Course.objects.create(
            name="Khác", slug="khac", about="", is_public=True
        )
        ExamGroup.objects.filter(pk=self.group_a.pk).update(course=other)
        LINKS.link_courses(apps, connection.schema_editor(atomic=False))
        self.group_a.refresh_from_db()
        self.assertEqual(self.group_a.course, other)

    def test_group_cards_link_to_visible_courses_only(self):
        response = self.client.get(self.youth.get_absolute_url())
        cards = {
            card["group"].slug: card["group"].visible_course
            for card in response.context["group_cards"]
        }
        self.assertEqual(cards["bang-a"], self.open_course)
        self.assertIsNone(cards["bang-b"])
        self.assertIsNone(cards["bang-c1"])
        self.assertContains(response, self.course_link(self.open_course), count=1)
        self.assertNotContains(response, self.course_link(self.hidden_course))
        self.assertContains(response, "exam-card-foot", count=1)
        # The series page itself only links a course when it has no divisions.
        self.assertIsNone(response.context["course_link"])

    def test_member_of_a_private_course_sees_its_link(self):
        CourseRole.objects.create(
            course=self.hidden_course, user=self.profile, role="ST"
        )
        self.client.force_login(self.user)
        response = self.client.get(self.youth.get_absolute_url())
        self.assertContains(response, self.course_link(self.hidden_course), count=1)

    def test_group_page_links_its_course_in_the_heading(self):
        response = self.client.get(self.group_a.get_absolute_url())
        self.assertEqual(response.context["course_link"], self.open_course)
        self.assertContains(response, gettext("Open course"))
        self.assertContains(response, self.course_link(self.open_course), count=1)

        response = self.client.get(self.group_b.get_absolute_url())
        self.assertIsNone(response.context["course_link"])
        self.assertNotContains(response, self.course_link(self.hidden_course))

    def test_series_without_divisions_links_its_course(self):
        response = self.client.get(self.national.get_absolute_url())
        self.assertEqual(response.context["course_link"], self.national_course)
        self.assertContains(response, self.course_link(self.national_course), count=1)

    def test_admin_sets_courses_on_the_series_and_its_groups(self):
        admin = User.objects.create_superuser("link_admin", password="pass")
        Profile.objects.get_or_create(
            user=admin, defaults={"language": make_language()}
        )
        self.client.force_login(admin)
        response = self.client.get(
            reverse("admin:judge_examseries_change", args=[self.youth.pk])
        )
        self.assertContains(response, 'name="course"')
        self.assertContains(response, 'name="groups-0-course"')
