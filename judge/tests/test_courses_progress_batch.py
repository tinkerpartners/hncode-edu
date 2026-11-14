from django.core.cache import cache
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from judge.models import Course, CourseLesson, CourseLessonProblem, CourseRole
from judge.models.course import RoleInCourse
from judge.tests.test_grades_export import GradesExportTest
from judge.views.course import (
    bulk_calculate_courses_total_progress,
    bulk_max_case_points_per_problem,
    bulk_calculate_lessons_progress,
    bulk_calculate_contests_progress,
    calculate_total_progress,
)


class CoursesProgressBatchTest(TestCase):
    fixtures = ["language_small"]

    @classmethod
    def setUpTestData(cls):
        GradesExportTest.setUpTestData.__func__(cls)
        cls.courses = [cls.course]
        for i in range(8):
            course = Course.objects.create(name="Course %s" % i, slug="a39-%s" % i)
            CourseRole.objects.create(
                course=course, user=cls.student, role=RoleInCourse.STUDENT
            )
            for j in range(3):
                lesson = CourseLesson.objects.create(
                    course=course, title="Lesson %s" % j, points=30
                )
                CourseLessonProblem.objects.create(
                    lesson=lesson, problem=cls.problem, score=50
                )
            cls.courses.append(course)

    def setUp(self):
        cache.clear()

    def test_query_count_does_not_grow_with_courses_or_lessons(self):
        with CaptureQueriesContext(connection) as one:
            single = bulk_calculate_courses_total_progress(
                self.student, self.courses[:1], viewer=self.student.user
            )
        cache.clear()
        with CaptureQueriesContext(connection) as nine:
            result = bulk_calculate_courses_total_progress(
                self.student, self.courses, viewer=self.student.user
            )
        self.assertEqual(single[self.course.id], result[self.course.id])
        self.assertEqual(len(result), 9)
        self.assertLessEqual(len(nine), len(one) + 1)
        self.assertLessEqual(len(nine), 15)
        for course in self.courses[1:]:
            self.assertEqual(
                result[course.id],
                {"achieved_points": 36, "total_points": 90, "percentage": 40},
            )

    def test_same_totals_as_individual_course_helpers(self):
        lessons = list(self.course.get_lessons())
        problems = [p for lesson in lessons for p in lesson.get_problems()]
        points = bulk_max_case_points_per_problem(
            [self.student], problems, viewer=self.student.user
        )
        expected = calculate_total_progress(
            bulk_calculate_lessons_progress([self.student], lessons, points)[
                self.student
            ],
            bulk_calculate_contests_progress(
                [self.student], list(self.course.get_contests())
            )[self.student],
        )
        result = bulk_calculate_courses_total_progress(
            self.student, self.courses, viewer=self.student.user
        )
        self.assertEqual(result[self.course.id], expected)

    def test_hidden_lessons_and_nonstudent_courses_are_excluded(self):
        CourseLesson.objects.filter(course=self.courses[1]).update(is_visible=False)
        result = bulk_calculate_courses_total_progress(self.teacher, self.courses)
        self.assertEqual(result, {})
        result = bulk_calculate_courses_total_progress(self.student, self.courses)
        self.assertEqual(result[self.courses[1].id]["total_points"], 0)
        self.assertEqual(bulk_calculate_courses_total_progress(None, self.courses), {})
