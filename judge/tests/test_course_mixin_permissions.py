"""Course permission mixins must deny BEFORE the request handler runs.

CourseAccessibleMixin / CourseEditableMixin / CourseAdminMixin used to call
super().dispatch() first and only then raise for unauthorized users, so a
denied POST still performed its side effects behind the 403/404 response
(e.g. a student saving course edits). The checks now run pre-dispatch.
"""

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from judge.models import Course, CourseRole, Language, Profile
from judge.models.course import RoleInCourse


class CourseMixinPermissionTest(TestCase):
    fixtures = ["language_small"]

    def setUp(self):
        self.lang = Language.objects.first()
        self.course = Course.objects.create(
            name="Mixin Course",
            slug="mixin-course",
            about="a",
            is_public=True,
            is_open=True,
        )
        self.teacher = self._make_profile("mx_teacher")
        self.student = self._make_profile("mx_student")
        self.outsider = self._make_profile("mx_outsider")
        CourseRole.objects.create(
            course=self.course, user=self.teacher, role=RoleInCourse.TEACHER
        )
        CourseRole.objects.create(
            course=self.course, user=self.student, role=RoleInCourse.STUDENT
        )

    def _make_profile(self, name):
        user = User.objects.create_user(name, f"{name}@x.com", "pw")
        profile, _ = Profile.objects.get_or_create(
            user=user, defaults={"language": self.lang}
        )
        return profile

    # --- CourseEditableMixin (course_edit) ---

    def test_teacher_can_get_edit_page(self):
        self.client.force_login(self.teacher.user)
        url = reverse("course_edit", args=[self.course.slug])
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_student_edit_denied_without_side_effects(self):
        self.client.force_login(self.student.user)
        url = reverse("course_edit", args=[self.course.slug])
        self.assertEqual(self.client.get(url).status_code, 404)
        response = self.client.post(
            url, {"name": "hacked", "about": "hacked", "slug": self.course.slug}
        )
        self.assertEqual(response.status_code, 404)
        self.course.refresh_from_db()
        self.assertEqual(self.course.name, "Mixin Course")

    def test_anonymous_edit_redirects_to_login(self):
        url = reverse("course_edit", args=[self.course.slug])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response.url)

    # --- CourseAccessibleMixin (course_grades) ---

    def test_enrolled_student_can_get_grades(self):
        self.client.force_login(self.student.user)
        url = reverse("course_grades", args=[self.course.slug])
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_outsider_grades_denied(self):
        self.client.force_login(self.outsider.user)
        url = reverse("course_grades", args=[self.course.slug])
        self.assertEqual(self.client.get(url).status_code, 403)

    # --- CourseAdminMixin (course_members) — regression guard ---

    def test_student_members_denied_without_side_effects(self):
        self.client.force_login(self.student.user)
        url = reverse("course_members", args=[self.course.slug])
        self.assertEqual(self.client.get(url).status_code, 404)
        before = CourseRole.objects.filter(course=self.course).count()
        response = self.client.post(
            url, {"users": "mx_outsider", "role": RoleInCourse.TEACHER}
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(CourseRole.objects.filter(course=self.course).count(), before)

    # --- flags still reach templates (setup_course idempotence) ---

    def test_course_detail_context_flags(self):
        self.client.force_login(self.student.user)
        response = self.client.get(reverse("course_detail", args=[self.course.slug]))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["is_accessible"])
        self.assertFalse(response.context["is_editable"])
