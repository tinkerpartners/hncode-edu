"""The two proctoring endpoints, treated as if the client were hostile.

The cases that matter are the ones a tampered browser would try: writing a
server-only action, replaying a report, or claiming a grace period expired that
the server never saw start.
"""

import json
from datetime import timedelta

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from judge.models import ContestParticipation, ContestViolationLog
from judge.tests.strict_helpers import StrictContestMixin


@override_settings(LANGUAGE_CODE="en")
class StrictEventEndpointTest(StrictContestMixin, TestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.url = reverse("contest_strict_event", args=(self.contest.key,))

    def post(self, action, **extra):
        payload = {"action": action, "nonce": extra.pop("nonce", "")}
        payload.update(extra)
        return self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )

    def test_anonymous_is_redirected_to_login(self):
        response = self.post(ContestViolationLog.FOCUS_LOST)
        self.assertEqual(response.status_code, 302)

    def test_get_is_rejected(self):
        self.join()
        self.login()
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_not_in_the_contest_reports_inactive(self):
        self.login()
        response = self.post(ContestViolationLog.FOCUS_LOST)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": False, "state": "inactive"})

    def test_spectator_is_not_proctored(self):
        self.join(profile=self.author, virtual=ContestParticipation.SPECTATE)
        self.login(self.author)
        self.assertFalse(self.post(ContestViolationLog.FOCUS_LOST).json()["ok"])

    def test_non_strict_contest_reports_inactive(self):
        self.contest.is_strict = False
        self.contest.save()
        self.join()
        self.login()
        self.assertFalse(self.post(ContestViolationLog.FOCUS_LOST).json()["ok"])

    def test_valid_report_counts_and_logs(self):
        participation = self.join()
        self.login()

        data = self.post(ContestViolationLog.PASTE, nonce="a1").json()

        self.assertTrue(data["ok"])
        self.assertEqual(data["violations"], 1)
        self.assertEqual(data["limit"], 3)
        self.assertEqual(data["grace_seconds"], 20)
        participation.refresh_from_db()
        self.assertEqual(participation.strict_violations, 1)

    def test_unknown_action_is_rejected(self):
        self.join()
        self.login()
        self.assertEqual(self.post("nonsense").status_code, 400)

    def test_client_cannot_write_a_server_action(self):
        # The privilege boundary: auto_ban and admin_unban are what justify or
        # reverse a disqualification, so a browser must never forge one.
        participation = self.join()
        self.login()
        for action in sorted(ContestViolationLog.SERVER_ACTIONS):
            self.assertEqual(
                self.post(action, nonce=action).status_code,
                400,
                msg="%s should be server-only" % action,
            )
        self.assertEqual(participation.violations.count(), 0)

    def test_replayed_nonce_counts_once(self):
        participation = self.join()
        self.login()

        first = self.post(ContestViolationLog.PASTE, nonce="dup").json()
        second = self.post(ContestViolationLog.PASTE, nonce="dup").json()

        self.assertEqual(first["violations"], 1)
        self.assertEqual(second["violations"], 1)
        participation.refresh_from_db()
        self.assertEqual(participation.strict_violations, 1)

    def test_one_alt_tab_costs_one_violation(self):
        # blur, visibilitychange and fullscreenchange all fire for a single
        # alt-tab. Without coalescing, a limit of 3 would be spent instantly.
        participation = self.join()
        self.login()

        self.post(ContestViolationLog.FULLSCREEN_EXIT, nonce="n1")
        self.post(ContestViolationLog.FOCUS_LOST, nonce="n2")
        self.post(ContestViolationLog.FOCUS_LOST, nonce="n3")

        participation.refresh_from_db()
        self.assertEqual(participation.strict_violations, 1)

    def test_grace_expired_without_a_corroborating_exit_does_not_ban(self):
        participation = self.join()
        self.login()

        data = self.post(ContestViolationLog.GRACE_EXPIRED, nonce="g1").json()

        self.assertFalse(data["banned"])
        participation.refresh_from_db()
        self.assertFalse(participation.is_disqualified)

    def test_grace_expired_after_a_real_exit_bans(self):
        participation = self.join()
        self.login()
        exit_row = ContestViolationLog.log_action(
            participation, ContestViolationLog.FULLSCREEN_EXIT
        )
        # auto_now_add ignores an explicit value on create, so back-date the row
        # to make it look like a genuinely expired grace period.
        ContestViolationLog.objects.filter(pk=exit_row.pk).update(
            created=timezone.now() - timedelta(seconds=60)
        )

        data = self.post(ContestViolationLog.GRACE_EXPIRED, nonce="g2").json()

        self.assertTrue(data["banned"])
        self.assertIn("redirect", data)
        participation.refresh_from_db()
        self.assertTrue(participation.is_disqualified)

    def test_session_start_arms_without_counting(self):
        participation = self.join(armed=False)
        self.login()

        data = self.post(ContestViolationLog.SESSION_START).json()

        self.assertTrue(data["armed"])
        self.assertEqual(data["violations"], 0)
        participation.refresh_from_db()
        self.assertIsNotNone(participation.strict_armed_at)
        self.assertIsNotNone(participation.strict_last_seen)

    def test_malformed_json_is_rejected(self):
        self.join()
        self.login()
        response = self.client.post(
            self.url, data="{not json", content_type="application/json"
        )
        self.assertEqual(response.status_code, 400)


@override_settings(LANGUAGE_CODE="en")
class StrictHeartbeatTest(StrictContestMixin, TestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.url = reverse("contest_strict_heartbeat", args=(self.contest.key,))

    def beat(self, **extra):
        return self.client.post(
            self.url,
            data=json.dumps(dict({"fullscreen": True}, **extra)),
            content_type="application/json",
        )

    def test_heartbeat_updates_last_seen(self):
        participation = self.join()
        ContestParticipation.objects.filter(pk=participation.pk).update(
            strict_last_seen=timezone.now() - timedelta(minutes=5)
        )
        self.login()

        response = self.beat()

        self.assertTrue(response.json()["ok"])
        participation.refresh_from_db()
        self.assertLess(
            timezone.now() - participation.strict_last_seen, timedelta(seconds=30)
        )

    def test_a_long_silence_is_recorded(self):
        participation = self.join()
        ContestParticipation.objects.filter(pk=participation.pk).update(
            strict_last_seen=timezone.now() - timedelta(minutes=10)
        )
        self.login()

        self.beat()

        self.assertTrue(
            participation.violations.filter(
                action=ContestViolationLog.STRICT_GAP
            ).exists()
        )
        participation.refresh_from_db()
        # Recorded for a human to read, never counted: flaky wifi produces it.
        self.assertEqual(participation.strict_violations, 0)

    def test_a_changed_address_is_recorded(self):
        participation = self.join()
        ContestViolationLog.log_action(
            participation, ContestViolationLog.SESSION_START, ip="10.0.0.1"
        )
        self.login()

        self.beat()

        self.assertTrue(
            participation.violations.filter(
                action=ContestViolationLog.IP_CHANGE
            ).exists()
        )
        participation.refresh_from_db()
        self.assertEqual(participation.strict_violations, 0)

    def test_a_beat_outside_fullscreen_does_not_keep_the_session_alive(self):
        # Otherwise leaving fullscreen and reloading would dodge the grace
        # countdown while still preserving the right to submit.
        participation = self.join()
        stale = timezone.now() - timedelta(minutes=5)
        ContestParticipation.objects.filter(pk=participation.pk).update(
            strict_last_seen=stale
        )
        self.login()

        self.beat(fullscreen=False)

        participation.refresh_from_db()
        self.assertEqual(participation.strict_last_seen.replace(microsecond=0), stale.replace(microsecond=0))

    def test_a_form_encoded_false_does_not_read_as_fullscreen(self):
        participation = self.join()
        stale = timezone.now() - timedelta(minutes=5)
        ContestParticipation.objects.filter(pk=participation.pk).update(
            strict_last_seen=stale
        )
        self.login()

        self.client.post(self.url, {"fullscreen": "false"})

        participation.refresh_from_db()
        self.assertEqual(
            participation.strict_last_seen.replace(microsecond=0),
            stale.replace(microsecond=0),
        )

    def test_not_in_the_contest_reports_inactive(self):
        self.login()
        self.assertEqual(self.beat().json(), {"ok": False, "state": "inactive"})
