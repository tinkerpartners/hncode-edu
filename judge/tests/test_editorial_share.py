"""Secret, expiring links to a private editorial."""

from datetime import timedelta

from django.contrib.auth.models import Permission, User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from judge.models import Profile, Solution, SolutionShareLink
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
        return SolutionShareLink.objects.latest("pk")

    def test_admin_creates_links_with_the_chosen_length(self):
        for key, days in (("7d", 7), ("30d", 30), ("1y", 365)):
            link = self.create(key)
            self.assertAlmostEqual(
                link.expires_at - link.created_at,
                timedelta(days=days),
                delta=timedelta(seconds=5),
            )
        page = self.client.get(self.editorial)
        self.assertContains(page, 'id="editorial-share"')
        self.assertContains(page, link.get_absolute_url())

    def test_anyone_with_the_link_reads_it_without_an_account(self):
        link = self.create()
        self.client.logout()
        self.assertEqual(self.client.get(self.editorial).status_code, 404)
        response = self.client.get(link.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "<strong>secret</strong>", html=True)
        self.assertContains(response, 'class="hn-print-logo"')
        self.assertEqual(response["X-Robots-Tag"], "noindex, nofollow")

    def test_expired_revoked_and_unknown_links_show_nothing(self):
        link = self.create()
        url = link.get_absolute_url()
        SolutionShareLink.objects.filter(pk=link.pk).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
        self.client.logout()
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)
        self.assertNotContains(response, "secret", status_code=404)
        self.assertEqual(self.client.get("/editorial/s/nope").status_code, 404)

        link = self.create()
        self.client.post(
            reverse("problem_editorial_share_revoke", args=[self.problem.code, link.pk])
        )
        self.assertFalse(SolutionShareLink.objects.filter(pk=link.pk).exists())
        self.client.logout()
        self.assertEqual(self.client.get(link.get_absolute_url()).status_code, 404)

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
