"""Joining a strict contest goes through an explicit consent step.

An auto-disqualification the contestant was never warned about is not
defensible, so no participation may be created until they have seen the rules.
"""

from django.test import TestCase, override_settings
from django.urls import reverse

from judge.models import ContestParticipation
from judge.tests.strict_helpers import StrictContestMixin


@override_settings(LANGUAGE_CODE="en")
class StrictConsentTest(StrictContestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.url = reverse("contest_join", args=(self.contest.key,))

    def test_joining_without_acknowledgement_shows_the_rules_and_joins_nobody(self):
        self.login()

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "contest/strict_consent.html")
        self.assertFalse(
            ContestParticipation.objects.filter(
                contest=self.contest, user=self.participant
            ).exists()
        )

    def test_acknowledging_joins(self):
        self.login()

        response = self.client.post(self.url, {"strict_ack": "1"})

        self.assertEqual(response.status_code, 302)
        participation = ContestParticipation.objects.get(
            contest=self.contest, user=self.participant
        )
        self.assertEqual(participation.virtual, ContestParticipation.LIVE)
        # Consent is not arming: fullscreen needs a click on the next page.
        self.assertIsNone(participation.strict_armed_at)

    def test_non_strict_contest_skips_the_consent_step(self):
        self.contest.is_strict = False
        self.contest.save()
        self.login()

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            ContestParticipation.objects.filter(
                contest=self.contest, user=self.participant
            ).exists()
        )

    def test_editors_are_not_asked_to_consent(self):
        self.login(self.author)

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, 302)
        participation = ContestParticipation.objects.get(
            contest=self.contest, user=self.author
        )
        self.assertEqual(participation.virtual, ContestParticipation.SPECTATE)

    def test_consent_composes_with_an_access_code(self):
        self.contest.access_code = "sesame"
        self.contest.save()
        self.login()

        # Right code, no acknowledgement yet -> rules, still nobody joined.
        response = self.client.post(self.url, {"access_code": "sesame"})
        self.assertTemplateUsed(response, "contest/strict_consent.html")
        self.assertFalse(
            ContestParticipation.objects.filter(
                contest=self.contest, user=self.participant
            ).exists()
        )

        # The consent form carries the code forward, so the second post joins.
        response = self.client.post(
            self.url, {"access_code": "sesame", "strict_ack": "1"}
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            ContestParticipation.objects.filter(
                contest=self.contest, user=self.participant
            ).exists()
        )

    def test_a_wrong_access_code_still_cannot_join_after_consenting(self):
        self.contest.access_code = "sesame"
        self.contest.save()
        self.login()

        self.client.post(self.url, {"access_code": "wrong", "strict_ack": "1"})

        self.assertFalse(
            ContestParticipation.objects.filter(
                contest=self.contest, user=self.participant
            ).exists()
        )
