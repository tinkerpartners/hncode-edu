from django.test import SimpleTestCase

from judge.markdown import markdown


class MarkdownCaptionTests(SimpleTestCase):
    def test_table_caption_is_kept_without_event_handlers(self):
        html = markdown(
            '<table><caption onclick="x()">Bảng 1</caption>'
            "<tbody><tr><td>a</td></tr></tbody></table>"
        )
        self.assertIn("<caption>Bảng 1</caption>", html)
        self.assertNotIn("onclick", html)
