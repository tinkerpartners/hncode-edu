from datetime import timedelta
from unittest.mock import patch

from bs4 import BeautifulSoup

from django.contrib.auth.models import Permission, User
from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.core.exceptions import ValidationError
from django.contrib.auth.models import AnonymousUser
from judge.sitemap import ContestSitemap
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext, ngettext

from judge.models import (
    BestSubmission,
    BlogPost,
    Comment,
    Contest,
    ContestProblem,
    ContestSubmission,
    Course,
    CourseLesson,
    CourseLessonProblem,
    CourseRole,
    ExamDeferredSubmission,
    ExamDivision,
    ExamMockArchive,
    ExamMockSession,
    ExamPaper,
    ExamSeries,
    Problem,
    ProblemData,
    Profile,
    Submission,
)
from judge.tasks.exam_library import dispatch_exam_submissions
from judge.tests.strict_helpers import make_group, make_language, make_online_judge
from judge.utils.exam_library import create_mock_contest
from judge.utils.exam_import import import_sources
from judge.views.exam_library import prepare_progress
from judge.judgeapi import judge_submission


@override_settings(EXAM_LIBRARY_DELAYED_GRADING_ENABLED=True)
class ExamLibraryTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    @classmethod
    def setUpTestData(cls):
        cls.language = make_language()
        cls.judge = make_online_judge(cls.language)
        cls.user = User.objects.create_user("library_student", password="pass")
        cls.profile, _ = Profile.objects.get_or_create(
            user=cls.user, defaults={"language": cls.language}
        )
        cls.course = Course.objects.create(
            name="HSG Hà Nội", slug="test-exam-course", is_public=True
        )
        CourseRole.objects.create(course=cls.course, user=cls.profile, role="ST")
        cls.lesson = CourseLesson.objects.create(
            course=cls.course, title="2025–2026", points=100
        )
        cls.problem = Problem.objects.create(
            code="library_test_code",
            name="Tổng hai số",
            group=make_group(),
            description="Tính tổng hai số.",
            time_limit=1,
            memory_limit=65536,
            points=100,
            is_public=True,
        )
        cls.problem.allowed_languages.add(cls.language)
        cls.judge.problems.add(cls.problem)
        CourseLessonProblem.objects.create(
            lesson=cls.lesson, problem=cls.problem, score=100
        )
        cls.series = ExamSeries.objects.create(
            title="Đề HSG Hà Nội",
            slug="test-hsg",
            category="HSG",
            level="THCS",
            region="Hà Nội",
            is_published=True,
        )
        cls.paper = ExamPaper.objects.create(
            series=cls.series,
            title="Đề năm 2025",
            slug="year-2025",
            year=2025,
            lesson=cls.lesson,
            is_published=True,
            list_metadata=True,
            mock_minutes=60,
            mock_limit=1,
        )
        cls.paper.divisions.add(
            ExamDivision.objects.create(name="Bảng A"),
            ExamDivision.objects.create(name="Bảng B"),
        )

    def login(self):
        self.client.force_login(self.user)

    def login_admin(self):
        admin = User.objects.create_superuser("exam_admin", password="pass")
        Profile.objects.get_or_create(user=admin, defaults={"language": self.language})
        self.client.force_login(admin)
        return admin

    def start(self, delayed=False):
        self.paper.mock_delayed = delayed
        self.paper.save(update_fields=["mock_delayed"])
        mock = create_mock_contest(self.paper, self.profile)
        self.login()
        response = self.client.post(reverse("exam_mock_start", args=[self.paper.pk]))
        self.assertRedirects(
            response,
            reverse("contest_problems", args=[mock.key]),
            fetch_redirect_response=False,
        )
        self.profile.refresh_from_db(fields=["current_contest"])
        return ExamMockSession.objects.get(
            paper=self.paper, participation_id=self.profile.current_contest_id
        )

    def submit(self, session):
        return self.client.post(
            reverse("problem_submit", args=[self.problem.code]),
            {
                "source": "print(sum(map(int, input().split())))",
                "language": self.language.pk,
                "library_mock_session": session.participation_id,
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

    def test_public_catalog_can_list_metadata_without_disclosing_items(self):
        self.assertContains(self.client.get(reverse("exam_library")), self.series.title)
        response = self.client.get(self.paper.get_absolute_url())
        self.assertContains(response, self.paper.title)
        self.assertNotContains(response, self.problem.code)
        response = self.client.get(reverse("exam_library"), {"q": self.problem.code})
        self.assertNotContains(response, self.paper.title)
        self.assertEqual(
            self.client.get(
                reverse("get_comments"),
                {
                    "content_type_id": ContentType.objects.get_for_model(ExamPaper).pk,
                    "object_id": self.paper.pk,
                },
            ).status_code,
            404,
        )

    def test_search_by_code_and_shared_division(self):
        self.login()
        for division in ("Bảng A", "Bảng B"):
            response = self.client.get(
                reverse("exam_library"), {"q": self.problem.code, "division": division}
            )
            self.assertContains(response, self.paper.title)
        self.assertNotContains(
            self.client.get(
                reverse("exam_library"),
                {
                    "q": self.problem.code,
                    "division": "Bảng C",
                },
            ),
            self.paper.title,
        )

    def test_series_only_calculates_progress_for_the_current_page(self):
        ExamPaper.objects.bulk_create(
            [
                ExamPaper(
                    series=self.series,
                    lesson=self.lesson,
                    slug=f"test-paper-{index}",
                    title=f"Bộ đề {index}",
                    year=2025,
                    is_published=True,
                )
                for index in range(20)
            ]
        )
        self.login()
        with patch(
            "judge.views.exam_library.prepare_progress", wraps=prepare_progress
        ) as populate_progress:
            response = self.client.get(self.series.get_absolute_url(), {"page": 2})
        self.assertEqual(response.status_code, 200)
        populate_progress.assert_called_once()
        self.assertEqual(len(populate_progress.call_args.args[1]), 1)

    def test_shared_guide_is_available_inside_the_paper_only(self):
        guide = BlogPost.objects.create(
            title="Hướng dẫn chung",
            slug="exam-shared-guide",
            content="Cách luyện đề",
            publish_on=timezone.now(),
            visible=True,
        )
        self.series.guide = guide
        self.series.save(update_fields=["guide"])
        guide_href = f'href="{guide.get_absolute_url()}"'
        self.assertNotContains(
            self.client.get(self.paper.get_absolute_url()), guide_href
        )
        self.login()
        self.assertNotContains(
            self.client.get(self.series.get_absolute_url()), guide_href
        )
        response = self.client.get(self.paper.get_absolute_url())
        self.assertContains(response, guide_href)
        self.assertContains(response, gettext("Solution guide"))

    def test_list_displays_academic_year_once(self):
        for separator in ("–", "-", "/"):
            self.paper.title = f"Đề năm 2025{separator}2026"
            self.paper.save(update_fields=["title"])
            response = self.client.get(self.series.get_absolute_url())
            self.assertContains(response, self.paper.title)
            self.assertNotContains(response, "<li>2025–2026</li>")
        self.paper.title = "Đề thi chính thức"
        self.paper.save(update_fields=["title"])
        self.assertContains(
            self.client.get(self.series.get_absolute_url()),
            "<li>2025–2026</li>",
            count=1,
        )

    def test_progress_uses_existing_attempts_and_applies_each_paper_weight(self):
        submission = Submission.objects.create(
            user=self.profile,
            problem=self.problem,
            language=self.language,
            status="D",
            result="WA",
            case_points=8,
            case_total=10,
            points=80,
        )
        BestSubmission.recalculate_for_user_problem(self.profile.pk, self.problem.pk)
        self.login()
        response = self.client.get(self.paper.get_absolute_url())
        self.assertContains(response, gettext("Partial score"))
        self.assertContains(
            response,
            '80 <span class="assessment-denominator">/ 100</span>',
        )
        contest = Contest.objects.create(
            key="library_source",
            name="Nguồn 2025",
            start_time=timezone.now() - timedelta(days=2),
            end_time=timezone.now() - timedelta(days=1),
            is_visible=True,
        )
        ContestProblem.objects.create(
            contest=contest, problem=self.problem, points=20, order=1
        )
        other = ExamPaper.objects.create(
            series=self.series,
            title="Thang điểm khác",
            slug="scale",
            year=2025,
            contest=contest,
            is_published=True,
        )
        self.assertContains(
            self.client.get(other.get_absolute_url()),
            '16 <span class="assessment-denominator">/ 20</span>',
        )
        Submission.objects.filter(pk=submission.pk).update(case_points=10, result="AC")
        BestSubmission.recalculate_for_user_problem(self.profile.pk, self.problem.pk)
        response = self.client.get(reverse("exam_library"), {"tab": "complete"})
        self.assertContains(response, other.title)
        self.assertContains(response, self.paper.title)

    def test_save_is_idempotent_and_returns_to_the_filtered_list(self):
        self.login()
        listing = reverse("exam_library") + "?tab=saved&year=2025"
        for _ in range(2):
            response = self.client.post(
                reverse("exam_mark", args=[self.paper.pk]),
                {
                    "field": "saved",
                    "value": "1",
                    "next": listing,
                },
            )
            self.assertRedirects(response, listing, fetch_redirect_response=False)
        self.assertContains(self.client.get(listing), self.paper.title)
        self.client.post(
            reverse("exam_mark", args=[self.paper.pk]), {"field": "saved", "value": "0"}
        )
        self.assertNotContains(self.client.get(listing), self.paper.title)

    def test_preview_import_does_not_write_and_apply_is_repeatable(self):
        series = ExamSeries.objects.create(
            title="Nhập thử", slug="import-series", category="HSG", region="Hà Nội"
        )
        preview = import_sources(series, self.course, [])
        self.assertEqual(preview[0]["year"], 2025)
        self.assertFalse(series.papers.exists())
        self.assertTrue(
            import_sources(series, self.course, [], apply=True)[0]["created"]
        )
        self.assertFalse(
            import_sources(series, self.course, [], apply=True)[0]["created"]
        )
        self.assertFalse(series.papers.get().is_published)

    def test_mock_resume_reuses_session_and_keeps_a_fixed_deadline(self):
        session = self.start()
        self.assertEqual(session.participation.virtual, 1)
        self.assertEqual(
            session.deadline - session.participation.real_start, timedelta(hours=1)
        )
        self.client.post(reverse("exam_mock_start", args=[self.paper.pk]))
        self.assertEqual(ExamMockSession.objects.filter(paper=self.paper).count(), 1)
        self.assertEqual(session.participation.score, 0)

    def test_practice_problem_and_ide_offer_mock_without_starting_it(self):
        create_mock_contest(self.paper, self.profile)
        self.login()
        query = {"exam_paper": self.paper.pk}
        args = [self.course.slug, self.lesson.pk, self.problem.code]
        mock_href = f'href="{reverse("exam_mock_start", args=[self.paper.pk])}"'
        response = self.client.get(self.paper.get_absolute_url())
        practice_href = (
            reverse("course_lesson_problem_detail", args=args)
            + f"?exam_paper={self.paper.pk}"
        )
        self.assertContains(response, f'href="{practice_href}"')
        for route in ("course_lesson_problem_detail", "course_lesson_problem_ide"):
            self.assertContains(
                self.client.get(reverse(route, args=args), query), mock_href
            )
        self.assertNotContains(
            self.client.get(reverse("problem_detail", args=[self.problem.code]), query),
            mock_href,
        )
        self.assertFalse(ExamMockSession.objects.exists())

    def test_confirmation_shows_rules_before_starting_and_can_resume(self):
        mock = create_mock_contest(self.paper, self.profile)
        self.login()
        url = reverse("exam_mock_start", args=[self.paper.pk])
        response = self.client.get(url)
        self.assertContains(response, gettext("Confirm mock exam"))
        self.assertContains(response, gettext("%(count)s minutes") % {"count": 60})
        self.assertContains(response, gettext("Submissions per problem"))
        self.assertContains(response, gettext("Start now"))
        response = self.client.get(url, HTTP_X_EXAM_CONFIRMATION="1")
        self.assertEqual(response["X-Exam-Confirmation"], "1")
        self.assertContains(response, gettext("Go back"))
        self.assertContains(response, gettext("View contest"))
        self.assertContains(response, gettext("Start now"))
        self.assertContains(
            response, f'href="{reverse("contest_view", args=[mock.key])}"'
        )
        overview = self.client.get(reverse("contest_view", args=[mock.key]))
        self.assertContains(overview, gettext("Paper and mock exam history"))
        self.assertContains(overview, gettext("%(count)s minutes") % {"count": 60})
        self.assertFalse(ExamMockSession.objects.exists())

        self.assertRedirects(
            self.client.post(url),
            reverse("contest_problems", args=[mock.key]),
            fetch_redirect_response=False,
        )
        session = ExamMockSession.objects.get(paper=self.paper)
        self.assertContains(self.client.get(url), gettext("Resume exam"))
        self.client.post(url)
        self.assertEqual(
            ExamMockSession.objects.get(paper=self.paper).deadline, session.deadline
        )

    def test_unavailable_mock_confirmation_does_not_offer_entry(self):
        self.login()
        response = self.client.get(
            reverse("exam_mock_start", args=[self.paper.pk]),
            HTTP_X_EXAM_CONFIRMATION="1",
        )
        self.assertContains(
            response,
            gettext("A mock exam is not available for this paper right now."),
            status_code=409,
        )
        self.assertNotContains(response, 'type="submit"', status_code=409)
        self.assertFalse(ExamMockSession.objects.exists())

    def test_revisit_can_be_toggled_without_saving_paper(self):
        self.login()
        paper_url = self.paper.get_absolute_url()
        self.assertContains(self.client.get(paper_url), 'class="fa fa-redo"')
        self.client.post(
            reverse("exam_mark", args=[self.paper.pk]),
            {"field": "revisit", "value": "1"},
        )
        self.assertContains(self.client.get(paper_url), 'class="fa fa-check-circle"')
        self.assertContains(
            self.client.get(reverse("exam_library"), {"tab": "revisit"}),
            self.paper.title,
        )
        self.assertNotContains(
            self.client.get(reverse("exam_library"), {"tab": "saved"}), self.paper.title
        )
        self.client.post(
            reverse("exam_mark", args=[self.paper.pk]),
            {"field": "revisit", "value": "0"},
        )
        self.assertContains(self.client.get(paper_url), 'class="fa fa-redo"')

    def test_mock_forms_identify_session_and_reject_stale_practice_form(self):
        session = self.start()
        for route, args in (
            (
                "contest_problem_detail",
                [session.participation.contest.key, self.problem.code],
            ),
            ("contest_ide", [session.participation.contest.key, self.problem.code]),
            ("problem_submit", [self.problem.code]),
        ):
            self.assertContains(
                self.client.get(reverse(route, args=args)),
                f'name="library_mock_session" value="{session.participation_id}"',
            )
        response = self.client.post(
            reverse("problem_submit", args=[self.problem.code]),
            {
                "source": "print(sum(map(int, input().split())))",
                "language": self.language.pk,
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 409)
        self.assertFalse(Submission.objects.exists())

    def test_unpublished_paper_blocks_its_generated_contest_problem(self):
        session = self.start()
        self.paper.is_published = False
        self.paper.save(update_fields=["is_published"])
        self.assertEqual(
            self.client.get(
                reverse(
                    "contest_problem_detail",
                    args=[session.participation.contest.key, self.problem.code],
                )
            ).status_code,
            404,
        )
        self.assertFalse(self.course.get_contests().exists())

    @patch("judge.models.submission.Submission.judge")
    def test_delayed_submission_is_queued_after_deadline_and_limit_is_enforced(
        self, judge
    ):
        session = self.start(delayed=True)
        response = self.submit(session)
        self.assertEqual(response.status_code, 201, response.content[:500])
        self.assertIsNotNone(response.json()["scheduled_grading_at"])
        self.assertEqual(self.submit(session).status_code, 403)
        judge.assert_not_called()
        dispatch_exam_submissions()
        judge.assert_not_called()
        ExamDeferredSubmission.objects.update(
            ready_at=timezone.now() - timedelta(seconds=1)
        )
        dispatch_exam_submissions()
        dispatch_exam_submissions()
        judge.assert_called_once()

    def test_delayed_configuration_requires_a_positive_submission_limit(self):
        self.paper.mock_delayed = True
        self.paper.mock_limit = 0
        with self.assertRaises(ValidationError):
            self.paper.clean()
        self.paper.save(update_fields=["mock_delayed", "mock_limit"])
        with self.assertRaises(ValidationError):
            create_mock_contest(self.paper, self.profile)

    @override_settings(EXAM_LIBRARY_MAX_PENDING_SUBMISSIONS=1)
    def test_delayed_backlog_cap_survives_starting_another_attempt(self):
        session = self.start(delayed=True)
        self.assertEqual(self.submit(session).status_code, 201)
        self.client.post(
            reverse("contest_leave", args=[session.participation.contest.key]),
            {"library_mock_session": session.participation_id},
        )
        next_session = self.start(delayed=True)
        self.assertEqual(self.submit(next_session).status_code, 429)
        self.assertEqual(ExamDeferredSubmission.objects.count(), 1)

    def test_delayed_mode_is_closed_until_the_scheduler_is_confirmed(self):
        session = self.start(delayed=True)
        with override_settings(EXAM_LIBRARY_DELAYED_GRADING_ENABLED=False):
            with self.assertRaises(ValidationError):
                self.paper.clean()
            self.assertEqual(
                self.client.post(
                    reverse("exam_mock_start", args=[self.paper.pk])
                ).status_code,
                409,
            )
            self.assertEqual(self.submit(session).status_code, 409)
            self.assertFalse(Submission.objects.exists())

    def test_mock_contests_are_only_listed_in_the_dedicated_library(self):
        contest = create_mock_contest(self.paper, self.profile)
        for user in (AnonymousUser(), self.user):
            self.assertFalse(
                Contest.get_visible_contests(user).filter(pk=contest.pk).exists()
            )
        self.assertNotIn((contest.key,), ContestSitemap().items())
        self.paper.lesson.lesson_problems.update(score=75)
        updated = create_mock_contest(self.paper, self.profile, synchronize=True)
        for target in (contest, updated):
            self.assertFalse(
                Contest.get_visible_contests(self.user).filter(pk=target.pk).exists()
            )

    @patch("judge.judgeapi._post_update_submission")
    @patch(
        "judge.judgeapi.judge_request",
        return_value={"name": "rejected", "submission-id": 0},
    )
    def test_rejected_judge_reply_publishes_error_and_can_be_retried(
        self, request, publish
    ):
        session = self.start()
        with patch("judge.models.submission.Submission.judge", return_value=True):
            self.assertEqual(self.submit(session).status_code, 201)
        submission = Submission.objects.get()
        self.assertFalse(judge_submission(submission))
        submission.refresh_from_db()
        self.assertEqual(submission.status, "IE")
        publish.assert_called_once_with(submission)

    def test_mock_ac_improves_shared_problem_progress_without_adding_contest_grade(
        self,
    ):
        session = self.start()
        submission = Submission.objects.create(
            user=self.profile,
            problem=self.problem,
            language=self.language,
            contest_object=session.participation.contest,
            status="D",
            result="AC",
            points=100,
            case_points=100,
            case_total=100,
        )
        ContestSubmission.objects.create(
            submission=submission,
            participation=session.participation,
            problem=session.participation.contest.contest_problems.get(
                problem=self.problem
            ),
        )
        BestSubmission.recalculate_for_user_problem(self.profile.pk, self.problem.pk)
        self.assertFalse(self.course.get_contests().exists())
        response = self.client.get(self.course.get_absolute_url())
        self.assertEqual(
            response.context_data["total_progress"]["achieved_points"], 100
        )

    @patch("judge.models.submission.Submission.judge", side_effect=[False, True])
    def test_deferred_dispatch_retries_when_the_judge_is_unavailable(self, judge):
        session = self.start(delayed=True)
        self.assertEqual(self.submit(session).status_code, 201)
        ExamDeferredSubmission.objects.update(
            ready_at=timezone.now() - timedelta(seconds=1)
        )
        dispatch_exam_submissions()
        row = ExamDeferredSubmission.objects.get()
        self.assertIsNone(row.dispatched_at)
        self.assertGreater(row.claim_until, timezone.now())
        ExamDeferredSubmission.objects.update(
            claim_until=timezone.now() - timedelta(seconds=1)
        )
        dispatch_exam_submissions()
        row.refresh_from_db()
        self.assertIsNotNone(row.dispatched_at)
        self.assertEqual(judge.call_count, 2)

    @patch("judge.models.submission.Submission.judge")
    def test_immediate_submission_is_sent_to_the_judge(self, judge):
        session = self.start()
        response = self.submit(session)
        self.assertEqual(response.status_code, 201, response.content[:500])
        judge.assert_called_once()
        self.assertFalse(ExamDeferredSubmission.objects.exists())

    def test_source_contest_ban_also_blocks_an_existing_mock_session(self):
        now = timezone.now()
        source = Contest.objects.create(
            key="library_source_ban",
            name="Contest nguồn",
            is_visible=True,
            start_time=now - timedelta(days=2),
            end_time=now - timedelta(days=1),
        )
        ContestProblem.objects.create(
            contest=source, problem=self.problem, points=100, order=1
        )
        self.paper.lesson = None
        self.paper.contest = source
        self.paper.save(update_fields=["lesson", "contest"])
        session = self.start()

        source.banned_users.add(self.profile)
        start_url = reverse("exam_mock_start", args=[self.paper.pk])
        self.assertContains(
            self.client.get(start_url),
            gettext("You no longer have access to the current mock exam."),
            status_code=409,
        )
        self.assertEqual(self.client.post(start_url).status_code, 403)
        self.assertEqual(self.submit(session).status_code, 403)
        self.assertFalse(Submission.objects.exists())

    def test_expired_mock_form_does_not_submit_as_practice(self):
        session = self.start()
        type(session.participation).objects.filter(pk=session.participation_id).update(
            library_deadline=timezone.now() - timedelta(seconds=2),
        )
        self.assertEqual(self.submit(session).status_code, 409)
        self.assertFalse(Submission.objects.exists())

    def test_leaving_mock_finishes_attempt_and_releases_held_results(self):
        session = self.start(delayed=True)
        self.assertEqual(self.submit(session).status_code, 201)
        deadline = session.deadline
        leave_url = reverse("contest_leave", args=[session.participation.contest.key])
        request_data = {"library_mock_session": session.participation_id}
        self.assertRedirects(
            self.client.post(leave_url, request_data), self.paper.get_absolute_url()
        )
        self.profile.refresh_from_db()
        session.refresh_from_db()
        self.assertIsNone(self.profile.current_contest_id)
        self.assertFalse(session.active)
        # Results held for after the attempt are due as soon as it ends.
        ready_at = ExamDeferredSubmission.objects.get().ready_at
        self.assertLess(ready_at, deadline)
        self.assertLessEqual(ready_at, timezone.now())
        self.assertEqual(self.submit(session).status_code, 409)
        paper = self.client.get(self.paper.get_absolute_url())
        self.assertContains(paper, gettext("Problem list"))
        self.assertNotContains(paper, gettext("Resume exam"))
        self.start()
        self.profile.refresh_from_db()
        new_attempt = self.profile.current_contest_id
        self.assertNotEqual(new_attempt, session.participation_id)
        self.client.post(leave_url, request_data)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.current_contest_id, new_attempt)

    def test_paper_lists_problem_specs_and_uses_default_streams(self):
        self.login()
        response = self.client.get(self.paper.get_absolute_url())
        self.assertContains(response, f"{gettext('stdin')} / {gettext('stdout')}")
        self.assertContains(response, "1.0s · 64M")
        self.assertContains(response, "table striped hn-assessment-table")
        self.assertContains(response, gettext("Difficulty"))
        self.assertNotContains(response, gettext("Problem types"))
        self.assertNotContains(
            response, f'<span class="exam-meta">{self.problem.code}</span>'
        )
        ProblemData.objects.create(
            problem=self.problem, fileio_input="sum.inp", fileio_output="sum.out"
        )
        response = self.client.get(self.paper.get_absolute_url())
        self.assertContains(response, "sum.inp")
        self.assertContains(response, "sum.out")
        self.login_admin()
        self.assertContains(
            self.client.get(self.paper.get_absolute_url()), gettext("Problem types")
        )

    def test_paper_shows_no_difficulty_during_its_mock(self):
        self.problem.points = 1200
        self.problem.save(update_fields=["points"])
        self.login()

        def cells():
            response = self.client.get(self.paper.get_absolute_url())
            self.assertEqual(response.status_code, 200)
            page = BeautifulSoup(response.content, "html.parser")
            return [td.get_text(strip=True) for td in page.select(".exam-problems td")]

        self.assertIn("1200", cells())
        self.start()
        self.assertNotIn("1200", cells())

    def test_admin_enables_mock_using_original_duration(self):
        self.login_admin()
        self.paper.mock_minutes = None
        self.paper.duration_minutes = 90
        self.paper.save(update_fields=["mock_minutes", "duration_minutes"])
        change_url = reverse("admin:judge_exampaper_change", args=[self.paper.pk])
        page = self.client.get(change_url)
        self.assertContains(page, "Chưa bật thi thử.")
        self.assertContains(page, "Bật thi thử khi lưu")
        data = {
            "_save": "1",
            "series": self.series.pk,
            "title": self.paper.title,
            "slug": self.paper.slug,
            "year": self.paper.year,
            "academic_year": "on",
            "lesson": self.lesson.pk,
            "duration_minutes": 90,
            "mock_minutes": 60,
            "mock_limit": 3,
            "is_published": "on",
        }
        self.assertRedirects(
            self.client.post(change_url, data),
            reverse("admin:judge_exampaper_changelist"),
            fetch_redirect_response=False,
        )
        self.paper.refresh_from_db()
        self.assertEqual(self.paper.mock_minutes, 60)
        self.assertEqual(self.paper.mock_limit, 3)
        self.assertIsNone(self.paper.mock_contest_id)

        response = self.client.post(
            change_url, {**data, "mock_minutes": "", "enable_mock_on_save": "1"}
        )
        self.assertRedirects(response, change_url, fetch_redirect_response=False)
        self.paper.refresh_from_db()
        self.assertEqual(self.paper.mock_minutes, 90)
        self.assertEqual(self.paper.mock_limit, 3)
        self.assertIsNotNone(self.paper.mock_contest_id)
        page = self.client.get(change_url)
        self.assertContains(page, "Đã bật thi thử.")
        self.assertNotContains(page, "Bật thi thử khi lưu")
        self.assertContains(
            self.client.get(
                reverse("admin:judge_exampaper_changelist"), {"mock": "configured"}
            ),
            "Đã tạo · 90 phút",
        )

    def test_admin_imports_only_selected_sources_with_edited_metadata(self):
        self.login_admin()
        source = CourseLesson.objects.create(
            course=self.course, title="Đề chưa ghi năm", order=2, points=100
        )
        other = CourseLesson.objects.create(
            course=self.course, title="Không cần nhập 2024", order=3, points=100
        )
        url = reverse("admin:judge_exampaper_import")
        form = {"series": self.series.pk, "course": self.course.pk}
        self.assertContains(self.client.post(url, form), "Đề chưa ghi năm")
        self.assertFalse(ExamPaper.objects.filter(lesson=source).exists())
        division = self.paper.divisions.first()
        response = self.client.post(
            url,
            {
                **form,
                "apply": "1",
                "papers-TOTAL_FORMS": 2,
                "papers-INITIAL_FORMS": 2,
                "papers-0-source_id": source.pk,
                "papers-0-selected": "on",
                "papers-0-title": "Đề mới",
                "papers-0-academic_year": "on",
                "papers-0-year": 2023,
                "papers-0-round_name": "Vòng 1",
                "papers-0-divisions": [division.pk],
                "papers-1-source_id": other.pk,
            },
        )
        self.assertRedirects(
            response,
            reverse("admin:judge_exampaper_changelist"),
            fetch_redirect_response=False,
        )
        paper = ExamPaper.objects.get(lesson=source)
        self.assertEqual(
            (paper.title, paper.year, paper.round_name), ("Đề mới", 2023, "Vòng 1")
        )
        self.assertFalse(paper.is_published)
        self.assertEqual(list(paper.divisions.all()), [division])
        self.assertFalse(ExamPaper.objects.filter(lesson=other).exists())

    def test_sync_preserves_previous_virtual_attempt_and_new_attempt_uses_new_source(
        self,
    ):
        admin = self.login_admin()
        create_mock_contest(self.paper, admin.profile)
        session = self.start()
        old_contest = session.participation.contest
        site_url = self.series.get_absolute_url()
        start_url = reverse("exam_mock_start", args=[self.paper.pk])
        ranking = reverse("contest_ranking", args=[old_contest.key]) + "?virtual=1"
        CourseLessonProblem.objects.filter(
            lesson=self.lesson, problem=self.problem
        ).update(score=80)
        self.assertNotContains(self.client.get(ranking), 'id="ranking-container"')
        self.client.force_login(admin)
        overview = reverse("admin:judge_exampaper_mock", args=[self.paper.pk])
        self.assertContains(
            self.client.get(overview, {"student": self.user.username}),
            "Hãy đồng bộ thi thử",
        )
        sync_url = reverse("admin:judge_exampaper_mock_sync", args=[self.paper.pk])
        self.assertEqual(self.client.get(sync_url).status_code, 405)
        self.assertRedirects(
            self.client.post(sync_url), overview, fetch_redirect_response=False
        )
        self.paper.refresh_from_db()
        new_contest = self.paper.mock_contest
        self.assertNotEqual(old_contest, new_contest)
        self.assertEqual(
            ExamMockArchive.objects.get(contest=old_contest).paper, self.paper
        )
        self.assertEqual(old_contest.contest_problems.get().points, 100)
        self.assertEqual(new_contest.contest_problems.get().points, 80)
        self.assertFalse(self.course.get_contests().exists())
        self.assertContains(
            self.client.get(overview, {"q": self.user.username, "status": "active"}),
            self.user.username,
        )
        self.assertContains(self.client.get(ranking), self.user.username)
        self.login()
        self.assertRedirects(
            self.client.post(start_url),
            reverse("contest_problems", args=[old_contest.key]),
            fetch_redirect_response=False,
        )
        leave = reverse("contest_leave", args=[old_contest.key])
        self.assertRedirects(
            self.client.post(leave, {"library_mock_session": session.participation_id}),
            self.paper.get_absolute_url(),
            fetch_redirect_response=False,
        )
        self.assertContains(self.client.get(site_url), f'href="{start_url}"')
        self.assertRedirects(
            self.client.post(start_url),
            reverse("contest_problems", args=[new_contest.key]),
            fetch_redirect_response=False,
        )
        self.assertNotContains(self.client.get(ranking), 'id="ranking-container"')

    def test_drift_hides_student_entry_and_admin_shows_reason(self):
        create_mock_contest(self.paper, self.profile)
        CourseLessonProblem.objects.filter(
            lesson=self.lesson, problem=self.problem
        ).update(score=80)
        start_url = reverse("exam_mock_start", args=[self.paper.pk])
        self.login()
        self.assertNotContains(
            self.client.get(self.series.get_absolute_url()), f'href="{start_url}"'
        )
        self.assertNotContains(
            self.client.get(self.paper.get_absolute_url()), f'href="{start_url}"'
        )
        self.login_admin()
        self.assertContains(
            self.client.get(
                reverse("admin:judge_exampaper_mock", args=[self.paper.pk]),
                {"student": self.user.username},
            ),
            "Hãy đồng bộ thi thử",
        )

    def test_contest_mock_list_shows_only_accessible_current_papers(self):
        target = create_mock_contest(self.paper, self.profile)
        contest_url = reverse("contest_view", args=[target.key])
        mock_url = reverse("mock_contest_list")
        self.assertNotContains(self.client.get(mock_url), contest_url)
        self.login()
        response = self.client.get(mock_url, {"contest": target.key})
        self.assertContains(response, f'href="{contest_url}"')
        self.assertContains(response, gettext("%(count)s minutes") % {"count": 60})
        self.assertRegex(
            response.content.decode(), r"id='past-url'[^>]*aria-current=\"page\""
        )
        for tab in ("active", "current", "future"):
            self.assertNotContains(
                self.client.get(mock_url, {"tab": tab}), f'href="{contest_url}"'
            )
        self.assertNotContains(
            self.client.get(reverse("contest_list"), {"tab": "past"}),
            f'href="{contest_url}"',
        )
        self.start()
        self.assertContains(
            self.client.get(mock_url, {"tab": "active"}), gettext("Resume exam")
        )
        self.paper.is_published = False
        self.paper.save(update_fields=["is_published"])
        self.assertNotContains(self.client.get(mock_url), f'href="{contest_url}"')

    def test_contest_mock_list_counts_distinct_participants(self):
        contest = create_mock_contest(self.paper, self.profile)
        list_url = reverse("mock_contest_list")
        self.login()
        self.assertContains(
            self.client.get(list_url),
            ngettext("%(count)s participant", "%(count)s participants", 0)
            % {"count": 0},
        )

        first = self.start()
        leave_url = reverse("contest_leave", args=[contest.key])
        self.client.post(leave_url, {"library_mock_session": first.participation_id})
        second = self.start()
        self.assertNotEqual(first.participation_id, second.participation_id)
        self.assertContains(
            self.client.get(list_url),
            ngettext("%(count)s participant", "%(count)s participants", 1)
            % {"count": 1},
        )
        self.assertContains(
            self.client.get(list_url, {"tab": "active"}),
            ngettext("%(count)s participant", "%(count)s participants", 1)
            % {"count": 1},
        )

    def test_contest_mock_edit_opens_paper_admin_for_authorized_staff(self):
        contest = create_mock_contest(self.paper, self.profile)
        contest_url = reverse("contest_view", args=[contest.key])
        edit_url = reverse("contest_edit", args=[contest.key])
        paper_edit_url = reverse("admin:judge_exampaper_change", args=[self.paper.pk])

        self.login()
        self.assertEqual(self.client.get(edit_url).status_code, 403)
        self.assertNotContains(self.client.get(contest_url), gettext("Edit paper"))

        self.login_admin()
        self.assertRedirects(self.client.get(edit_url), paper_edit_url)
        response = self.client.get(contest_url)
        self.assertContains(
            response, f'href="{paper_edit_url}" id="edit-tab" data-force_new_page="1"'
        )
        self.assertNotContains(response, f'href="{edit_url}"')
        self.assertEqual(
            self.client.post(edit_url, {"name": "Tên bị sửa"}).status_code, 403
        )
        contest.refresh_from_db()
        self.assertNotEqual(contest.name, "Tên bị sửa")

    def test_enrolled_student_can_load_paper_comments(self):
        content_type = ContentType.objects.get_for_model(ExamPaper)
        comment = Comment.objects.create(
            author=self.profile,
            content_type=content_type,
            object_id=self.paper.pk,
            body="Một cách giải khác",
        )
        self.login()
        response = self.client.get(
            reverse("get_comments"),
            {
                "content_type_id": content_type.pk,
                "object_id": self.paper.pk,
            },
        )
        self.assertContains(response, comment.body)

    def test_new_paper_takes_the_mock_defaults_of_its_exam(self):
        self.series.mock_limit_default = 1
        self.series.mock_delayed_default = True
        self.series.save()
        lesson = CourseLesson.objects.create(
            course=self.course, title="2026", points=100
        )
        paper = ExamPaper.objects.create(
            series=self.series,
            title="Đề 2026",
            slug="year-2026",
            year=2026,
            lesson=lesson,
        )
        self.assertEqual((paper.mock_limit, paper.mock_delayed), (1, True))
        # Values chosen for the paper itself win over the exam defaults.
        chosen = ExamPaper.objects.create(
            series=self.series,
            title="Đề 2027",
            slug="year-2027",
            year=2027,
            lesson=CourseLesson.objects.create(
                course=self.course, title="2027", points=100
            ),
            mock_limit=3,
        )
        self.assertEqual((chosen.mock_limit, chosen.mock_delayed), (3, False))

    @override_settings(EXAM_LIBRARY_DELAYED_GRADING_ENABLED=False)
    def test_new_paper_shows_results_at_once_until_delayed_grading_is_on(self):
        self.series.mock_limit_default = 1
        self.series.mock_delayed_default = True
        self.series.save()
        lesson = CourseLesson.objects.create(
            course=self.course, title="2026", points=100
        )
        paper = ExamPaper.objects.create(
            series=self.series,
            title="Đề 2026",
            slug="year-2026",
            year=2026,
            lesson=lesson,
        )
        self.assertEqual((paper.mock_limit, paper.mock_delayed), (1, False))
        paper.full_clean()

    def test_delayed_default_needs_a_submission_limit(self):
        self.series.mock_delayed_default = True
        with self.assertRaises(ValidationError):
            self.series.full_clean()

    def apply_defaults(self, model, ids):
        name = model._meta.model_name
        return self.client.post(
            reverse(f"admin:judge_{name}_changelist"),
            {"action": "apply_mock_defaults", "_selected_action": ids},
            follow=True,
        )

    def test_admin_applies_exam_defaults_to_its_papers(self):
        self.login_admin()
        self.series.mock_limit_default = 2
        self.series.mock_delayed_default = True
        self.series.save()
        response = self.apply_defaults(ExamSeries, [self.series.pk])
        self.assertContains(response, "Đã áp mặc định thi thử cho 1 bộ đề.")
        self.paper.refresh_from_db()
        self.assertEqual((self.paper.mock_limit, self.paper.mock_delayed), (2, True))

        self.paper.mock_limit = 5
        self.paper.save(update_fields=["mock_limit"])
        self.apply_defaults(ExamPaper, [self.paper.pk])
        self.paper.refresh_from_db()
        self.assertEqual(self.paper.mock_limit, 2)

    @override_settings(EXAM_LIBRARY_DELAYED_GRADING_ENABLED=False)
    def test_admin_warns_when_delayed_results_are_not_available(self):
        self.login_admin()
        self.series.mock_limit_default = 1
        self.series.mock_delayed_default = True
        self.series.save()
        response = self.apply_defaults(ExamSeries, [self.series.pk])
        self.assertContains(response, "chưa bật chấm sau giờ")
        self.paper.refresh_from_db()
        self.assertEqual((self.paper.mock_limit, self.paper.mock_delayed), (1, False))

    def test_only_superusers_apply_mock_defaults(self):
        staff = User.objects.create_user("exam_staff", password="pass", is_staff=True)
        Profile.objects.get_or_create(user=staff, defaults={"language": self.language})
        staff.user_permissions.add(
            *Permission.objects.filter(
                codename__in=["change_examseries", "view_examseries"]
            )
        )
        self.client.force_login(staff)
        response = self.client.get(reverse("admin:judge_examseries_changelist"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "apply_mock_defaults")

    def test_migration_sets_hsg_defaults(self):
        from importlib import import_module

        from django.apps import apps
        from django.db import connection

        seed = import_module("judge.migrations.0302_seed_exam_series_mock_defaults")
        hsg = ExamSeries.objects.create(
            title="HSG Quốc gia",
            slug="hsg-tin-hoc-quoc-gia",
            category="HSG",
            region="Toàn quốc",
        )
        seed.set_defaults(apps, connection.schema_editor(atomic=False))
        hsg.refresh_from_db()
        self.assertEqual((hsg.mock_limit_default, hsg.mock_delayed_default), (1, True))
        self.series.refresh_from_db()
        self.assertEqual(self.series.mock_limit_default, 0)
