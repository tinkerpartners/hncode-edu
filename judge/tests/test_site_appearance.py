from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from judge.models import Language, Profile


class SiteAppearanceTests(TestCase):
    fixtures = ["language_small"]

    def setUp(self):
        user = User.objects.create_user("appearance_user")
        Profile.objects.get_or_create(
            user=user, defaults={"language": Language.objects.first()}
        )
        self.client.force_login(user)

    def test_pink_persists_across_pages_and_can_be_replaced(self):
        settings_url = reverse("theme_settings")
        response = self.client.post(
            settings_url, {"action": "update_mode", "mode": "pink"}, follow=True
        )
        self.assertContains(response, 'value="pink" aria-pressed="true"')
        response = self.client.get(reverse("home"))
        self.assertContains(response, 'data-appearance="pink"')
        self.assertContains(response, "pink-theme.css")
        self.assertContains(response, "darkmode-theme.css")

        self.client.post(settings_url, {"action": "update_mode", "mode": "light"})
        response = self.client.get(reverse("home"))
        self.assertContains(response, 'data-appearance="light"')
        self.assertNotContains(response, "pink-theme.css")
        self.assertNotContains(response, "darkmode-theme.css")

    def test_legacy_dark_session_and_quick_toggle(self):
        session = self.client.session
        session["darkmode"] = True
        session.save()
        self.assertContains(self.client.get(reverse("home")), 'data-appearance="dark"')

        self.client.post(reverse("toggle_darkmode_ajax"), {"mode": "pink"})
        response = self.client.get(
            reverse("toggle_darkmode"), {"next": reverse("home")}, follow=True
        )
        self.assertContains(response, 'data-appearance="light"')

    def test_settings_reject_an_invalid_mode(self):
        response = self.client.post(
            reverse("theme_settings"), {"action": "update_mode", "mode": "neon"}
        )
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("appearance", self.client.session)
