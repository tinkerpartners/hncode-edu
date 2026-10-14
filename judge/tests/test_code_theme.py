"""A23: per-user code background presets.

The choice lives on Profile.code_theme, is saved through the Settings page
(auth + CSRF + choices validation), is rendered as <html data-code-theme>, and
pins the Ace editor to a matching theme.
"""

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from judge.forms import ProfileForm
from judge.models import Language, Profile


class CodeThemeTest(TestCase):
    fixtures = ["language_small"]

    def setUp(self):
        self.lang = Language.objects.first()
        self.profile = self._profile("codetheme_a")
        self.other = self._profile("codetheme_b")

    def _profile(self, username):
        user = User.objects.create_user(username, f"{username}@x.com", "pw")
        profile, _ = Profile.objects.get_or_create(
            user=user, defaults={"language": self.lang}
        )
        return profile

    def _post(self, client, code_theme):
        return client.post(
            reverse("user_edit_profile"),
            {
                "first_name": "",
                "last_name": "",
                "timezone": self.profile.timezone,
                "language": self.lang.pk,
                "ace_theme": "twilight",
                "code_theme": code_theme,
                "tshirt_size": "",
                "date_of_birth": "",
                "address": "",
            },
        )

    def _html_tag(self, client):
        content = client.get(reverse("home")).content.decode()
        return content[
            content.index("<html") : content.index(">", content.index("<html"))
        ]

    # --- model ---
    def test_default_is_follow_theme(self):
        self.assertEqual(self.profile.code_theme, "")

    def test_editor_theme_without_preset_is_users_ace_theme(self):
        self.profile.ace_theme = "twilight"
        self.assertEqual(self.profile.editor_ace_theme, "twilight")

    def test_editor_theme_follows_preset(self):
        self.profile.ace_theme = "twilight"
        for preset, ace in (
            ("white", "github"),
            ("gray", "github"),
            ("dark", "monokai"),
        ):
            self.profile.code_theme = preset
            self.assertEqual(self.profile.editor_ace_theme, ace)

    # --- validation ---
    def test_form_rejects_unknown_preset(self):
        form = ProfileForm(
            {
                "timezone": self.profile.timezone,
                "language": self.lang.pk,
                "ace_theme": "github",
                "code_theme": "#ff00ff",
            },
            instance=self.profile,
            profile=self.profile,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("code_theme", form.errors)

    # --- endpoint ---
    def test_save_persists_and_touches_only_own_profile(self):
        self.client.force_login(self.profile.user)
        self.assertEqual(self._post(self.client, "dark").status_code, 302)
        self.profile.refresh_from_db()
        self.other.refresh_from_db()
        self.assertEqual(self.profile.code_theme, "dark")
        self.assertEqual(self.other.code_theme, "")

    def test_invalid_preset_is_not_saved(self):
        self.client.force_login(self.profile.user)
        self.assertEqual(self._post(self.client, "neon").status_code, 200)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.code_theme, "")

    def test_reset_to_default(self):
        self.profile.code_theme = "gray"
        self.profile.save()
        self.client.force_login(self.profile.user)
        self._post(self.client, "")
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.code_theme, "")

    def test_anonymous_cannot_save(self):
        resp = self._post(self.client, "dark")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/accounts/login/", resp.url)

    def test_csrf_is_enforced(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.profile.user)
        self.assertEqual(self._post(client, "dark").status_code, 403)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.code_theme, "")

    # --- rendering ---
    def test_preset_is_rendered_on_html_and_survives_relogin(self):
        self.client.force_login(self.profile.user)
        self._post(self.client, "white")
        self.client.logout()
        self.client.force_login(self.profile.user)
        self.assertIn('data-code-theme="white"', self._html_tag(self.client))

    def test_default_and_other_users_render_no_attribute(self):
        self.profile.code_theme = "dark"
        self.profile.save()
        self.client.force_login(self.other.user)
        self.assertNotIn("data-code-theme", self._html_tag(self.client))
        self.assertNotIn("data-code-theme", self._html_tag(Client()))

    def test_settings_page_offers_the_presets(self):
        self.client.force_login(self.profile.user)
        content = self.client.get(reverse("user_edit_profile")).content.decode()
        self.assertIn('name="code_theme"', content)
        for preset in ("white", "gray", "dark"):
            self.assertIn(f'value="{preset}"', content)
