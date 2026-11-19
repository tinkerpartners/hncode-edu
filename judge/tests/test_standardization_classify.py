from io import BytesIO
from unittest import mock

from django.db.models.fields.files import FieldFile
from django.test import SimpleTestCase, TestCase

from judge.models import Problem, ProblemGroup, ProblemTranslation
from judge.standardization import classify
from judge.standardization.extract_pdf import PdfText, extract_text

STATEMENT = (
    "Cho hai số nguyên $a, b$ (|a|, |b| <= 10^9).\n\n### Input\n- Hai số.\n\n"
    "### Output\n- Tổng.\n\n### Example\n```\n1 2\n```\n```\n3\n```\n"
)


def minimal_pdf(text):
    """A one-page PDF with a Helvetica text object, enough for pypdf."""
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 144] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        None,
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    stream = f"BT /F1 12 Tf 20 100 Td ({text}) Tj ET".encode()
    objects[3] = (
        b"<< /Length "
        + str(len(stream)).encode()
        + b" >>\nstream\n"
        + stream
        + b"\nendstream"
    )
    out = BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return out.getvalue()


class ExtractPdfTest(SimpleTestCase):
    def test_real_pdf_text_is_extracted(self):
        result = extract_text(BytesIO(minimal_pdf("Hello statement world")))
        self.assertEqual(result.error, "")
        self.assertEqual(result.pages, 1)
        self.assertIn("Hello statement world", result.text)
        self.assertTrue(result.is_scan)  # 21 chars on one page is below the threshold
        with self.settings(STANDARDIZATION_PDF_SCAN_CHARS_PER_PAGE=10):
            self.assertTrue(
                extract_text(BytesIO(minimal_pdf("Hello statement world"))).readable
            )

    def test_garbage_is_unreadable_not_an_exception(self):
        result = extract_text(BytesIO(b"not a pdf"))
        self.assertTrue(result.error)
        self.assertFalse(result.readable)


class ClassifyProblemTest(TestCase):
    fixtures = ["language_small"]

    def test_sample_only_text_is_not_a_readable_problem_statement(self):
        text = "Ví dụ:\n#input:\n```\n9\n3 17\n1 11\n```\n#output:\n```\n6\n5\n```"
        self.assertTrue(classify.only_examples(text))
        self.assertTrue(classify._is_placeholder(text))
        self.assertTrue(
            classify.classify_problem(self.make("only_sample", text)).needs_source
        )

    def test_sample_only_source_can_fall_back_to_real_translation(self):
        problem = self.make("sample_translation", "Example:\n```\n1\n```\n```\n2\n```")
        ProblemTranslation.objects.create(
            problem=problem, language="en", name="Task", description=STATEMENT
        )
        classification = classify.classify_problem(problem)
        self.assertEqual(classification.primary_source, "TRANSLATION")
        self.assertEqual(classification.extracted_text, STATEMENT)

    def test_sample_only_translation_is_not_a_fallback_source(self):
        problem = self.make("bad_sample_translation", "")
        ProblemTranslation.objects.create(
            problem=problem,
            language="en",
            name="Task",
            description="Example:\n```\n1\n```\n```\n2\n```",
        )
        self.assertTrue(classify.classify_problem(problem).needs_source)

    def test_real_task_or_input_description_is_not_sample_only(self):
        self.assertFalse(classify.only_examples(STATEMENT))
        self.assertFalse(
            classify.only_examples(
                "Tính tổng hai số.\n### Example\n```sample\n1 2\n```\n```sample\n3\n```"
            )
        )
        self.assertFalse(classify.only_examples("Ví dụ:\n```sample\n1\n```"))

    @classmethod
    def setUpTestData(cls):
        cls.group = ProblemGroup.objects.create(name="stdcl", full_name="std classify")

    def make(self, code, description, **kwargs):
        return Problem.objects.create(
            code=code,
            name=code,
            description=description,
            group=self.group,
            time_limit=1,
            memory_limit=262144,
            points=100,
            **kwargs,
        )

    def test_markdown_statement(self):
        c = classify.classify_problem(self.make("cl1", STATEMENT))
        self.assertEqual(c.primary_source, "MARKDOWN")
        self.assertEqual(c.source_kinds, ["MARKDOWN"])
        self.assertEqual(c.quality_flags, [])
        self.assertFalse(c.needs_source)

    def test_legacy_html_and_missing_sections(self):
        c = classify.classify_problem(
            self.make(
                "cl2", "<p>Cho n số nguyên, hãy in ra tổng của chúng với n lớn.</p>"
            )
        )
        self.assertEqual(c.primary_source, "LEGACY_HTML")
        self.assertIn("missing_required_section", c.quality_flags)
        self.assertIn("missing_example", c.quality_flags)

    def test_empty_description_with_translation_is_not_missing(self):
        p = self.make("cl3", "")
        ProblemTranslation.objects.create(
            problem=p, language="en", name="cl3", description=STATEMENT
        )
        c = classify.classify_problem(p)
        self.assertEqual(c.primary_source, "TRANSLATION")
        self.assertEqual(c.extracted_text, STATEMENT)
        self.assertNotIn("statement_missing", c.quality_flags)

    def test_placeholder_vs_missing(self):
        self.assertIn(
            "placeholder",
            classify.classify_problem(self.make("cl4", "TODO")).quality_flags,
        )
        c = classify.classify_problem(self.make("cl5", ""))
        self.assertIn("statement_missing", c.quality_flags)
        self.assertTrue(c.needs_source)

    def _with_pdf(self, code, pdf_result):
        p = self.make(code, "", pdf_description=f"{code}/statement.pdf")
        with mock.patch.object(
            FieldFile, "open", return_value=BytesIO(b"%PDF")
        ), mock.patch.object(classify, "extract_text", return_value=pdf_result):
            return classify.classify_problem(p)

    def test_pdf_text_becomes_primary_source(self):
        c = self._with_pdf("cl6", PdfText(2, 900, "Cho n. Input ... Output ..."))
        self.assertEqual(c.primary_source, "PDF_TEXT")
        self.assertEqual(c.source_kinds, ["PDF_TEXT"])
        self.assertEqual(c.pdf["pages"], 2)
        self.assertFalse(c.needs_source)

    def test_pdf_scan_needs_source(self):
        c = self._with_pdf("cl7", PdfText(3, 20, "a"))
        self.assertEqual(c.source_kinds, ["PDF_SCAN"])
        self.assertIn("ocr_uncertain", c.quality_flags)
        self.assertTrue(c.needs_source)

    def test_broken_pdf_file(self):
        p = self.make("cl8", "", pdf_description="cl8/statement.pdf")
        with mock.patch.object(
            FieldFile, "open", side_effect=FileNotFoundError("gone")
        ):
            c = classify.classify_problem(p)
        self.assertIn("broken_asset", c.quality_flags)
        self.assertTrue(c.needs_source)

    def test_image_only_and_interactive_flags(self):
        c = classify.classify_problem(self.make("cl9", "![đề](https://cdn/x.png)"))
        self.assertEqual(c.source_kinds, ["IMAGE"])
        self.assertIn("needs_expert", c.quality_flags)
        self.assertTrue(c.needs_source)
        c2 = classify.classify_problem(
            self.make("cl10", STATEMENT + "\nĐây là bài tương tác.")
        )
        self.assertIn("needs_expert", c2.quality_flags)
        self.assertFalse(c2.needs_source)
