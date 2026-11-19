"""Auto-apply policy evaluator (spec §7.2, PR-H).

A ``StandardizationPolicy`` says which field groups may be applied without a
per-problem human review, for which problems, under which thresholds and how
many per day. ``evaluate`` checks the seven conditions of §7.2 for one
proposal against one policy and returns every condition's outcome, pass or
fail, so the review page can show *why* a proposal was held back. ``auto_apply``
turns a passing evaluation into an AUTO_ACCEPT decision followed by the same
audited ``apply_proposal`` a human apply uses; ``sweep`` does that for every
PENDING_REVIEW proposal in scope, up to the daily cap.

A failing condition never lowers the bar: the proposal stays PENDING_REVIEW
with the reasons written into ``evidence_json["auto_apply"]``. Auto-apply
never deletes or merges problems, never publishes a Solution, never touches
tests or checkers (none of those are field groups here).

Post-apply audit: a fraction of auto-applied proposals becomes a
``StandardizationAuditSample``. A human marks each OK or WRONG; a proposal
that gets RESTORED counts as wrong on its own. When the wrong fraction over
the most recent audited samples passes ``audit_fail_threshold`` (after at
least ``audit_min_samples``), the policy is halted and everything falls back
to manual review until an admin resumes it.

Thresholds (``thresholds_json``), all optional, per group key:

    content:  {"min_validation": "STRICT_OK"|"HEADINGS_OK"}
    tags:     {"min_confidence": "high"}
    points:   {"min_confidence": "high", "require_cf_source": false,
               "max_abs_delta": null, "calibration_min_reviewed": 30,
               "calibration_min_approval": 0.8}
    solution: {"min_level": "current_tests_passed"}
"""

import logging
import random
from dataclasses import dataclass, field

from django.conf import settings
from django.db import transaction
from django.db.models import Case, Count, IntegerField, Q, Value, When
from django.utils import timezone

from judge.models import Problem
from judge.models.standardization import (
    StandardizationAuditSample,
    StandardizationDecision,
    StandardizationPolicy,
    StandardizationProposal,
)
from judge.standardization.verification import current_full_verification
from judge.standardization import config_profile, format_v1
from judge.standardization.apply import (
    StandardizationError,
    _record,
    apply_proposal,
    is_in_live_contest,
    transition,
)
from judge.standardization.budget import BudgetExceeded, batch_is_open, check_budget
from judge.standardization.enums import (
    AuditVerdict,
    DecisionAction,
    FieldGroup,
    ProposalStatus,
    QualityFlag,
    VerificationLevel,
)
from judge.standardization.hashing import compute_source_hash
from judge.standardization.integrity import can_auto_rewrite
from judge.standardization.tag_aliases import UNCLASSIFIED

logger = logging.getLogger(__name__)

CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2}
VALIDATION_RANK = {"INVALID": 0, "HEADINGS_OK": 1, "STRICT_OK": 2}
LEVEL_RANK = {
    VerificationLevel.UNVERIFIED: 0,
    VerificationLevel.FAILED: 0,
    VerificationLevel.UNKNOWN: 0,
    VerificationLevel.COMPILED: 1,
    VerificationLevel.SAMPLE_PASSED: 2,
    VerificationLevel.CURRENT_TESTS_PASSED: 3,
    VerificationLevel.ADDITIONAL_CHECKS_PASSED: 4,
}
# Source flags that make a draft unsafe to apply unseen (§7.2 condition 2).
BLOCKING_FLAGS = {
    QualityFlag.UNREADABLE,
    QualityFlag.OCR_UNCERTAIN,
    QualityFlag.CONTRADICTION,
    QualityFlag.NEEDS_EXPERT,
    QualityFlag.BROKEN_ASSET,
    QualityFlag.STATEMENT_MISSING,
    QualityFlag.PLACEHOLDER,
}
DEFAULT_THRESHOLDS = {
    FieldGroup.CONTENT: {"min_validation": "STRICT_OK"},
    FieldGroup.TAGS: {"min_confidence": "high"},
    FieldGroup.POINTS: {
        "min_confidence": "high",
        "require_cf_source": False,
        "max_abs_delta": None,
        "calibration_min_reviewed": 30,
        "calibration_min_approval": 0.8,
    },
    FieldGroup.SOLUTION: {"min_level": VerificationLevel.CURRENT_TESTS_PASSED},
    FieldGroup.CONFIG: {},
}
# Groups a policy is ever allowed to name. Everything else is a separate
# process by spec (delete/merge, publish Solutions, tests, checkers).
POLICY_GROUPS = tuple(DEFAULT_THRESHOLDS)

CONDITION_NAMES = (
    "policy",
    "source",
    "validators",
    "solution_verified",
    "calibrated",
    "config_resolvable",
    "problem_unchanged",
)


@dataclass
class Evaluation:
    policy_version: str
    proposal_id: int
    group: str
    conditions: dict = field(default_factory=dict)  # name -> (ok, detail)

    def fail(self, name, detail):
        self.conditions[name] = (False, detail)

    def ok(self, name, detail="ok"):
        self.conditions.setdefault(name, (True, detail))

    @property
    def passed(self):
        return all(ok for ok, _ in self.conditions.values())

    @property
    def reasons(self):
        return [f"{n}: {d}" for n, (ok, d) in self.conditions.items() if not ok]

    def as_json(self):
        return {
            "policy_version": self.policy_version,
            "passed": self.passed,
            "conditions": {
                n: {"ok": ok, "detail": d} for n, (ok, d) in self.conditions.items()
            },
            "evaluated_at": timezone.now().isoformat(),
        }


def thresholds_for(policy, group):
    merged = dict(DEFAULT_THRESHOLDS.get(FieldGroup(group), {}))
    merged.update((policy.thresholds_json or {}).get(str(group), {}))
    return merged


# --- counters ---------------------------------------------------------------


def applied_today(policy):
    start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
    return StandardizationDecision.objects.filter(
        action=DecisionAction.AUTO_ACCEPT,
        policy_version=policy.version,
        created_at__gte=start,
    ).count()


def calibration(group, prompt_version, step="", model=""):
    """Human review outcomes for proposals like this one: how many were
    reviewed and what fraction was approved. Pulled from decision rows, so
    it reflects real reviewer behaviour rather than the model's own
    confidence (§7.2 condition 5)."""
    qs = StandardizationDecision.objects.filter(
        proposal__field_group=group,
        proposal__prompt_version=prompt_version,
        action__in=(DecisionAction.APPROVE, DecisionAction.REJECT),
    )
    if step:
        qs = qs.filter(proposal__step=step)
    if model:
        qs = qs.filter(proposal__model=model)
    counts = dict(
        qs.values_list("action").annotate(c=Count("id")).values_list("action", "c")
    )
    approved = counts.get(DecisionAction.APPROVE, 0)
    reviewed = approved + counts.get(DecisionAction.REJECT, 0)
    return reviewed, (approved / reviewed if reviewed else 0.0)


# --- the seven conditions ----------------------------------------------------


def _cond_policy(ev, policy, proposal, problem):
    if (proposal.evidence_json or {}).get("guided_workflow") and not getattr(
        settings, "STANDARDIZATION_GUIDED_AUTO_APPLY", False
    ):
        return ev.fail("policy", "guided workflow is scheduled for manual review")
    if not policy.is_enabled:
        return ev.fail("policy", "policy disabled")
    if policy.halted:
        return ev.fail("policy", f"policy halted: {policy.halted_reason or 'audit'}")
    group = proposal.field_group
    if group not in (policy.groups_json or []):
        return ev.fail("policy", f"group {group} not in policy")
    if group not in POLICY_GROUPS:
        return ev.fail("policy", f"group {group} can never be auto-applied")
    if policy.steps_json and proposal.step not in policy.steps_json:
        return ev.fail("policy", f"step {proposal.step} not in policy")
    if (
        policy.prompt_versions_json
        and proposal.prompt_version not in policy.prompt_versions_json
    ):
        return ev.fail(
            "policy", f"prompt version {proposal.prompt_version} not in policy"
        )
    if problem.id < policy.id_from or (
        policy.id_through and problem.id > policy.id_through
    ):
        return ev.fail("policy", "problem outside policy id range")
    if policy.batch_id and proposal.batch_id != policy.batch_id:
        return ev.fail("policy", "proposal not from the policy's batch")
    if problem.is_public and not policy.include_public:
        return ev.fail("policy", "public problem; policy covers private only")
    if proposal.status != ProposalStatus.PENDING_REVIEW:
        return ev.fail("policy", f"proposal is {proposal.status}, not pending review")
    if (
        proposal.batch_id
        and not batch_is_open(proposal.batch)
        and proposal.batch.status != "finished"
    ):
        return ev.fail("policy", f"drafting batch is {proposal.batch.status}")
    today = applied_today(policy)
    if today >= policy.daily_cap:
        return ev.fail("policy", f"daily cap reached ({today}/{policy.daily_cap})")
    if policy.total_cap is not None and policy.applied_count >= policy.total_cap:
        return ev.fail(
            "policy", f"total cap reached ({policy.applied_count}/{policy.total_cap})"
        )
    ev.ok("policy")


def _cond_source(ev, policy, proposal, problem):
    evidence = proposal.evidence_json or {}
    flags = set((evidence.get("classification") or {}).get("quality_flags") or [])
    blocking = sorted(flags & {f.value for f in BLOCKING_FLAGS})
    if blocking:
        return ev.fail("source", f"quality flags {blocking}")
    if proposal.field_group == FieldGroup.CONTENT:
        if evidence.get("source_readable") is False:
            return ev.fail("source", "model reported the source unreadable")
        if evidence.get("warnings"):
            return ev.fail("source", f"{len(evidence['warnings'])} open warning(s)")
        if evidence.get("unverified_spans"):
            return ev.fail(
                "source", f"{len(evidence['unverified_spans'])} unverified span(s)"
            )
    if proposal.field_group == FieldGroup.POINTS and evidence.get(
        "insufficient_evidence"
    ):
        return ev.fail("source", "model reported insufficient evidence for a rating")
    ev.ok("source")


def _cond_validators(ev, policy, proposal, problem):
    group = FieldGroup(proposal.field_group)
    th = thresholds_for(policy, group)
    payload = proposal.payload_json or {}
    evidence = proposal.evidence_json or {}
    if group == FieldGroup.CONTENT:
        if (
            evidence.get("guided_rewrite")
            or evidence.get("ai_rewrite")
            or proposal.provider not in ("", "rules")
        ):
            if not can_auto_rewrite(
                proposal.snapshot.extracted_text,
                payload.get("description", ""),
                payload.get("structured") or {},
            ) or evidence.get("integrity_errors"):
                return ev.fail(
                    "validators", "statement preservation is not independently proven"
                )
        result = format_v1.validate(payload.get("description", ""))
        need = VALIDATION_RANK.get(th.get("min_validation", "STRICT_OK"), 2)
        if VALIDATION_RANK[result.label] < need:
            return ev.fail(
                "validators",
                f"statement validates {result.label}, policy needs "
                f"{th.get('min_validation', 'STRICT_OK')}: {'; '.join(result.errors) or 'no admonitions'}",
            )
        structured = payload.get("structured") or {}
        if structured and not structured.get("examples"):
            return ev.fail("validators", "no examples carried from the source")
    elif group == FieldGroup.TAGS:
        ids = payload.get("type_ids") or []
        if not ids:
            return ev.fail("validators", "no catalogue tag resolved")
        if payload.get("unresolved") or evidence.get("unresolved"):
            return ev.fail(
                "validators",
                f"unresolved tags {payload.get('unresolved') or evidence.get('unresolved')}",
            )
        if set(payload.get("names") or []) <= {UNCLASSIFIED}:
            return ev.fail("validators", "only the unclassified tag")
    elif group == FieldGroup.POINTS:
        if evidence.get("in_range") is False:
            return ev.fail("validators", "rating outside the CF range")
        try:
            new = float(payload["points"])
        except (KeyError, TypeError, ValueError):
            return ev.fail("validators", "payload has no numeric points")
        max_delta = th.get("max_abs_delta")
        if max_delta is not None and abs(new - float(problem.points)) > float(
            max_delta
        ):
            return ev.fail(
                "validators",
                f"|delta| {abs(new - float(problem.points)):g} > {max_delta}",
            )
    elif group == FieldGroup.SOLUTION:
        if not (payload.get("cpp_code") or "").strip():
            return ev.fail("validators", "no reference code in payload")
        if evidence.get("guided"):
            structured = payload.get("structured") or {}
            if (
                evidence.get("needs_clarification")
                or evidence.get("solution_quality_errors")
                or any(
                    not (structured.get(name) or "").strip()
                    for name in (
                        "summary",
                        "algorithm",
                        "correctness",
                        "complexity_time",
                        "complexity_memory",
                    )
                )
            ):
                return ev.fail(
                    "validators", "editorial is incomplete or still ambiguous"
                )
    ev.ok("validators")


def _cond_solution_verified(ev, policy, proposal, problem):
    evidence = proposal.evidence_json or {}
    if evidence.get("guided") and evidence.get("solution_proposal_id"):
        solution = StandardizationProposal.objects.filter(
            pk=evidence["solution_proposal_id"],
            problem=problem,
            field_group=FieldGroup.SOLUTION,
        ).first()
        if not solution or not current_full_verification(solution, problem):
            return ev.fail(
                "solution_verified", "linked solution has no current full verification"
            )
    if proposal.field_group != FieldGroup.SOLUTION:
        return ev.ok("solution_verified", "n/a")
    th = thresholds_for(policy, FieldGroup.SOLUTION)
    row = proposal.verifications.order_by("-created_at", "-pk").first()
    level = row.level if row else VerificationLevel.UNVERIFIED
    need = LEVEL_RANK.get(
        th.get("min_level", VerificationLevel.CURRENT_TESTS_PASSED), 3
    )
    if need < LEVEL_RANK[VerificationLevel.CURRENT_TESTS_PASSED]:
        need = LEVEL_RANK[
            VerificationLevel.CURRENT_TESTS_PASSED
        ]  # spec: compile-only is never enough
    if LEVEL_RANK.get(level, 0) < need:
        return ev.fail("solution_verified", f"verification level is {level}")
    if not current_full_verification(proposal, problem):
        return ev.fail(
            "solution_verified", "verified against an older test/config version"
        )
    ev.ok("solution_verified", level)


def _cond_calibrated(ev, policy, proposal, problem):
    group = FieldGroup(proposal.field_group)
    th = thresholds_for(policy, group)
    evidence = proposal.evidence_json or {}
    if (
        group == FieldGroup.POINTS
        and proposal.provider == "rules"
        and evidence.get("source_confirmed_by")
        and evidence.get("cf_source_url")
        and evidence.get("official_rating")
        == (proposal.payload_json or {}).get("points")
    ):
        return ev.ok("calibrated", "official Codeforces rating from confirmed source")
    if group in (FieldGroup.TAGS, FieldGroup.POINTS):
        conf = evidence.get("confidence") or (proposal.payload_json or {}).get(
            "confidence"
        )
        need = th.get("min_confidence", "high")
        if CONFIDENCE_RANK.get(conf, -1) < CONFIDENCE_RANK.get(need, 2):
            return ev.fail("calibrated", f"confidence {conf!r} below {need!r}")
        if not (evidence.get("reasoning") or "").strip():
            return ev.fail("calibrated", "no reasoning recorded")
    if group == FieldGroup.POINTS:
        if (
            th.get("require_cf_source")
            and not (evidence.get("cf_source_url") or "").strip()
        ):
            return ev.fail("calibrated", "no Codeforces source for the rating")
        reviewed, approval = calibration(
            group, proposal.prompt_version, proposal.step, proposal.model
        )
        min_reviewed = int(th.get("calibration_min_reviewed", 30))
        min_approval = float(th.get("calibration_min_approval", 0.8))
        if reviewed < min_reviewed:
            return ev.fail(
                "calibrated",
                f"only {reviewed} human-reviewed {group} proposals for prompt "
                f"{proposal.prompt_version!r}; policy needs {min_reviewed}",
            )
        if approval < min_approval:
            return ev.fail(
                "calibrated",
                f"reviewer approval {approval:.0%} below {min_approval:.0%}",
            )
        return ev.ok("calibrated", f"{reviewed} reviewed, {approval:.0%} approved")
    ev.ok("calibrated")


def _cond_config_resolvable(ev, policy, proposal, problem):
    if proposal.field_group != FieldGroup.CONFIG:
        return ev.ok("config_resolvable", "n/a")
    try:
        config_profile.resolve_context()
    except Exception as exc:  # noqa: BLE001 - any resolution failure holds the group
        return ev.fail("config_resolvable", f"{type(exc).__name__}: {exc}")
    ev.ok("config_resolvable")


def _cond_problem_unchanged(ev, policy, proposal, problem):
    snapshot = proposal.snapshot
    if snapshot is None or not snapshot.before_json:
        return ev.fail("problem_unchanged", "no before-snapshot to restore from")
    current_hash = compute_source_hash(problem)
    matches = current_hash == snapshot.source_hash
    if not matches:
        # Only an audited automatic sibling write can advance this snapshot.
        # Any external edit/test change produces a different full hash and holds it.
        matches = StandardizationProposal.objects.filter(
            problem=problem,
            snapshot=snapshot,
            status=ProposalStatus.APPLIED,
            evidence_json__auto_apply_after_hash=current_hash,
        ).exists()
    if not matches:
        return ev.fail(
            "problem_unchanged", "problem source/config changed since the draft"
        )
    if is_in_live_contest(problem):
        return ev.fail("problem_unchanged", "problem is in a running contest")
    try:
        check_budget(problem=problem, batch=proposal.batch)
    except BudgetExceeded as exc:
        return ev.fail("problem_unchanged", f"budget exceeded ({exc.scope})")
    ev.ok("problem_unchanged")


CONDITIONS = (
    _cond_policy,
    _cond_source,
    _cond_validators,
    _cond_solution_verified,
    _cond_calibrated,
    _cond_config_resolvable,
    _cond_problem_unchanged,
)


def evaluate(proposal, policy, problem=None):
    """Run all seven conditions (none short-circuits, so the report is complete)."""
    problem = problem or Problem.objects.get(pk=proposal.problem_id)
    ev = Evaluation(policy.version, proposal.pk, proposal.field_group)
    for cond in CONDITIONS:
        try:
            cond(ev, policy, proposal, problem)
        except (
            Exception
        ) as exc:  # noqa: BLE001 - an evaluator crash is a failed condition
            logger.exception(
                "policy condition %s crashed on proposal %s", cond.__name__, proposal.pk
            )
            ev.fail(
                cond.__name__.removeprefix("_cond_"),
                f"evaluator error {type(exc).__name__}: {exc}",
            )
    return ev


# --- acting on an evaluation -------------------------------------------------


def _note_evaluation(proposal, ev):
    evidence = dict(proposal.evidence_json or {})
    evidence["auto_apply"] = ev.as_json()
    StandardizationProposal.objects.filter(pk=proposal.pk).update(
        evidence_json=evidence
    )
    proposal.evidence_json = evidence


def _maybe_sample(policy, proposal, rng):
    if policy.sample_rate <= 0:
        return None
    if policy.sample_rate < 1 and rng.random() >= policy.sample_rate:
        return None
    sample, _ = StandardizationAuditSample.objects.get_or_create(
        proposal=proposal, defaults={"policy": policy}
    )
    return sample


@transaction.atomic
def auto_apply(proposal, policy, *, rng=random):
    """Evaluate; on pass, AUTO_ACCEPT + apply + maybe sample. Returns
    (evaluation, applied_proposal_or_None). Refusals inside apply (a stale
    group, a contest) are recorded by the apply layer and reported here."""
    problem = Problem.objects.select_for_update().get(pk=proposal.problem_id)
    # Serialize caps/counters across workers; evaluate the locked current rows,
    # not a stale policy instance or a proposal checked before the transaction.
    type(policy).objects.select_for_update().get(pk=policy.pk)
    policy.refresh_from_db()
    proposal = StandardizationProposal.objects.select_for_update().get(pk=proposal.pk)
    ev = evaluate(proposal, policy, problem)
    _note_evaluation(proposal, ev)
    if not ev.passed:
        return ev, None
    with transaction.atomic():
        locked = StandardizationProposal.objects.select_for_update().get(pk=proposal.pk)
        if locked.status != ProposalStatus.PENDING_REVIEW:
            ev.fail("policy", f"proposal became {locked.status} meanwhile")
            return ev, None
        transition(locked, ProposalStatus.AUTO_ACCEPTED)
        _record(
            locked,
            DecisionAction.AUTO_ACCEPT,
            None,
            policy_version=policy.version,
            conditions_json=ev.as_json(),
            note=f"auto-accepted by policy {policy.version}",
        )
    try:
        applied = apply_proposal(
            locked,
            None,
            policy_version=policy.version,
            conditions=ev.as_json(),
            note=f"auto-applied by policy {policy.version}",
        )
    except StandardizationError as exc:
        ev.fail("problem_unchanged", f"apply refused: {exc}")
        _note_evaluation(locked, ev)
        return ev, None
    StandardizationPolicy.objects.filter(pk=policy.pk).update(
        applied_count=policy.applied_count + 1
    )
    policy.applied_count += 1
    evidence = dict(applied.evidence_json or {})
    evidence["auto_apply_after_hash"] = compute_source_hash(
        Problem.objects.get(pk=problem.pk)
    )
    StandardizationProposal.objects.filter(pk=applied.pk).update(evidence_json=evidence)
    applied.evidence_json = evidence
    _maybe_sample(policy, applied, rng)
    return ev, applied


def candidates(policy, *, limit=None):
    """PENDING_REVIEW proposals inside the policy's static scope, newest per
    (problem, group). The dynamic conditions run in ``evaluate``."""
    qs = StandardizationProposal.objects.filter(
        status=ProposalStatus.PENDING_REVIEW,
        field_group__in=[g for g in (policy.groups_json or []) if g in POLICY_GROUPS],
    ).select_related("problem", "snapshot", "batch")
    if policy.id_from:
        qs = qs.filter(problem_id__gte=policy.id_from)
    if policy.id_through:
        qs = qs.filter(problem_id__lte=policy.id_through)
    if policy.batch_id:
        qs = qs.filter(batch_id=policy.batch_id)
    if policy.steps_json:
        qs = qs.filter(step__in=policy.steps_json)
    if policy.prompt_versions_json:
        qs = qs.filter(prompt_version__in=policy.prompt_versions_json)
    if not policy.include_public:
        qs = qs.filter(problem__is_public=False)
    picked, seen = [], set()
    # Apply target configuration first; code can then be rechecked once under
    # those limits before publishing its editorial/tags/rating.
    qs = qs.annotate(
        _group_order=Case(
            *[
                When(field_group=str(group), then=Value(index))
                for index, group in enumerate(
                    (
                        FieldGroup.CONFIG,
                        FieldGroup.CONTENT,
                        FieldGroup.SOLUTION,
                        FieldGroup.TAGS,
                        FieldGroup.POINTS,
                    )
                )
            ],
            output_field=IntegerField(),
        )
    )
    for p in qs.order_by("problem_id", "_group_order", "-created_at", "-id").iterator(
        chunk_size=500
    ):
        key = (p.problem_id, p.field_group)
        if key in seen:
            continue
        seen.add(key)
        picked.append(p)
        if limit is not None and len(picked) >= limit:
            break
    return picked


def refresh_guided_verification(proposal, policy):
    """Rejudge stored code after an explained sibling change; never call AI.

    This runs outside row locks. auto_apply re-evaluates everything under locks
    afterwards, so a source edit during judging still cannot be applied.
    """
    if not (proposal.evidence_json or {}).get("guided"):
        return
    ev = evaluate(proposal, policy)
    failures = {name for name, (ok, _) in ev.conditions.items() if not ok}
    if failures != {"solution_verified"}:
        return
    solution = (
        proposal
        if proposal.field_group == FieldGroup.SOLUTION
        else StandardizationProposal.objects.filter(
            pk=proposal.evidence_json.get("solution_proposal_id"),
            problem_id=proposal.problem_id,
            field_group=FieldGroup.SOLUTION,
        ).first()
    )
    if (
        not solution
        or not solution.verifications.filter(
            level=VerificationLevel.CURRENT_TESTS_PASSED
        ).exists()
    ):
        return
    # One attempt per sweep window, bounded after an unavailable judge.
    latest = solution.verifications.order_by("-created_at", "-pk").first()
    if latest and latest.level == VerificationLevel.UNKNOWN:
        if (timezone.now() - latest.created_at).total_seconds() < 300:
            return
        if solution.verifications.filter(level=VerificationLevel.UNKNOWN).count() >= 3:
            return
    from judge.standardization.verification import verify_solution_proposal

    verify_solution_proposal(solution)


def sweep(policy, *, limit=None, rng=random):
    """Evaluate every candidate; apply the passing ones until the cap. Returns a tally."""
    tally = {
        "policy": policy.version,
        "candidates": 0,
        "applied": [],
        "held": {},
        "refused": {},
    }
    if policy.is_active and check_breaker(policy):
        policy.refresh_from_db()
    if not policy.is_active:
        tally["skipped"] = "policy inactive" + (
            f" ({policy.halted_reason})" if policy.halted else ""
        )
        return tally
    for proposal in candidates(policy, limit=limit):
        tally["candidates"] += 1
        refresh_guided_verification(proposal, policy)
        ev, applied = auto_apply(proposal, policy, rng=rng)
        if applied is not None:
            tally["applied"].append(applied.pk)
        elif any(d.startswith("apply refused") for _, d in ev.conditions.values()):
            tally["refused"][str(proposal.pk)] = ev.reasons
        else:
            tally["held"][str(proposal.pk)] = ev.reasons
        if any(
            d.startswith("daily cap") or d.startswith("total cap")
            for _, d in ev.conditions.values()
        ):
            tally["stopped"] = "cap reached"
            break
    return tally


def sweep_all(*, limit=None):
    return [
        sweep(p, limit=limit)
        for p in StandardizationPolicy.objects.filter(is_enabled=True, halted=False)
    ]


# --- post-apply audit -------------------------------------------------------------


def audit_stats(policy):
    """Wrong fraction over the most recent audited samples. A RESTORED proposal
    counts as wrong whatever its verdict says."""
    recent = list(
        StandardizationAuditSample.objects.filter(policy=policy)
        .filter(
            ~Q(verdict=AuditVerdict.PENDING)
            | Q(proposal__status=ProposalStatus.RESTORED)
        )
        .select_related("proposal")
        .order_by("-reviewed_at", "-created_at")[: policy.audit_window]
    )
    wrong = sum(
        1
        for s in recent
        if s.verdict == AuditVerdict.WRONG
        or s.proposal.status == ProposalStatus.RESTORED
    )
    pending = StandardizationAuditSample.objects.filter(
        policy=policy, verdict=AuditVerdict.PENDING
    ).count()
    return {
        "audited": len(recent),
        "wrong": wrong,
        "wrong_fraction": (wrong / len(recent)) if recent else 0.0,
        "pending": pending,
    }


def check_breaker(policy):
    """Halt the policy when the audited wrong fraction passes the threshold."""
    stats = audit_stats(policy)
    if (
        stats["audited"] >= policy.audit_min_samples
        and stats["wrong_fraction"] > policy.audit_fail_threshold
    ):
        if not policy.halted:
            policy.halted = True
            policy.halted_at = timezone.now()
            policy.halted_reason = (
                f"audit: {stats['wrong']}/{stats['audited']} wrong "
                f"({stats['wrong_fraction']:.0%} > {policy.audit_fail_threshold:.0%})"
            )
            policy.save(update_fields=["halted", "halted_at", "halted_reason"])
            logger.warning(
                "standardization policy %s halted: %s",
                policy.version,
                policy.halted_reason,
            )
        return True
    return False


def record_audit(sample, verdict, actor=None, *, note=""):
    verdict = AuditVerdict(verdict)
    sample.verdict = verdict
    sample.reviewed_by = actor
    sample.reviewed_at = timezone.now()
    sample.note = note
    sample.save(update_fields=["verdict", "reviewed_by", "reviewed_at", "note"])
    halted = check_breaker(sample.policy)
    return sample, halted


def resume(policy, actor=None, *, note=""):
    policy.halted = False
    policy.halted_reason = ""
    policy.halted_at = None
    policy.save(update_fields=["halted", "halted_reason", "halted_at"])
    logger.info(
        "standardization policy %s resumed by %s: %s", policy.version, actor, note
    )
    return policy
