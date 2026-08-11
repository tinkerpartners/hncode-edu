"""The Violations tab, its unban button, and the locked IDE page."""

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from judge.models import ContestViolationLog, Profile
from judge.tests.strict_helpers import StrictContestMixin
from judge.utils.contest_strict import record_violation


@override_settings(LANGUAGE_CODE="en")
class ViolationTabTest(StrictContestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.url = reverse("contest_strict_violations", args=(self.contest.key,))
        self.unban_url = reverse("contest_strict_unban", args=(self.contest.key,))

    def test_a_contestant_cannot_read_the_log(self):
        self.join()
        self.login()
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_the_author_can_read_the_log(self):
        participation = self.join()
        record_violation(participation, ContestViolationLog.PASTE)
        self.login(self.author)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Paste blocked")
        self.assertContains(response, self.participant.username)

    def test_a_superuser_can_read_the_log(self):
        admin = User.objects.create_superuser("strictadmin", "sa@x.invalid", "pw")
        Profile.objects.get_or_create(user=admin, defaults={"language": self.language})
        self.client.force_login(admin)
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_the_tab_only_appears_for_strict_contests(self):
        self.login(self.author)
        detail = reverse("contest_view", args=(self.contest.key,))

        self.assertContains(self.client.get(detail), self.url)

        self.contest.is_strict = False
        self.contest.save()
        self.assertNotContains(self.client.get(detail), self.url)

    def test_filtering_by_user(self):
        other = self.make_profile("strictother")
        record_violation(self.join(), ContestViolationLog.PASTE)
        record_violation(self.join(profile=other), ContestViolationLog.CUT)
        self.login(self.author)

        response = self.client.get(self.url, {"user": other.username})

        self.assertEqual(len(response.context["object_list"]), 1)

    def test_unban_clears_the_ban_and_the_counter(self):
        participation = self.join()
        for _ in range(3):
            record_violation(participation, ContestViolationLog.FOCUS_LOST)
        participation.refresh_from_db()
        self.assertTrue(participation.is_disqualified)
        self.login(self.author)

        response = self.client.post(
            self.unban_url, {"participation": participation.id}
        )

        self.assertEqual(response.status_code, 302)
        participation.refresh_from_db()
        self.assertFalse(participation.is_disqualified)
        self.assertEqual(participation.strict_violations, 0)
        self.assertFalse(
            self.contest.banned_users.filter(id=self.participant.id).exists()
        )
        self.assertTrue(
            participation.violations.filter(
                action=ContestViolationLog.ADMIN_UNBAN, is_automated=False
            ).exists()
        )

    def test_a_contestant_cannot_unban_themselves(self):
        participation = self.join()
        for _ in range(3):
            record_violation(participation, ContestViolationLog.FOCUS_LOST)
        self.client.force_login(self.participant.user)

        response = self.client.post(
            self.unban_url, {"participation": participation.id}
        )

        self.assertEqual(response.status_code, 404)
        participation.refresh_from_db()
        self.assertTrue(participation.is_disqualified)

    def test_resetting_an_undisqualified_participant_clears_the_counter(self):
        participation = self.join()
        record_violation(participation, ContestViolationLog.PASTE)
        self.login(self.author)

        self.client.post(self.unban_url, {"participation": participation.id})

        participation.refresh_from_db()
        self.assertEqual(participation.strict_violations, 0)


@override_settings(LANGUAGE_CODE="en")
class ContestIDETest(StrictContestMixin, TestCase):
    def ide_url(self, problem=None):
        return reverse(
            "contest_ide", args=(self.contest.key, (problem or self.problem).code)
        )

    def test_the_ide_renders_statement_editor_and_sidebar(self):
        self.join()
        self.login()

        response = self.client.get(self.ide_url())
        body = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn('id="problem_submit"', body)
        self.assertIn("ace_source", body)
        self.assertIn("Strict Problem", body)
        self.assertIn("ide-shell", body)

    def test_the_problem_page_redirects_into_the_ide(self):
        self.join()
        self.login()

        response = self.client.get(
            reverse("contest_problem_detail", args=(self.contest.key, self.problem.code))
        )

        self.assertRedirects(response, self.ide_url(), fetch_redirect_response=False)

    def test_the_standalone_submit_page_redirects_into_the_ide(self):
        self.join()
        self.login()

        response = self.client.get(
            reverse("problem_submit", args=(self.problem.code,))
        )

        self.assertRedirects(response, self.ide_url(), fetch_redirect_response=False)

    def test_the_ide_does_not_redirect_to_itself(self):
        self.join()
        self.login()
        self.assertEqual(self.client.get(self.ide_url()).status_code, 200)

    def test_a_problem_outside_the_contest_is_not_redirected(self):
        self.join()
        self.login()

        response = self.client.get(
            reverse("problem_detail", args=(self.outside_problem.code,))
        )

        self.assertEqual(response.status_code, 200)

    def test_no_redirect_when_the_contest_is_not_strict(self):
        self.contest.is_strict = False
        self.contest.save()
        self.join()
        self.login()

        response = self.client.get(
            reverse("contest_problem_detail", args=(self.contest.key, self.problem.code))
        )

        self.assertEqual(response.status_code, 200)

    def test_a_non_strict_contest_has_no_ide(self):
        self.contest.is_strict = False
        self.contest.save()
        self.join()
        self.login()
        self.assertEqual(self.client.get(self.ide_url()).status_code, 404)

    def test_another_users_submission_is_not_shown(self):
        from judge.models import Submission

        other = self.make_profile("strictsomeoneelse")
        submission = Submission.objects.create(
            user=other, problem=self.problem, language=self.language
        )
        self.join()
        self.login()

        response = self.client.get(
            self.ide_url(), {"submission": submission.id}
        )

        self.assertEqual(response.status_code, 404)
