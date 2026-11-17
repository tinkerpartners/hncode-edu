from django.contrib.auth.models import User
from django.test import Client, TestCase
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
        self.assertNotContains(response, "darkmode-theme.css")

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

        response = self.client.post(reverse("toggle_darkmode_ajax"), {"mode": "pink"})
        self.assertEqual(response.json(), {"darkmode": False})
        response = self.client.get(
            reverse("toggle_darkmode"), {"next": reverse("home")}, follow=True
        )
        self.assertContains(response, 'data-appearance="dark"')

    def test_settings_reject_an_invalid_mode(self):
        response = self.client.post(
            reverse("theme_settings"), {"action": "update_mode", "mode": "neon"}
        )
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("appearance", self.client.session)

    def test_quick_picker_lists_all_modes_and_preserves_the_current_page(self):
        path = reverse("home") + "?tab=suggestions"
        response = self.client.get(path)
        self.assertContains(response, 'class="nav-appearance-options"')
        for mode in ("light", "dark", "pink"):
            self.assertContains(response, f'name="mode" value="{mode}"')
            response = self.client.post(
                reverse("update_appearance"), {"mode": mode, "next": path}, follow=True
            )
            self.assertRedirects(response, path)
            self.assertContains(response, f'data-appearance="{mode}"')
            self.assertContains(response, f'value="{mode}" aria-pressed="true"')

    def test_guest_can_set_an_appearance_only_with_a_valid_post(self):
        guest = Client(enforce_csrf_checks=True)
        url = reverse("update_appearance")
        self.assertEqual(guest.get(url).status_code, 405)
        self.assertEqual(guest.post(url, {"mode": "pink"}).status_code, 403)
        guest.get(reverse("home"))
        token = guest.cookies["csrftoken"].value
        response = guest.post(
            url,
            {
                "mode": "pink",
                "next": "//outside.example/path",
                "csrfmiddlewaretoken": token,
            },
            follow=True,
        )
        self.assertRedirects(response, reverse("home"))
        self.assertContains(response, 'data-appearance="pink"')

        response = guest.post(url, {"mode": "neon", "csrfmiddlewaretoken": token})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(guest.session["appearance"], "pink")
