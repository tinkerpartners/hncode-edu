from io import BytesIO
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from openpyxl import load_workbook

from judge.models import (
    BestSubmission,
    ContestParticipation,
    ContestProblem,
    CourseLessonProblem,
    CourseRole,
    Problem,
    Quiz,
    CourseLessonQuiz,
    QuizQuestion,
    QuizQuestionAssignment,
    QuizAttempt,
    BestQuizAttempt,
    Submission,
    ContestSubmission,
)
from judge.models.course import RoleInCourse
from judge.tests.test_grades_live_refresh import GradesLiveRefreshTest, make_profile
from judge.utils.grades_export import grades_workbook
from judge.utils.grades_version import grades_version


class GradesExportTest(TestCase):
    fixtures = ["language_small"]

    @classmethod
    def setUpTestData(cls):
        GradesLiveRefreshTest.setUpTestData.__func__(cls)
        cls.student.user.first_name = "Nguyễn Thị Ánh"
        cls.student.user.save()
        cls.lesson.points = 200
        cls.lesson.save()
        cls.lp.score = 75
        cls.lp.save()
        cls.cc.points = 100
        cls.cc.save()
        sub = cls.lang.submission_set.create(
            user=cls.student,
            problem=cls.problem,
            status="D",
            result="WA",
            points=40,
            case_points=40,
            case_total=100,
        )
        BestSubmission.objects.create(
            user=cls.student,
            problem=cls.problem,
            submission=sub,
            points=40,
            case_total=100,
        )
        ContestProblem.objects.create(
            contest=cls.contest, problem=cls.problem, points=100, order=1
        )
        ContestParticipation.objects.create(
            contest=cls.contest, user=cls.student, score=50
        )
        cls.quiz = Quiz.objects.create(code="a28quiz", title="Trắc nghiệm có dấu")
        cls.lq = CourseLessonQuiz.objects.create(
            lesson=cls.lesson, quiz=cls.quiz, points=25
        )
        question = QuizQuestion.objects.create(title="Câu hỏi", question_type="SA")
        cls.assignment = QuizQuestionAssignment.objects.create(
            quiz=cls.quiz, question=question, points=10
        )
        attempt = QuizAttempt.objects.create(
            quiz=cls.quiz,
            lesson_quiz=cls.lq,
            user=cls.student,
            score=4,
            max_score=10,
            is_submitted=True,
        )
        cls.best_quiz = BestQuizAttempt.objects.create(
            user=cls.student, lesson_quiz=cls.lq, attempt=attempt, score=4, max_score=10
        )

    def setUp(self):
        cache.clear()
        self.client.force_login(self.teacher.user)

    def urls(self):
        return [
            reverse("course_grades_export", args=[self.course.slug]),
            reverse(
                "course_grades_lesson_export", args=[self.course.slug, self.lesson.id]
            ),
        ]

    def workbook(self, url, **params):
        response = self.client.get(url, params)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertIn(".xlsx", response["Content-Disposition"])
        workbook = load_workbook(BytesIO(response.content))
        self.assertEqual(workbook.active.freeze_panes, "D2")
        return list(workbook.active.values)

    def test_weighted_course_and_raw_lesson_totals_and_unicode(self):
        course, lesson = [self.workbook(url) for url in self.urls()]
        self.assertEqual(lesson[1][:3], (1, self.student.username, "Nguyễn Thị Ánh"))
        self.assertEqual(lesson[1][3:], (0.4, 40, 100, 10, 30))
        self.assertAlmostEqual(course[1][3], 130 / 300)
        self.assertEqual(course[1][4:], (130, 300, 80, 50))

    def test_percentages_are_numeric_ratios_with_excel_percent_format(self):
        for url in self.urls():
            response = self.client.get(url)
            sheet = load_workbook(BytesIO(response.content)).active
            self.assertEqual(sheet["D2"].number_format, "0.00%")
            self.assertEqual(sheet["D2"].data_type, "n")
            self.assertAlmostEqual(
                sheet["D2"].value, sheet["E2"].value / sheet["F2"].value
            )
            self.assertNotIn("%", sheet["E2"].number_format)

    def test_export_is_unpaginated_and_retains_search_and_global_rank(self):
        second = make_profile("a28_second")
        CourseRole.objects.create(
            course=self.course, user=second, role=RoleInCourse.STUDENT
        )
        cache.clear()
        with patch("judge.views.course.GRADES_PAGE_SIZE", 1):
            for url in self.urls():
                rows = self.workbook(url, page=1)
                self.assertEqual(len(rows), 3)
                filtered = self.workbook(url, search="a28_second", page=999)
                self.assertEqual(len(filtered), 2)
                self.assertEqual(filtered[1][0:2], (2, "a28_second"))
                self.assertEqual(len(self.workbook(url, search="no_match")), 1)

    def test_permissions_teacher_assistant_admin_only(self):
        for user in (None, self.other.user, self.student.user):
            self.client.logout()
            if user:
                self.client.force_login(user)
            for url in self.urls():
                self.assertEqual(self.client.get(url).status_code, 403 if user else 302)
        CourseRole.objects.create(
            course=self.course, user=self.other, role=RoleInCourse.ASSISTANT
        )
        cache.clear()
        self.client.force_login(self.other.user)
        for url in self.urls():
            self.assertEqual(self.client.get(url).status_code, 200)

    def test_strings_are_text_even_when_they_look_like_formulas(self):
        response = grades_workbook(
            ["Họ tên", "Điểm"],
            [['=HYPERLINK("https://example.com")\x00', 12.345]],
            "test",
        )
        workbook = load_workbook(BytesIO(response.content), data_only=False)
        self.assertEqual(workbook.active["A2"].data_type, "s")
        self.assertTrue(workbook.active["A2"].value.startswith("=HYPERLINK"))
        self.assertEqual(workbook.active["B2"].data_type, "n")

    def test_quiz_denominator_and_balanced_weights_invalidate_version(self):
        def version():
            return grades_version(self.course, [self.lesson])

        before = version()
        QuizQuestionAssignment.objects.filter(pk=self.assignment.pk).update(points=20)
        self.assertNotEqual(before, version())
        extra = CourseLessonProblem.objects.create(
            lesson=self.lesson,
            problem=Problem.objects.create(
                code="a28extra",
                name="Extra",
                group=self.problem.group,
                time_limit=1,
                memory_limit=65536,
                points=100,
            ),
            score=25,
        )
        before = version()
        CourseLessonProblem.objects.filter(pk=self.lp.pk).update(score=50)
        CourseLessonProblem.objects.filter(pk=extra.pk).update(score=50)
        self.assertNotEqual(before, version())

    def test_html_shows_total_and_export_only_for_manager(self):
        for name, args in (
            ("course_grades", [self.course.slug]),
            ("course_grades_lesson", [self.course.slug, self.lesson.id]),
        ):
            url = reverse(name, args=args)
            self.assertContains(self.client.get(url), "Xuất Ranking (Excel)")
            self.assertContains(self.client.get(url), 'class="total-points"')
            self.client.force_login(self.student.user)
            self.assertNotContains(self.client.get(url), "Xuất Ranking (Excel)")
            self.client.force_login(self.teacher.user)

    def test_hidden_best_uses_visible_fallback_and_its_rejudge_changes_version(self):
        cp = ContestProblem.objects.get(contest=self.contest)
        cp.is_result_hidden = True
        cp.save()
        hidden = Submission.objects.create(
            user=self.student,
            problem=self.problem,
            language=self.lang,
            status="D",
            points=90,
            case_points=90,
            case_total=100,
        )
        ContestSubmission.objects.create(
            submission=hidden,
            problem=cp,
            participation=ContestParticipation.objects.get(
                contest=self.contest, user=self.student
            ),
            points=90,
        )
        BestSubmission.objects.filter(user=self.student, problem=self.problem).update(
            submission=hidden, points=90
        )
        version = grades_version(self.course, [self.lesson])
        rows = self.workbook(self.urls()[1])
        self.assertEqual(rows[1][4], 40)
        Submission.objects.filter(user=self.student, problem=self.problem).exclude(
            pk=hidden.pk
        ).update(case_points=20, points=20)
        self.assertNotEqual(version, grades_version(self.course, [self.lesson]))
        self.assertEqual(self.workbook(self.urls()[1])[1][4], 25)
