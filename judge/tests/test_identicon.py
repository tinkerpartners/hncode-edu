"""Locally generated avatars.

Avatars were the last thing a normal page fetched from a third-party host
(gravatar.com), which matters because a strict contest runs on a network that
allows nothing but this site.
"""

import re

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from judge.models import Language, Profile
from judge.views.avatar import GRID, identicon_svg

DIGEST = "b460ba94b4ba7a8ce1822cbaf51b961c"
OTHER = "d41d8cd98f00b204e9800998ecf8427e"


class IdenticonViewTest(TestCase):
    def _url(self, digest=DIGEST):
        return reverse("identicon", args=(digest,))

    def test_serves_an_svg(self):
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/svg+xml")
        self.assertTrue(response.content.decode().startswith("<svg"))

    def test_is_cached_immutably(self):
        """Same digest, same picture forever -- so it never needs revalidating."""
        cache = self.client.get(self._url())["Cache-Control"]
        self.assertIn("public", cache)
        self.assertIn("immutable", cache)
        self.assertIn("max-age=31536000", cache)

    def test_same_digest_gives_the_same_image(self):
        self.assertEqual(identicon_svg(DIGEST), identicon_svg(DIGEST))

    def test_different_digests_give_different_images(self):
        self.assertNotEqual(identicon_svg(DIGEST), identicon_svg(OTHER))

    def test_the_pattern_is_mirrored(self):
        """Left and right halves must match, or it reads as noise."""
        svg = identicon_svg(DIGEST)
        # The background rect carries no x/y, so only cells match here.
        rects = re.findall(r'<rect x="(\d+)" y="(\d+)" width="16"', svg)
        cells = {(int(x), int(y)) for x, y in rects}
        self.assertTrue(cells, "identicon has no filled cells")

        xs = sorted({x for x, _ in cells})
        step = xs[1] - xs[0] if len(xs) > 1 else 16
        left = min(xs)
        for x, y in cells:
            column = (x - left) // step
            mirrored = left + (GRID - 1 - column) * step
            self.assertIn((mirrored, y), cells, f"cell at {x},{y} is not mirrored")

    def test_a_bad_digest_is_not_served(self):
        self.assertEqual(self.client.get("/avatar/nothex.svg").status_code, 404)
        self.assertEqual(self.client.get("/avatar/abc.svg").status_code, 404)


class GravatarFunctionTest(TestCase):
    def setUp(self):
        language, _ = Language.objects.get_or_create(
            key="PY3",
            defaults={
                "name": "Python 3",
                "short_name": "PY3",
                "common_name": "Python",
                "ace": "python",
                "pygments": "python3",
                "template": "",
            },
        )
        user = User.objects.create_user("iconuser", "icon@example.com", "pw")
        self.profile, _ = Profile.objects.get_or_create(
            user=user, defaults={"language": language}
        )

    def test_points_at_the_local_identicon_by_default(self):
        from judge.jinja2.gravatar import gravatar

        url = gravatar(self.profile.id)
        self.assertTrue(url.startswith("/avatar/"), url)
        self.assertNotIn("gravatar.com", url)
        self.assertEqual(self.client.get(url).status_code, 200)

    @override_settings(USE_GRAVATAR=True)
    def test_the_setting_restores_gravatar(self):
        from judge.jinja2.gravatar import gravatar

        self.assertIn("gravatar.com", gravatar(self.profile.id))

    def test_no_rendered_page_still_points_at_gravatar(self):
        html = self.client.get(reverse("home")).content.decode()
        self.assertNotIn("gravatar.com", html)
