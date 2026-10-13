"""A04 (#96): a lesson can hide its problems' test data from students.

Owner decisions (2026-10-13):
  * Hidden = test input, expected output and checker feedback. Verdict, time,
    memory, points and the submitter's own output stay visible.
  * The flag is on CourseLesson but applies to the problem everywhere: any
    submission of a problem that sits in a hiding lesson hides its test data,
    whether it came from the lesson, the problem page or a contest.
  * Exempt: superusers, problem editors, the submission's contest editors and
    the Teacher/Assistant of a course with a hiding lesson.

Both data paths are checked: the submission status page and the AJAX
testcase fragment (SubmissionTestCaseQuery), which share one template.
"""

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from judge.models import (
    Contest,
    ContestParticipation,
    ContestProblem,
    ContestSubmission,
    Course,
    CourseLesson,
    CourseLessonProblem,
    CourseRole,
    Language,
    Problem,
    ProblemGroup,
    ProblemTestCase,
    Profile,
    Submission,
    SubmissionSource,
    SubmissionTestCase,
)
from judge.utils.problem_data import get_file_cachekey
from judge.utils.testcase_visibility import is_testcase_manager, testcases_hidden_for

INPUT = "SECRET-INPUT-7f3a"
ANSWER = "SECRET-ANSWER-9c1e"
FEEDBACK = "expected SECRET-ANSWER-9c1e"
OUTPUT = "STUDENT-OUTPUT-42"


def make_profile(username, **kw):
    user = User.objects.create_user(username, f"{username}@x.com", "pw", **kw)
    profile, _ = Profile.objects.get_or_create(
        user=user, defaults={"language": Language.objects.get(key="PY3")}
    )
    return profile


def _submit(problem, lang, profile, contest=None):
    sub = Submission.objects.create(
        user=profile,
        problem=problem,
        language=lang,
        contest_object=contest,
        status="D",
        result="WA",
        points=0,
        case_points=0,
        case_total=100,
        time=0.1,
        memory=1024,
    )
    SubmissionSource.objects.create(submission=sub, source="print(1)")
    SubmissionTestCase.objects.create(
        submission=sub,
        case=1,
        status="WA",
        time=0.05,
        memory=512,
        points=0,
        total=100,
        feedback=FEEDBACK,
        extended_feedback="",
        output=OUTPUT,
    )
    return sub


class LessonHideTestcasesTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        Language.objects.get_or_create(
            key="PY3",
            defaults={
                "name": "Python 3",
                "short_name": "PY3",
                "common_name": "Python",
                "ace": "python",
                "pygments": "python3",
                "template": "",
            },
        )
        cls.lang = Language.objects.get(key="PY3")
        group, _ = ProblemGroup.objects.get_or_create(
            name="a04", defaults={"full_name": "A04"}
        )
        cls.student = make_profile("a04_student")
        cls.outsider = make_profile("a04_outsider")  # not in the course
        cls.teacher = make_profile("a04_teacher")
        cls.assistant = make_profile("a04_assistant")
        cls.other_teacher = make_profile("a04_otherteacher")
        cls.author = make_profile("a04_author")
        cls.superuser = make_profile("a04_root", is_staff=True, is_superuser=True)

        cls.problem = Problem.objects.create(
            code="a04prob",
            name="A04 Problem",
            group=group,
            time_limit=1.0,
            memory_limit=65536,
            points=100,
            is_public=True,
            description="Sample: SAMPLE-IN-STATEMENT",
        )
        cls.problem.authors.add(cls.author)
        ProblemTestCase.objects.create(
            dataset=cls.problem,
            order=1,
            type="C",
            input_file="1.in",
            output_file="1.out",
            points=100,
            is_pretest=False,
        )

        cls.course = Course.objects.create(
            name="A04 Course",
            slug="a04-course",
            about="",
            is_public=False,
            is_open=False,
        )
        cls.lesson = CourseLesson.objects.create(
            course=cls.course, title="Closed tests", points=100, hide_testcases=True
        )
        CourseLessonProblem.objects.create(
            lesson=cls.lesson, problem=cls.problem, score=100
        )
        for p, role in [
            (cls.student, "ST"),
            (cls.teacher, "TE"),
            (cls.assistant, "AS"),
        ]:
            CourseRole.objects.create(course=cls.course, user=p, role=role)

        other_course = Course.objects.create(
            name="Other", slug="a04-other", about="", is_public=False, is_open=False
        )
        CourseRole.objects.create(
            course=other_course, user=cls.other_teacher, role="TE"
        )

        cls.student_sub = _submit(
            cls.problem, cls.lang, cls.student
        )  # from the problem page: no lesson
        cls.outsider_sub = _submit(cls.problem, cls.lang, cls.outsider)
        cls.teacher_sub = _submit(cls.problem, cls.lang, cls.teacher)
        cls.other_teacher_sub = _submit(cls.problem, cls.lang, cls.other_teacher)

    def setUp(self):
        for name, data in (("1.in", INPUT), ("1.out", ANSWER)):
            cache.set(
                "problem_archive:%s:%s" % ("a04prob", get_file_cachekey(name)),
                data,
                600,
            )

    def tearDown(self):
        for name in ("1.in", "1.out"):
            cache.delete("problem_archive:%s:%s" % ("a04prob", get_file_cachekey(name)))

    # -- helpers --------------------------------------------------------------

    def submit(self, profile, contest=None):
        return _submit(self.problem, self.lang, profile, contest)

    def fetch(self, profile, sub):
        """Both data paths for one viewer: status page and AJAX fragment."""
        self.client.logout()
        self.client.force_login(profile.user)
        pages = [
            self.client.get(reverse("submission_status", args=(sub.id,))),
            self.client.get(reverse("submission_testcases_query"), {"id": sub.id}),
        ]
        return pages

    def assert_hidden(self, profile, sub):
        for r in self.fetch(profile, sub):
            self.assertEqual(r.status_code, 200)
            html = r.content.decode()
            self.assertNotIn(INPUT, html)
            self.assertNotIn(ANSWER, html)  # covers the checker feedback too
            # Kept: verdict, own output, time.
            self.assertIn("Wrong Answer", html)
            self.assertIn(OUTPUT, html)
            self.assertIn("50 ms", html)

    def assert_visible(self, profile, sub):
        for r in self.fetch(profile, sub):
            self.assertEqual(r.status_code, 200)
            html = r.content.decode()
            self.assertIn(INPUT, html)
            self.assertIn(ANSWER, html)
            self.assertIn(FEEDBACK, html)
            self.assertIn(OUTPUT, html)

    # -- decision function ------------------------------------------------------

    def test_decision_matrix(self):
        cases = [
            (self.student, True),
            (self.outsider, True),
            (self.other_teacher, True),  # Teacher, but of an unrelated course
            (self.teacher, False),
            (self.assistant, False),
            (self.author, False),
            (self.superuser, False),
        ]
        for profile, hidden in cases:
            with self.subTest(profile=profile.user.username):
                self.assertEqual(
                    testcases_hidden_for(profile.user, self.problem), hidden
                )
                self.assertEqual(
                    is_testcase_manager(profile.user, self.problem), not hidden
                )

    # -- data paths -------------------------------------------------------------

    def test_student_own_submission_hides_test_data(self):
        self.assert_hidden(self.student, self.student_sub)

    def test_hidden_note_is_shown(self):
        r = self.fetch(self.student, self.student_sub)[0]
        self.assertContains(r, "testcases-hidden-note")

    def test_submission_from_outside_the_course_is_hidden_too(self):
        # No side door: same problem, submitted by someone not in the course.
        self.assert_hidden(self.outsider, self.outsider_sub)

    def test_teacher_of_another_course_does_not_see_test_data(self):
        self.assert_hidden(self.other_teacher, self.other_teacher_sub)

    def test_managers_see_everything(self):
        for profile in (self.teacher, self.author, self.superuser):
            with self.subTest(profile=profile.user.username):
                sub = self.teacher_sub if profile == self.teacher else self.student_sub
                self.assert_visible(profile, sub)

    def test_assistant_sees_test_data_on_own_submission(self):
        sub = self.submit(self.assistant)
        self.assert_visible(self.assistant, sub)

    def test_after_ac_classmate_submission_is_still_hidden(self):
        # Solving a public problem lets you open other people's submissions.
        ac = self.submit(self.outsider)
        Submission.objects.filter(id=ac.id).update(
            result="AC", points=100, case_points=100
        )
        from judge.models import BestSubmission

        BestSubmission.objects.create(
            user=self.outsider,
            problem=self.problem,
            submission=ac,
            points=100,
            case_total=100,
        )
        from judge.utils.problems import user_completed_ids

        user_completed_ids.dirty(self.outsider)
        self.assert_hidden(self.outsider, self.student_sub)

    # -- policy changes ---------------------------------------------------------

    def test_switching_the_policy_applies_immediately(self):
        CourseLesson.objects.filter(id=self.lesson.id).update(hide_testcases=False)
        self.assert_visible(self.student, self.student_sub)
        CourseLesson.objects.filter(id=self.lesson.id).update(hide_testcases=True)
        self.assert_hidden(self.student, self.student_sub)

    def test_strictest_lesson_wins(self):
        open_lesson = CourseLesson.objects.create(
            course=self.course, title="Open tests", points=10, hide_testcases=False
        )
        CourseLessonProblem.objects.create(
            lesson=open_lesson, problem=self.problem, score=10
        )
        self.assert_hidden(self.student, self.student_sub)

    def test_removing_the_problem_from_the_lesson_reopens_it(self):
        CourseLessonProblem.objects.filter(lesson=self.lesson).delete()
        self.assert_visible(self.student, self.student_sub)

    def test_contest_show_testcases_does_not_override_the_lesson(self):
        now = timezone.now()
        contest = Contest.objects.create(
            key="a04contest",
            name="A04 Contest",
            start_time=now - timezone.timedelta(hours=2),
            end_time=now - timezone.timedelta(hours=1),
            is_visible=True,
        )
        cp = ContestProblem.objects.create(
            contest=contest,
            problem=self.problem,
            points=100,
            order=0,
            show_testcases=True,
        )
        part = ContestParticipation.objects.create(
            contest=contest, user=self.student, virtual=0, score=0
        )
        sub = self.submit(self.student, contest)
        ContestSubmission.objects.create(
            submission=sub, problem=cp, participation=part, points=0
        )
        self.assert_hidden(self.student, sub)

    # -- what stays public ------------------------------------------------------

    def test_statement_sample_is_unaffected(self):
        self.client.force_login(self.student.user)
        r = self.client.get(reverse("problem_detail", args=(self.problem.code,)))
        self.assertContains(r, "SAMPLE-IN-STATEMENT")

    def test_problem_without_a_hiding_lesson_is_unchanged(self):
        CourseLesson.objects.filter(id=self.lesson.id).update(hide_testcases=False)
        self.assertFalse(testcases_hidden_for(self.student.user, self.problem))
        self.assert_visible(self.outsider, self.outsider_sub)

    def test_lesson_form_offers_the_switch(self):
        self.client.force_login(self.teacher.user)
        r = self.client.get(
            reverse("edit_course_lessons_new", args=(self.course.slug, self.lesson.id))
        )
        self.assertContains(r, 'name="hide_testcases"')
