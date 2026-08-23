import threading

import markdown as _markdown
import bleach
from django.utils.html import escape
from bs4 import BeautifulSoup
from pymdownx import superfences, arithmatex
from django.conf import settings
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

from judge.markdown_extensions import (
    YouTubeExtension,
    EmoticonExtension,
    BlockMathPaddingExtension,
)

EXTENSIONS = [
    BlockMathPaddingExtension(),
    "pymdownx.arithmatex",
    "pymdownx.magiclink",
    "pymdownx.betterem",
    "pymdownx.details",
    "pymdownx.emoji",
    "pymdownx.inlinehilite",
    "pymdownx.tabbed",
    "pymdownx.superfences",
    "pymdownx.highlight",
    "pymdownx.tasklist",
    "markdown.extensions.footnotes",
    "markdown.extensions.attr_list",
    "markdown.extensions.def_list",
    "markdown.extensions.tables",
    "markdown.extensions.admonition",
    "markdown.extensions.toc",
    "nl2br",
    "mdx_breakless_lists",
    YouTubeExtension(),
    EmoticonExtension(),
]

EXTENSION_CONFIGS = {
    "pymdownx.arithmatex": {
        "generic": True,
    },
    "pymdownx.tabbed": {
        "alternate_style": True,
    },
    "pymdownx.superfences": {
        "custom_fences": [
            {
                "name": "sample",
                "class": "no-border",
                "format": superfences.fence_code_format,
            },
            {
                "name": "math",
                "class": "arithmatex",
                "format": arithmatex.arithmatex_fenced_format(which="generic"),
            },
        ],
    },
    "pymdownx.highlight": {
        "auto_title": True,
        "auto_title_map": {
            "Text Only": "",
        },
        "guess_lang": False,
    },
}

ALLOWED_TAGS = list(bleach.sanitizer.ALLOWED_TAGS) + [
    "img",
    "center",
    "iframe",
    "div",
    "span",
    "table",
    "tr",
    "td",
    "th",
    "tr",
    "pre",
    "code",
    "p",
    "hr",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "thead",
    "tbody",
    "sup",
    # <sub> is the pair of <sup>, which was already here — without it bleach
    # escapes it and the reader sees a literal "<sub>". Subscripts are ordinary
    # notation in problem statements and contest announcements (a_i, x_1).
    "sub",
    "dl",
    "dt",
    "dd",
    "br",
    "details",
    "summary",
    "video",
    "source",
    "input",
    "label",
]

# Inline styles are how hand-authored posts and flatpages carry their layout —
# the migrated tinhoctre.vn announcements are a page of styled <div>s and nothing
# else. bleach drops any attribute not listed here, so without "style" those
# pages render as unstyled text.
#
# "style" is only safe because every declaration is filtered against
# SAFE_CSS_PROPERTIES below: this same filter renders comments and chat
# messages, which any registered user can write.
ALLOWED_ATTRS = [
    "style",
    "src",
    "width",
    "height",
    "href",
    "class",
    "id",
    "open",
    "title",
    "frameborder",
    "allow",
    "allowfullscreen",
    "loading",
    "controls",
    "type",
    "name",
    "checked",
    "for",
    "data-tabs",
]


# Presentational properties only. Deliberately absent:
#   position / top / right / bottom / left / z-index — an absolutely positioned,
#     high z-index element in a comment can cover the page and capture clicks.
#   transform / filter / animation / transition / content / pointer-events —
#     same class of problem, no legitimate use in authored content.
# display and opacity are allowed: hiding your own content is the author's call
# and cannot reach outside the element.
SAFE_CSS_PROPERTIES = [
    # typography
    "color",
    "direction",
    "font",
    "font-family",
    "font-size",
    "font-style",
    "font-variant",
    "font-weight",
    "letter-spacing",
    "line-height",
    "text-align",
    "text-decoration",
    "text-indent",
    "text-transform",
    "vertical-align",
    "white-space",
    "word-break",
    "overflow-wrap",
    # box model
    "background",
    "background-color",
    "border",
    "border-bottom",
    "border-collapse",
    "border-color",
    "border-left",
    "border-radius",
    "border-right",
    "border-spacing",
    "border-style",
    "border-top",
    "border-width",
    "box-sizing",
    "clear",
    "display",
    "float",
    "height",
    "margin",
    "margin-bottom",
    "margin-left",
    "margin-right",
    "margin-top",
    "max-height",
    "max-width",
    "min-height",
    "min-width",
    "opacity",
    "padding",
    "padding-bottom",
    "padding-left",
    "padding-right",
    "padding-top",
    "width",
    # lists and tables
    "caption-side",
    "list-style",
    "list-style-position",
    "list-style-type",
]

try:
    from bleach.css_sanitizer import CSSSanitizer

    _css_sanitizer = CSSSanitizer(allowed_css_properties=SAFE_CSS_PROPERTIES)
except ImportError:  # pragma: no cover - depends on tinycss2 being installed
    # Without tinycss2 bleach cannot filter declarations, and an unfiltered
    # style attribute is worse than an absent one. Fall back to dropping it.
    _css_sanitizer = None
    ALLOWED_ATTRS = [attr for attr in ALLOWED_ATTRS if attr != "style"]


# tinycss2 filters declarations by PROPERTY name, never by value, so an allowed
# property can still carry one of these.
UNSAFE_STYLE_FUNCTIONS = (
    "url(",  # `background: url(//tracker/x.png)` — every reader fetches it
    "expression(",  # legacy IE, executes script
    "-moz-binding",  # legacy Firefox, loads XBL
    "image-set(",  # another way to name a remote image
)


def _strip_unsafe_style_functions(soup):
    """Drop style declarations whose *value* pulls in a resource or runs code.

    Removes only the offending declaration; the rest of the rule is kept, so a
    styled block loses its tracking pixel and nothing else.
    """
    for element in soup.find_all(style=True):
        declarations = [d for d in element["style"].split(";") if d.strip()]
        kept = [
            d
            for d in declarations
            if not any(bad in d.lower() for bad in UNSAFE_STYLE_FUNCTIONS)
        ]
        if len(kept) == len(declarations):
            continue
        if kept:
            element["style"] = ";".join(kept).strip() + ";"
        else:
            del element["style"]
    return soup


def _wrap_img_iframe_with_lazy_load(soup):
    for img in soup.findAll("img"):
        if img.get("src"):
            img["loading"] = "lazy"
    for img in soup.findAll("iframe"):
        if img.get("src"):
            img["loading"] = "lazy"
    return soup


def _wrap_images_with_featherlight(soup):
    for img in soup.findAll("img"):
        if img.get("src"):
            link = soup.new_tag(
                "a",
                href=img["src"],
                **{
                    "data-featherlight": "image",
                    "data-featherlight-variant": "image-widget-lightbox",
                }
            )
            img.wrap(link)
    return soup


def _open_external_links_in_new_tab(soup):
    domain = settings.SITE_DOMAIN.lower()
    for a in soup.findAll("a", href=True):
        href = a["href"]
        if href.startswith("http://") or href.startswith("https://"):
            try:
                link_domain = urlparse(href).netloc.lower()
                if link_domain != domain:
                    a["target"] = "_blank"
            except Exception:
                continue
    return soup


def _iframe_host_allowed(src):
    """Return True if the iframe src points at an allowlisted host.

    Matching is done on the parsed netloc (host only, port/userinfo stripped)
    against settings.IFRAME_ALLOWED_HOSTS using exact comparison, so tricks like
    ``youtube.com.evil.com`` or ``youtube.com@evil.com`` do not pass.
    """
    if not src:
        return False
    try:
        netloc = urlparse(src).netloc.lower()
    except ValueError:
        return False
    # Strip optional userinfo ("user@host") and port (":443").
    host = netloc.rsplit("@", 1)[-1].split(":", 1)[0]
    if not host:
        return False
    allowed = getattr(settings, "IFRAME_ALLOWED_HOSTS", [])
    return host in {h.lower() for h in allowed}


def _sanitize_iframe_sources(soup):
    """Drop iframes whose src host is not allowlisted (default-deny).

    Disallowed iframes are replaced with a plain text link to the URL so the
    content is not silently lost while no foreign page is embedded. This blocks
    phishing/clickjacking via arbitrary iframe injection in user markdown.
    """
    for iframe in soup.findAll("iframe"):
        src = iframe.get("src")
        if _iframe_host_allowed(src):
            continue
        if src:
            link = soup.new_tag("a", href=src)
            link["rel"] = "nofollow noopener"
            link.string = src
            iframe.replace_with(link)
        else:
            iframe.decompose()
    return soup


def _sanitize_iframe_autoplay(soup):
    """Remove autoplay parameters from iframe src URLs and attributes to prevent autoplay"""
    for iframe in soup.findAll("iframe"):
        try:
            # 1. Sanitize src URL parameters
            src = iframe.get("src")
            if src:
                # Parse the URL
                parsed = urlparse(src)

                # Get query parameters
                query_params = parse_qs(parsed.query)

                # Remove autoplay parameters (set to 0 if present)
                autoplay_params = ["autoplay", "auto_play", "auto-play"]
                modified = False

                for param in autoplay_params:
                    if param in query_params:
                        # Set autoplay to 0 instead of removing to be explicit
                        query_params[param] = ["0"]
                        modified = True

                # If we modified parameters, rebuild the URL
                if modified:
                    new_query = urlencode(query_params, doseq=True)
                    new_parsed = parsed._replace(query=new_query)
                    iframe["src"] = urlunparse(new_parsed)

            # 2. Remove/sanitize allow attribute that might permit autoplay
            allow_attr = iframe.get("allow")
            if allow_attr:
                # Remove autoplay from allow attribute
                allow_values = [val.strip() for val in allow_attr.split(";")]
                allow_values = [
                    val for val in allow_values if not val.startswith("autoplay")
                ]

                if allow_values:
                    iframe["allow"] = "; ".join(allow_values)
                else:
                    # Remove empty allow attribute
                    del iframe["allow"]

        except Exception:
            # If URL parsing fails, continue with next iframe
            continue

    return soup


_markdown_local = threading.local()


def _get_markdown_instance():
    inst = getattr(_markdown_local, "instance", None)
    if inst is None:
        inst = _markdown.Markdown(
            extensions=EXTENSIONS, extension_configs=EXTENSION_CONFIGS
        )
        _markdown_local.instance = inst
    return inst


def markdown(value, lazy_load=False):
    md = _get_markdown_instance()
    html = md.reset().convert(value)

    html = bleach.clean(
        html,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRS,
        css_sanitizer=_css_sanitizer,
    )

    if not html:
        html = escape(value)

    soup = BeautifulSoup(html, features="html.parser")
    if lazy_load:
        soup = _wrap_img_iframe_with_lazy_load(soup)

    soup = _wrap_images_with_featherlight(soup)
    soup = _open_external_links_in_new_tab(soup)
    soup = _sanitize_iframe_sources(soup)
    soup = _sanitize_iframe_autoplay(soup)
    soup = _strip_unsafe_style_functions(soup)
    html = str(soup)

    return '<div class="md-typeset content-description">%s</div>' % html
