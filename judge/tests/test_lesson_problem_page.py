"""The in-lesson problem page: /course/<slug>/lesson/<id>/problem/<code>.

Same page as the standalone problem view, plus a left column listing every
problem in the lesson. Anonymous -> login redirect; non-member -> 404; problem
outside the lesson -> 404; enrolled member -> 200 with the whole lesson listed.
"""

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from judge.models import (
    Course,
    CourseLesson,
    CourseLessonProblem,
    CourseRole,
    Language,
    Problem,
    ProblemGroup,
    Profile,
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
        self.assertIn('id="lesson-problem-sidebar"', html)
        self.assertIn("Second Problem", html)
        self.assertIn(self._url(self.second), html)
        # ...and links back to the lesson.
        self.assertIn(
            reverse("course_lesson_detail", args=(self.course.slug, self.lesson.id)),
            html,
        )

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
