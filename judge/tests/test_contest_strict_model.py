"""Violation accounting and the auto-disqualify path.

The important guarantees: only counted actions move the counter, crossing the
limit produces the same ban the rankings-page disqualify button does, and every
unban clears the counter -- otherwise the unbanned contestant comes back already
at the limit and is banned again by their next tab switch.
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

    def test_reaching_the_limit_disqualifies_and_bans(self):
        participation = self.join()
        for _ in range(2):
            state = record_violation(participation, ContestViolationLog.FOCUS_LOST)
            self.assertFalse(state["banned"])

        state = record_violation(participation, ContestViolationLog.FOCUS_LOST)

        self.assertTrue(state["banned"])
        participation.refresh_from_db()
        self.participant.refresh_from_db()
        self.assertTrue(participation.is_disqualified)
        self.assertEqual(participation.score, -9999)
        self.assertIsNone(self.participant.current_contest)
        self.assertTrue(
            self.contest.banned_users.filter(id=self.participant.id).exists()
        )
        self.assertTrue(
            participation.violations.filter(
                action=ContestViolationLog.AUTO_BAN
            ).exists()
        )

    def test_monitor_only_mode_records_but_never_bans(self):
        self.contest.strict_autoban = False
        self.contest.save()
        participation = self.join()

        for _ in range(5):
            state = record_violation(participation, ContestViolationLog.PASTE)

        participation.refresh_from_db()
        self.assertEqual(participation.strict_violations, 5)
        self.assertFalse(state["banned"])
        self.assertFalse(participation.is_disqualified)
        self.assertFalse(
            self.contest.banned_users.filter(id=self.participant.id).exists()
        )

    def test_force_ban_disqualifies_below_the_limit(self):
        participation = self.join()
        state = record_violation(
            participation, ContestViolationLog.GRACE_EXPIRED, force_ban=True
        )

        self.assertTrue(state["banned"])
        participation.refresh_from_db()
        self.assertTrue(participation.is_disqualified)

    def test_violations_after_a_ban_do_not_re_ban(self):
        participation = self.join()
        record_violation(
            participation, ContestViolationLog.GRACE_EXPIRED, force_ban=True
        )
        before = participation.violations.filter(
            action=ContestViolationLog.AUTO_BAN
        ).count()

        record_violation(participation, ContestViolationLog.FOCUS_LOST)

        self.assertEqual(
            participation.violations.filter(
                action=ContestViolationLog.AUTO_BAN
            ).count(),
            before,
        )

    def test_undisqualifying_resets_the_counter(self):
        participation = self.join()
        for _ in range(3):
            record_violation(participation, ContestViolationLog.FOCUS_LOST)
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
