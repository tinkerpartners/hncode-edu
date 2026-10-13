"""A18 (#105): official-exam mode on top of strict contests.

Owner decisions (2026-10-13):
  * Over the limit (or out of fullscreen past the grace period) the contestant
    is LOCKED, not disqualified: no problems, no quizzes, no submissions until
    an administrator unlocks them; scores, rating and the ban list untouched.
  * New strict contests default to a limit of 1.
  * Quizzes M1/M2 live inside the strict contest: same gate, same lock.
  * Moving between the contest's problems and quizzes is allowed navigation.
"""

from datetime import timedelta

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from judge.models import Contest, ContestProblem, ContestViolationLog
from judge.models.quiz import (
    Quiz,
    QuizAttempt,
    QuizQuestion,
    QuizQuestionAssignment,
)
from judge.tests.strict_helpers import StrictContestMixin
from judge.utils.contest_strict import record_violation, strict_session_ok


@override_settings(LANGUAGE_CODE="en")
class StrictLockTest(StrictContestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.question = QuizQuestion.objects.create(
            question_type="SA",
            title="M1 Q1",
            content="2+2?",
            correct_answers={"answers": ["4"]},
            is_public=True,
        )
        self.quiz = Quiz.objects.create(
            code="m1exam", title="M1", description="", time_limit=60, is_public=True
        )
        QuizQuestionAssignment.objects.create(
            quiz=self.quiz, question=self.question, points=10, order=1
        )
        ContestProblem.objects.create(
            contest=self.contest, quiz=self.quiz, points=10, order=1
        )

    # -- helpers ----------------------------------------------------------------

    def lock(self, participation):
        for _ in range(self.contest.strict_violation_limit):
            record_violation(participation, ContestViolationLog.FOCUS_LOST)
        participation.refresh_from_db()
        self.assertIsNotNone(participation.strict_locked_at)

    def attempt(self, participation):
        return QuizAttempt.objects.create(
            user=self.participant, quiz=self.quiz, contest_participation=participation
        )

    def save(self, attempt, answer="4"):
        return self.client.post(
            reverse(
                "quiz_save_answer",
                kwargs={"code": self.quiz.code, "attempt_id": attempt.id},
            ),
            data='{"question_id": %d, "answer": "%s"}' % (self.question.id, answer),
            content_type="application/json",
        )

    # -- defaults ----------------------------------------------------------------

    def test_new_strict_contests_lock_on_the_first_exit(self):
        now = timezone.now()
        contest = Contest.objects.create(
            key="examdefault",
            name="E",
            start_time=now,
            end_time=now + timedelta(hours=1),
            is_strict=True,
        )
        self.assertEqual(contest.strict_violation_limit, 1)

    # -- locked: nothing works ----------------------------------------------------

    def test_locked_contestant_cannot_submit(self):
        participation = self.join()
        self.lock(participation)
        ok, reason = strict_session_ok(participation)
        self.assertFalse(ok)
        self.assertIn("locked out", str(reason))

    def test_locked_contestant_cannot_open_problems(self):
        participation = self.join()
        self.lock(participation)
        self.login()
        for url in (
            reverse("contest_ide", args=(self.contest.key, self.problem.code)),
            reverse("problem_detail", args=(self.problem.code,)),
            reverse("problem_raw", args=(self.problem.code,)),
        ):
            with self.subTest(url=url):
                r = self.client.get(url)
                self.assertEqual(r.status_code, 403)
                self.assertContains(r, "Locked out of the contest", status_code=403)

    def test_locked_contestant_cannot_open_start_or_work_on_quizzes(self):
        participation = self.join()
        attempt = self.attempt(participation)
        self.lock(participation)
        self.login()
        r = self.client.get(reverse("quiz_detail", kwargs={"code": self.quiz.code}))
        self.assertEqual(r.status_code, 403)
        r = self.client.post(reverse("quiz_start", kwargs={"code": self.quiz.code}))
        self.assertEqual(r.status_code, 403)
        r = self.client.get(
            reverse(
                "quiz_take", kwargs={"code": self.quiz.code, "attempt_id": attempt.id}
            )
        )
        self.assertEqual(r.status_code, 403)
        r = self.save(attempt)
        self.assertEqual(r.status_code, 403)
        self.assertTrue(r.json()["strict"])
        self.assertTrue(r.json()["locked"])
        r = self.client.post(
            reverse(
                "quiz_submit", kwargs={"code": self.quiz.code, "attempt_id": attempt.id}
            )
        )
        self.assertEqual(r.status_code, 403)
        attempt.refresh_from_db()
        self.assertFalse(attempt.is_submitted)

    def test_answers_saved_before_the_lock_are_kept(self):
        participation = self.join()
        attempt = self.attempt(participation)
        self.login()
        self.assertEqual(self.save(attempt, "4").status_code, 200)
        self.lock(participation)
        self.assertEqual(self.save(attempt, "5").status_code, 403)
        answer = attempt.answers.get(question=self.question)
        self.assertEqual(answer.answer, "4")

    def test_locked_session_is_not_counted_or_kept_alive(self):
        participation = self.join()
        self.lock(participation)
        before = participation.strict_violations
        self.login()
        r = self.client.post(
            reverse("contest_strict_event", args=(self.contest.key,)),
            data='{"action": "focus_lost", "nonce": "x1"}',
            content_type="application/json",
        )
        self.assertTrue(r.json()["locked"])
        last_seen = participation.strict_last_seen
        r = self.client.post(
            reverse("contest_strict_heartbeat", args=(self.contest.key,)),
            data='{"fullscreen": true}',
            content_type="application/json",
        )
        self.assertTrue(r.json()["locked"])
        participation.refresh_from_db()
        self.assertEqual(participation.strict_violations, before)
        self.assertEqual(participation.strict_last_seen, last_seen)

    def test_scores_survive_the_lock_and_the_unlock(self):
        participation = self.join()
        participation.score = 42
        participation.save(update_fields=["score"])
        self.lock(participation)
        participation.refresh_from_db()
        self.assertEqual(participation.score, 42)
        self.assertFalse(participation.is_disqualified)

    # -- quiz gate while not locked -------------------------------------------------

    def test_quiz_needs_a_live_proctored_session(self):
        participation = self.join()
        attempt = self.attempt(participation)
        self.login()
        self.assertEqual(self.save(attempt).status_code, 200)
        # The heartbeat lapsed (script killed, or out of fullscreen too long).
        type(participation).objects.filter(pk=participation.pk).update(
            strict_last_seen=timezone.now() - timedelta(minutes=5)
        )
        r = self.save(attempt)
        self.assertEqual(r.status_code, 403)
        self.assertFalse(r.json()["locked"])
        # The page itself still opens: that is where they re-enter fullscreen.
        r = self.client.get(
            reverse(
                "quiz_take", kwargs={"code": self.quiz.code, "attempt_id": attempt.id}
            )
        )
        self.assertEqual(r.status_code, 200)

    def test_quiz_outside_a_strict_contest_is_unaffected(self):
        Contest.objects.filter(id=self.contest.id).update(is_strict=False)
        participation = self.join(armed=False)
        attempt = self.attempt(participation)
        self.login()
        self.assertEqual(self.save(attempt).status_code, 200)

    def test_expired_attempt_is_still_submitted_when_locked(self):
        participation = self.join()
        attempt = self.attempt(participation)
        self.login()
        self.save(attempt, "4")
        self.lock(participation)
        Contest.objects.filter(id=self.contest.id).update(
            end_time=timezone.now() - timedelta(minutes=1)
        )
        r = self.client.post(
            reverse(
                "quiz_submit", kwargs={"code": self.quiz.code, "attempt_id": attempt.id}
            )
        )
        self.assertIn(r.status_code, (200, 302))
        attempt.refresh_from_db()
        self.assertTrue(attempt.is_submitted)

    # -- unlock -----------------------------------------------------------------

    def test_unlock_by_an_administrator_restores_access(self):
        participation = self.join()
        attempt = self.attempt(participation)
        self.lock(participation)
        self.login(self.author)
        self.client.post(
            reverse("contest_strict_unban", args=(self.contest.key,)),
            {"participation": participation.id, "reason": "power cut"},
        )
        participation.refresh_from_db()
        self.assertIsNone(participation.strict_locked_at)
        # Back in after re-arming.
        now = timezone.now()
        type(participation).objects.filter(pk=participation.pk).update(
            strict_armed_at=now, strict_last_seen=now
        )
        self.client.logout()
        self.login()
        self.assertEqual(self.save(attempt).status_code, 200)

    # -- proctor view and contestant page ----------------------------------------

    def test_violation_page_flags_locked_contestants(self):
        participation = self.join()
        self.lock(participation)
        self.login(self.author)
        r = self.client.get(
            reverse("contest_strict_violations", args=(self.contest.key,))
        )
        self.assertContains(r, "Locked")
        self.assertContains(r, "Unlock")
        self.assertContains(r, "participant(s) locked out")
        self.assertContains(r, 'name="reason"')

    def test_config_allows_this_contests_quizzes_and_reports_the_lock(self):
        import json
        import re

        participation = self.join()
        self.login()
        html = self.client.get(
            reverse("contest_view", args=(self.contest.key,))
        ).content.decode()
        config = json.loads(
            re.search(
                r'<script id="strict-contest-config" type="application/json">(.*?)</script>',
                html,
                re.S,
            ).group(1)
        )
        self.assertEqual(config["quizCodes"], ["m1exam"])
        self.assertFalse(config["locked"])
        self.lock(participation)
        html = self.client.get(
            reverse("contest_view", args=(self.contest.key,))
        ).content.decode()
        config = json.loads(
            re.search(
                r'<script id="strict-contest-config" type="application/json">(.*?)</script>',
                html,
                re.S,
            ).group(1)
        )
        self.assertTrue(config["locked"])

    def test_ide_sidebar_lists_the_contest_quizzes(self):
        self.join()
        self.login()
        r = self.client.get(
            reverse("contest_ide", args=(self.contest.key, self.problem.code))
        )
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, reverse("quiz_detail", kwargs={"code": "m1exam"}))
