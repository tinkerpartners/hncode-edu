"""Scoped IDE navigation, score colours and automatic judge selection."""

from django import forms
from django.urls import reverse
from django.utils import timezone

from judge.forms import ProblemSubmitForm
from judge.models import (
    Contest,
    ContestParticipation,
    ContestProblem,
    ContestSubmission,
    Course,
    CourseLesson,
    CourseLessonProblem,
    CourseLessonPrerequisite,
    CourseRole,
    Problem,
    Submission,
)
from judge.models.course import RoleInCourse
from judge.tests.test_ide_run import IDEFixture
from judge.utils.course_prerequisites import update_lesson_unlock_states


class IDENavigationTests(IDEFixture):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.course = Course.objects.create(
            slug="idecourse",
            name="IDE Course",
            about="QA",
            is_public=True,
            is_open=True,
        )
        CourseRole.objects.create(
            course=cls.course, user=cls.profile, role=RoleInCourse.STUDENT
        )
        cls.lesson = CourseLesson.objects.create(
            course=cls.course, title="IDE lesson", content="", order=1, points=100
        )
        cls.problems = [cls.problem]
        for code in ("ideac", "idewrong", "idepartial"):
            problem = Problem.objects.create(
                code=code,
                name=code,
                description="QA",
                group=cls.problem.group,
                time_limit=1,
                memory_limit=65536,
                points=100,
                partial=True,
                is_public=True,
                date=timezone.now(),
            )
            problem.allowed_languages.add(cls.language)
            cls.judge.problems.add(problem)
            cls.problems.append(problem)
        for index, problem in enumerate(cls.problems):
            CourseLessonProblem.objects.create(
                lesson=cls.lesson, problem=problem, score=25, order=index * 3 + 2
            )

    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)

    def lesson_url(self, problem=None):
        return reverse(
            "course_lesson_problem_ide",
            args=(self.course.slug, self.lesson.id, (problem or self.problem).code),
        )

    def submit(self, problem, points=0, result="WA", status="D", **kwargs):
        return Submission.objects.create(
            user=self.profile,
            problem=problem,
            language=self.language,
            status=status,
            result=result,
            case_points=points,
            case_total=100,
            **kwargs
        )

    def contest(self):
        now = timezone.now()
        contest = Contest.objects.create(
            key="idenav",
            name="IDE nav",
            start_time=now - timezone.timedelta(hours=1),
            end_time=now + timezone.timedelta(hours=1),
            is_visible=True,
        )
        for index, problem in enumerate(self.problems):
            ContestProblem.objects.create(
                contest=contest, problem=problem, points=100, partial=True, order=index
            )
        participation = ContestParticipation.objects.create(
            contest=contest, user=self.profile, virtual=ContestParticipation.LIVE
        )
        self.profile.current_contest = participation
        self.profile.save()
        return contest, participation

    def test_legacy_lesson_link_redirects_to_guarded_scoped_ide(self):
        back = reverse(
            "course_lesson_problem_detail",
            args=(self.course.slug, self.lesson.id, self.problem.code),
        )
        response = self.client.get(
            reverse("problem_ide", args=[self.problem.code]), {"from": back}
        )
        self.assertRedirects(response, self.lesson_url())

    def test_four_states_follow_best_result_and_lesson_order(self):
        self.submit(self.problems[1], 100, "AC")
        self.submit(self.problems[1], 0, "WA")
        self.submit(self.problems[2])
        self.submit(self.problems[3], 30)
        response = self.client.get(self.lesson_url())
        self.assertEqual(response.status_code, 200)
        entries = response.context["ide_navigation"]
        self.assertEqual(
            [entry["state"] for entry in entries], ["new", "ok", "bad", "partial"]
        )
        detail = self.client.get(
            reverse(
                "course_lesson_problem_detail",
                args=(self.course.slug, self.lesson.id, self.problem.code),
            )
        )
        self.assertEqual(
            [entry["state"] for entry in detail.context["lesson_sidebar_problems"]],
            [entry["state"] for entry in entries],
        )
        self.assertEqual([entry["number"] for entry in entries], [1, 2, 3, 4])
        self.assertEqual(
            [entry["url"] for entry in entries],
            [self.lesson_url(p) for p in self.problems],
        )
        self.assertContains(response, 'data-state="new"')
        self.assertContains(response, 'aria-current="page"')
        self.assertContains(response, "<span>Run</span>")

    def test_pending_is_neutral_and_compile_error_is_red(self):
        self.submit(self.problem, status="QU", result=None)
        self.submit(self.problems[1], status="CE", result=None)
        states = self.client.get(self.lesson_url()).context["ide_navigation"]
        self.assertEqual([entry["state"] for entry in states[:2]], ["new", "bad"])

    def test_pretest_accept_is_not_full_accept(self):
        self.submit(self.problem, 100, "AC", is_pretested=True)
        entries = self.client.get(self.lesson_url()).context["ide_navigation"]
        self.assertEqual(entries[0]["state"], "partial")

    def test_other_user_results_do_not_colour_my_navigation(self):
        Submission.objects.create(
            user=self.other_profile,
            problem=self.problem,
            language=self.language,
            status="D",
            result="AC",
            case_points=100,
            case_total=100,
        )
        self.assertEqual(
            self.client.get(self.lesson_url()).context["ide_navigation"][0]["state"],
            "new",
        )

    def test_non_member_cannot_use_scoped_ide(self):
        self.client.force_login(self.other_user)
        self.assertEqual(self.client.get(self.lesson_url()).status_code, 404)

    def test_anonymous_goes_to_login(self):
        self.client.logout()
        response = self.client.get(self.lesson_url())
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response["Location"])

    def test_problem_outside_lesson_is_rejected(self):
        CourseLessonProblem.objects.filter(
            lesson=self.lesson, problem=self.problem
        ).delete()
        self.assertEqual(self.client.get(self.lesson_url()).status_code, 404)

    def test_scoped_ide_enforces_lesson_prerequisites(self):
        self.lesson.order = 2
        self.lesson.save(update_fields=["order"])
        CourseLesson.objects.create(
            course=self.course, title="Prerequisite", content="", order=1, points=100
        )
        CourseLessonPrerequisite.objects.create(
            course=self.course, source_order=1, target_order=2, required_percentage=50
        )
        # The role/lesson signals initialized progress before the edge existed.
        # Build a locked fixture explicitly; on_commit jobs do not run in TestCase.
        update_lesson_unlock_states(self.profile, self.course)
        self.assertEqual(self.client.get(self.lesson_url()).status_code, 403)

    def test_private_contest_ide_checks_access(self):
        contest, participation = self.contest()
        self.profile.current_contest = None
        self.profile.save()
        contest.is_private = True
        contest.save()
        response = self.client.get(
            reverse("contest_ide", args=(contest.key, self.problem.code))
        )
        self.assertEqual(response.status_code, 403)

    def test_external_from_and_mismatched_problem_do_not_select_context(self):
        wrong = reverse(
            "course_lesson_problem_detail",
            args=(self.course.slug, self.lesson.id, self.problems[1].code),
        )
        for back in ("https://example.org" + wrong, wrong):
            with self.subTest(back=back):
                response = self.client.get(
                    reverse("problem_ide", args=[self.problem.code]), {"from": back}
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context["ide_navigation"], [])

    def test_contest_results_are_scoped_and_hidden_results_stay_neutral(self):
        self.submit(self.problem, 100, "AC")  # Practice AC must not colour contest.
        contest, participation = self.contest()
        cp = contest.contest_problems.get(problem=self.problems[1])
        cp.is_result_hidden = True
        cp.save()
        sub = self.submit(self.problems[1], 100, "AC", contest_object=contest)
        ContestSubmission.objects.create(
            submission=sub,
            participation=participation,
            problem=cp,
            points=100,
            is_result_hidden=True,
        )
        response = self.client.get(
            reverse("contest_ide", args=(contest.key, self.problem.code))
        )
        self.assertEqual(response.status_code, 200)
        entries = response.context["ide_navigation"]
        self.assertEqual(entries[0]["state"], "new")
        self.assertEqual(entries[1]["state"], "hidden")

    def test_judge_control_stays_hidden_even_for_problem_editors(self):
        form = ProblemSubmitForm(judge_choices=(("j1", "Judge 1"),))
        self.assertIsInstance(form.fields["judge"].widget, forms.HiddenInput)
        self.assertEqual(form.fields["judge"].clean(""), "")

    def test_anonymous_problem_page_uses_the_compact_layout(self):
        self.client.logout()
        response = self.client.get(reverse("problem_detail", args=[self.problem.code]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="hn-problem-detail"')
        self.assertContains(response, "problem-detail.css")
