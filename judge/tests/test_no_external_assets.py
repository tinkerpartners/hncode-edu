"""KaTeX and the web fonts must come off our own origin.

These used to be linked from cdn.jsdelivr.net and fonts.googleapis.com. The
point of vendoring them was that no page should reach a third-party CDN for
them any more, which is easy to undo by accident in a template — hence a test
rather than a comment.
"""

import os
import re

from django.contrib.staticfiles import finders
from django.test import TestCase
from django.urls import reverse

BANNED = (
    "fonts.googleapis.com",
    "fonts.gstatic.com",
    "cdn.jsdelivr.net/npm/katex",
)

FONT_CSS = "libs/fonts/fonts.css"
KATEX_CSS = "libs/katex/katex.min.css"
KATEX_ASSETS = (
    KATEX_CSS,
    "libs/katex/katex.min.js",
    "libs/katex/contrib/auto-render.min.js",
)


class SelfHostedAssetsTest(TestCase):
    def test_home_page_links_no_cdn_for_katex_or_fonts(self):
        html = self.client.get(reverse("home")).content.decode()
        for host in BANNED:
            self.assertNotIn(host, html, f"{host} is linked from the home page")
        self.assertIn("libs/fonts/fonts.css", html)
        self.assertIn("libs/katex/katex.min.js", html)

    def test_every_referenced_asset_is_actually_collected(self):
        """A `static()` call for a missing file renders a URL that 404s."""
        for path in KATEX_ASSETS + (FONT_CSS,):
            self.assertIsNotNone(
                finders.find(path), f"{path} is not in any static files dir"
            )

    def test_font_stylesheet_is_self_contained(self):
        """Every src in fonts.css must resolve to a file next to it."""
        path = finders.find(FONT_CSS)
        css = open(path, encoding="utf-8").read()
        directory = os.path.dirname(path)

        urls = re.findall(r"url\(([^)]+)\)", css)
        self.assertTrue(urls, "fonts.css declares no font files")
        for url in urls:
            url = url.strip("'\"")
            self.assertFalse(
                url.startswith(("http://", "https://", "//")),
                f"fonts.css still points off-origin: {url}",
            )
            self.assertTrue(
                os.path.exists(os.path.join(directory, url)),
                f"fonts.css references a missing file: {url}",
            )

    def test_katex_stylesheet_finds_its_fonts(self):
        """katex.min.css uses relative font paths, so fonts/ must sit beside it."""
        path = finders.find(KATEX_CSS)
        css = open(path, encoding="utf-8").read()
        directory = os.path.dirname(path)

        urls = {u.strip("'\"").split("?")[0] for u in re.findall(r"url\(([^)]+)\)", css)}
        self.assertTrue(urls, "katex.min.css declares no font files")
        for url in urls:
            self.assertFalse(
                url.startswith(("http://", "https://", "//")),
                f"katex.min.css still points off-origin: {url}",
            )
            self.assertTrue(
                os.path.exists(os.path.join(directory, url)),
                f"katex.min.css references a missing file: {url}",
            )
