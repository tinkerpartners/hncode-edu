"""
Inline styles in markdown-rendered content.

Posts and flatpages migrated from the old HNOJ site are hand-authored HTML whose
entire layout lives in style attributes. bleach used to drop every one of them,
so those pages rendered as unstyled text.

The same filter renders comments and chat messages, which any registered user can
write, so the attribute is only allowed to the extent that every declaration is
filtered — these tests pin both halves of that.
"""

from django.test import SimpleTestCase

from judge.markdown import SAFE_CSS_PROPERTIES, markdown


class InlineStylePreservedTest(SimpleTestCase):
    def test_typography_and_box_model_survive(self):
        html = markdown(
            '<div style="font-family: Arial, sans-serif; font-size: 15px; '
            "line-height: 1.7; color: #1f2937; max-width: 900px; margin: 0 auto; "
            'padding: 24px 28px; background: #ffffff;">xin chào</div>'
        )
        for declaration in (
            "font-family",
            "font-size",
            "line-height",
            "color",
            "max-width",
            "margin",
            "padding",
            "background",
        ):
            self.assertIn(declaration, html, declaration)
        self.assertIn("xin chào", html)

    def test_the_properties_these_pages_actually_use_are_allowed(self):
        """Every property found across the migrated posts and flatpages."""
        used = [
            "color",
            "font-size",
            "margin-bottom",
            "padding",
            "font-weight",
            "margin",
            "border",
            "text-align",
            "padding-left",
            "border-radius",
            "margin-top",
            "background",
            "width",
            "background-color",
            "line-height",
            "border-bottom",
            "padding-bottom",
            "border-left",
            "font-family",
            "text-decoration",
            "border-top",
            "text-transform",
            "display",
            "max-width",
            "border-collapse",
            "padding-top",
        ]
        missing = [p for p in used if p not in SAFE_CSS_PROPERTIES]
        self.assertEqual(missing, [], f"not allowed: {missing}")

    def test_multiline_style_attribute(self):
        """The migrated content writes one declaration per line."""
        html = markdown('<div style="\n  color: red;\n  font-size: 15px;\n">x</div>')
        self.assertIn("color", html)
        self.assertIn("font-size", html)

    def test_style_on_table_cells(self):
        html = markdown(
            '<table><tr><td style="text-align: center; padding: 6px">1</td></tr></table>'
        )
        self.assertIn("text-align", html)


class InlineStyleSanitizedTest(SimpleTestCase):
    """A comment author must not be able to reach outside their own element."""

    def test_position_is_dropped(self):
        html = markdown('<div style="position: fixed; top: 0; color: red">x</div>')
        self.assertNotIn("position", html)
        self.assertNotIn("top:", html)
        self.assertIn("color", html)  # the rest of the rule survives

    def test_z_index_is_dropped(self):
        html = markdown('<div style="z-index: 9999; color: red">x</div>')
        self.assertNotIn("z-index", html)

    def test_transform_and_animation_are_dropped(self):
        html = markdown(
            '<div style="transform: scale(40); animation: x 1s; color: red">x</div>'
        )
        self.assertNotIn("transform", html)
        self.assertNotIn("animation", html)

    def test_expression_is_dropped(self):
        """`width` is an allowed property, so only a value check catches this."""
        html = markdown('<div style="width: expression(alert(1))">x</div>')
        self.assertNotIn("expression", html)

    def test_moz_binding_is_dropped(self):
        html = markdown('<div style="-moz-binding: url(//evil/x.xml)">x</div>')
        self.assertNotIn("binding", html)

    def test_image_set_is_dropped(self):
        html = markdown(
            '<div style="background: image-set(\'//tracker/x.png\' 1x); color: red">x</div>'
        )
        self.assertNotIn("tracker", html)
        self.assertIn("color", html)

    def test_javascript_url_is_neutered(self):
        html = markdown('<div style="background: url(javascript:alert(1))">x</div>')
        self.assertNotIn("javascript:alert", html)

    def test_external_url_is_dropped(self):
        """No tracking pixel via `background: url(...)` in a comment."""
        html = markdown(
            '<div style="background: url(//tracker.example/x.png)">x</div>'
        )
        self.assertNotIn("tracker.example", html)
        self.assertNotIn("url(", html)

    def test_url_declaration_dropped_but_siblings_kept(self):
        html = markdown(
            '<div style="color: red; background: url(//tracker.example/x.png); '
            'font-size: 15px">x</div>'
        )
        self.assertNotIn("tracker.example", html)
        self.assertIn("color", html)
        self.assertIn("font-size", html)

    def test_script_still_stripped(self):
        html = markdown('<div style="color: red"><script>alert(1)</script></div>')
        self.assertNotIn("<script>", html)

    def test_onclick_still_stripped(self):
        html = markdown('<div style="color: red" onclick="alert(1)">x</div>')
        self.assertNotIn("onclick", html)


class MarkdownStillWorksTest(SimpleTestCase):
    """Allowing an attribute must not disturb normal markdown."""

    def test_headings_and_emphasis(self):
        html = markdown("## Tiêu đề\n\n**đậm** và *nghiêng*")
        self.assertIn("<h2", html)
        self.assertIn("<strong>", html)
        self.assertIn("<em>", html)

    def test_tables(self):
        html = markdown("| a | b |\n| :--- | ---: |\n| 1 | 2 |")
        self.assertIn("<table>", html)

    def test_wrapper_is_unchanged(self):
        html = markdown("hello")
        self.assertTrue(html.startswith('<div class="md-typeset content-description">'))
