"""The in-lesson problem page: /course/<slug>/lesson/<id>/problem/<code>.

Same page as the standalone problem view, plus a left column listing every
problem in the lesson. Anonymous -> login redirect; non-member -> 404; problem
outside the lesson -> 404; enrolled member -> 200 with the whole lesson listed.
"""

from datetime import timedelta
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage, default_storage
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from judge.models import (
    Course,
    CourseLesson,
    CourseLessonProblem,
    CourseRole,
    Language,
    Problem,
    ProblemGroup,
    Profile,
    Solution,
)
from judge.models.course import RoleInCourse


class LessonProblemPageTest(TestCase):
    fixtures = ["language_small"]

    def setUp(self):
        self.lang = Language.objects.first()
        self.group = ProblemGroup.objects.create(name="g", full_name="Group")

        self.student = self._make_profile("student")
        self.outsider = self._make_profile("outsider")

        self.course = Course.objects.create(
            name="C", slug="c", about="a", is_public=True, is_open=True
        )
        CourseRole.objects.create(
            course=self.course, user=self.student, role=RoleInCourse.STUDENT
        )

        self.lesson = CourseLesson.objects.create(
            course=self.course, title="Lesson One", content="c", order=1, points=100
        )
        self.first = self._make_problem("p1", "First Problem")
        self.second = self._make_problem("p2", "Second Problem")
        CourseLessonProblem.objects.create(
            lesson=self.lesson, problem=self.first, order=1, score=60
        )
        CourseLessonProblem.objects.create(
            lesson=self.lesson, problem=self.second, order=2, score=40
        )
        self.unrelated = self._make_problem("p3", "Unrelated Problem")

    def _make_profile(self, name):
        user = User.objects.create_user(name, f"{name}@x.com", "pw")
        profile, _ = Profile.objects.get_or_create(
            user=user, defaults={"language": self.lang}
        )
        return profile

    def _make_problem(self, code, name):
        problem = Problem.objects.create(
            code=code,
            name=name,
            description="d",
            group=self.group,
            time_limit=1.0,
            memory_limit=65536,
            points=100.0,
            is_public=True,
            date="2020-01-01T00:00:00Z",
        )
        problem.allowed_languages.set(Language.objects.all())
        return problem

    def _url(self, problem=None):
        return reverse(
            "course_lesson_problem_detail",
            args=(self.course.slug, self.lesson.id, (problem or self.first).code),
        )

    def test_anonymous_is_redirected_to_login(self):
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response["Location"])

    def test_non_member_gets_404(self):
        self.client.force_login(self.outsider.user)
        self.assertEqual(self.client.get(self._url()).status_code, 404)

    def test_problem_outside_the_lesson_gets_404(self):
        self.client.force_login(self.student.user)
        self.assertEqual(self.client.get(self._url(self.unrelated)).status_code, 404)

    def test_enrolled_member_sees_the_problem_and_the_lesson_sidebar(self):
        self.client.force_login(self.student.user)
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()

        # The problem page itself is still rendered.
        self.assertIn("First Problem", html)
        # The extra left column lists every problem in the lesson...
        self.assertIn('class="left-sidebar"', html)
        self.assertIn("sidebar-problem", html)
        self.assertIn("Second Problem", html)
        self.assertIn(self._url(self.second), html)
        # ...and links back to the lesson.
        self.assertIn(
            reverse("course_lesson_detail", args=(self.course.slug, self.lesson.id)),
            html,
        )

    def test_title_names_the_lesson_and_the_problem(self):
        self.client.force_login(self.student.user)
        response = self.client.get(self._url())
        self.assertEqual(response.context["title"], "Lesson One - First Problem")

    def test_page_keeps_the_card_wrapper(self):
        # The lesson nav sits inside the card next to the problem, so this page
        # keeps base.html's .wrapper rather than going full-bleed.
        self.client.force_login(self.student.user)
        html = self.client.get(self._url()).content.decode()
        self.assertIn('<main id="content" class="wrapper">', html)

    def test_current_problem_is_marked_active_in_the_sidebar(self):
        self.client.force_login(self.student.user)
        entries = self.client.get(self._url()).context["lesson_sidebar_problems"]
        self.assertEqual(
            [(e["problem"].code, e["is_current"]) for e in entries],
            [("p1", True), ("p2", False)],
        )

    def test_lesson_page_links_problems_to_the_in_lesson_page(self):
        self.client.force_login(self.student.user)
        response = self.client.get(
            reverse("course_lesson_detail", args=(self.course.slug, self.lesson.id))
        )
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn(self._url(self.first), html)
        self.assertNotIn(
            'href="%s"' % reverse("problem_detail", args=[self.first.code]), html
        )

    def test_pdf_in_media_storage_opens_from_standalone_and_lesson_pages(self):
        pdf_path = reverse("problem_pdf_description", args=[self.first.code])
        pdf_bytes = b"%PDF-1.4\n%%EOF\n"
        with TemporaryDirectory() as media_root, self.settings(MEDIA_ROOT=media_root):
            self.first.pdf_description = default_storage.save(
                "problem_pdfs/uploaded-statement.pdf", ContentFile(pdf_bytes)
            )
            self.first.save(update_fields=["pdf_description"])

            self.client.force_login(self.student.user)
            for page_url in (
                reverse("problem_detail", args=[self.first.code]),
                self._url(),
            ):
                with self.subTest(page_url=page_url):
                    page = self.client.get(page_url)
                    self.assertContains(page, f'href="{pdf_path}"')
                    self.assertContains(page, f'src="{pdf_path}"')
                    pdf = self.client.get(pdf_path)
                    self.assertEqual(pdf.status_code, 200)
                    self.assertEqual(pdf["Content-Type"], "application/pdf")
                    self.assertEqual(
                        pdf["Content-Disposition"], 'inline; filename="p1.pdf"'
                    )
                    self.assertEqual(b"".join(pdf.streaming_content), pdf_bytes)

    def test_pdf_in_judge_storage_preserves_private_problem_access(self):
        self.first.is_public = False
        pdf_path = reverse("problem_pdf_description", args=[self.first.code])
        pdf_bytes = b"%PDF-1.4\n%%EOF\n"
        with TemporaryDirectory() as data_root, patch.object(
            Problem._meta.get_field("pdf_description"),
            "storage",
            FileSystemStorage(location=data_root),
        ):
            self.first.refresh_from_db(fields=["pdf_description"])
            self.first.pdf_description.save(
                "statement.pdf", ContentFile(pdf_bytes), save=False
            )
            self.first.save(update_fields=["is_public", "pdf_description"])

            self.client.force_login(self.outsider.user)
            self.assertEqual(self.client.get(pdf_path).status_code, 404)

            self.client.force_login(self.student.user)
            pdf = self.client.get(pdf_path)
            self.assertEqual(pdf.status_code, 200)
            self.assertEqual(b"".join(pdf.streaming_content), pdf_bytes)

    def test_missing_media_pdf_returns_404(self):
        self.first.pdf_description = "problem_pdfs/missing-statement.pdf"
        self.first.save(update_fields=["pdf_description"])
        with TemporaryDirectory() as media_root, self.settings(MEDIA_ROOT=media_root):
            response = self.client.get(
                reverse("problem_pdf_description", args=[self.first.code])
            )
            self.assertEqual(response.status_code, 404)

    def test_editorial_shortcut_matches_editorial_permissions(self):
        now = timezone.now()
        solution = Solution.objects.create(
            problem=self.first, content="A solution", is_public=False, publish_on=now
        )
        detail_url = reverse("problem_detail", args=[self.first.code])
        editorial_url = reverse("problem_editorial", args=[self.first.code])
        shortcut = f'href="{editorial_url}"'

        self.client.force_login(self.student.user)
        for page_url in (detail_url, self._url()):
            self.assertNotContains(self.client.get(page_url), shortcut)
        self.assertEqual(self.client.get(editorial_url).status_code, 404)

        solution.is_public = True
        solution.publish_on = now + timedelta(days=1)
        solution.save()
        self.assertNotContains(self.client.get(self._url()), shortcut)
        self.assertEqual(self.client.get(editorial_url).status_code, 404)

        solution.publish_on = now
        solution.save()
        self.assertContains(self.client.get(self._url()), shortcut)
        self.assertEqual(self.client.get(editorial_url).status_code, 200)

        solution.is_public = False
        solution.save()
        admin = self._make_profile("editorial_admin").user
        admin.is_superuser = True
        admin.is_staff = True
        admin.save(update_fields=["is_superuser", "is_staff"])
        self.client.force_login(admin)
        for page_url in (detail_url, self._url()):
            self.assertContains(self.client.get(page_url), shortcut)
        self.assertEqual(self.client.get(editorial_url).status_code, 200)

        solution.delete()
        self.assertNotContains(self.client.get(detail_url), shortcut)
        self.assertEqual(self.client.get(editorial_url).status_code, 404)

    def test_sidebar_management_labels_follow_interface_language(self):
        admin = self._make_profile("sidebar_admin").user
        admin.is_superuser = True
        admin.save(update_fields=["is_superuser"])
        self.client.force_login(admin)

        response = self.client.get(self._url(), headers={"accept-language": "en"})
        self.assertContains(response, "Management tools")
        self.assertContains(response, "Available judges")

        response = self.client.get(self._url(), headers={"accept-language": "vi"})
        self.assertContains(response, "Công cụ quản lý")
        self.assertContains(response, "Máy chấm sẵn sàng")
