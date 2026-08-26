"""KaTeX, Ace, Socket.IO and the web fonts must come off our own origin.

These were linked from cdn.jsdelivr.net, cdnjs.cloudflare.com, cdn.socket.io
and fonts.googleapis.com. They are vendored so that a browser on a network that
only allows this site can still load every page — which is easy to undo by
accident in a template, hence a test rather than a comment.
"""

import os
import re

from django.conf import settings
from django.contrib.staticfiles import finders
from django.test import TestCase
from django.urls import reverse

BANNED = (
    "fonts.googleapis.com",
    "fonts.gstatic.com",
    "cdn.jsdelivr.net/npm/katex",
    "cdn.socket.io",
    "cdnjs.cloudflare.com/ajax/libs/ace",
)

FONT_CSS = "libs/fonts/fonts.css"
KATEX_CSS = "libs/katex/katex.min.css"
KATEX_ASSETS = (
    KATEX_CSS,
    "libs/katex/katex.min.js",
    "libs/katex/contrib/auto-render.min.js",
)
SOCKETIO_JS = "libs/socketio/socket.io.min.js"

# Modes Ace loads lazily by name at runtime; a missing one only shows up when
# someone opens the editor for that language.
# Union of both production sites' Language.ace values at vendoring time.
PRODUCTION_ACE_MODES = (
    "assembly_x86", "c_cpp", "cobol", "csharp", "d", "groovy", "haskell",
    "java", "javascript", "kotlin", "lua", "objectivec", "ocaml", "pascal",
    "perl", "php", "plain_text", "prolog", "python", "ruby", "rust", "scala",
    "swift", "text",
)

# Ace has never shipped a mode for these, in any case spelling, so the editor
# already fell back to no highlighting when it was loaded from the CDN.
# Not a regression from self-hosting; see this test's PR.
MODES_ACE_DOES_NOT_SHIP = frozenset({"AWK"})

ACE_ASSETS = (
    "libs/ace/ace.js",
    "libs/ace/mode-c_cpp.js",
    "libs/ace/mode-python.js",
    "libs/ace/mode-java.js",
    "libs/ace/mode-pascal.js",
    "libs/ace/theme-github.js",
    "libs/ace/theme-monokai.js",
    "libs/ace/ext-language_tools.js",
)


class SelfHostedAssetsTest(TestCase):
    def _html(self, url_name):
        response = self.client.get(reverse(url_name))
        self.assertEqual(response.status_code, 200, url_name)
        return response.content.decode()

    def test_no_page_links_a_banned_cdn(self):
        for url_name in ("home", "all_submissions"):
            html = self._html(url_name)
            for host in BANNED:
                self.assertNotIn(host, html, f"{host} is linked from {url_name}")

    def test_base_template_links_the_local_fonts_and_katex(self):
        html = self._html("home")
        self.assertIn("libs/fonts/fonts.css", html)
        self.assertIn("libs/katex/katex.min.js", html)

    def test_socketio_is_linked_locally_where_it_is_used(self):
        """event-load.html is per-page, not in base -- the submission list has it."""
        self.assertIn("libs/socketio/socket.io.min.js", self._html("all_submissions"))

    def test_every_referenced_asset_is_actually_collected(self):
        """A `static()` call for a missing file renders a URL that 404s."""
        for path in KATEX_ASSETS + ACE_ASSETS + (FONT_CSS, SOCKETIO_JS):
            self.assertIsNotNone(
                finders.find(path), f"{path} is not in any static files dir"
            )

    def test_ace_url_is_local_and_keeps_its_trailing_slash(self):
        """FileEditWidget does urljoin(ACE_URL, "ace.js"); no slash, no version."""
        from urllib.parse import urljoin

        self.assertTrue(
            settings.ACE_URL.startswith("/"),
            f"ACE_URL is not same-origin: {settings.ACE_URL}",
        )
        self.assertTrue(
            settings.ACE_URL.endswith("/"),
            f"ACE_URL must end in a slash: {settings.ACE_URL}",
        )
        resolved = urljoin(settings.ACE_URL, "ace.js")
        self.assertEqual(resolved, settings.ACE_URL + "ace.js")
        self.assertIsNotNone(
            finders.find(resolved[len(settings.STATIC_URL):]),
            f"{resolved} does not resolve to a collected file",
        )

    def test_a_mode_file_ships_for_every_language_in_production(self):
        """Ace resolves mode-<Language.ace>.js at runtime, so each must exist.

        Listed literally rather than read from Language: the test database is
        empty, so a query would assert nothing. This is the union of both
        production sites' configured modes as of vendoring.
        """
        for mode in PRODUCTION_ACE_MODES:
            self.assertIsNotNone(
                finders.find(f"libs/ace/mode-{mode}.js"),
                f"no Ace mode file for language mode {mode!r}",
            )

    def test_any_language_added_since_still_has_its_mode(self):
        """Catches a language configured with a mode we do not ship."""
        from judge.models import Language

        missing = sorted(
            mode
            for mode in set(Language.objects.values_list("ace", flat=True))
            if mode
            and mode not in MODES_ACE_DOES_NOT_SHIP
            and finders.find(f"libs/ace/mode-{mode}.js") is None
        )
        self.assertEqual(missing, [], f"no Ace mode file for: {missing}")

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
