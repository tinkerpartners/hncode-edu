"""AI steps: one model call → persisted proposals (spec F03, F05–F07).

Each step is idempotent per (problem, source_hash, field_group, step,
PROMPT_VERSION): a second run finds the existing proposal and returns it.
Steps never apply anything; they create ``PENDING_REVIEW`` (or
``NEEDS_SOURCE`` / ``NEEDS_CLARIFICATION`` / ``ERROR``) proposals that the
review UI or the auto-apply policy (later PR) decides on. Every model call
goes through ``llm.call_json`` so it is in the ledger even when it fails.
"""

import difflib
import logging
from dataclasses import dataclass, field

from django.db import transaction

from judge.models import Problem
from judge.models.standardization import ProblemSnapshot, StandardizationProposal
from judge.standardization import format_v1, prompts, schemas
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

RATING_MIN, RATING_MAX = 800, 3500


@dataclass
class StepResult:
    step: str
    proposals: list = field(default_factory=list)
    skipped: str = ""  # "dedupe" | "needs_source" | ""
    ledger_id: int | None = None
    error: str = ""

    @property
    def statuses(self):
        return [p.status for p in self.proposals]


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


def _create_proposal(
    *,
    snapshot,
    batch,
    problem,
    group,
    step,
    key,
    status,
    payload,
    diff,
    evidence,
    ledger=None,
):
    proposal = StandardizationProposal.objects.create(
        snapshot=snapshot,
        batch=batch,
        problem=problem,
        field_group=group,
        step=step,
        prompt_version=prompts.PROMPT_VERSION,
        model=ledger.model if ledger else "",
        provider=ledger.provider if ledger else "rules",
        dedupe_key=key,
        status=status,
        payload_json=payload,
        diff_json=diff,
        evidence_json=evidence,
        cost_usd=ledger.cost_usd if ledger else None,
        provider_request_id=(ledger.request_id if ledger else "")[:120],
    )
    if ledger is not None and ledger.proposal_id is None:
        ledger.proposal = proposal
        ledger.save(update_fields=["proposal"])
    return proposal


def _fresh(problem):
    return Problem.objects.select_related("group").get(pk=problem.pk)


def _call(request, *, step, problem, batch, provider):
    """Run the model; return (payload, ledger, error_code)."""
    try:
        payload, ledger = call_json(
            request, step=step, problem=problem, batch=batch, provider=provider
        )
        return payload, ledger, ""
    except LLMProviderError as exc:
        return None, None, f"provider:{exc.code}"
    except StepValidationError as exc:
        return None, None, f"schema:{exc}"


def _error_proposal(snapshot, batch, problem, group, step, key, error, classification):
    return _create_proposal(
        snapshot=snapshot,
        batch=batch,
        problem=problem,
        group=group,
        step=step,
        key=key,
        status=ProposalStatus.ERROR,
        payload={},
        diff={},
        evidence={"error": error, "classification": classification.as_json()},
    )


def _diff_stats(before, after):
    b, a = before.splitlines(), after.splitlines()
    ratio = difflib.SequenceMatcher(None, b, a).ratio()
    return {
        "before_lines": len(b),
        "after_lines": len(a),
        "similarity": round(ratio, 3),
    }


# ---------------------------------------------------------------- content


def run_content_step(problem, *, batch=None, provider=None):
    problem = _fresh(problem)
    source_hash = compute_source_hash(problem)
    key = dedupe_key(
        problem.pk,
        source_hash,
        FieldGroup.CONTENT,
        STEP_CONTENT,
        prompts.PROMPT_VERSION,
    )
    existing = StandardizationProposal.objects.filter(dedupe_key=key).first()
    if existing is not None:
        return StepResult(STEP_CONTENT, [existing], skipped="dedupe")
    classification = classify_problem(problem)
    with transaction.atomic():
        snapshot = ensure_snapshot(problem, source_hash, batch, classification)
    if classification.needs_source:
        proposal = _create_proposal(
            snapshot=snapshot,
            batch=batch,
            problem=problem,
            group=FieldGroup.CONTENT,
            step=STEP_CONTENT,
            key=key,
            status=ProposalStatus.NEEDS_SOURCE,
            payload={},
            diff={},
            evidence={"classification": classification.as_json()},
        )
        return StepResult(STEP_CONTENT, [proposal], skipped="needs_source")

    request = LLMRequest(
        system=prompts.CONTENT_SYSTEM,
        user=prompts.content_user_prompt(problem, classification),
        schema=schemas.CONTENT_SCHEMA,
        custom_id=key,
    )
    payload, ledger, error = _call(
        request, step=STEP_CONTENT, problem=problem, batch=batch, provider=provider
    )
    if error:
        proposal = _error_proposal(
            snapshot,
            batch,
            problem,
            FieldGroup.CONTENT,
            STEP_CONTENT,
            key,
            error,
            classification,
        )
        return StepResult(STEP_CONTENT, [proposal], error=error)

    markdown = format_v1.render(payload)
    validation = format_v1.validate(markdown)
    before = problem.description or ""
    status = ProposalStatus.PENDING_REVIEW
    if not payload["source_readable"] or not validation.ok:
        status = ProposalStatus.NEEDS_CLARIFICATION
    proposal = _create_proposal(
        snapshot=snapshot,
        batch=batch,
        problem=problem,
        group=FieldGroup.CONTENT,
        step=STEP_CONTENT,
        key=key,
        status=status,
        payload={"description": markdown, "structured": payload},
        diff=_diff_stats(before, markdown),
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
    return StepResult(STEP_CONTENT, [proposal], ledger_id=ledger.id)


# ------------------------------------------------------------ tags/points


def run_tags_points_step(problem, *, batch=None, provider=None):
    problem = _fresh(problem)
    source_hash = compute_source_hash(problem)
    keys = {
        group: dedupe_key(
            problem.pk, source_hash, group, STEP_TAGS_POINTS, prompts.PROMPT_VERSION
        )
        for group in (FieldGroup.TAGS, FieldGroup.POINTS)
    }
    existing = list(
        StandardizationProposal.objects.filter(dedupe_key__in=list(keys.values()))
    )
    if len(existing) == 2:
        return StepResult(STEP_TAGS_POINTS, existing, skipped="dedupe")
    classification = classify_problem(problem)
    with transaction.atomic():
        snapshot = ensure_snapshot(problem, source_hash, batch, classification)
    if classification.needs_source:
        proposals = [
            _create_proposal(
                snapshot=snapshot,
                batch=batch,
                problem=problem,
                group=group,
                step=STEP_TAGS_POINTS,
                key=keys[group],
                status=ProposalStatus.NEEDS_SOURCE,
                payload={},
                diff={},
                evidence={"classification": classification.as_json()},
            )
            for group in (FieldGroup.TAGS, FieldGroup.POINTS)
            if not any(p.dedupe_key == keys[group] for p in existing)
        ]
        return StepResult(
            STEP_TAGS_POINTS, existing + proposals, skipped="needs_source"
        )

    existing_tags = list(problem.types.values_list("full_name", flat=True))
    solution = _existing_solution(problem)
    request = LLMRequest(
        system=prompts.TAGS_POINTS_SYSTEM.format(
            catalogue="\n".join(f"- {name}" for name in catalogue_names())
        ),
        user=prompts.tags_points_user_prompt(
            problem, classification, existing_tags, solution.content if solution else ""
        ),
        schema=schemas.TAGS_POINTS_SCHEMA,
        custom_id=keys[FieldGroup.POINTS],
    )
    payload, ledger, error = _call(
        request, step=STEP_TAGS_POINTS, problem=problem, batch=batch, provider=provider
    )
    if error:
        proposals = [
            _error_proposal(
                snapshot,
                batch,
                problem,
                group,
                STEP_TAGS_POINTS,
                keys[group],
                error,
                classification,
            )
            for group in (FieldGroup.TAGS, FieldGroup.POINTS)
            if not any(p.dedupe_key == keys[group] for p in existing)
        ]
        return StepResult(STEP_TAGS_POINTS, existing + proposals, error=error)

    proposals = list(existing)
    current_ids = sorted(problem.types.values_list("id", flat=True))
    if not any(p.dedupe_key == keys[FieldGroup.TAGS] for p in existing):
        type_ids, canonical, unresolved = resolve_tags(payload["tags"][:4])
        tag_status = (
            ProposalStatus.PENDING_REVIEW
            if type_ids
            else ProposalStatus.NEEDS_CLARIFICATION
        )
        proposals.append(
            _create_proposal(
                snapshot=snapshot,
                batch=batch,
                problem=problem,
                group=FieldGroup.TAGS,
                step=STEP_TAGS_POINTS,
                key=keys[FieldGroup.TAGS],
                status=tag_status,
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
    if not any(p.dedupe_key == keys[FieldGroup.POINTS] for p in existing):
        rating = int(payload["rating"])
        in_range = RATING_MIN <= rating <= RATING_MAX
        points_status = (
            ProposalStatus.PENDING_REVIEW
            if in_range and not payload["insufficient_evidence"]
            else ProposalStatus.NEEDS_CLARIFICATION
        )
        proposals.append(
            _create_proposal(
                snapshot=snapshot,
                batch=batch,
                problem=problem,
                group=FieldGroup.POINTS,
                step=STEP_TAGS_POINTS,
                key=keys[FieldGroup.POINTS],
                status=points_status,
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
    return StepResult(STEP_TAGS_POINTS, proposals, ledger_id=ledger.id)


# ---------------------------------------------------------------- solution


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


def run_solution_step(problem, *, batch=None, provider=None):
    problem = _fresh(problem)
    source_hash = compute_source_hash(problem)
    key = dedupe_key(
        problem.pk,
        source_hash,
        FieldGroup.SOLUTION,
        STEP_SOLUTION,
        prompts.PROMPT_VERSION,
    )
    existing = StandardizationProposal.objects.filter(dedupe_key=key).first()
    if existing is not None:
        return StepResult(STEP_SOLUTION, [existing], skipped="dedupe")
    classification = classify_problem(problem)
    with transaction.atomic():
        snapshot = ensure_snapshot(problem, source_hash, batch, classification)
    if classification.needs_source:
        proposal = _create_proposal(
            snapshot=snapshot,
            batch=batch,
            problem=problem,
            group=FieldGroup.SOLUTION,
            step=STEP_SOLUTION,
            key=key,
            status=ProposalStatus.NEEDS_SOURCE,
            payload={},
            diff={},
            evidence={"classification": classification.as_json()},
        )
        return StepResult(STEP_SOLUTION, [proposal], skipped="needs_source")

    solution = _existing_solution(problem)
    existing_text = solution.content if solution else ""
    request = LLMRequest(
        system=prompts.SOLUTION_SYSTEM,
        user=prompts.solution_user_prompt(problem, classification, existing_text),
        schema=schemas.SOLUTION_SCHEMA,
        custom_id=key,
        max_tokens=24000,
    )
    payload, ledger, error = _call(
        request, step=STEP_SOLUTION, problem=problem, batch=batch, provider=provider
    )
    if error:
        proposal = _error_proposal(
            snapshot,
            batch,
            problem,
            FieldGroup.SOLUTION,
            STEP_SOLUTION,
            key,
            error,
            classification,
        )
        return StepResult(STEP_SOLUTION, [proposal], error=error)

    content = render_solution(payload)
    proposal = _create_proposal(
        snapshot=snapshot,
        batch=batch,
        problem=problem,
        group=FieldGroup.SOLUTION,
        step=STEP_SOLUTION,
        key=key,
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
            "classification": classification.as_json(),
        },
        ledger=ledger,
    )
    return StepResult(STEP_SOLUTION, [proposal], ledger_id=ledger.id)


STEP_RUNNERS = {
    STEP_CONTENT: run_content_step,
    STEP_TAGS_POINTS: run_tags_points_step,
    STEP_SOLUTION: run_solution_step,
}


def run_step(step, problem, *, batch=None, provider=None):
    try:
        runner = STEP_RUNNERS[step]
    except KeyError:
        raise ValueError(f"unknown step {step!r}") from None
    return runner(problem, batch=batch, provider=provider)
