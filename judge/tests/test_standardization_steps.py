from decimal import Decimal
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import TestCase

from judge.models import Problem, ProblemGroup, ProblemType, Solution
from judge.models.standardization import (
    LLMUsageLedger,
    ProblemSnapshot,
    StandardizationBatch,
    StandardizationProposal,
)
from judge.standardization import format_v1, prompts, steps
from judge.standardization.enums import FieldGroup, ProposalStatus
from judge.standardization.tag_aliases import canonical_name, resolve_tags
from judge.tests.test_standardization_apply import ensure_languages
from llm_service.providers import LLMProvider, LLMProviderError, LLMResponse, LLMUsage

STATEMENT = (
    "Cho hai số nguyên $a, b$.\n\n**Yêu cầu:** in tổng.\n\n### Input\n- Hai số $a, b$ ($|a|, |b| \\le 10^9$).\n\n"
    "### Output\n- Tổng.\n\n### Example\n```\n1 2\n```\n```\n3\n```\n"
)

CONTENT_PAYLOAD = {
    "language": "vi",
    "source_readable": True,
    "statement": "Cho hai số nguyên $a, b$.\n\n**Yêu cầu:** in ra tổng của chúng.",
    "input": "- Một dòng chứa hai số nguyên $a, b$ ($|a|, |b| \\le 10^9$).",
    "output": "- Một số nguyên là tổng $a + b$.",
    "scoring": None,
    "examples": [{"input": "1 2", "output": "3", "note": "Vì $1 + 2 = 3$."}],
    "io_contract": {"kind": "stdin_stdout", "input_file": None, "output_file": None},
    "kept_images": [],
    "warnings": [],
    "unverified_spans": [],
}
TAGS_PAYLOAD = {
    "insufficient_evidence": False,
    "tags": ["implementation", "Math - Toán học cơ bản", "no-such-tag"],
    "primary_tag": "implementation",
    "rating": 800,
    "rating_low": 800,
    "rating_high": 900,
    "confidence": "high",
    "reasoning": "Đọc vào và in tổng.",
    "cf_source_url": None,
}
SOLUTION_PAYLOAD = {
    "summary": "Tính tổng hai số.",
    "key_observations": ["Dùng long long."],
    "algorithm": "Đọc $a, b$, in $a+b$.",
    "correctness": "Hiển nhiên.",
    "complexity_time": "$O(1)$",
    "complexity_memory": "$O(1)$",
    "edge_cases": ["Số âm."],
    "subtasks": [],
    "language_key": "CPP17",
    "cpp_code": "#include <iostream>\nint main(){long long a,b;std::cin>>a>>b;std::cout<<a+b;}",
    "notes_on_existing": None,
    "confidence": "high",
}


class FakeProvider(LLMProvider):
    name = "fake"
    default_model = "fake-model"

    def __init__(self, payloads):
        import json

        self.payloads = payloads  # step prefix -> dict or Exception
        self.calls = []
        self._json = json

    def complete(self, request):
        self.calls.append(request)
        payload = self.payloads.get(self._kind(request))
        if isinstance(payload, Exception):
            raise payload
        return LLMResponse(
            text=self._json.dumps(payload),
            provider=self.name,
            model=self.default_model,
            usage=LLMUsage(100, 50),
            cost_usd=Decimal("0.001"),
            request_id="req",
            custom_id=request.custom_id,
        )

    @staticmethod
    def _kind(request):
        if "editorial" in request.system[:200].lower():
            return "solution"
        if "classifying" in request.system[:200]:
            return "tags"
        return "content"


class StepsTest(TestCase):
    fixtures = ["language_small"]

    @classmethod
    def setUpTestData(cls):
        ensure_languages()
        cls.group = ProblemGroup.objects.create(name="stdst", full_name="std steps")
        ProblemType.objects.create(name="implementation", full_name="implementation")
        ProblemType.objects.create(name="math", full_name="math")
        ProblemType.objects.create(name="chuaphanloai", full_name="Chưa phân loại")

    def make(self, code, description=STATEMENT, **kw):
        return Problem.objects.create(
            code=code,
            name=code,
            description=description,
            group=self.group,
            time_limit=1,
            memory_limit=1048576,
            points=100,
            is_public=True,
            **kw,
        )

    def provider(self, **overrides):
        payloads = {
            "content": CONTENT_PAYLOAD,
            "tags": TAGS_PAYLOAD,
            "solution": SOLUTION_PAYLOAD,
        }
        payloads.update(overrides)
        return FakeProvider(payloads)

    def test_content_step_renders_v1_and_dedupes(self):
        p = self.make("st1")
        provider = self.provider()
        result = steps.run_content_step(p, provider=provider)
        proposal = result.proposals[0]
        self.assertEqual(proposal.status, ProposalStatus.PENDING_REVIEW)
        self.assertEqual(proposal.field_group, FieldGroup.CONTENT)
        self.assertEqual(proposal.step, steps.STEP_CONTENT)
        self.assertEqual(proposal.prompt_version, prompts.PROMPT_VERSION)
        md = proposal.payload_json["description"]
        self.assertEqual(format_v1.validate(md).label, "STRICT_OK")
        self.assertIn('!!! question "Test 1"', md)
        self.assertIn("        1 2", md)
        self.assertEqual(proposal.cost_usd, Decimal("0.001"))
        self.assertEqual(proposal.model, "fake-model")
        ledger = LLMUsageLedger.objects.get()
        self.assertEqual(ledger.proposal_id, proposal.pk)
        self.assertEqual(ProblemSnapshot.objects.filter(problem=p).count(), 1)
        self.assertIn("MARKDOWN", ProblemSnapshot.objects.get().source_kinds_json)
        # second run: no model call
        again = steps.run_content_step(p, provider=provider)
        self.assertEqual(again.skipped, "dedupe")
        self.assertEqual(again.proposals[0].pk, proposal.pk)
        self.assertEqual(len(provider.calls), 1)

    def test_prompt_contains_statement_only(self):
        p = self.make("st2")
        provider = self.provider()
        steps.run_content_step(p, provider=provider)
        request = provider.calls[0]
        self.assertIn("Cho hai số nguyên", request.user)
        self.assertIn("### Input", request.system)  # exemplar
        self.assertEqual(
            request.custom_id, StandardizationProposal.objects.get().dedupe_key
        )

    def test_needs_source_skips_model(self):
        p = self.make("st3", description="")
        provider = self.provider()
        result = steps.run_content_step(p, provider=provider)
        self.assertEqual(result.skipped, "needs_source")
        self.assertEqual(result.proposals[0].status, ProposalStatus.NEEDS_SOURCE)
        self.assertEqual(provider.calls, [])
        self.assertFalse(LLMUsageLedger.objects.exists())

    def test_unreadable_source_needs_clarification(self):
        p = self.make("st4")
        payload = dict(CONTENT_PAYLOAD, source_readable=False, warnings=["ảnh mờ"])
        result = steps.run_content_step(p, provider=self.provider(content=payload))
        self.assertEqual(result.proposals[0].status, ProposalStatus.NEEDS_CLARIFICATION)
        self.assertEqual(result.proposals[0].evidence_json["warnings"], ["ảnh mờ"])

    def test_provider_error_becomes_error_proposal_with_ledger(self):
        p = self.make("st5")
        provider = self.provider(content=LLMProviderError("quota", "rate limited"))
        result = steps.run_content_step(p, provider=provider)
        self.assertEqual(result.error, "provider:quota")
        self.assertEqual(result.proposals[0].status, ProposalStatus.ERROR)
        self.assertEqual(result.proposals[0].evidence_json["error"], "provider:quota")
        self.assertEqual(LLMUsageLedger.objects.get().status, "error")

    def test_schema_violation_is_error(self):
        p = self.make("st6")
        bad = dict(CONTENT_PAYLOAD)
        del bad["examples"]
        result = steps.run_content_step(p, provider=self.provider(content=bad))
        self.assertTrue(result.error.startswith("schema:"))
        self.assertEqual(result.proposals[0].status, ProposalStatus.ERROR)

    def test_separate_rating_step_also_rejects_inverted_rating_range(self):
        problem = self.make("inverted_rating")
        payload = dict(TAGS_PAYLOAD, rating_low=1800, rating_high=900)
        result = steps.run_tags_points_step(
            problem, provider=self.provider(tags=payload)
        )
        points = next(p for p in result.proposals if p.field_group == FieldGroup.POINTS)
        self.assertEqual(points.status, ProposalStatus.NEEDS_CLARIFICATION)
        self.assertFalse(points.evidence_json["in_range"])

    def test_tags_points_step_resolves_aliases_and_splits_groups(self):
        p = self.make("st7")
        provider = self.provider()
        result = steps.run_tags_points_step(p, provider=provider)
        by_group = {pr.field_group: pr for pr in result.proposals}
        tags, points = by_group[FieldGroup.TAGS], by_group[FieldGroup.POINTS]
        self.assertEqual(tags.status, ProposalStatus.PENDING_REVIEW)
        self.assertEqual(tags.payload_json["names"], ["implementation", "math"])
        self.assertEqual(tags.payload_json["unresolved"], ["no-such-tag"])
        self.assertEqual(len(tags.payload_json["type_ids"]), 2)
        self.assertEqual(points.status, ProposalStatus.PENDING_REVIEW)
        self.assertEqual(points.payload_json["points"], 800)
        self.assertEqual(points.payload_json["points_before"], 100.0)
        self.assertEqual(points.diff_json, {"points_from": 100.0, "points_to": 800})
        self.assertEqual(
            points.payload_json["label"], "độ khó ước lượng theo thang Codeforces"
        )
        self.assertEqual(len(provider.calls), 1)  # one call, two proposals
        self.assertIn("CATALOGUE", provider.calls[0].system)
        self.assertIn("- implementation", provider.calls[0].system)
        self.assertNotIn(
            "Chưa phân loại", provider.calls[0].system.split("CATALOGUE:\n")[1]
        )
        self.assertEqual(LLMUsageLedger.objects.count(), 1)
        again = steps.run_tags_points_step(p, provider=provider)
        self.assertEqual(again.skipped, "dedupe")
        self.assertEqual(len(provider.calls), 1)

    def test_insufficient_evidence_or_out_of_range_needs_clarification(self):
        p = self.make("st8")
        payload = dict(TAGS_PAYLOAD, insufficient_evidence=True)
        result = steps.run_tags_points_step(p, provider=self.provider(tags=payload))
        points = next(
            pr for pr in result.proposals if pr.field_group == FieldGroup.POINTS
        )
        self.assertEqual(points.status, ProposalStatus.NEEDS_CLARIFICATION)
        p2 = self.make("st9")
        payload = dict(TAGS_PAYLOAD, rating=50, tags=["nothing-known"])
        result = steps.run_tags_points_step(p2, provider=self.provider(tags=payload))
        statuses = {pr.field_group: pr.status for pr in result.proposals}
        self.assertEqual(
            statuses[FieldGroup.POINTS], ProposalStatus.NEEDS_CLARIFICATION
        )
        self.assertEqual(statuses[FieldGroup.TAGS], ProposalStatus.NEEDS_CLARIFICATION)

    def test_solution_step_keeps_existing_in_prompt_and_renders(self):
        p = self.make("st10")
        Solution.objects.create(
            problem=p,
            content="Lời giải cũ khá tốt.",
            is_public=True,
            publish_on="2020-01-01T00:00Z",
        )
        provider = self.provider()
        result = steps.run_solution_step(p, provider=provider)
        proposal = result.proposals[0]
        self.assertEqual(proposal.status, ProposalStatus.PENDING_REVIEW)
        self.assertIn("Lời giải cũ khá tốt.", provider.calls[0].user)
        content = proposal.payload_json["content"]
        self.assertIn("### Thuật toán", content)
        self.assertIn("### Code tham chiếu (C++17)", content)
        self.assertIn("```cpp", content)
        self.assertEqual(proposal.payload_json["language_key"], "CPP17")
        self.assertTrue(proposal.diff_json["existing_solution"])
        self.assertTrue(proposal.diff_json["existing_public"])
        self.assertEqual(proposal.evidence_json["verification"], "unverified")

    def test_run_command_sync_with_report(self):
        p = self.make("st11")
        provider = self.provider()
        out = StringIO()
        import tempfile, json, os

        with tempfile.TemporaryDirectory() as tmp, mock.patch(
            "judge.management.commands.standardize_run.get_provider",
            return_value=provider,
        ):
            report = os.path.join(tmp, "pilot.jsonl")
            call_command(
                "standardize_run",
                "--codes",
                p.code,
                "--steps",
                "all",
                "--report",
                report,
                stdout=out,
            )
            rows = [json.loads(l) for l in open(report, encoding="utf-8")]
        self.assertEqual([r["step"] for r in rows], list(steps.ALL_STEPS))
        summary = json.loads(out.getvalue().splitlines()[-1])
        self.assertEqual(summary["results"], {"drafted": 3})
        batch = StandardizationBatch.objects.get(pk=summary["batch_id"])
        self.assertEqual(batch.status, "finished")
        self.assertEqual(batch.kind, "ai_pipeline")
        self.assertEqual(StandardizationProposal.objects.filter(batch=batch).count(), 4)
        self.assertEqual(len(provider.calls), 3)

    def test_run_step_unknown(self):
        with self.assertRaises(ValueError):
            steps.run_step("nope", self.make("st12"))


class TagAliasTest(TestCase):
    def test_aliases_and_resolution(self):
        a = ProblemType.objects.create(name="fenwick tree", full_name="fenwick tree")
        b = ProblemType.objects.create(name="lca", full_name="lca")
        ProblemType.objects.create(name="chuaphanloai", full_name="Chưa phân loại")
        self.assertEqual(canonical_name(" Binary  Indexed Tree "), "fenwick tree")
        ids, names, unresolved = resolve_tags(
            [
                "Binary Indexed Tree",
                "lowest common ancestor",
                "Fenwick Tree",
                "Chưa phân loại",
                "xyz",
            ]
        )
        self.assertEqual(ids, [a.id, b.id])
        self.assertEqual(names, ["fenwick tree", "lca"])
        self.assertEqual(unresolved, ["xyz"])
        ids, names, _ = resolve_tags(["chuaphanloai"])
        self.assertEqual(
            names, ["Chưa phân loại"]
        )  # alone it is kept, reviewer decides


class RenderTest(TestCase):
    def test_render_without_examples_still_validates_and_has_scoring(self):
        payload = dict(
            CONTENT_PAYLOAD, examples=[], scoring="- Subtask 1 (50%): $n \\le 10$."
        )
        md = format_v1.render(payload)
        r = format_v1.validate(md)
        self.assertEqual(r.label, "HEADINGS_OK")
        self.assertEqual(
            [h.name for h in r.headings], ["Input", "Output", "Scoring", "Example"]
        )
        self.assertIn("không có ví dụ", md)
        self.assertNotIn("\r", md)

    def test_render_multiline_sample_indentation(self):
        payload = dict(
            CONTENT_PAYLOAD,
            examples=[{"input": "3\n1 2 3\n", "output": "6", "note": None}],
        )
        md = format_v1.render(payload)
        self.assertIn("        ```sample\n        3\n        1 2 3\n        ```", md)
        self.assertNotIn("warning", md)
