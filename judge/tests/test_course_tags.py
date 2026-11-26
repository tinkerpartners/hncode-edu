from importlib import import_module
from io import StringIO

from django.apps import apps
from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.management import call_command
from django.db import connection
from django.test import TestCase
from django.urls import reverse

from judge.models import Course, CourseTag, ExamGroup, ExamSeries, Profile
from judge.tests.strict_helpers import make_language
from judge.utils.course_tags import sync_library_course_tags, tag_slug

ORDER = import_module("judge.migrations.0300_seed_featured_course_order")


def make_course(slug, name=None, **fields):
    return Course.objects.create(
        name=name or slug, slug=slug, about="", is_public=True, **fields
    )


class CourseTagSyncTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.youth = ExamSeries.objects.create(
            title="Tin học trẻ Hà Nội",
            slug="tht-hn",
            category="Tin học trẻ",
            region="Hà Nội",
            level="Phổ thông",
        )
        cls.national = ExamSeries.objects.create(
            title="HSG Quốc gia", slug="hsgqg", category="HSG", region="Toàn quốc"
        )
        cls.group_course = make_course("tht-hn-a")
        cls.series_course = make_course("hsgqg-course")
        ExamGroup.objects.create(
            series=cls.youth, title="Bảng A", slug="bang-a", course=cls.group_course
        )
        ExamGroup.objects.create(series=cls.youth, title="Bảng B", slug="bang-b")
        cls.national.course = cls.series_course
        cls.national.save()

    def names(self, course):
        return sorted((tag.kind, tag.name) for tag in course.tags.all())

    def test_courses_get_the_tags_of_their_exam_and_division(self):
        self.assertEqual(sync_library_course_tags(), 6)
        self.assertEqual(
            self.names(self.group_course),
            [
                ("exam", "Tin học trẻ Hà Nội"),
                ("group", "Bảng A"),
                ("level", "Phổ thông"),
                ("region", "Hà Nội"),
            ],
        )
        self.assertEqual(
            self.names(self.series_course),
            [("exam", "HSG Quốc gia"), ("region", "Toàn quốc")],
        )
        self.assertEqual(
            CourseTag.objects.get(kind="region", name="Hà Nội").slug, "region-ha-noi"
        )

    def test_rerunning_adds_nothing_and_keeps_tags_set_by_hand(self):
        manual = CourseTag.objects.create(kind="level", name="THCS", slug="level-thcs")
        self.group_course.tags.add(manual)
        out = StringIO()
        call_command("sync_course_tags", stdout=out)
        self.assertIn("Added 6", out.getvalue())
        count = CourseTag.objects.count()
        self.assertEqual(sync_library_course_tags(), 0)
        self.assertEqual(CourseTag.objects.count(), count)
        self.assertIn(manual, self.group_course.tags.all())

    def test_slugs_keep_vietnamese_letters_readable(self):
        self.assertEqual(tag_slug("exam", "Đề HSG Đà Nẵng"), "exam-de-hsg-da-nang")


class CourseListTagTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.exam = CourseTag.objects.create(kind="exam", name="HSG", slug="exam-hsg")
        cls.region = CourseTag.objects.create(
            kind="region", name="Hà Nội", slug="region-ha-noi"
        )
        cls.both = make_course("both", "Cả hai")
        cls.exam_only = make_course("exam-only", "Chỉ kỳ thi")
        cls.untagged = make_course("untagged", "Chưa gắn")
        cls.both.tags.add(cls.exam, cls.region)
        cls.exam_only.tags.add(cls.exam)

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def listed(self, **params):
        response = self.client.get(reverse("course_list"), {"tab": "all", **params})
        return response, [course.slug for course in response.context["courses"]]

    def test_filter_by_one_tag(self):
        _, slugs = self.listed(tag="exam-hsg")
        self.assertEqual(sorted(slugs), ["both", "exam-only"])

    def test_tags_of_different_kinds_must_all_match(self):
        response = self.client.get(
            reverse("course_list"),
            {"tab": "all", "tag": ["exam-hsg", "region-ha-noi"]},
        )
        self.assertEqual([c.slug for c in response.context["courses"]], ["both"])
        self.assertEqual(
            response.context["selected_tag_slugs"], {"exam-hsg", "region-ha-noi"}
        )

    def test_unknown_tag_is_ignored(self):
        _, slugs = self.listed(tag="nope")
        self.assertEqual(sorted(slugs), ["both", "exam-only", "untagged"])

    def test_filters_and_cards_show_the_tags(self):
        response, _ = self.listed()
        kinds = [kind for kind, _, _ in response.context["tag_filters"]]
        self.assertEqual(kinds, ["exam", "region"])
        self.assertContains(response, 'name="tag"', count=2)
        self.assertContains(response, 'class="course-tags"', count=2)
        self.assertContains(response, "?tab=all&amp;tag=region-ha-noi")

    def test_pages_keep_the_chosen_tags(self):
        for index in range(10):
            make_course(f"more-{index}").tags.add(self.exam)
        response = self.client.get(
            reverse("course_list"), {"tab": "all", "tag": "exam-hsg"}
        )
        self.assertIn("tag=exam-hsg", response.context["page_prefix"])
        self.assertTrue(response.context["page_obj"].has_next())


class FeaturedOrderTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def test_featured_tab_follows_the_order_then_the_name(self):
        make_course("late", "B late", is_featured=True, featured_order=30)
        make_course("first", "Z first", is_featured=True, featured_order=10)
        make_course("tie-b", "B tie", is_featured=True, featured_order=20)
        make_course("tie-a", "A tie", is_featured=True, featured_order=20)
        response = self.client.get(reverse("course_list"), {"tab": "featured"})
        self.assertEqual(
            [course.slug for course in response.context["courses"]],
            ["first", "tie-a", "tie-b", "late"],
        )

    def test_migration_orders_library_courses_and_keeps_manual_order(self):
        make_course("thd_hsgqg_v1")
        make_course("thd_tinhoctre_tq_a")
        make_course("thd_olympic_sv", featured_order=5)
        ORDER.set_order(apps, connection.schema_editor(atomic=False))
        orders = dict(Course.objects.values_list("slug", "featured_order"))
        self.assertEqual(orders["thd_tinhoctre_tq_a"], 10)
        self.assertEqual(orders["thd_hsgqg_v1"], 130)
        self.assertEqual(orders["thd_olympic_sv"], 5)

    def test_only_superusers_edit_the_order_in_admin(self):
        admin = User.objects.create_superuser("order_admin", password="pass")
        Profile.objects.get_or_create(
            user=admin, defaults={"language": make_language()}
        )
        self.client.force_login(admin)
        make_course("listed")
        response = self.client.get(reverse("admin:judge_course_changelist"))
        self.assertContains(response, "featured_order")
