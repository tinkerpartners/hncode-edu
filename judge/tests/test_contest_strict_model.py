"""Violation accounting and the auto-lock path.

The important guarantees: only counted actions (leaving the screen) move the
counter; crossing the limit locks the contestant out -- since A18 a lock, not a
disqualification: scores, rating and the ban list are untouched; unlocking (and
un-disqualifying, for a manual disqualification) clears the counter, otherwise
the contestant comes back already at the limit.
"""

from django.test import TestCase, override_settings

from judge.models import ContestViolationLog
from judge.tests.strict_helpers import StrictContestMixin
from judge.utils.contest_strict import record_violation


@override_settings(LANGUAGE_CODE="en")
class RecordViolationTest(StrictContestMixin, TestCase):
    def test_counted_action_increments_and_logs(self):
        participation = self.join()
        state = record_violation(participation, ContestViolationLog.FULLSCREEN_EXIT)

        participation.refresh_from_db()
        self.assertEqual(participation.strict_violations, 1)
        self.assertEqual(state["violations"], 1)
        self.assertFalse(state["banned"])

        entry = participation.violations.get()
        self.assertEqual(entry.action, ContestViolationLog.FULLSCREEN_EXIT)
        self.assertEqual(entry.violation_number, 1)
        self.assertEqual(entry.contest_id, self.contest.id)

    def test_uncounted_action_logs_without_incrementing(self):
        participation = self.join()
        for action in (
            ContestViolationLog.COPY,
            ContestViolationLog.CONTEXT_MENU,
            ContestViolationLog.BLOCKED_KEY,
        ):
            record_violation(participation, action)

        participation.refresh_from_db()
        self.assertEqual(participation.strict_violations, 0)
        self.assertEqual(participation.violations.count(), 3)
        self.assertEqual(
            list(participation.violations.values_list("violation_number", flat=True)),
            [0, 0, 0],
        )

    def test_reaching_the_limit_locks_without_disqualifying(self):
        participation = self.join()
        for _ in range(2):
            state = record_violation(participation, ContestViolationLog.FOCUS_LOST)
            self.assertFalse(state["locked"])

        state = record_violation(participation, ContestViolationLog.FOCUS_LOST)

        self.assertTrue(state["locked"])
        self.assertFalse(state["banned"])
        participation.refresh_from_db()
        self.participant.refresh_from_db()
        self.assertIsNotNone(participation.strict_locked_at)
        self.assertFalse(participation.is_disqualified)
        self.assertNotEqual(participation.score, -9999)
        self.assertEqual(self.participant.current_contest, participation)
        self.assertFalse(
            self.contest.banned_users.filter(id=self.participant.id).exists()
        )
        self.assertTrue(
            participation.violations.filter(
                action=ContestViolationLog.AUTO_LOCK
            ).exists()
        )

    def test_the_contestant_and_the_proctors_are_notified_of_the_lock(self):
        from judge.models.notification import Notification, NotificationCategory

        participation = self.join()
        for _unused in range(3):
            record_violation(participation, ContestViolationLog.FOCUS_LOST)

        notification = Notification.objects.filter(
            owner=self.participant,
            category=NotificationCategory.CONTEST_LOCKED,
        ).first()
        self.assertIsNotNone(notification)
        self.assertIn(self.contest.name, notification.html_link)
        self.assertTrue(
            Notification.objects.filter(
                owner=self.author,
                category=NotificationCategory.CONTEST_PROCTOR_ALERT,
            ).exists()
        )

    def test_monitor_only_mode_does_not_notify(self):
        from judge.models.notification import Notification, NotificationCategory

        self.contest.strict_autoban = False
        self.contest.save()
        participation = self.join()
        for _unused in range(5):
            record_violation(participation, ContestViolationLog.FOCUS_LOST)

        self.assertFalse(
            Notification.objects.filter(
                owner=self.participant,
                category__in=(
                    NotificationCategory.CONTEST_LOCKED,
                    NotificationCategory.CONTEST_DISQUALIFIED,
                ),
            ).exists()
        )

    def test_monitor_only_mode_records_but_never_locks(self):
        self.contest.strict_autoban = False
        self.contest.save()
        participation = self.join()

        for _ in range(5):
            state = record_violation(participation, ContestViolationLog.FOCUS_LOST)

        participation.refresh_from_db()
        self.assertEqual(participation.strict_violations, 5)
        self.assertFalse(state["locked"])
        self.assertIsNone(participation.strict_locked_at)
        self.assertFalse(participation.is_disqualified)

    def test_paste_and_cut_are_recorded_but_never_counted(self):
        # A18: with the exam default of a limit of 1, counting these would lock
        # a contestant out for one reflexive ctrl+v.
        participation = self.join()
        for action in (ContestViolationLog.PASTE, ContestViolationLog.CUT):
            record_violation(participation, action)
        participation.refresh_from_db()
        self.assertEqual(participation.strict_violations, 0)
        self.assertIsNone(participation.strict_locked_at)
        self.assertEqual(participation.violations.count(), 2)

    def test_force_ban_locks_below_the_limit(self):
        participation = self.join()
        state = record_violation(
            participation, ContestViolationLog.GRACE_EXPIRED, force_ban=True
        )

        self.assertTrue(state["locked"])
        participation.refresh_from_db()
        self.assertIsNotNone(participation.strict_locked_at)
        self.assertFalse(participation.is_disqualified)

    def test_violations_after_a_lock_do_not_re_lock(self):
        participation = self.join()
        record_violation(
            participation, ContestViolationLog.GRACE_EXPIRED, force_ban=True
        )
        before = participation.violations.filter(
            action=ContestViolationLog.AUTO_LOCK
        ).count()

        record_violation(participation, ContestViolationLog.FOCUS_LOST)

        self.assertEqual(
            participation.violations.filter(
                action=ContestViolationLog.AUTO_LOCK
            ).count(),
            before,
        )

    def test_unlocking_resets_the_session(self):
        from judge.utils.contest_strict import unlock_participation

        participation = self.join()
        for _ in range(3):
            record_violation(participation, ContestViolationLog.FOCUS_LOST)
        participation.refresh_from_db()
        self.assertIsNotNone(participation.strict_locked_at)

        unlock_participation(participation, self.author, "network trouble")

        participation.refresh_from_db()
        self.assertIsNone(participation.strict_locked_at)
        self.assertEqual(participation.strict_violations, 0)
        self.assertIsNone(participation.strict_armed_at)
        unlock = participation.violations.get(action=ContestViolationLog.ADMIN_UNLOCK)
        self.assertEqual(unlock.moderator, self.author)
        self.assertEqual(unlock.detail, "network trouble")

    def test_undisqualifying_resets_the_counter(self):
        # Disqualifying is manual now (rankings page); un-disqualifying must
        # still come back clean (PATCHES.md 16).
        participation = self.join()
        for _ in range(2):
            record_violation(participation, ContestViolationLog.FOCUS_LOST)
        participation.set_disqualified(True)
        participation.refresh_from_db()
        self.assertTrue(participation.is_disqualified)

        participation.set_disqualified(False)

        participation.refresh_from_db()
        self.assertEqual(participation.strict_violations, 0)
        self.assertIsNone(participation.strict_armed_at)
        self.assertIsNone(participation.strict_last_seen)
        self.assertFalse(
            self.contest.banned_users.filter(id=self.participant.id).exists()
        )

    def test_detail_is_truncated_and_stripped(self):
        participation = self.join()
        record_violation(
            participation,
            ContestViolationLog.NAVIGATE_AWAY,
            detail="a\x00b\x07" + "x" * 400,
        )
        entry = participation.violations.get()
        self.assertNotIn("\x00", entry.detail)
        self.assertLessEqual(len(entry.detail), ContestViolationLog.DETAIL_MAX_LENGTH)
