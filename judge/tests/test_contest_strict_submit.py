"""The submission liveness gate.

This is the only part of strict mode a hostile client cannot route around: with
no armed, still-beating proctored session there is no submission, whatever the
browser does or does not report.
"""

from datetime import timedelta

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from judge.models import ContestParticipation, Submission
from judge.tests.strict_helpers import StrictContestMixin


@override_settings(LANGUAGE_CODE="en")
class StrictSubmitGateTest(StrictContestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.url = reverse("problem_submit", args=(self.problem.code,))

    def submit(self):
        return self.client.post(
            self.url,
            {
                "source": "print(1)",
                "language": self.language.id,
                "judge": "",
            },
        )

    def assert_no_submission(self):
        self.assertFalse(
            Submission.objects.filter(
                user=self.participant, problem=self.problem
            ).exists()
        )

    def test_never_armed_is_refused(self):
        self.join(armed=False)
        self.login()

        response = self.submit()

        self.assertEqual(response.status_code, 403)
        self.assert_no_submission()

    def test_stale_session_is_refused(self):
        participation = self.join()
        ContestParticipation.objects.filter(pk=participation.pk).update(
            strict_last_seen=timezone.now() - timedelta(minutes=5)
        )
        self.login()

        response = self.submit()

        self.assertEqual(response.status_code, 403)
        self.assert_no_submission()

    def test_disqualified_is_refused(self):
        participation = self.join()
        participation.is_disqualified = True
        participation.save(update_fields=["is_disqualified"])
        self.login()

        self.assertEqual(self.submit().status_code, 403)
        self.assert_no_submission()

    def test_a_live_session_may_submit(self):
        self.join()
        self.login()

        response = self.submit()

        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            Submission.objects.filter(
                user=self.participant, problem=self.problem
            ).exists()
        )

    def test_a_non_strict_contest_is_unaffected(self):
        self.contest.is_strict = False
        self.contest.save()
        self.join(armed=False)
        self.login()

        self.assertEqual(self.submit().status_code, 302)
        self.assertTrue(
            Submission.objects.filter(
                user=self.participant, problem=self.problem
            ).exists()
        )

    def test_submitting_from_the_ide_returns_to_the_ide(self):
        self.join()
        self.login()

        response = self.client.post(
            "%s?ide=%s" % (self.url, self.contest.key),
            {"source": "print(1)", "language": self.language.id, "judge": ""},
        )

        self.assertEqual(response.status_code, 302)
        submission = Submission.objects.get(user=self.participant, problem=self.problem)
        self.assertEqual(
            response["Location"],
            "%s?submission=%d"
            % (
                reverse("contest_ide", args=(self.contest.key, self.problem.code)),
                submission.id,
            ),
        )
