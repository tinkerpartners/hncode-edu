"""Bulk add course members: /course/<slug>/members.

The add form takes a multiline textarea of usernames (whitespace/comma
separated, deduped) plus a role. Unknown usernames block the submit with an
error listing them; usernames already enrolled are skipped and reported in the
result message. The members table is paginated at MEMBERS_PAGE_SIZE per page.
"""

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from judge.models import Course, CourseRole, Language, Profile
from judge.models.course import RoleInCourse
from judge.views.course import CourseMemberForm, MEMBERS_PAGE_SIZE


class CourseMembersBulkBase(TestCase):
    fixtures = ["language_small"]

    def setUp(self):
        self.lang = Language.objects.first()
        self.course = Course.objects.create(
            name="C", slug="c", about="a", is_public=True, is_open=True
        )
        self.teacher = self._make_profile("teacher")
        CourseRole.objects.create(
            course=self.course, user=self.teacher, role=RoleInCourse.TEACHER
        )
        self.alice = self._make_profile("alice")
        self.bob = self._make_profile("bob")
        self.carol = self._make_profile("carol")

    def _make_profile(self, name):
        user = User.objects.create_user(name, f"{name}@x.com", "pw")
        profile, _ = Profile.objects.get_or_create(
            user=user, defaults={"language": self.lang}
        )
        return profile


class CourseMemberFormTest(CourseMembersBulkBase):
    def _form(self, users, role=RoleInCourse.STUDENT, current_user_role=None):
        return CourseMemberForm(
            {"users": users, "role": role},
            course=self.course,
            current_user_role=current_user_role or RoleInCourse.TEACHER,
        )

    def test_multiline_input(self):
        form = self._form("alice\nbob\ncarol")
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(
            [p.user.username for p in form.cleaned_data["users"]],
            ["alice", "bob", "carol"],
        )

    def test_commas_whitespace_and_duplicates(self):
        form = self._form("alice, bob\n  carol,alice\n\nbob")
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(
            [p.user.username for p in form.cleaned_data["users"]],
            ["alice", "bob", "carol"],
        )

    def test_case_insensitive_match(self):
        form = self._form("ALICE")
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["users"], [self.alice])

    def test_unknown_username_blocks_submit(self):
        form = self._form("alice\nnosuchuser\nbob")
        self.assertFalse(form.is_valid())
        self.assertIn("nosuchuser", str(form.errors["users"]))

    def test_already_member_is_skipped_not_error(self):
        CourseRole.objects.create(
            course=self.course, user=self.alice, role=RoleInCourse.STUDENT
        )
        form = self._form("alice\nbob")
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["users"], [self.bob])
        self.assertEqual(form.skipped_usernames, ["alice"])

    def test_empty_input(self):
        form = self._form("")
        self.assertFalse(form.is_valid())
        form = self._form(" , \n , ")
        self.assertFalse(form.is_valid())

    def test_assistant_cannot_assign_teacher_role(self):
        form = self._form(
            "alice",
            role=RoleInCourse.TEACHER,
            current_user_role=RoleInCourse.ASSISTANT,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("role", form.errors)

    def test_assistant_can_assign_student_role(self):
        form = self._form(
            "alice",
            role=RoleInCourse.STUDENT,
            current_user_role=RoleInCourse.ASSISTANT,
        )
        self.assertTrue(form.is_valid(), form.errors)


class CourseMembersViewTest(CourseMembersBulkBase):
    def _url(self, page=None):
        url = reverse("course_members", args=[self.course.slug])
        return f"{url}?page={page}" if page else url

    def test_bulk_add_creates_roles(self):
        self.client.force_login(self.teacher.user)
        response = self.client.post(
            self._url(),
            {"users": "alice\nbob", "role": RoleInCourse.STUDENT},
        )
        self.assertRedirects(response, self._url())
        self.assertTrue(
            CourseRole.objects.filter(
                course=self.course, user=self.alice, role=RoleInCourse.STUDENT
            ).exists()
        )
        self.assertTrue(
            CourseRole.objects.filter(
                course=self.course, user=self.bob, role=RoleInCourse.STUDENT
            ).exists()
        )

    def test_bulk_add_skips_existing_and_reports(self):
        CourseRole.objects.create(
            course=self.course, user=self.alice, role=RoleInCourse.STUDENT
        )
        self.client.force_login(self.teacher.user)
        response = self.client.post(
            self._url(),
            {"users": "alice\nbob", "role": RoleInCourse.STUDENT},
            follow=True,
        )
        messages = [str(m) for m in response.context["messages"]]
        self.assertTrue(any("alice" in m for m in messages), messages)
        self.assertEqual(
            CourseRole.objects.filter(course=self.course, user=self.alice).count(), 1
        )
        self.assertTrue(
            CourseRole.objects.filter(course=self.course, user=self.bob).exists()
        )

    def test_unknown_username_adds_nothing(self):
        self.client.force_login(self.teacher.user)
        response = self.client.post(
            self._url(),
            {"users": "alice\nnosuchuser", "role": RoleInCourse.STUDENT},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("nosuchuser", str(response.context["form"].errors["users"]))
        self.assertFalse(
            CourseRole.objects.filter(course=self.course, user=self.alice).exists()
        )
        # The members table (and its pagination context) still renders
        self.assertIn("page_obj", response.context)

    def test_pagination(self):
        for i in range(2 * MEMBERS_PAGE_SIZE + 5):
            CourseRole.objects.create(
                course=self.course,
                user=self._make_profile(f"student{i:03d}"),
                role=RoleInCourse.STUDENT,
            )
        self.client.force_login(self.teacher.user)

        response = self.client.get(self._url())
        page_obj = response.context["page_obj"]
        # 25 students + 1 teacher = 26 members -> 3 pages of 10
        self.assertEqual(page_obj.paginator.count, 2 * MEMBERS_PAGE_SIZE + 6)
        self.assertEqual(page_obj.paginator.num_pages, 3)
        self.assertEqual(len(response.context["members"]), MEMBERS_PAGE_SIZE)
        # Teacher sorts first on page 1
        self.assertEqual(response.context["members"][0].role, RoleInCourse.TEACHER)

        response = self.client.get(self._url(page=2))
        self.assertEqual(len(response.context["members"]), MEMBERS_PAGE_SIZE)

        # Out-of-range page clamps to the last page instead of erroring
        response = self.client.get(self._url(page=999))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["page_obj"].number, 3)

    def test_student_cannot_access(self):
        student = self._make_profile("student")
        CourseRole.objects.create(
            course=self.course, user=student, role=RoleInCourse.STUDENT
        )
        self.client.force_login(student.user)
        self.assertEqual(self.client.get(self._url()).status_code, 404)
        response = self.client.post(
            self._url(), {"users": "alice", "role": RoleInCourse.STUDENT}
        )
        self.assertEqual(response.status_code, 404)
        self.assertFalse(
            CourseRole.objects.filter(course=self.course, user=self.alice).exists()
        )
