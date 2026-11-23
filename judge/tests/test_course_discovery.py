from django.core.cache import cache
from django.contrib.auth.models import Permission
from django.test import Client, TestCase
from django.urls import reverse

from judge.models import Course, CourseRole, Organization
from judge.models.course import RoleInCourse
from judge.tests.test_visibility_permissions import make_profile


class CourseDiscoveryTests(TestCase):
    fixtures = ["language_small"]

    @classmethod
    def setUpTestData(cls):
        cls.admin = make_profile("discovery_admin", is_staff=True, is_superuser=True)
        cls.teacher = make_profile("discovery_teacher")
        cls.student = make_profile("discovery_student")
        cls.other = make_profile("discovery_other")
        cls.public = Course.objects.create(
            name="Khoá thuật toán nổi bật",
            slug="discovery-public",
            is_public=True,
            is_featured=True,
        )
        cls.private = Course.objects.create(
            name="Khoá riêng của lớp", slug="discovery-private", is_featured=True
        )
        cls.paused = Course.objects.create(
            name="Khoá đang tạm ẩn",
            slug="discovery-paused",
            is_public=True,
            is_featured=True,
            is_suspended=True,
        )
        cls.group = Organization.objects.create(
            name="Nhóm thuật toán",
            slug="discovery-group",
            short_name="Group",
            about="Nhóm kiểm thử",
            registrant=cls.teacher,
        )
        cls.group.members.add(cls.student)
        cls.group_course = Course.objects.create(
            name="Khoá riêng của Nhóm",
            slug="discovery-group-course",
            is_public=True,
            is_featured=True,
        )
        cls.group_course.organizations.add(cls.group)
        CourseRole.objects.create(course=cls.private, user=cls.student)
        CourseRole.objects.create(
            course=cls.public, user=cls.teacher, role=RoleInCourse.TEACHER
        )

    def setUp(self):
        cache.clear()

    def courses(self, tab, **params):
        return self.client.get(reverse("course_list"), {"tab": tab, **params})

    def interest_url(self, course):
        return reverse("course_interest", args=[course.slug])

    def test_featured_requires_selection_and_respects_scope(self):
        ordinary = Course.objects.create(
            name="Khoá thường khác", slug="discovery-normal", is_public=True
        )
        response = self.courses("featured")
        self.assertEqual(set(response.context_data["courses"]), {self.public})
        self.assertNotContains(response, ordinary.name)
        self.client.force_login(self.student.user)
        self.assertEqual(
            set(self.courses("featured").context_data["courses"]),
            {self.public, self.private, self.group_course},
        )
        self.group.is_suspended = True
        self.group.save(update_fields=["is_suspended"])
        self.assertEqual(
            set(self.courses("featured").context_data["courses"]),
            {self.public, self.private},
        )
        self.client.force_login(self.admin.user)
        self.assertNotContains(self.courses("featured"), self.paused.name)

    def test_interest_is_personal_and_idempotent(self):
        self.client.force_login(self.student.user)
        next_url = reverse("course_list") + "?tab=all&search=thuật+toán"
        for _ in range(2):
            response = self.client.post(
                self.interest_url(self.public), {"action": "save", "next": next_url}
            )
            self.assertRedirects(response, next_url, fetch_redirect_response=False)
        self.assertEqual(self.public.interested_users.count(), 1)
        self.assertFalse(self.public.courserole_set.filter(user=self.student).exists())
        self.assertContains(self.courses("interested"), self.public.name)
        detail = self.client.get(self.public.get_absolute_url())
        self.assertContains(detail, 'aria-pressed="true"')
        self.assertContains(detail, 'title="Bỏ lưu khoá học"')
        self.client.force_login(self.other.user)
        self.assertNotContains(self.courses("interested"), self.public.name)
        self.client.force_login(self.student.user)
        for _ in range(2):
            self.client.post(self.interest_url(self.public), {"action": "remove"})
        self.assertNotContains(self.courses("interested"), self.public.name)
        self.assertFalse(self.public.interested_users.exists())

    def test_interest_hides_inaccessible_courses_and_reappears_if_restored(self):
        self.client.force_login(self.student.user)
        self.client.post(self.interest_url(self.group_course), {"action": "save"})
        self.assertContains(self.courses("interested"), self.group_course.name)
        self.group.is_suspended = True
        self.group.save(update_fields=["is_suspended"])
        self.assertNotContains(self.courses("interested"), self.group_course.name)
        self.assertEqual(
            self.client.post(
                self.interest_url(self.group_course), {"action": "save"}
            ).status_code,
            404,
        )
        self.assertTrue(
            self.group_course.interested_users.filter(pk=self.student.pk).exists()
        )
        self.group.is_suspended = False
        self.group.save(update_fields=["is_suspended"])
        self.assertContains(self.courses("interested"), self.group_course.name)

    def test_interest_write_requires_login_and_csrf(self):
        self.assertEqual(self.courses("interested").status_code, 302)
        self.assertEqual(
            self.client.post(
                self.interest_url(self.public), {"action": "save"}
            ).status_code,
            302,
        )
        authenticated = Client(enforce_csrf_checks=True)
        authenticated.force_login(self.student.user)
        self.assertEqual(
            authenticated.post(
                self.interest_url(self.public), {"action": "save"}
            ).status_code,
            403,
        )
        self.client.force_login(self.other.user)
        self.assertEqual(
            self.client.post(
                self.interest_url(self.private), {"action": "save"}
            ).status_code,
            404,
        )
        result = self.client.post(
            self.interest_url(self.public),
            {
                "action": "save",
                "next": "https://example.com/",
            },
        )
        self.assertRedirects(result, self.public.get_absolute_url())

    def test_only_system_admin_sees_featured_control_in_course_settings(self):
        url = reverse("course_edit", args=[self.public.slug])
        fields = {
            "name": self.public.name,
            "slug": self.public.slug,
            "about": "",
            "visibility_mode": "public",
            "participation_mode": "approval",
            "show_rankings": "on",
        }
        self.client.force_login(self.teacher.user)
        self.assertNotContains(self.client.get(url), 'name="is_featured"')
        self.assertEqual(self.client.post(url, fields).status_code, 302)
        self.public.refresh_from_db()
        self.assertTrue(self.public.is_featured)
        self.client.force_login(self.admin.user)
        self.assertContains(self.client.get(url), 'name="is_featured"')
        admin_list = reverse("admin:judge_course_changelist")
        self.assertContains(self.client.get(admin_list), "Khoá học nổi bật")
        self.assertEqual(self.client.post(url, fields).status_code, 302)
        self.public.refresh_from_db()
        self.assertFalse(self.public.is_featured)

    def test_staff_cannot_change_featured_through_admin_forms(self):
        staff = self.teacher.user
        staff.is_staff = True
        staff.save(update_fields=["is_staff"])
        staff.user_permissions.add(Permission.objects.get(codename="change_course"))
        self.client.force_login(staff)
        change_url = reverse("admin:judge_course_change", args=[self.public.pk])
        response = self.client.get(change_url)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("is_featured", response.context["adminform"].form.fields)
        for old_value in (True, False):
            Course.objects.filter(pk=self.public.pk).update(is_featured=old_value)
            response = self.client.post(
                reverse("admin:judge_course_changelist"),
                {
                    "_save": "Save",
                    "form-TOTAL_FORMS": "1",
                    "form-INITIAL_FORMS": "1",
                    "form-0-id": self.public.pk,
                    "form-0-is_featured": "" if old_value else "on",
                },
            )
            self.assertEqual(response.status_code, 302)
            self.public.refresh_from_db()
            self.assertEqual(self.public.is_featured, old_value)
        self.client.force_login(self.admin.user)
        form = self.client.get(change_url).context["adminform"].form
        self.assertIn("is_featured", form.fields)
        self.assertFalse(form.fields["is_featured"].disabled)

    def test_group_interested_query_only_marks_courses_actually_saved(self):
        self.client.force_login(self.student.user)
        url = reverse("organization_courses", args=[self.group.pk, self.group.slug])
        response = self.client.get(url, {"tab": "interested"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.group_course.name)
        self.assertEqual(response.context_data["interested_course_ids"], set())
        self.group_course.interested_users.add(self.student)
        response = self.client.get(url, {"tab": "interested"})
        self.assertEqual(
            response.context_data["interested_course_ids"], {self.group_course.pk}
        )
