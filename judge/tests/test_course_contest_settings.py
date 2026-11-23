from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from bs4 import BeautifulSoup

from judge.models import (
    Contest,
    ContestParticipation,
    ContestProblem,
    Course,
    CourseContest,
    CourseLesson,
    CourseLessonProblem,
    CourseRole,
    Problem,
    ProblemGroup,
    ProblemType,
)
from judge.models.course import RoleInCourse
from judge.tests.test_grades_live_refresh import make_profile


class CourseContestSettingsTests(TestCase):
    fixtures = ["language_small"]

    @classmethod
    def setUpTestData(cls):
        cls.teacher = make_profile("options_teacher")
        cls.student = make_profile("options_student")
        cls.other = make_profile("options_other")
        cls.course = Course.objects.create(name="Khoá kiểm thử", slug="course-options")
        CourseRole.objects.create(
            course=cls.course, user=cls.teacher, role=RoleInCourse.TEACHER
        )
        for profile in (cls.student, cls.other):
            CourseRole.objects.create(course=cls.course, user=profile)
        cls.lesson = CourseLesson.objects.create(
            course=cls.course, title="Bài mở đầu", points=50
        )
        cls.problem = Problem.objects.create(
            code="optionsproblem",
            time_limit=1,
            memory_limit=65536,
            name="Tổng hai số",
            points=800,
            is_public=True,
            group=ProblemGroup.objects.create(name="options", full_name="Options"),
        )
        cls.problem.types.add(
            ProblemType.objects.create(name="options-math", full_name="Số học")
        )
        CourseLessonProblem.objects.create(
            lesson=cls.lesson, problem=cls.problem, score=35
        )
        now = timezone.now()
        cls.contest = Contest.objects.create(
            key="optionscontest",
            name="Thi thử",
            is_visible=True,
            start_time=now - timezone.timedelta(hours=2),
            end_time=now - timezone.timedelta(hours=1),
        )
        cls.contest.authors.add(cls.teacher)
        cls.link = CourseContest.objects.create(
            course=cls.course, contest=cls.contest, points=60
        )
        ContestProblem.objects.create(
            contest=cls.contest, problem=cls.problem, points=75, order=1
        )
        for p, score in ((cls.student, 25), (cls.other, 70)):
            ContestParticipation.objects.create(
                contest=cls.contest, user=p, score=score
            )

    def setUp(self):
        cache.clear()

    def course_url(self, name, *args):
        return reverse(name, args=[self.course.slug, *args])

    def test_lesson_and_contest_tables_show_distinct_score_columns_and_tags(self):
        self.client.force_login(self.student.user)
        for url, label in (
            (self.course_url("course_lesson_detail", self.lesson.pk), "Điểm Lesson"),
            (reverse("contest_problems", args=[self.contest.key]), "Điểm Contest"),
        ):
            response = self.client.get(url)
            self.assertContains(response, label)
            self.assertContains(response, "Điểm bài")
            self.assertContains(response, "Số học")

    def test_active_contest_problem_list_hides_tags_and_private_status(self):
        self.contest.end_time = timezone.now() + timezone.timedelta(hours=1)
        self.contest.scoreboard_visibility = Contest.SCOREBOARD_AFTER_CONTEST
        self.contest.save(update_fields=["end_time", "scoreboard_visibility"])
        self.contest.contest_problems.update(is_result_hidden=True)
        self.student.current_contest = self.contest.users.get(user=self.student)
        self.student.save(update_fields=["current_contest"])
        self.client.force_login(self.student.user)
        response = self.client.get(reverse("contest_problems", args=[self.contest.key]))
        self.assertContains(response, "Điểm Contest")
        table = BeautifulSoup(response.content, "html.parser").select_one(
            "#contest-problems"
        )
        self.assertIsNotNone(table.select_one(".fa-eye-slash"))
        self.assertIn("Sau kỳ thi", table.get_text(" ", strip=True))
        self.assertNotIn("Số học", table.get_text(" ", strip=True))
