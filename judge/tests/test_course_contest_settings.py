from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext
from openpyxl import load_workbook
from io import BytesIO
from bs4 import BeautifulSoup

from judge.models import (
    BestSubmission,
    Contest,
    ContestParticipation,
    ContestProblem,
    Course,
    CourseContest,
    CourseLesson,
    CourseLessonProblem,
    CourseRole,
    Organization,
    Problem,
    ProblemData,
    ProblemGroup,
    ProblemType,
    Quiz,
    Submission,
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
        cls.outsider = make_profile("options_outsider")
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

    def assert_peer_absent(self, response):
        self.assertEqual(response.status_code, 200)
        table = BeautifulSoup(response.content, "html.parser").select_one(
            "#users-table"
        )
        self.assertIsNotNone(table)
        self.assertNotIn(self.other.username, table.get_text(" ", strip=True))

    def test_private_invite_respects_group_and_revocation(self):
        manage = self.course_url("course_manage_invite")
        self.client.force_login(self.student.user)
        self.assertEqual(
            self.client.post(manage, {"action": "generate"}).status_code, 404
        )
        self.client.force_login(self.teacher.user)
        self.assertEqual(
            self.client.post(manage, {"action": "generate"}).status_code, 302
        )
        self.course.refresh_from_db()
        invite = self.course_url("course_invite", self.course.invite_token)
        self.assertContains(self.client.get(self.course_url("course_members")), invite)
        org = Organization.objects.create(
            name="Nhóm kiểm thử",
            slug="invite-test",
            short_name="Invite",
            registrant=self.teacher,
        )
        self.course.organizations.add(org)
        self.client.force_login(self.outsider.user)
        self.assertEqual(self.client.get(invite).status_code, 403)
        self.assertEqual(self.client.post(invite).status_code, 403)
        self.outsider.organizations.add(org)
        self.assertContains(self.client.get(invite), gettext("Join course"))
        self.assertFalse(
            CourseRole.objects.filter(course=self.course, user=self.outsider).exists()
        )
        self.assertEqual(self.client.post(invite).status_code, 302)
        self.assertEqual(
            CourseRole.objects.get(course=self.course, user=self.outsider).role,
            RoleInCourse.STUDENT,
        )
        self.client.force_login(self.teacher.user)
        self.assertEqual(
            self.client.post(manage, {"action": "revoke"}).status_code, 302
        )
        self.client.force_login(self.outsider.user)
        self.assertEqual(self.client.get(invite).status_code, 404)

    def test_course_private_grades_filter_on_server_and_deny_peers_popup(self):
        self.client.force_login(self.student.user)
        main = self.course_url("course_grades")
        before = self.client.get(main)
        self.assertContains(before, "options_other")
        self.course.show_rankings = False
        self.course.save(update_fields=["show_rankings"])
        for url in (main, self.course_url("course_grades_lesson", self.lesson.id)):
            res = self.client.get(
                url, {"focus": self.other.username, "search": self.other.username}
            )
            self.assertContains(res, gettext("My score"))
            self.assertContains(res, "options_student")
            self.assert_peer_absent(res)
            self.assertEqual(res.context_data["global_rank"], {})
        res = self.client.get(
            main, {"live": "1", "since": before.context_data["grades_version"]}
        )
        self.assert_peer_absent(res)
        self.assertEqual(
            self.client.get(
                self.course_url(
                    "course_lesson_user_submissions_ajax",
                    self.lesson.id,
                    self.other.username,
                    self.problem.code,
                )
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(self.course_url("course_grades_export")).status_code, 403
        )
        self.client.force_login(self.teacher.user)
        self.assertContains(self.client.get(main), "options_other")

    def test_excluded_items_are_out_of_course_totals_and_export(self):
        excluded = CourseLesson.objects.create(
            course=self.course,
            title="Ôn tập tự do",
            points=95,
            exclude_from_grades=True,
        )
        self.client.force_login(self.teacher.user)
        response = self.client.post(
            self.course_url("course_contest_list"),
            {
                "action": "set_grades_inclusion",
                "cc_id": self.link.pk,
                "include": "0",
            },
        )
        self.assertEqual(response.status_code, 302)
        res = self.client.get(self.course_url("course_grades"))
        self.assertEqual([l.pk for l in res.context_data["lessons"]], [self.lesson.pk])
        self.assertEqual(res.context_data["course_contests"], [])
        self.assertEqual(
            res.context_data["grade_total"][self.student]["total_points"], 50
        )
        self.client.force_login(self.student.user)
        detail = self.client.get(self.course_url("course_detail"))
        cards = self.client.get(reverse("course_list"))
        self.assertEqual(detail.context_data["total_progress"]["total_points"], 50)
        self.assertIn(excluded, detail.context_data["lessons"])
        self.assertIn(self.link, detail.context_data["course_contests"])
        self.assertEqual(
            cards.context_data["course_progress"][self.course.pk]["total_points"], 50
        )
        self.client.force_login(self.teacher.user)
        book = load_workbook(
            BytesIO(self.client.get(self.course_url("course_grades_export")).content)
        )
        self.assertFalse(
            any(
                "Ôn tập tự do" in str(cell.value) or "Thi thử" in str(cell.value)
                for cell in book.active[1]
            )
        )
        self.assertEqual(
            self.client.get(
                self.course_url("course_grades_lesson", excluded.pk)
            ).status_code,
            200,
        )

    def test_always_hidden_contest_hides_other_scores_after_end(self):
        self.contest.scoreboard_visibility = Contest.SCOREBOARD_ALWAYS_HIDDEN
        self.contest.save(update_fields=["scoreboard_visibility"])
        url = reverse("contest_ranking", args=[self.contest.key])
        self.client.force_login(self.student.user)
        self.assert_peer_absent(self.client.get(url, {"user": self.other.username}))
        self.assertEqual(self.client.get(url, {"format": "xlsx"}).status_code, 403)
        self.assertNotContains(
            self.client.get(self.course_url("course_grades")), "options_other"
        )
        self.client.force_login(self.teacher.user)
        self.assertContains(self.client.get(url), "options_other")

    def test_hidden_contest_score_cannot_be_read_through_lesson_or_quiz_popup(self):
        self.contest.scoreboard_visibility = Contest.SCOREBOARD_ALWAYS_HIDDEN
        self.contest.save(update_fields=["scoreboard_visibility"])
        other_submission = Submission.objects.create(
            user=self.other,
            language=self.other.language,
            problem=self.problem,
            contest_object=self.contest,
            status="D",
            result="AC",
            points=800,
            case_points=100,
            case_total=100,
        )
        BestSubmission.objects.update_or_create(
            user=self.other,
            problem=self.problem,
            defaults={"submission": other_submission, "points": 100, "case_total": 100},
        )
        quiz = Quiz.objects.create(code="optionsquiz", title="Quiz")
        ContestProblem.objects.create(
            contest=self.contest, quiz=quiz, points=20, order=2
        )
        participation = self.contest.users.get(user=self.other)
        self.client.force_login(self.student.user)
        response = self.client.get(
            self.course_url("course_grades_lesson", self.lesson.pk)
        )
        self.assertTrue(
            response.context_data["grades"][self.other][self.problem.pk][
                "is_result_hidden"
            ]
        )
        self.assertEqual(
            self.client.get(
                reverse(
                    "contest_quiz_attempts_ajax",
                    args=[self.contest.key, participation.pk, quiz.pk],
                )
            ).status_code,
            404,
        )

    def set_contest_running(self):
        self.contest.end_time = timezone.now() + timezone.timedelta(hours=1)
        self.contest.save(update_fields=["end_time"])

    def rate_problem(self):
        self.problem.points = 1200
        self.problem.save(update_fields=["points"])

    def page(self, url, user):
        self.client.force_login(user)
        return BeautifulSoup(self.client.get(url).content, "html.parser")

    def assert_table(self, url, user, difficulty, types):
        table = self.page(url, user).select_one(".hn-assessment-table")
        headers = [th.get_text(strip=True) for th in table.select("th")]
        text = table.get_text(" ", strip=True)
        self.assertEqual(gettext("Difficulty") in headers, difficulty)
        self.assertEqual("1200" in text, difficulty)
        self.assertEqual("Số học" in text, types)
        self.assertIsNone(table.select_one("small.assessment-code"))

    def test_lesson_and_contest_tables_show_difficulty_and_types_when_allowed(self):
        self.rate_problem()
        lesson = self.course_url("course_lesson_detail", self.lesson.pk)
        contest = reverse("contest_problems", args=[self.contest.key])
        student, teacher = self.student.user, self.teacher.user
        self.assert_table(lesson, student, True, False)
        self.assert_table(lesson, teacher, True, True)
        # The contest has ended, so everything is open for review.
        self.assert_table(contest, student, True, True)
        self.set_contest_running()
        self.assert_table(contest, teacher, True, True)
        self.student.current_contest = self.contest.users.get(user=self.student)
        self.student.save(update_fields=["current_contest"])
        self.assert_table(contest, student, False, False)

    def limits(self, url, user):
        row = self.page(url, user).select_one(".hn-assessment-table tbody tr")
        cells = [s.get_text(strip=True) for s in row.select("td.assessment-limit span")]
        line = row.select_one(".assessment-limits-line").get_text(" ", strip=True)
        return cells, line

    def test_tables_show_files_and_limits_to_everyone(self):
        student = self.student.user
        lesson = self.course_url("course_lesson_detail", self.lesson.pk)
        contest = reverse("contest_problems", args=[self.contest.key])
        console = [f"{gettext('stdin')} / {gettext('stdout')}", "1.0s · 64M"]
        for url in (lesson, contest):
            self.assertEqual(self.limits(url, student)[0], console)
        ProblemData.objects.create(
            problem=self.problem, fileio_input="TONG.INP", fileio_output="TONG.OUT"
        )
        self.set_contest_running()
        self.student.current_contest = self.contest.users.get(user=self.student)
        self.student.save(update_fields=["current_contest"])
        for url in (lesson, contest):
            cells, line = self.limits(url, student)
            self.assertEqual(cells, ["TONG.INP", "TONG.OUT", "1.0s · 64M"])
            self.assertEqual(line, "TONG.INP / TONG.OUT 1.0s · 64M")

    def assert_problem_page(self, url, user, difficulty, types):
        page = self.page(url, user)
        value = page.select_one(".hn-difficulty").get_text(strip=True)
        self.assertEqual(value, "1200" if difficulty else "-")
        self.assertEqual(page.select_one("#problem-types") is not None, types)
        self.assertEqual("hide_difficulty=1" in page.decode(), not difficulty)

    def test_problem_page_hides_difficulty_only_during_contests(self):
        self.rate_problem()
        lesson = self.course_url(
            "course_lesson_problem_detail", self.lesson.pk, self.problem.code
        )
        contest = reverse(
            "contest_problem_detail", args=[self.contest.key, self.problem.code]
        )
        plain = reverse("problem_detail", args=[self.problem.code])
        student, teacher = self.student.user, self.teacher.user
        self.assert_problem_page(lesson, student, True, False)
        self.assert_problem_page(lesson, teacher, True, True)
        self.assert_problem_page(contest, student, True, True)
        self.assert_problem_page(plain, student, True, True)
        self.set_contest_running()
        self.student.current_contest = self.contest.users.get(user=self.student)
        self.student.save(update_fields=["current_contest"])
        for url in (contest, plain):
            self.assert_problem_page(url, student, False, False)
        raw = self.client.get(reverse("problem_raw", args=[self.problem.code]))
        self.assertTrue(raw.context["hide_difficulty"])
        self.assert_problem_page(contest, teacher, True, True)

    def test_points_below_the_rating_scale_are_not_shown_as_difficulty(self):
        self.problem.points = 100
        self.problem.save(update_fields=["points"])
        url = reverse("problem_detail", args=[self.problem.code])
        page = self.page(url, self.student.user)
        self.assertEqual(page.select_one(".hn-difficulty").get_text(strip=True), "-")

    def test_active_contest_problem_list_hides_tags_and_private_status(self):
        self.assert_active_contest_visibility(Contest.SCOREBOARD_AFTER_CONTEST)

    def test_always_hidden_active_contest_problem_list_hides_tags_and_private_status(
        self,
    ):
        self.assert_active_contest_visibility(Contest.SCOREBOARD_ALWAYS_HIDDEN)

    def assert_active_contest_visibility(self, scoreboard_visibility):
        self.contest.end_time = timezone.now() + timezone.timedelta(hours=1)
        self.contest.scoreboard_visibility = scoreboard_visibility
        self.contest.save(update_fields=["end_time", "scoreboard_visibility"])
        self.contest.contest_problems.update(is_result_hidden=True)
        self.student.current_contest = self.contest.users.get(user=self.student)
        self.student.save(update_fields=["current_contest"])
        self.client.force_login(self.student.user)
        response = self.client.get(reverse("contest_problems", args=[self.contest.key]))
        self.assertContains(response, gettext("Points"))
        table = BeautifulSoup(response.content, "html.parser").select_one(
            "#contest-problems"
        )
        self.assertIsNotNone(table.select_one(".fa-eye-slash"))
        self.assertNotIn("Số học", table.get_text(" ", strip=True))
