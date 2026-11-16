"""AI steps: one model call → persisted proposals (spec F03, F05–F07).

Each step is split in two halves so the synchronous pilot runner and the
Message Batches ingester share one code path:

* ``prepare(step, problem, batch)`` — fresh row, source hash, dedupe keys,
  classification, snapshot, and the ``LLMRequest``; it short-circuits with
  ready proposals for dedupe hits and NEEDS_SOURCE (no model call).
* ``finalize(step, prepared, payload, ledger)`` — turns a validated payload
  into proposals (PENDING_REVIEW / NEEDS_CLARIFICATION), updating DRAFT rows
  created at batch-submit time when they exist; ``fail(...)`` records ERROR.

Steps never apply anything. Every model call goes through ``llm.call_json``
(sync) or the batch ingester, so it is in the ledger even when it fails.
Budget caps are checked before spending; hitting one pauses the batch.
"""

import difflib
import logging
from dataclasses import dataclass, field

from django.db import transaction

from judge.models import Problem
from judge.models.standardization import ProblemSnapshot, StandardizationProposal
from judge.standardization import format_v1, prompts, schemas
from judge.standardization.apply import transition
from judge.standardization.budget import (
    BudgetExceeded,
    batch_is_open,
    check_budget,
    pause_batch,
)
from judge.standardization.classify import classify_problem
from judge.standardization.enums import FieldGroup, ProposalStatus
from judge.standardization.hashing import compute_source_hash, dedupe_key
from judge.standardization.llm import StepValidationError, call_json
from judge.standardization.snapshot import capture_all
from judge.standardization.tag_aliases import catalogue_names, resolve_tags
from llm_service.providers import LLMProviderError, LLMRequest

logger = logging.getLogger(__name__)

STEP_CONTENT = "draft_content_v1"
STEP_TAGS_POINTS = "tags_points_v1"
STEP_SOLUTION = "draft_solution_v1"
ALL_STEPS = (STEP_CONTENT, STEP_TAGS_POINTS, STEP_SOLUTION)

STEP_GROUPS = {
    STEP_CONTENT: (FieldGroup.CONTENT,),
    STEP_TAGS_POINTS: (FieldGroup.TAGS, FieldGroup.POINTS),
    STEP_SOLUTION: (FieldGroup.SOLUTION,),
}
STEP_SCHEMAS = {
    STEP_CONTENT: schemas.CONTENT_SCHEMA,
    STEP_TAGS_POINTS: schemas.TAGS_POINTS_SCHEMA,
    STEP_SOLUTION: schemas.SOLUTION_SCHEMA,
}
# The group whose dedupe key is the request's custom_id.
STEP_PRIMARY_GROUP = {
    STEP_CONTENT: FieldGroup.CONTENT,
    STEP_TAGS_POINTS: FieldGroup.POINTS,
    STEP_SOLUTION: FieldGroup.SOLUTION,
}

RATING_MIN, RATING_MAX = 800, 3500


@dataclass
class StepResult:
    step: str
    proposals: list = field(default_factory=list)
    skipped: str = ""  # dedupe | needs_source | pending_batch | budget | batch_<status>
    ledger_id: int | None = None
    error: str = ""

    @property
    def statuses(self):
        return [p.status for p in self.proposals]


@dataclass
class Prepared:
    step: str
    problem: object
    source_hash: str
    keys: dict  # group -> dedupe_key
    classification: object = None
    snapshot: object = None
    request: LLMRequest | None = None
    existing: dict = field(default_factory=dict)  # group -> proposal
    result: StepResult | None = None  # set when nothing is left to do

    @property
    def custom_id(self):
        return self.keys[STEP_PRIMARY_GROUP[self.step]]

    @property
    def missing_groups(self):
        return [g for g in STEP_GROUPS[self.step] if g not in self.existing]


def _existing_solution(problem):
    try:
        return problem.solution
    except Exception:  # RelatedObjectDoesNotExist
        return None


def ensure_snapshot(problem, source_hash, batch=None, classification=None):
    snapshot = (
        ProblemSnapshot.objects.filter(problem=problem, source_hash=source_hash)
        .order_by("-created_at")
        .first()
    )
    if snapshot is not None:
        return snapshot
    return ProblemSnapshot.objects.create(
        problem=problem,
        batch=batch,
        source_hash=source_hash,
        before_json=capture_all(problem),
        source_kinds_json=classification.source_kinds if classification else [],
        quality_flags_json=classification.quality_flags if classification else [],
        extracted_text=(classification.extracted_text if classification else "")[
            :200000
        ],
    )


def _upsert_proposal(
    *,
    prepared,
    batch,
    group,
    status,
    payload,
    diff,
    evidence,
    ledger=None,
    draft=None,
):
    """Create the proposal for ``group`` or fill the DRAFT row created at
    batch-submit time, moving it through the state machine."""
    key = prepared.keys[group]
    draft = (
        draft
        or StandardizationProposal.objects.filter(
            dedupe_key=key, status=ProposalStatus.DRAFT
        ).first()
    )
    fields = dict(
        payload_json=payload,
        diff_json=diff,
        evidence_json=evidence,
        model=ledger.model if ledger else "",
        provider=ledger.provider if ledger else "rules",
        cost_usd=ledger.cost_usd if ledger else None,
        provider_request_id=(ledger.request_id if ledger else "")[:120],
    )
    if draft is not None:
        for name, value in fields.items():
            setattr(draft, name, value)
        if batch is not None and draft.batch_id is None:
            draft.batch = batch
        if status != ProposalStatus.DRAFT:
            transition(draft, status, save=False)
        draft.save()
        proposal = draft
    else:
        proposal = StandardizationProposal.objects.create(
            snapshot=prepared.snapshot,
            batch=batch,
            problem=prepared.problem,
            field_group=group,
            step=prepared.step,
            prompt_version=prompts.PROMPT_VERSION,
            dedupe_key=key,
            status=status,
            **fields,
        )
    if ledger is not None and ledger.proposal_id is None:
        ledger.proposal = proposal
        ledger.save(update_fields=["proposal"])
    return proposal


def _diff_stats(before, after):
    b, a = before.splitlines(), after.splitlines()
    ratio = difflib.SequenceMatcher(None, b, a).ratio()
    return {
        "before_lines": len(b),
        "after_lines": len(a),
        "similarity": round(ratio, 3),
    }


# ------------------------------------------------------------------ prepare


def prepare(step, problem, batch=None):
    if step not in STEP_GROUPS:
        raise ValueError(f"unknown step {step!r}")
    problem = Problem.objects.select_related("group").get(pk=problem.pk)
    source_hash = compute_source_hash(problem)
    keys = {
        group: dedupe_key(problem.pk, source_hash, group, step, prompts.PROMPT_VERSION)
        for group in STEP_GROUPS[step]
    }
    prepared = Prepared(step=step, problem=problem, source_hash=source_hash, keys=keys)
    for proposal in StandardizationProposal.objects.filter(
        dedupe_key__in=list(keys.values())
    ):
        if proposal.status == ProposalStatus.DRAFT and not (
            proposal.evidence_json or {}
        ).get("pending_batch", True):
            continue  # re-queued batch request: submit again
        prepared.existing[proposal.field_group] = proposal
    if not prepared.missing_groups:
        drafts = [
            p for p in prepared.existing.values() if p.status == ProposalStatus.DRAFT
        ]
        prepared.result = StepResult(
            step,
            list(prepared.existing.values()),
            skipped="pending_batch" if drafts else "dedupe",
        )
        return prepared
    prepared.classification = classify_problem(problem)
    with transaction.atomic():
        prepared.snapshot = ensure_snapshot(
            problem, source_hash, batch, prepared.classification
        )
    if prepared.classification.needs_source:
        proposals = list(prepared.existing.values())
        for group in prepared.missing_groups:
            proposals.append(
                _upsert_proposal(
                    prepared=prepared,
                    batch=batch,
                    group=group,
                    status=ProposalStatus.NEEDS_SOURCE,
                    payload={},
                    diff={},
                    evidence={"classification": prepared.classification.as_json()},
                )
            )
        prepared.result = StepResult(step, proposals, skipped="needs_source")
        return prepared
    prepared.request = build_request(prepared)
    return prepared


def build_request(prepared):
    problem, classification, step = (
        prepared.problem,
        prepared.classification,
        prepared.step,
    )
    if step == STEP_CONTENT:
        return LLMRequest(
            system=prompts.CONTENT_SYSTEM,
            user=prompts.content_user_prompt(problem, classification),
            schema=schemas.CONTENT_SCHEMA,
            custom_id=prepared.custom_id,
        )
    if step == STEP_TAGS_POINTS:
        solution = _existing_solution(problem)
        return LLMRequest(
            system=prompts.TAGS_POINTS_SYSTEM.format(
                catalogue="\n".join(f"- {name}" for name in catalogue_names())
            ),
            user=prompts.tags_points_user_prompt(
                problem,
                classification,
                list(problem.types.values_list("full_name", flat=True)),
                solution.content if solution else "",
            ),
            schema=schemas.TAGS_POINTS_SCHEMA,
            custom_id=prepared.custom_id,
        )
    solution = _existing_solution(problem)
    return LLMRequest(
        system=prompts.SOLUTION_SYSTEM,
        user=prompts.solution_user_prompt(
            problem, classification, solution.content if solution else ""
        ),
        schema=schemas.SOLUTION_SCHEMA,
        custom_id=prepared.custom_id,
        max_tokens=24000,
    )


def create_drafts(prepared, batch, provider_batch_id=""):
    """Placeholder DRAFT rows for a request submitted in a provider batch."""
    drafts = []
    for group in prepared.missing_groups:
        previous = (
            StandardizationProposal.objects.filter(
                dedupe_key=prepared.keys[group], status=ProposalStatus.DRAFT
            )
            .values_list("evidence_json", flat=True)
            .first()
        ) or {}
        evidence = {
            "pending_batch": True,
            "provider_batch_id": provider_batch_id,
            "custom_id": prepared.custom_id,
            "classification": prepared.classification.as_json(),
        }
        if previous.get("requeue"):  # keep the re-submission count across submits
            evidence["requeue"] = previous["requeue"]
            evidence["last_error"] = previous.get("last_error", "")
        drafts.append(
            _upsert_proposal(
                prepared=prepared,
                batch=batch,
                group=group,
                status=ProposalStatus.DRAFT,
                payload={},
                diff={},
                evidence=evidence,
            )
        )
    return drafts


# ----------------------------------------------------------------- finalize


def fail(prepared, error, batch=None, ledger=None):
    proposals = list(prepared.existing.values())
    for group in prepared.missing_groups:
        proposals.append(
            _upsert_proposal(
                prepared=prepared,
                batch=batch,
                group=group,
                status=ProposalStatus.ERROR,
                payload={},
                diff={},
                evidence={
                    "error": error,
                    "classification": (
                        prepared.classification.as_json()
                        if prepared.classification
                        else {}
                    ),
                },
                ledger=ledger,
            )
        )
    return StepResult(
        prepared.step, proposals, error=error, ledger_id=ledger.id if ledger else None
    )


def finalize(prepared, payload, ledger, batch=None):
    handler = {
        STEP_CONTENT: _finalize_content,
        STEP_TAGS_POINTS: _finalize_tags_points,
        STEP_SOLUTION: _finalize_solution,
    }[prepared.step]
    proposals = list(prepared.existing.values()) + handler(
        prepared, payload, ledger, batch
    )
    return StepResult(prepared.step, proposals, ledger_id=ledger.id if ledger else None)


def _finalize_content(prepared, payload, ledger, batch):
    problem, classification = prepared.problem, prepared.classification
    markdown = format_v1.render(payload)
    validation = format_v1.validate(markdown)
    status = ProposalStatus.PENDING_REVIEW
    if not payload["source_readable"] or not validation.ok:
        status = ProposalStatus.NEEDS_CLARIFICATION
    return [
        _upsert_proposal(
            prepared=prepared,
            batch=batch,
            group=FieldGroup.CONTENT,
            status=status,
            payload={"description": markdown, "structured": payload},
            diff=_diff_stats(problem.description or "", markdown),
            evidence={
                "classification": classification.as_json(),
                "validation": validation.label,
                "validation_errors": validation.errors,
                "warnings": payload["warnings"],
                "unverified_spans": payload["unverified_spans"],
                "io_contract": payload["io_contract"],
                "kept_images": payload["kept_images"],
                "source_readable": payload["source_readable"],
                "examples": len(payload["examples"]),
            },
            ledger=ledger,
        )
    ]


def _finalize_tags_points(prepared, payload, ledger, batch):
    problem, classification = prepared.problem, prepared.classification
    out = []
    current_ids = sorted(problem.types.values_list("id", flat=True))
    if FieldGroup.TAGS in prepared.missing_groups:
        type_ids, canonical, unresolved = resolve_tags(payload["tags"][:4])
        out.append(
            _upsert_proposal(
                prepared=prepared,
                batch=batch,
                group=FieldGroup.TAGS,
                status=(
                    ProposalStatus.PENDING_REVIEW
                    if type_ids
                    else ProposalStatus.NEEDS_CLARIFICATION
                ),
                payload={
                    "type_ids": type_ids,
                    "names": canonical,
                    "primary_tag": payload["primary_tag"],
                    "unresolved": unresolved,
                },
                diff={
                    "added": sorted(set(type_ids) - set(current_ids)),
                    "removed": sorted(set(current_ids) - set(type_ids)),
                },
                evidence={
                    "proposed_names": payload["tags"],
                    "unresolved": unresolved,
                    "confidence": payload["confidence"],
                    "reasoning": payload["reasoning"],
                    "classification": classification.as_json(),
                },
                ledger=ledger,
            )
        )
    if FieldGroup.POINTS in prepared.missing_groups:
        rating = int(payload["rating"])
        in_range = RATING_MIN <= rating <= RATING_MAX
        out.append(
            _upsert_proposal(
                prepared=prepared,
                batch=batch,
                group=FieldGroup.POINTS,
                status=(
                    ProposalStatus.PENDING_REVIEW
                    if in_range and not payload["insufficient_evidence"]
                    else ProposalStatus.NEEDS_CLARIFICATION
                ),
                payload={
                    "points": rating,
                    "points_before": float(problem.points),
                    "range": [int(payload["rating_low"]), int(payload["rating_high"])],
                    "confidence": payload["confidence"],
                    "label": "độ khó ước lượng theo thang Codeforces",
                },
                diff={"points_from": float(problem.points), "points_to": rating},
                evidence={
                    "reasoning": payload["reasoning"],
                    "cf_source_url": payload["cf_source_url"],
                    "insufficient_evidence": payload["insufficient_evidence"],
                    "in_range": in_range,
                    "confidence": payload["confidence"],
                    "classification": classification.as_json(),
                },
                ledger=ledger,
            )
        )
    return out


def render_solution(payload):
    """Editorial Markdown in the owner's Solution style (### sections)."""
    out = [payload["summary"].strip(), ""]
    if payload["key_observations"]:
        out += (
            ["### Nhận xét chính"]
            + [f"- {x.strip()}" for x in payload["key_observations"]]
            + [""]
        )
    out += ["### Thuật toán", payload["algorithm"].strip(), ""]
    out += ["### Tính đúng", payload["correctness"].strip(), ""]
    out += [
        "### Độ phức tạp",
        f"- Thời gian: {payload['complexity_time'].strip()}",
        f"- Bộ nhớ: {payload['complexity_memory'].strip()}",
        "",
    ]
    if payload["edge_cases"]:
        out += (
            ["### Trường hợp biên"]
            + [f"- {x.strip()}" for x in payload["edge_cases"]]
            + [""]
        )
    if payload["subtasks"]:
        out += ["### Subtask"] + [f"- {x.strip()}" for x in payload["subtasks"]] + [""]
    lang = "C++20" if payload["language_key"] == "CPP20" else "C++17"
    out += [
        f"### Code tham chiếu ({lang})",
        "```cpp",
        payload["cpp_code"].rstrip(),
        "```",
        "",
    ]
    return "\n".join(out)


def _finalize_solution(prepared, payload, ledger, batch):
    solution = _existing_solution(prepared.problem)
    existing_text = solution.content if solution else ""
    content = render_solution(payload)
    return [
        _upsert_proposal(
            prepared=prepared,
            batch=batch,
            group=FieldGroup.SOLUTION,
            status=ProposalStatus.PENDING_REVIEW,
            payload={
                "content": content,
                "cpp_code": payload["cpp_code"],
                "language_key": payload["language_key"],
                "structured": {k: v for k, v in payload.items() if k != "cpp_code"},
            },
            diff={
                "existing_solution": bool(existing_text),
                "existing_chars": len(existing_text),
                "existing_public": bool(solution.is_public) if solution else None,
                "new_chars": len(content),
            },
            evidence={
                "confidence": payload["confidence"],
                "notes_on_existing": payload["notes_on_existing"],
                "verification": "unverified",
                "classification": prepared.classification.as_json(),
            },
            ledger=ledger,
        )
    ]


# ------------------------------------------------------------- sync runner


def run_step(step, problem, *, batch=None, provider=None):
    """Synchronous path: prepare → budget → model → finalize."""
    prepared = prepare(step, problem, batch)
    if prepared.result is not None:
        return prepared.result
    if not batch_is_open(batch):
        return StepResult(
            step, list(prepared.existing.values()), skipped=f"batch_{batch.status}"
        )
    try:
        check_budget(problem=prepared.problem, batch=batch)
    except BudgetExceeded as exc:
        with transaction.atomic():
            pause_batch(batch, str(exc))
        logger.warning("standardization budget hit: %s", exc)
        return StepResult(
            step,
            list(prepared.existing.values()),
            skipped="budget",
            error=f"budget:{exc.scope}",
        )
    try:
        payload, ledger = call_json(
            prepared.request,
            step=step,
            problem=prepared.problem,
            batch=batch,
            provider=provider,
        )
    except LLMProviderError as exc:
        return fail(prepared, f"provider:{exc.code}", batch)
    except StepValidationError as exc:
        return fail(prepared, f"schema:{exc}", batch)
    return finalize(prepared, payload, ledger, batch)


# Backwards-compatible names used by tests / callers.
def run_content_step(problem, *, batch=None, provider=None):
    return run_step(STEP_CONTENT, problem, batch=batch, provider=provider)


def run_tags_points_step(problem, *, batch=None, provider=None):
    return run_step(STEP_TAGS_POINTS, problem, batch=batch, provider=provider)


def run_solution_step(problem, *, batch=None, provider=None):
    return run_step(STEP_SOLUTION, problem, batch=batch, provider=provider)
