"""No template may quietly start loading an asset from another host.

Strict contests run on a network that allows this site and nothing else, so an
off-origin `<script>`, `<link>` or `url()` is not a slow page — it is a missing
editor, a missing lightbox or a missing font, discovered by a student mid-exam.

Vendoring the known ones fixes today. This scans the source tree so tomorrow's
addition has to be a deliberate entry in ALLOWED rather than a silent
regression. It reads files rather than rendering pages on purpose: a page only
shows what its own view reaches, and the audit that missed
`fonts.cdnfonts.com` on the user rankings page missed it exactly that way.
"""

import os
import re

from django.conf import settings
from django.test import TestCase

# src=, href=, url(), and dynamic `script.src =` assignments, absolute only.
# Template-tag output is excluded via {} so `{{ static(...) }}` never matches.
REFERENCE = re.compile(
    r"""(?:src|href)\s*=\s*["']((?:https?:)?//[^"'{}]+)"""
    r"""|url\(\s*["']?((?:https?:)?//[^)"'{}]+)"""
    r"""|\.src\s*=\s*["']((?:https?:)?//[^"'{}]+)""",
)

SUFFIXES = (".html", ".js", ".css", ".scss")

# Vendored third-party bundles. Their internals are not ours to police, and
# anything they reference at runtime is their own business.
SKIP_DIRS = (
    "resources/libs/pdfjs",
    "resources/libs/ace",
    "resources/libs/katex",
    "resources/libs/select2",
    "resources/libs/chart.js",
    "resources/libs/timezone-map",
    "resources/icofont",
    "resources/datetime-picker",
    "resources/martor",
    "resources/pagedown",
)

# Hosts a file is still allowed to name, and why. Every entry here is a plain
# link in prose -- nothing in this list is an asset the browser fetches. No
# script, stylesheet, font or image may be served from another host: strict
# contests run on a network that allows this site and nothing else.
ALLOWED = {
    "dmoj.ca": {"templates/about/about.html"},
    "github.com": {"templates/about/about.html"},
    "www.facebook.com": {"templates/about/about.html"},
    "thpt-lequydon-danang.edu.vn": {"templates/about/about.html"},
}

# Files whose references must be same-origin no matter what. Anything matching
# REFERENCE in these is an asset load, never prose.
ASSET_SUFFIXES = (".js", ".css", ".scss")


def _scan():
    """Yield (host, repo-relative path) for every off-origin asset reference."""
    root = str(settings.BASE_DIR)
    for top in ("templates", "resources"):
        for dirpath, _, filenames in os.walk(os.path.join(root, top)):
            rel_dir = os.path.relpath(dirpath, root)
            if any(rel_dir.startswith(skip) for skip in SKIP_DIRS):
                continue
            for filename in filenames:
                if not filename.endswith(SUFFIXES):
                    continue
                path = os.path.join(dirpath, filename)
                rel = os.path.relpath(path, root)
                with open(path, encoding="utf-8", errors="ignore") as handle:
                    body = handle.read()
                for groups in REFERENCE.findall(body):
                    url = next(g for g in groups if g)
                    yield url.split("//", 1)[1].split("/")[0], rel


class NoExternalSourcesTest(TestCase):
    def test_no_unapproved_off_origin_asset_references(self):
        unexpected = sorted(
            {
                (host, path)
                for host, path in _scan()
                if path not in ALLOWED.get(host, ())
            }
        )
        self.assertEqual(
            unexpected,
            [],
            "off-origin asset reference(s) added without an ALLOWED entry:\n"
            + "\n".join(f"  {host}  <-  {path}" for host, path in unexpected),
        )

    def test_the_allowlist_has_no_dead_entries(self):
        """A stale entry hides the next real one behind a false sense of cover."""
        found = set(_scan())
        dead = sorted(
            (host, path)
            for host, paths in ALLOWED.items()
            for path in paths
            if (host, path) not in found
        )
        self.assertEqual(
            dead,
            [],
            "ALLOWED lists reference(s) that no longer exist; remove them:\n"
            + "\n".join(f"  {host}  <-  {path}" for host, path in dead),
        )

    def test_no_stylesheet_or_script_file_references_another_host(self):
        """A .js/.css/.scss reference is always an asset load, never prose."""
        hits = sorted(
            {(h, p) for h, p in _scan() if p.endswith(ASSET_SUFFIXES)}
        )
        self.assertEqual(hits, [], f"asset file reaches off-origin: {hits}")

    def test_no_font_host_is_referenced_at_all(self):
        """Fonts are fully vendored; none of these may come back."""
        font_hosts = {
            "fonts.googleapis.com",
            "fonts.gstatic.com",
            "fonts.cdnfonts.com",
            "use.typekit.net",
        }
        hits = sorted({(h, p) for h, p in _scan() if h in font_hosts})
        self.assertEqual(hits, [], f"a font is loaded off-origin: {hits}")
