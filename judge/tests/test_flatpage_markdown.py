"""
Markdown flatpages.

A flatpage picks its format through FlatPage.template_name. The admin exposes
that as a two-option dropdown instead of a free-text path, and new pages default
to Markdown. Existing pages must keep rendering exactly as before — several
dozen of them are hand-written HTML that bleach would strip.
"""

from django.contrib.auth.models import User
from django.contrib.flatpages.models import FlatPage
from django.contrib.sites.models import Site
from django.test import TestCase

from judge.admin.flatpage import (
    FLATPAGE_MARKDOWN_TEMPLATE,
    FlatPageAdmin,
    FlatPageForm,
)
from judge.models import Language, Profile


MARKDOWN_BODY = """# Đề thi 2022

Một số **quan trọng** và một số *nghiêng*.

- ý thứ nhất
- ý thứ hai

| Cột A | Cột B |
|---|---|
| 1 | 2 |

Công thức: $x^2 + y^2 = z^2$

```cpp
int main() { return 0; }
```
"""


def make_page(url, content, template_name=FLATPAGE_MARKDOWN_TEMPLATE, title="Test"):
    page = FlatPage.objects.create(
        url=url, title=title, content=content, template_name=template_name
    )
    page.sites.add(Site.objects.get_current())
    return page


class FlatPageMarkdownRenderTest(TestCase):
    fixtures = ["language_small"]

    def test_markdown_page_renders_markdown(self):
        make_page("/dethi/", MARKDOWN_BODY, title="Đề thi")
        html = self.client.get("/dethi/").content.decode()

        self.assertIn("<h1", html)  # heading, not a literal '#'
        self.assertNotIn("# Đề thi 2022", html)
        self.assertIn("<strong>quan trọng</strong>", html)
        self.assertIn("<em>nghiêng</em>", html)
        self.assertIn("<li>", html)
        self.assertIn("<table>", html)

    def test_markdown_page_keeps_latex_for_the_math_renderer(self):
        make_page("/nam2022/", "Công thức: $x^2 + y^2 = z^2$")
        html = self.client.get("/nam2022/").content.decode()
        # arithmatex hands LaTeX to KaTeX rather than rendering it server-side.
        self.assertIn("x^2 + y^2 = z^2", html)

    def test_markdown_page_renders_fenced_code(self):
        make_page("/code/", "```cpp\nint main() { return 0; }\n```")
        html = self.client.get("/code/").content.decode()
        self.assertIn("<code", html)
        # superfences + highlight tokenize the source, so the literal
        # "int main()" is split across spans — assert on the structure.
        self.assertIn('class="highlight"', html)
        self.assertIn(">int</span>", html)
        self.assertIn(">main</span>", html)
        self.assertNotIn("```cpp", html)

    def test_markdown_output_is_not_double_wrapped(self):
        """markdown() already emits .md-typeset.content-description."""
        make_page("/wrap/", "hello")
        html = self.client.get("/wrap/").content.decode()
        self.assertEqual(html.count("md-typeset content-description"), 1)

    def test_markdown_page_is_sanitized(self):
        make_page("/xss/", "Hello\n\n<script>alert(1)</script>")
        html = self.client.get("/xss/").content.decode()
        self.assertNotIn("<script>alert(1)</script>", html)

    def test_title_and_edit_link_still_render(self):
        make_page("/dethi/", MARKDOWN_BODY, title="Đề thi 2022")
        html = self.client.get("/dethi/").content.decode()
        self.assertIn("Đề thi 2022", html)

    def test_markdown_math_template_still_works(self):
        """Legacy pages naming markdown_math.html must not break."""
        make_page("/legacy/", "**bold** and $x^2$", "flatpages/markdown_math.html")
        html = self.client.get("/legacy/").content.decode()
        self.assertIn("<strong>bold</strong>", html)


class FlatPageHtmlUnchangedTest(TestCase):
    """The 50-odd existing pages must render exactly as they did."""

    fixtures = ["language_small"]

    def test_default_template_still_renders_raw_html(self):
        make_page(
            "/legacyhtml/",
            '<div style="color: red">kept</div>',
            template_name="",
        )
        html = self.client.get("/legacyhtml/").content.decode()
        # render_django() passes HTML through untouched — including the inline
        # style that the markdown pipeline's bleach pass would have stripped.
        self.assertIn('style="color: red"', html)
        self.assertIn("kept", html)

    def test_default_template_does_not_markdown_the_content(self):
        make_page("/legacyhtml2/", "# not a heading", template_name="")
        html = self.client.get("/legacyhtml2/").content.decode()
        self.assertIn("# not a heading", html)
        self.assertNotIn("<h1># not a heading</h1>", html)


class FlatPageAdminFormTest(TestCase):
    fixtures = ["language_small"]

    def setUp(self):
        self.site = Site.objects.get_current()

    def test_new_page_defaults_to_markdown(self):
        form = FlatPageForm()
        self.assertEqual(
            form.initial.get("template_name"), FLATPAGE_MARKDOWN_TEMPLATE
        )

    def test_existing_page_keeps_its_format(self):
        page = make_page("/old/", "<b>x</b>", template_name="")
        form = FlatPageForm(instance=page)
        self.assertFalse(form.initial.get("template_name"))

    def test_format_field_is_a_dropdown_with_both_options(self):
        values = [v for v, _ in FlatPageForm().fields["template_name"].widget.choices]
        self.assertIn(FLATPAGE_MARKDOWN_TEMPLATE, values)
        self.assertIn("", values)

    def test_unlisted_stored_template_stays_selectable(self):
        page = make_page("/legacy/", "x", "flatpages/markdown_math.html")
        values = [
            v
            for v, _ in FlatPageForm(instance=page).fields["template_name"].widget.choices
        ]
        self.assertIn("flatpages/markdown_math.html", values)

    def test_saving_a_page_switches_format(self):
        page = make_page("/switch/", "**bold**", template_name="")
        form = FlatPageForm(
            data={
                "url": "/switch/",
                "title": "Switch",
                "content": "**bold**",
                "template_name": FLATPAGE_MARKDOWN_TEMPLATE,
                "sites": [self.site.pk],
            },
            instance=page,
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        page.refresh_from_db()
        self.assertEqual(page.template_name, FLATPAGE_MARKDOWN_TEMPLATE)

        html = self.client.get("/switch/").content.decode()
        self.assertIn("<strong>bold</strong>", html)

    def test_saving_does_not_rewrite_an_unlisted_template(self):
        page = make_page("/legacy2/", "x", "flatpages/markdown_math.html")
        form = FlatPageForm(
            data={
                "url": "/legacy2/",
                "title": "Legacy",
                "content": "x",
                "template_name": "flatpages/markdown_math.html",
                "sites": [self.site.pk],
            },
            instance=page,
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        page.refresh_from_db()
        self.assertEqual(page.template_name, "flatpages/markdown_math.html")

    def test_admin_list_column_labels_each_format(self):
        markdown_page = make_page("/m/", "x")
        html_page = make_page("/h/", "x", template_name="")
        legacy = make_page("/l/", "x", "flatpages/markdown_math.html")

        column = FlatPageAdmin.content_format
        self.assertEqual(str(column(None, markdown_page)), "Markdown")
        self.assertEqual(str(column(None, html_page)), "HTML / Django template")
        self.assertEqual(str(column(None, legacy)), "flatpages/markdown_math.html")


class FlatPageAdminViewTest(TestCase):
    """The change form must actually render for a superuser."""

    fixtures = ["language_small"]

    def setUp(self):
        self.user = User.objects.create_superuser(
            username="fpadmin", email="fp@test.com", password="testpass"
        )
        Profile.objects.get_or_create(
            user=self.user, defaults={"language": Language.objects.first()}
        )
        self.client.force_login(self.user)

    def test_change_form_renders_the_format_picker(self):
        page = make_page("/dethi/", MARKDOWN_BODY, title="Đề thi")
        response = self.client.get(f"/admin/flatpages/flatpage/{page.pk}/change/")
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn("Content format", html)
        self.assertIn(FLATPAGE_MARKDOWN_TEMPLATE, html)

    def test_add_form_renders(self):
        response = self.client.get("/admin/flatpages/flatpage/add/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Content format", response.content.decode())
