"""A16 (#104): self-refreshing grades tables.

The page polls its own URL with ?live=1&since=<token>. Unchanged token ->
204 without rendering; changed -> the normal page with a new token. The token
must move on every change that can alter a grade and stay put otherwise.
"""

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from judge.models import (
    BestSubmission,
    Contest,
    ContestParticipation,
    Course,
    CourseContest,
    CourseLesson,
    CourseLessonProblem,
    CourseRole,
    Language,
    Problem,
    ProblemGroup,
    Profile,
    Submission,
)
from judge.models.course import RoleInCourse
from judge.utils.grades_version import grades_version


def make_profile(username, **kw):
    user = User.objects.create_user(username, f"{username}@x.com", "pw", **kw)
    profile, _ = Profile.objects.get_or_create(
        user=user, defaults={"language": Language.objects.first()}
    )
    return profile


class GradesLiveRefreshTest(TestCase):
    fixtures = ["language_small"]

    @classmethod
    def setUpTestData(cls):
        cls.lang = Language.objects.first()
        group, _ = ProblemGroup.objects.get_or_create(
            name="a16", defaults={"full_name": "A16"}
        )
        cls.student = make_profile("a16_student")
        cls.other = make_profile("a16_other")  # student of another course
        cls.teacher = make_profile("a16_teacher")
        cls.course = Course.objects.create(
            name="A16", slug="a16", about="", is_public=False, is_open=False
        )
        cls.other_course = Course.objects.create(
            name="A16b", slug="a16b", about="", is_public=False, is_open=False
        )
        CourseRole.objects.create(
            course=cls.course, user=cls.student, role=RoleInCourse.STUDENT
        )
        CourseRole.objects.create(
            course=cls.course, user=cls.teacher, role=RoleInCourse.TEACHER
        )
        CourseRole.objects.create(
            course=cls.other_course, user=cls.other, role=RoleInCourse.STUDENT
        )
        cls.problem = Problem.objects.create(
            code="a16p",
            name="P",
            group=group,
            time_limit=1,
            memory_limit=65536,
            points=100,
            is_public=True,
        )
        cls.lesson = CourseLesson.objects.create(
            course=cls.course, title="L1", points=100
        )
        cls.lp = CourseLessonProblem.objects.create(
            lesson=cls.lesson, problem=cls.problem, score=100
        )
        now = timezone.now()
        cls.contest = Contest.objects.create(
            key="a16c",
            name="A16 C",
            start_time=now - timezone.timedelta(hours=2),
            end_time=now - timezone.timedelta(hours=1),
            is_visible=True,
        )
        cls.cc = CourseContest.objects.create(
            course=cls.course, contest=cls.contest, order=1, points=10
        )

    def setUp(self):
        cache.clear()  # course role id lists are cached

    def lesson_version(self):
        return grades_version(self.course, [self.lesson])

    def course_version(self):
        course = Course.objects.get(id=self.course.id)
        return grades_version(course, course.get_lessons(), course.get_contests())

    def submit(self, points):
        sub = Submission.objects.create(
            user=self.student,
            problem=self.problem,
            language=self.lang,
            status="D",
            result="AC" if points == 100 else "WA",
            points=points,
            case_points=points,
            case_total=100,
        )
        return sub

    # -- the token ----------------------------------------------------------------

    def test_token_is_stable_without_changes(self):
        self.assertEqual(self.lesson_version(), self.lesson_version())
        self.assertEqual(self.course_version(), self.course_version())

    def test_token_moves_on_every_grade_relevant_change(self):
        seen = {self.lesson_version()}
        course_seen = {self.course_version()}

        def moved(label):
            v, cv = self.lesson_version(), self.course_version()
            self.assertNotIn(v, seen, label)
            self.assertNotIn(cv, course_seen, label)
            seen.add(v)
            course_seen.add(cv)

        sub = self.submit(40)
        best = BestSubmission.objects.create(
            user=self.student,
            problem=self.problem,
            submission=sub,
            points=40,
            case_total=100,
        )
        moved("new best submission")
        BestSubmission.objects.filter(id=best.id).update(points=70)  # rejudge
        moved("points changed")
        CourseLessonProblem.objects.filter(id=self.lp.id).update(score=50)
        moved("lesson problem score changed")
        newcomer = make_profile("a16_newcomer")
        CourseRole.objects.create(
            course=self.course, user=newcomer, role=RoleInCourse.STUDENT
        )
        cache.clear()
        moved("new student")

    def test_course_token_follows_course_contests(self):
        before = self.course_version()
        ContestParticipation.objects.create(
            contest=self.contest, user=self.student, virtual=0, score=5
        )
        self.assertNotEqual(self.course_version(), before)
        lesson_before = self.lesson_version()
        ContestParticipation.objects.filter(contest=self.contest).update(score=9)
        self.assertEqual(
            self.lesson_version(), lesson_before
        )  # lesson tables do not show contests

    def test_other_courses_do_not_move_the_token(self):
        before = self.lesson_version()
        other_lesson = CourseLesson.objects.create(
            course=self.other_course, title="X", points=10
        )
        CourseLessonProblem.objects.create(
            lesson=other_lesson, problem=self.problem, score=10
        )
        sub = Submission.objects.create(
            user=self.other,
            problem=self.problem,
            language=self.lang,
            status="D",
            result="AC",
            points=100,
            case_points=100,
            case_total=100,
        )
        BestSubmission.objects.create(
            user=self.other,
            problem=self.problem,
            submission=sub,
            points=100,
            case_total=100,
        )
        self.assertEqual(self.lesson_version(), before)

    # -- the endpoint ---------------------------------------------------------------

    def urls(self):
        return [
            reverse("course_grades_lesson", args=(self.course.slug, self.lesson.id)),
            reverse("course_grades", args=(self.course.slug,)),
        ]

    def test_unchanged_poll_is_an_empty_204(self):
        self.client.force_login(self.teacher.user)
        for url, version in zip(
            self.urls(), (self.lesson_version(), self.course_version())
        ):
            with self.subTest(url=url):
                r = self.client.get(url, {"live": "1", "since": version})
                self.assertEqual(r.status_code, 204)
                self.assertEqual(r.content, b"")
                self.assertEqual(r["Cache-Control"], "no-store")

    def test_unchanged_poll_does_not_render(self):
        """A 204 runs aggregates only -- never the per-row grade queries."""
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        self.client.force_login(self.teacher.user)
        url = self.urls()[0]
        version = self.lesson_version()
        with CaptureQueriesContext(connection) as cq:
            r = self.client.get(url, {"live": "1", "since": version})
        self.assertEqual(r.status_code, 204)
        rows = [
            q["sql"][:80]
            for q in cq.captured_queries
            if "judge_bestsubmission" in q["sql"] and "COUNT(" not in q["sql"]
        ]
        self.assertEqual(rows, [])
        self.assertLess(len(cq.captured_queries), 20)

    def test_polls_share_one_version_computation(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        self.client.force_login(self.teacher.user)
        url = self.urls()[0]
        version = self.lesson_version()
        self.client.get(url, {"live": "1", "since": version})  # computes and caches
        with CaptureQueriesContext(connection) as cq:
            r = self.client.get(url, {"live": "1", "since": version})
        self.assertEqual(r.status_code, 204)
        self.assertFalse(
            any("judge_bestsubmission" in q["sql"] for q in cq.captured_queries)
        )

    def test_changed_poll_returns_the_page_with_the_new_token(self):
        self.client.force_login(self.teacher.user)
        url = self.urls()[0]
        stale = self.lesson_version()
        sub = self.submit(100)
        BestSubmission.objects.create(
            user=self.student,
            problem=self.problem,
            submission=sub,
            points=100,
            case_total=100,
        )
        r = self.client.get(url, {"live": "1", "since": stale})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Cache-Control"], "no-store")
        self.assertContains(r, f'data-grades-version="{self.lesson_version()}"')
        self.assertContains(r, 'id="users-table"')

    def test_the_page_carries_its_token_and_the_script(self):
        self.client.force_login(self.student.user)
        for url in self.urls():
            with self.subTest(url=url):
                r = self.client.get(url)
                self.assertContains(r, "data-grades-version=")
                self.assertContains(r, "_gradesLive")

    def test_live_endpoint_keeps_the_page_permissions(self):
        url = self.urls()[0]
        version = self.lesson_version()
        r = self.client.get(url, {"live": "1", "since": version})
        self.assertEqual(r.status_code, 302)  # anonymous -> login
        self.client.force_login(self.other.user)  # enrolled elsewhere
        r = self.client.get(url, {"live": "1", "since": version})
        self.assertEqual(r.status_code, 403)
        r = self.client.get(url, {"live": "1", "since": "stale"})
        self.assertEqual(r.status_code, 403)
        self.assertNotIn(b"a16_student", r.content)

    def test_hidden_lesson_stays_hidden_from_students(self):
        CourseLesson.objects.filter(id=self.lesson.id).update(is_visible=False)
        self.client.force_login(self.student.user)
        r = self.client.get(self.urls()[0], {"live": "1", "since": "x"})
        self.assertEqual(r.status_code, 404)
