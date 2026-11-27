"""Secret, expiring links to a private editorial."""

from datetime import timedelta

from django.contrib.auth.models import Permission, User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from judge.models import (
    ContestParticipation,
    ContestProblem,
    Profile,
    Solution,
    SolutionShareLink,
)
from judge.models.solution_share import MAX_ACTIVE_LINKS
from judge.tests.strict_helpers import StrictContestMixin


@override_settings(LANGUAGE_CODE="en")
class EditorialShareTest(StrictContestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.problem = self.outside_problem
        self.problem.is_public = False
        self.problem.save()
        self.solution = Solution.objects.create(
            problem=self.problem,
            is_public=False,
            publish_on=timezone.now(),
            content="The **secret** idea.",
        )
        self.admin = User.objects.create_superuser("shareadmin", "s@x.invalid", "pw")
        Profile.objects.get_or_create(
            user=self.admin, defaults={"language": self.language}
        )
        self.editorial = reverse("problem_editorial", args=[self.problem.code])
        self.create_url = reverse("problem_editorial_share", args=[self.problem.code])

    def create(self, duration="7d"):
        self.client.force_login(self.admin)
        response = self.client.post(self.create_url, {"duration": duration})
        self.assertRedirects(
            response, self.editorial + "#editorial-share", fetch_redirect_response=False
        )
        link = SolutionShareLink.objects.latest("pk")
        return link, self.client.session["editorial_share_new"]["url"]

    def test_admin_creates_links_with_the_chosen_length(self):
        for key, days in (("7d", 7), ("30d", 30), ("1y", 365)):
            link, url = self.create(key)
            self.assertAlmostEqual(
                link.expires_at - link.created_at,
                timedelta(days=days),
                delta=timedelta(seconds=5),
            )
        page = self.client.get(self.editorial)
        self.assertContains(page, 'id="editorial-share"')
        self.assertContains(page, url)

    def test_anyone_with_the_link_reads_it_without_an_account(self):
        _link, url = self.create()
        self.client.logout()
        self.assertEqual(self.client.get(self.editorial).status_code, 404)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "<strong>secret</strong>", html=True)
        self.assertContains(response, 'class="hn-print-logo"')
        self.assertEqual(response["X-Robots-Tag"], "noindex, nofollow")

    def test_expired_revoked_and_unknown_links_show_nothing(self):
        link, url = self.create()
        SolutionShareLink.objects.filter(pk=link.pk).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
        self.client.logout()
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)
        self.assertNotContains(response, "secret", status_code=404)
        self.assertEqual(self.client.get("/editorial/s/nope").status_code, 404)

        link, url = self.create()
        self.client.post(
            reverse("problem_editorial_share_revoke", args=[self.problem.code, link.pk])
        )
        self.assertFalse(SolutionShareLink.objects.filter(pk=link.pk).exists())
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_only_editors_who_can_read_it_make_links(self):
        self.login()
        response = self.client.post(self.create_url, {"duration": "7d"})
        self.assertEqual(response.status_code, 404)
        # A problem author also needs to be able to read private editorials.
        self.problem.authors.add(self.participant)
        self.assertEqual(
            self.client.post(self.create_url, {"duration": "7d"}).status_code, 404
        )
        self.participant.user.user_permissions.add(
            Permission.objects.get(codename="see_private_solution")
        )
        self.assertEqual(
            self.client.post(self.create_url, {"duration": "7d"}).status_code, 302
        )
        self.assertEqual(SolutionShareLink.objects.count(), 1)

    def test_unknown_length_is_refused(self):
        self.client.force_login(self.admin)
        response = self.client.post(self.create_url, {"duration": "forever"})
        self.assertEqual(response.status_code, 404)
        self.assertFalse(SolutionShareLink.objects.exists())

    def test_ordinary_visitors_see_no_share_panel(self):
        self.solution.is_public = True
        self.solution.save()
        self.problem.is_public = True
        self.problem.save()
        self.login()
        self.assertNotContains(self.client.get(self.editorial), 'id="editorial-share"')

    def test_only_a_hash_of_the_link_is_stored_and_it_is_shown_once(self):
        link, url = self.create()
        token = url.rsplit("/", 1)[1]
        self.assertNotIn(token, link.token_hash)
        self.assertEqual(len(link.token_hash), 64)
        self.assertTrue(token.startswith(link.token_hint))
        first = self.client.get(self.editorial)
        self.assertContains(first, url)
        again = self.client.get(self.editorial)
        self.assertNotContains(again, url)
        self.assertContains(again, link.token_hint)

    def test_links_do_not_open_while_the_problem_is_in_a_running_contest(self):
        _link, url = self.create()
        ContestProblem.objects.create(
            contest=self.contest, problem=self.problem, points=100, order=1
        )
        self.assertContains(self.client.get(self.editorial), "editorial-share-warning")
        self.client.logout()
        response = self.client.get(url)
        self.assertEqual(response.status_code, 403)
        self.assertNotContains(response, "secret", status_code=403)

        now = timezone.now()
        self.contest.start_time = now - timedelta(hours=3)
        self.contest.end_time = now - timedelta(hours=1)
        self.contest.save()
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_links_do_not_open_during_the_viewers_own_attempt(self):
        _link, url = self.create()
        ContestProblem.objects.create(
            contest=self.contest, problem=self.problem, points=100, order=1
        )
        now = timezone.now()
        self.contest.start_time = now - timedelta(hours=3)
        self.contest.end_time = now - timedelta(hours=1)
        self.contest.save()
        participation = ContestParticipation.objects.create(
            contest=self.contest, user=self.participant, virtual=1, real_start=now
        )
        self.participant.current_contest = participation
        self.participant.save()
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 200)
        self.login()
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_links_stop_when_their_maker_loses_access(self):
        _link, url = self.create()
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 200)
        self.admin.is_superuser = False
        self.admin.save()
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_revoke_all(self):
        self.create()
        _link, url = self.create("30d")
        self.client.post(
            reverse("problem_editorial_share_revoke_all", args=[self.problem.code])
        )
        self.assertFalse(SolutionShareLink.objects.exists())
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_expired_links_are_purged_and_live_links_are_capped(self):
        old, _url = self.create()
        SolutionShareLink.objects.filter(pk=old.pk).update(
            expires_at=timezone.now() - timedelta(days=1)
        )
        for _ in range(MAX_ACTIVE_LINKS):
            self.create()
        self.assertFalse(SolutionShareLink.objects.filter(pk=old.pk).exists())
        self.assertEqual(SolutionShareLink.objects.count(), MAX_ACTIVE_LINKS)
        self.client.post(self.create_url, {"duration": "7d"})
        self.assertEqual(SolutionShareLink.objects.count(), MAX_ACTIVE_LINKS)
