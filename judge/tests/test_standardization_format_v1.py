import json
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase

from judge.models import Problem, ProblemGroup
from judge.models.standardization import StandardizationProposal
from judge.standardization import format_v1
from judge.standardization.apply import restore_proposal
from judge.standardization.enums import ProposalStatus

V1 = (
    "Cho hai số.\n\n**Yêu cầu:** tính tổng.\n\n### Input\n- Hai số $a, b$.\n\n"
    '### Output\n- Tổng.\n\n### Example\n!!! question "Test 1"\n    ???+ "Input"\n'
    '        ```sample\n        1 2\n        ```\n    ???+ success "Output"\n'
    "        ```sample\n        3\n        ```\n"
)
H4 = (
    V1.replace("### Input", "#### Input")
    .replace("### Output", "####Output")
    .replace("### Example", "## example")
)
FILE_IO = (
    "Đọc từ `THREEPOINT.INP`.\n\n#### Input\n- Dòng 1: n.\n\n#### Output\n"
    "- Ghi ra `THREEPOINT.OUT`.\n\n#### Example\n```\n3\n```\n"
)


class FormatV1Test(SimpleTestCase):
    def test_v1_validates_strict(self):
        r = format_v1.validate(V1)
        self.assertEqual(r.label, "STRICT_OK")
        self.assertEqual([h.name for h in r.headings], ["Input", "Output", "Example"])

    def test_level4_is_invalid_and_fixable(self):
        r = format_v1.validate(H4)
        self.assertEqual(r.label, "INVALID")
        self.assertIn("Input is level 4, not 3", r.errors)
        fixed = format_v1.rule_fix_headings(H4)
        self.assertEqual(fixed, V1)
        ok, reason = format_v1.can_auto_apply(H4, fixed)
        self.assertTrue(ok, reason)
        self.assertEqual(reason, "STRICT_OK")

    def test_headings_inside_fences_are_ignored(self):
        text = "### Input\n```\n#### Input\n### Output\n```\n### Output\n### Example\n"
        self.assertEqual(
            [h.name for h in format_v1.find_headings(text)],
            ["Input", "Output", "Example"],
        )
        self.assertEqual(format_v1.rule_fix_headings(text), text)
        self.assertTrue(format_v1.validate(text).ok)
        tilde = "### Input\n~~~\n## Output\n~~~\n### Output\n### Example\n"
        self.assertEqual(format_v1.rule_fix_headings(tilde), tilde)

    def test_file_io_names_and_body_untouched(self):
        fixed = format_v1.rule_fix_headings(FILE_IO)
        self.assertIn("`THREEPOINT.INP`", fixed)
        self.assertIn("`THREEPOINT.OUT`", fixed)
        self.assertNotIn("freopen", fixed)
        self.assertEqual(
            format_v1.strip_heading_lines(FILE_IO), format_v1.strip_heading_lines(fixed)
        )
        self.assertEqual(
            format_v1.validate(fixed).label, "HEADINGS_OK"
        )  # plain fence, not admonition

    def test_vietnamese_headings_are_not_rewritten(self):
        text = "### Dữ liệu vào\n- n\n### Kết quả\n- x\n### Ví dụ\n"
        self.assertEqual(format_v1.rule_fix_headings(text), text)
        r = format_v1.validate(text)
        self.assertFalse(r.ok)
        self.assertEqual(len(r.errors), 3)

    def test_duplicate_and_out_of_order_are_invalid(self):
        dup = "### Input\n#### Input\n### Output\n### Example\n"
        r = format_v1.validate(dup)
        self.assertIn("Input appears 2 times", r.errors)
        fixed = format_v1.rule_fix_headings(dup)
        ok, reason = format_v1.can_auto_apply(dup, fixed)
        self.assertFalse(ok)  # still two Input headings after the fix
        self.assertIn("appears 2 times", reason)
        order = "### Output\n### Input\n### Example\n"
        self.assertIn(
            "sections out of order (Input, Output, Example)",
            format_v1.validate(order).errors,
        )

    def test_scoring_optional_but_level3(self):
        ok = "### Input\n### Output\n### Scoring\n- 50%\n### Example\n"
        self.assertTrue(format_v1.validate(ok).ok)
        bad = ok.replace("### Scoring", "#### Scoring")
        self.assertIn("Scoring is level 4, not 3", format_v1.validate(bad).errors)
        self.assertEqual(format_v1.rule_fix_headings(bad), ok)

    def test_can_auto_apply_refuses_body_changes(self):
        ok, reason = format_v1.can_auto_apply(H4, V1 + "\nextra")
        self.assertFalse(ok)
        self.assertEqual(reason, "non-heading content changed")
        self.assertEqual(format_v1.can_auto_apply(V1, V1), (False, "no change"))

    def test_closing_hashes_and_indent_tolerated(self):
        text = "  ## Input ##\n### Output\n### Example\n"
        self.assertEqual(
            format_v1.rule_fix_headings(text), "### Input\n### Output\n### Example\n"
        )

    def test_crlf_statements_are_recognised_and_normalised(self):
        crlf = H4.replace("\n", "\r\n")
        r = format_v1.validate(crlf)
        self.assertIn("Input is level 4, not 3", r.errors)
        self.assertEqual(
            format_v1.validate(V1.replace("\n", "\r\n")).label, "STRICT_OK"
        )
        fixed = format_v1.rule_fix_headings(crlf)
        self.assertNotIn("\r", fixed)
        self.assertEqual(fixed, V1)
        ok, reason = format_v1.can_auto_apply(crlf, fixed)
        self.assertTrue(ok, reason)


class StandardizeHeadingsCommandTest(TestCase):
    fixtures = ["language_small"]

    @classmethod
    def setUpTestData(cls):
        cls.group = ProblemGroup.objects.create(name="stdhd", full_name="std headings")

    def make(self, code, description):
        return Problem.objects.create(
            code=code,
            name=code,
            description=description,
            group=self.group,
            time_limit=1,
            memory_limit=262144,
            points=100,
            is_public=True,
        )

    def run_cmd(self, tmp, name, *args):
        out = StringIO()
        call_command(
            "standardize_headings", "--report", str(Path(tmp) / name), *args, stdout=out
        )
        rows = [
            json.loads(l)
            for l in Path(tmp, name).read_text(encoding="utf-8").splitlines()
        ]
        return json.loads(out.getvalue()), rows

    def test_dry_run_classifies(self):
        self.make("hd1", H4)
        self.make("hd2", V1)
        self.make("hd3", "### Dữ liệu vào\n### Kết quả\n")
        self.make("hd4", "")
        with TemporaryDirectory() as tmp:
            summary, rows = self.run_cmd(tmp, "d.jsonl")
        self.assertEqual(
            summary["results"],
            {"would_apply": 1, "already_v1": 1, "needs_review": 1, "no_statement": 1},
        )
        by_code = {r["code"]: r for r in rows}
        self.assertEqual(by_code["hd1"]["level_after"], "STRICT_OK")
        self.assertEqual(by_code["hd2"]["level"], "STRICT_OK")
        self.assertIn("missing ### Input", by_code["hd3"]["validation_before"])
        self.assertFalse(StandardizationProposal.objects.exists())
        self.assertEqual(Problem.objects.get(code="hd1").description, H4)

    def test_apply_is_audited_restorable_and_idempotent(self):
        p = self.make("hd5", H4)
        with TemporaryDirectory() as tmp:
            summary, _ = self.run_cmd(tmp, "a.jsonl", "--apply")
            self.assertEqual(summary["results"], {"applied": 1})
            p.refresh_from_db()
            self.assertEqual(p.description, V1)
            proposal = StandardizationProposal.objects.get(problem=p)
            self.assertEqual(proposal.status, ProposalStatus.APPLIED)
            self.assertEqual(proposal.step, "heading_fix_v1")
            self.assertEqual(proposal.field_group, "content")
            self.assertEqual(proposal.before_json, {"description": H4})

            summary2, _ = self.run_cmd(tmp, "b.jsonl", "--apply")
        self.assertEqual(summary2["results"], {"already_v1": 1})
        restore_proposal(proposal)
        p.refresh_from_db()
        self.assertEqual(p.description, H4)
