import itertools
import json
import random
from hashlib import sha256
from io import StringIO

from django.contrib.auth.models import User
from django.core.management import CommandError, call_command
from django.test import TestCase
from django.utils import timezone

from judge.models import (
    Contest,
    ContestProblem,
    Language,
    Problem,
    ProblemGroup,
    ProblemType,
    Profile,
)
from judge.models.standardization import (
    StandardizationBatch,
    ProblemSnapshot,
    StandardizationAuditSample,
    StandardizationDecision,
    StandardizationPolicy,
    StandardizationProposal,
    StandardizationVerification,
)
from judge.review.hashing import compute_input_hash
from judge.standardization.hashing import compute_judge_hash
from judge.standardization import format_v1, policy as policy_mod
from judge.standardization.apply import restore_proposal, review_proposal
from judge.standardization.enums import (
    AuditVerdict,
    DecisionAction,
    FieldGroup,
    ProposalStatus,
    VerificationLevel,
)
from judge.standardization.hashing import compute_source_hash, dedupe_key
from judge.standardization.snapshot import capture_all

CONTENT_PAYLOAD = {
    "statement": "Cho hai số nguyên $a, b$.\n\n**Yêu cầu:** tính tổng.",
    "input": "- Một dòng chứa $a, b$.",
    "output": "- Tổng $a + b$.",
    "scoring": "",
    "examples": [{"input": "1 2", "output": "3", "note": ""}],
    "warnings": [],
    "unverified_spans": [],
    "source_readable": True,
}
PROMPT = "std_prompts_v1"


class PolicyTest(TestCase):
    fixtures = ["language_small"]

    @classmethod
    def setUpTestData(cls):
        cls.group = ProblemGroup.objects.create(name="stdp", full_name="std policy")
        cls.lang = Language.get_python3()
        cls.reviewer = Profile.objects.create(
            user=User.objects.create_user("polreviewer", "p@x.com", "x"),
            language=cls.lang,
        )
        cls.tag_dp = ProblemType.objects.create(name="dp", full_name="DP")

    def make_problem(self, code, public=False, points=100):
        return Problem.objects.create(
            code=code,
            name=code,
            description="Đề cũ.",
            group=self.group,
            time_limit=1,
            memory_limit=262144,
            points=points,
            partial=True,
            is_public=public,
        )

    _seq = itertools.count()

    def proposal(self, problem, group, payload, evidence=None, status=None, step=None):
        step = step or f"s{next(self._seq)}"  # unique dedupe key per proposal
        problem = Problem.objects.get(pk=problem.pk)
        snap = ProblemSnapshot.objects.create(
            problem=problem,
            source_hash=compute_source_hash(problem),
            before_json=capture_all(problem),
        )
        return StandardizationProposal.objects.create(
            snapshot=snap,
            problem=problem,
            field_group=group,
            step=step,
            prompt_version=PROMPT,
            dedupe_key=dedupe_key(problem.id, snap.source_hash, group, step, PROMPT),
            status=status or ProposalStatus.PENDING_REVIEW,
            payload_json=payload,
            evidence_json=evidence or {},
        )

    def content_proposal(self, problem, **overrides):
        structured = {**CONTENT_PAYLOAD, **overrides}
        markdown = format_v1.render(structured)
        evidence = {
            "classification": {"quality_flags": []},
            "validation": format_v1.validate(markdown).label,
            "warnings": structured["warnings"],
            "unverified_spans": structured["unverified_spans"],
            "source_readable": structured["source_readable"],
        }
        return self.proposal(
            problem,
            FieldGroup.CONTENT,
            {"description": markdown, "structured": structured},
            evidence,
        )

    def tags_proposal(self, problem, confidence="high", names=None):
        return self.proposal(
            problem,
            FieldGroup.TAGS,
            {"type_ids": [self.tag_dp.id], "names": names or ["DP"], "unresolved": []},
            {
                "classification": {"quality_flags": []},
                "confidence": confidence,
                "reasoning": "quy hoạch động 1 chiều",
                "unresolved": [],
            },
        )

    def points_proposal(self, problem, confidence="high", rating=1500):
        return self.proposal(
            problem,
            FieldGroup.POINTS,
            {"points": rating, "confidence": confidence},
            step="tp",  # calibration is per (group, prompt version, step)
            evidence={
                "classification": {"quality_flags": []},
                "confidence": confidence,
                "reasoning": "tương đương Div2 C",
                "cf_source_url": "",
                "insufficient_evidence": False,
                "in_range": True,
            },
        )

    def policy(self, groups, **kw):
        defaults = dict(
            version=f"test_{'_'.join(groups)}_{random.randrange(10**6)}",
            name="t",
            is_enabled=True,
            groups_json=list(groups),
            daily_cap=100,
            sample_rate=1.0,
            audit_min_samples=2,
            audit_fail_threshold=0.4,
        )
        defaults.update(kw)
        return StandardizationPolicy.objects.create(**defaults)

    def held_reasons(self, ev):
        return {name for name, (ok, _) in ev.conditions.items() if not ok}

    # --- conditions -----------------------------------------------------------

    def test_content_passes_only_when_strict_and_clean(self):
        p = self.make_problem("stdp1")
        pol = self.policy([FieldGroup.CONTENT])
        ok = self.content_proposal(p)
        ev = policy_mod.evaluate(ok, pol)
        self.assertTrue(ev.passed, ev.reasons)
        self.assertEqual(set(ev.conditions), set(policy_mod.CONDITION_NAMES))

        # AC26/27: an open warning or an unverified span holds the draft even
        # though the statement itself validates STRICT_OK
        warned = self.content_proposal(p, warnings=["constraint n chưa rõ"])
        self.assertEqual(
            self.held_reasons(policy_mod.evaluate(warned, pol)), {"source"}
        )
        spans = self.content_proposal(p, unverified_spans=["1 ≤ n ≤ ?"])
        self.assertEqual(self.held_reasons(policy_mod.evaluate(spans, pol)), {"source"})
        # no examples -> HEADINGS_OK only -> validator holds it
        noex = self.content_proposal(p, examples=[])
        self.assertEqual(
            self.held_reasons(policy_mod.evaluate(noex, pol)), {"validators"}
        )
        # blocking classification flag
        flagged = self.content_proposal(p)
        flagged.evidence_json["classification"]["quality_flags"] = ["ocr_uncertain"]
        flagged.save()
        self.assertIn("source", self.held_reasons(policy_mod.evaluate(flagged, pol)))

    def test_finished_batch_can_apply_but_paused_batch_cannot(self):
        problem = self.make_problem("finished_batch")
        proposal = self.tags_proposal(problem)
        batch = StandardizationBatch.objects.create(
            name="finished",
            kind="ai_pipeline",
            id_from=problem.pk,
            id_through=problem.pk,
            status="finished",
        )
        proposal.batch = batch
        proposal.save(update_fields=["batch"])
        policy = self.policy([FieldGroup.TAGS])
        self.assertTrue(policy_mod.evaluate(proposal, policy).passed)
        batch.status = "paused"
        batch.save(update_fields=["status"])
        proposal = StandardizationProposal.objects.get(pk=proposal.pk)
        self.assertIn(
            "policy", self.held_reasons(policy_mod.evaluate(proposal, policy))
        )

    def test_automatic_sibling_changes_are_traced_but_external_changes_hold(self):
        problem = self.make_problem("sibling_chain")
        content = self.content_proposal(problem)
        tags = self.tags_proposal(problem)
        tags.snapshot = content.snapshot
        tags.save(update_fields=["snapshot"])
        policy = self.policy([FieldGroup.CONTENT, FieldGroup.TAGS])
        _, applied = policy_mod.auto_apply(content, policy)
        self.assertIsNotNone(applied)
        self.assertTrue(policy_mod.evaluate(tags, policy).passed)
        problem.refresh_from_db()
        problem.description += "\nExternal edit"
        problem.save(update_fields=["description"])
        ev = policy_mod.evaluate(tags, policy)
        self.assertIn("problem_unchanged", self.held_reasons(ev))

    def test_ai_formatting_can_auto_apply_only_with_recomputed_preservation(self):
        problem = self.make_problem("format_proof")
        statement = format_v1.render(CONTENT_PAYLOAD)
        problem.description = statement.replace("### Input", "#### Input")
        problem.save(update_fields=["description"])
        proposal = self.content_proposal(problem)
        proposal.snapshot.extracted_text = problem.description
        proposal.snapshot.save(update_fields=["extracted_text"])
        proposal.evidence_json.update(
            ai_rewrite=True, source_fidelity={"body": "matched", "samples": "matched"}
        )
        proposal.save(update_fields=["evidence_json"])
        policy = self.policy([FieldGroup.CONTENT])
        self.assertTrue(policy_mod.evaluate(proposal, policy).passed)
        proposal.payload_json["description"] = statement.replace(
            "tính tổng", "tính hiệu"
        )
        # Legacy provider proposals without v2 evidence meet the same proof.
        proposal.provider = "openrouter"
        proposal.evidence_json["ai_rewrite"] = False
        proposal.save(update_fields=["payload_json", "provider", "evidence_json"])
        self.assertIn(
            "validators", self.held_reasons(policy_mod.evaluate(proposal, policy))
        )

    def test_policy_scope_switches_and_caps(self):
        private, public = self.make_problem("stdp2"), self.make_problem(
            "stdp3", public=True
        )
        pol = self.policy([FieldGroup.TAGS])
        self.assertTrue(policy_mod.evaluate(self.tags_proposal(private), pol).passed)
        # public excluded by default
        ev = policy_mod.evaluate(self.tags_proposal(public), pol)
        self.assertIn("public problem", ev.reasons[0])
        pol.include_public = True
        self.assertTrue(policy_mod.evaluate(self.tags_proposal(public), pol).passed)
        # id range, group, step, kill switch, halted
        pol.id_through = private.id - 1
        self.assertIn(
            "policy",
            self.held_reasons(policy_mod.evaluate(self.tags_proposal(private), pol)),
        )
        pol.id_through = 0
        pol.groups_json = [FieldGroup.POINTS]
        self.assertIn(
            "policy",
            self.held_reasons(policy_mod.evaluate(self.tags_proposal(private), pol)),
        )
        pol.groups_json = [FieldGroup.TAGS]
        pol.steps_json = ["other"]
        self.assertIn(
            "policy",
            self.held_reasons(policy_mod.evaluate(self.tags_proposal(private), pol)),
        )
        pol.steps_json = []
        pol.is_enabled = False
        self.assertIn(
            "policy disabled",
            policy_mod.evaluate(self.tags_proposal(private), pol).reasons[0],
        )
        pol.is_enabled, pol.halted = True, True
        self.assertIn(
            "halted", policy_mod.evaluate(self.tags_proposal(private), pol).reasons[0]
        )
        pol.halted = False
        # a non-pending proposal is never auto-applied
        approved = review_proposal(
            self.tags_proposal(private), self.reviewer, DecisionAction.APPROVE
        )
        self.assertIn(
            "not pending review", policy_mod.evaluate(approved, pol).reasons[0]
        )

    def test_tags_confidence_and_catalogue(self):
        p = self.make_problem("stdp4")
        pol = self.policy([FieldGroup.TAGS])
        low = self.tags_proposal(p, confidence="medium")
        self.assertEqual(
            self.held_reasons(policy_mod.evaluate(low, pol)), {"calibrated"}
        )
        pol.thresholds_json = {"tags": {"min_confidence": "medium"}}
        self.assertTrue(policy_mod.evaluate(low, pol).passed)
        unresolved = self.tags_proposal(p)
        unresolved.payload_json["unresolved"] = ["xyz"]
        unresolved.save()
        self.assertEqual(
            self.held_reasons(policy_mod.evaluate(unresolved, pol)), {"validators"}
        )

    def test_points_require_human_calibration_not_model_confidence(self):
        p = self.make_problem("stdp5")
        pol = self.policy([FieldGroup.POINTS])
        prop = self.points_proposal(p, confidence="high")
        ev = policy_mod.evaluate(prop, pol)
        self.assertEqual(self.held_reasons(ev), {"calibrated"})
        self.assertIn("human-reviewed", ev.reasons[0])
        # three reviewed, two approved -> 67% < 80% default
        others = [
            self.points_proposal(self.make_problem(f"stdp5{i}")) for i in range(3)
        ]
        review_proposal(others[0], self.reviewer, DecisionAction.APPROVE)
        review_proposal(others[1], self.reviewer, DecisionAction.APPROVE)
        review_proposal(others[2], self.reviewer, DecisionAction.REJECT)
        pol.thresholds_json = {"points": {"calibration_min_reviewed": 3}}
        ev = policy_mod.evaluate(prop, pol)
        self.assertIn("approval 67%", ev.reasons[0])
        pol.thresholds_json = {
            "points": {"calibration_min_reviewed": 3, "calibration_min_approval": 0.6}
        }
        self.assertTrue(policy_mod.evaluate(prop, pol).passed)
        pol.thresholds_json["points"]["require_cf_source"] = True
        self.assertIn("Codeforces source", policy_mod.evaluate(prop, pol).reasons[0])
        pol.thresholds_json["points"] = {
            "calibration_min_reviewed": 3,
            "calibration_min_approval": 0.6,
            "max_abs_delta": 500,
        }
        self.assertEqual(
            self.held_reasons(policy_mod.evaluate(prop, pol)), {"validators"}
        )

    def test_solution_needs_current_tests_passed_on_current_hash(self):
        p = self.make_problem("stdp6")
        pol = self.policy([FieldGroup.SOLUTION])
        prop = self.proposal(
            p,
            FieldGroup.SOLUTION,
            {"content": "x", "cpp_code": "int main(){}", "language_key": "CPP17"},
            {"classification": {"quality_flags": []}, "confidence": "high"},
        )
        self.assertEqual(
            self.held_reasons(policy_mod.evaluate(prop, pol)), {"solution_verified"}
        )
        StandardizationVerification.objects.create(
            proposal=prop,
            level=VerificationLevel.COMPILED,
            test_config_hash=compute_input_hash(p),
        )
        self.assertEqual(
            self.held_reasons(policy_mod.evaluate(prop, pol)), {"solution_verified"}
        )
        # policy cannot lower the bar below current_tests_passed
        pol.thresholds_json = {"solution": {"min_level": "compiled"}}
        self.assertEqual(
            self.held_reasons(policy_mod.evaluate(prop, pol)), {"solution_verified"}
        )
        StandardizationVerification.objects.create(
            proposal=prop,
            level=VerificationLevel.CURRENT_TESTS_PASSED,
            test_config_hash="stale",
        )
        self.assertIn("older test/config", policy_mod.evaluate(prop, pol).reasons[0])
        StandardizationVerification.objects.create(
            proposal=prop,
            level=VerificationLevel.CURRENT_TESTS_PASSED,
            test_config_hash=compute_judge_hash(p, Language.objects.get(key="CPP17")),
            details_json={
                "reason": "full_score",
                "source_hash": sha256(b"int main(){}").hexdigest(),
            },
        )
        evaluation = policy_mod.evaluate(prop, pol)
        self.assertTrue(evaluation.passed, evaluation.reasons)

    def test_problem_changed_or_in_contest_is_held(self):
        p = self.make_problem("stdp7")
        pol = self.policy([FieldGroup.TAGS])
        prop = self.tags_proposal(p)
        p.description = "đổi đề"
        p.save()
        ev = policy_mod.evaluate(prop, pol)
        self.assertIn("changed since the draft", ev.reasons[0])
        fresh = self.tags_proposal(p)
        now = timezone.now()
        contest = Contest.objects.create(
            key="stdpc",
            name="c",
            start_time=now,
            end_time=now + timezone.timedelta(hours=2),
        )
        ContestProblem.objects.create(contest=contest, problem=p, points=1, order=1)
        self.assertIn("running contest", policy_mod.evaluate(fresh, pol).reasons[0])

    # --- acting ---------------------------------------------------------------

    def test_sweep_applies_records_samples_and_breaker_halts(self):
        pol = self.policy([FieldGroup.TAGS], daily_cap=2)
        problems = [self.make_problem(f"stdp8{i}") for i in range(3)]
        props = [self.tags_proposal(p) for p in problems]
        held = self.tags_proposal(self.make_problem("stdp8x"), confidence="low")

        tally = policy_mod.sweep(pol)
        self.assertEqual(len(tally["applied"]), 2)  # daily cap
        self.assertEqual(tally.get("stopped"), "cap reached")
        applied = StandardizationProposal.objects.filter(pk__in=tally["applied"])
        self.assertTrue(all(a.status == ProposalStatus.APPLIED for a in applied))
        for a in applied:
            acts = list(
                a.decisions.order_by("id").values_list("action", "policy_version")
            )
            self.assertEqual(
                acts,
                [
                    (DecisionAction.AUTO_ACCEPT, pol.version),
                    (DecisionAction.APPLY, pol.version),
                ],
            )
            self.assertEqual(
                sorted(a.problem.types.values_list("id", flat=True)), [self.tag_dp.id]
            )
        self.assertEqual(
            StandardizationAuditSample.objects.filter(policy=pol).count(), 2
        )
        pol.refresh_from_db()
        self.assertEqual(pol.applied_count, 2)
        self.assertEqual(policy_mod.applied_today(pol), 2)
        # the held one carries its reasons on the proposal
        held.refresh_from_db()
        self.assertEqual(held.status, ProposalStatus.PENDING_REVIEW)
        if str(held.pk) in tally["held"]:
            self.assertFalse(held.evidence_json["auto_apply"]["passed"])
        # next sweep today: cap reached, nothing more applied
        self.assertEqual(policy_mod.sweep(pol)["applied"], [])

        # audit: one wrong of two -> 50% > 40% -> halted; sweeping is then a no-op
        samples = list(
            StandardizationAuditSample.objects.filter(policy=pol).order_by("id")
        )
        _, halted = policy_mod.record_audit(samples[0], AuditVerdict.OK, self.reviewer)
        self.assertFalse(halted)
        _, halted = policy_mod.record_audit(
            samples[1], AuditVerdict.WRONG, self.reviewer, note="sai tag"
        )
        self.assertTrue(halted)
        pol.refresh_from_db()
        self.assertTrue(pol.halted)
        self.assertIn("1/2 wrong", pol.halted_reason)
        pol.daily_cap = 100
        pol.save()
        self.assertEqual(
            policy_mod.sweep(pol).get("skipped", "")[:15], "policy inactive"
        )
        policy_mod.resume(pol, self.reviewer)
        self.assertFalse(StandardizationPolicy.objects.get(pk=pol.pk).halted)

    def test_restored_auto_apply_counts_as_wrong(self):
        pol = self.policy(
            [FieldGroup.TAGS], audit_min_samples=1, audit_fail_threshold=0.0
        )
        prop = self.tags_proposal(self.make_problem("stdp9"))
        ev, applied = policy_mod.auto_apply(prop, pol)
        self.assertTrue(ev.passed)
        self.assertIsNotNone(applied)
        restore_proposal(applied, self.reviewer)
        self.assertTrue(policy_mod.check_breaker(pol))
        self.assertEqual(policy_mod.audit_stats(pol)["wrong"], 1)

    def test_apply_refusal_is_reported_not_raised(self):
        pol = self.policy([FieldGroup.TAGS])
        p = self.make_problem("stdp10")
        prop = self.tags_proposal(p)
        # pass evaluation, then make the group stale before apply runs
        original = policy_mod.evaluate

        def evaluate_then_edit(proposal, policy, problem=None):
            ev = original(proposal, policy, problem)
            p.types.add(ProblemType.objects.create(name="greedy2", full_name="G"))
            return ev

        policy_mod.evaluate, saved = evaluate_then_edit, policy_mod.evaluate
        try:
            ev, applied = policy_mod.auto_apply(prop, pol)
        finally:
            policy_mod.evaluate = saved
        self.assertIsNone(applied)
        self.assertIn("apply refused", ev.reasons[-1])
        prop.refresh_from_db()
        self.assertEqual(prop.status, ProposalStatus.STALE)
        self.assertFalse(
            StandardizationAuditSample.objects.filter(proposal=prop).exists()
        )

    # --- command ---------------------------------------------------------------

    def run_cmd(self, *args):
        out = StringIO()
        call_command("standardize_policy", *args, stdout=out)
        lines = out.getvalue().strip().splitlines()
        return json.loads(lines[-1]), lines

    def test_command_end_to_end(self):
        p = self.make_problem("stdp11")
        prop = self.tags_proposal(p)
        with self.assertRaises(CommandError):
            self.run_cmd("create", "--policy", "cmd_v1", "--groups", "tests")
        row, lines = self.run_cmd(
            "create",
            "--policy",
            "cmd_v1",
            "--groups",
            "tags",
            "--sample-rate",
            "1",
            "--thresholds",
            '{"tags": {"min_confidence": "medium"}}',
            "--actor",
            "polreviewer",
        )
        self.assertFalse(row["enabled"])
        self.assertEqual(row["thresholds"]["tags"]["min_confidence"], "medium")
        with self.assertRaises(CommandError):
            self.run_cmd("create", "--policy", "cmd_v1", "--groups", "tags")
        ev, lines = self.run_cmd(
            "evaluate", "--policy", "cmd_v1", "--proposal-id", str(prop.pk)
        )
        self.assertFalse(ev["passed"])
        self.assertTrue(any("HOLD policy" in l for l in lines))
        self.run_cmd("enable", "--policy", "cmd_v1")
        dry, _ = self.run_cmd("sweep", "--policy", "cmd_v1", "--dry-run")
        self.assertEqual(dry[0]["would_apply"], [prop.pk])
        self.assertEqual(
            StandardizationProposal.objects.get(pk=prop.pk).status,
            ProposalStatus.PENDING_REVIEW,
        )
        real, _ = self.run_cmd("sweep", "--policy", "cmd_v1")
        self.assertEqual(real[0]["applied"], [prop.pk])
        audit, _ = self.run_cmd("audit", "--policy", "cmd_v1")
        self.assertEqual(len(audit["pending"]), 1)
        res, _ = self.run_cmd(
            "audit",
            "--sample-id",
            str(audit["pending"][0]["sample"]),
            "--verdict",
            "ok",
            "--actor",
            "polreviewer",
        )
        self.assertFalse(res["policy_halted"])
        listed, _ = self.run_cmd("list")
        self.assertEqual(listed[0]["applied_count"], 1)
        self.assertEqual(listed[0]["audit"]["audited"], 1)
        self.run_cmd("disable", "--policy", "cmd_v1")
        self.assertFalse(StandardizationPolicy.objects.get(version="cmd_v1").is_enabled)
        self.assertEqual(
            StandardizationDecision.objects.filter(
                proposal=prop, action=DecisionAction.AUTO_ACCEPT
            ).count(),
            1,
        )
