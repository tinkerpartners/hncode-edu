"""Draft a solution and tags using the strongest evidence available."""

import re
from dataclasses import dataclass
from hashlib import sha256

from django.conf import settings
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from judge.models import Problem, Submission
from judge.models.problem_data import ProblemData, ProblemSolutionCode
from judge.models.standardization import (
    LLMUsageLedger,
    StandardizationBatch,
    StandardizationProposal,
)
from judge.standardization import codeforces, config_profile, format_v1
from judge.standardization.apply import transition
from judge.standardization.budget import batch_is_open
from judge.standardization.classify import classify_problem, visible_text
from judge.standardization.contracts import read_contract, missing_files
from judge.standardization.enums import (
    BatchKind,
    BatchStatus,
    FieldGroup,
    ProposalStatus,
    VerificationLevel,
)
from judge.standardization.steps import (
    GUIDED_LUNA,
    GUIDED_REFERENCE,
    GUIDED_REPAIR,
    GUIDED_SOL,
    STEP_CONTENT,
    _existing_solution,
    ensure_snapshot,
    run_step,
)
from judge.standardization.hashing import (
    compute_judge_hash,
    compute_source_hash,
    compute_statement_hash,
    dedupe_key,
)
from judge.standardization.verification import (
    check_candidate,
    current_full_verification,
    has_full_score,
    missing_named_files,
    record_candidate,
    verify_solution_proposal,
)
from llm_service.providers import get_provider

VERSION = "guided_solution_v1"
LANGUAGES = ("CPP17", "CPP20", "PY3", "PYPY3")
ACTIVE = (
    BatchStatus.QUEUED,
    BatchStatus.RUNNING,
    BatchStatus.PAUSING,
    BatchStatus.PAUSED,
    BatchStatus.CANCEL_REQUESTED,
)
REPAIRABLE = {
    "CE",
    "missing_code",
    "partial_score",
    "WA",
    "TLE",
    "MLE",
    "RTE",
    "IR",
    "OLE",
    "io_mismatch",
}
EDITORIAL_SECTIONS = re.compile(r"^#{2,4}\s+(.+)$", re.MULTILINE)
TITLE_ONLY = re.compile(r"^(?:bài\s*\d+\s*[:.]\s*|đề bài\s*[:.]\s*)", re.IGNORECASE)


@dataclass(frozen=True)
class Reference:
    kind: str
    id: int
    source: str
    language: object

    @property
    def digest(self):
        return sha256(self.source.encode()).hexdigest()

    def as_json(self):
        return {
            "kind": self.kind,
            "id": self.id,
            "source_hash": self.digest,
            "language": self.language.key,
        }


def complete_editorial(editorial):
    if not editorial:
        return False
    chunks = EDITORIAL_SECTIONS.split(editorial.content or "")
    sections = {
        title.strip().casefold()
        for title, text in zip(chunks[1::2], chunks[2::2])
        if len(text.strip().split()) >= 3
    }
    return {"thuật toán", "tính đúng", "độ phức tạp"} <= sections


def allowed_submission(problem, submission_id, actor):
    if (
        not submission_id
        or not actor
        or not getattr(settings, "STANDARDIZATION_ALLOW_SUBMISSION_REFERENCES", False)
    ):
        return None
    submission = (
        Submission.objects.filter(
            pk=submission_id, problem=problem, language__key__in=LANGUAGES
        )
        .select_related("language", "source")
        .first()
    )
    if (
        submission
        and has_full_score(submission)
        and submission.is_accessible_by(actor)
        and hasattr(submission, "source")
    ):
        return Reference(
            "submission", submission.pk, submission.source.source, submission.language
        )
    return None


def references(problem, submission_id, actor):
    for code in (
        ProblemSolutionCode.objects.filter(
            problem=problem,
            expected_result="AC",
            language__key__in=LANGUAGES,
        )
        .select_related("language")
        .order_by("order", "id")[:2]
    ):
        if code.source_code.strip():
            yield Reference("solution_code", code.pk, code.source_code, code.language)
    submission = allowed_submission(problem, submission_id, actor)
    if submission and submission.source.strip():
        yield submission


@transaction.atomic
def queue_run(
    problem,
    actor,
    *,
    include_content=False,
    include_config=False,
    allow_sol=True,
    submission_id=None,
    cf_source="",
    budget_cap=None,
):
    Problem.objects.select_for_update().get(pk=problem.pk)
    existing = StandardizationBatch.objects.filter(
        config_version=VERSION,
        id_from=problem.pk,
        id_through=problem.pk,
        status__in=ACTIVE,
    ).first()
    if existing:
        return existing, False
    if submission_id and not allowed_submission(problem, submission_id, actor):
        raise ValueError(
            "Bài nộp không đủ điểm, không thuộc bài này hoặc chưa được phép dùng."
        )
    options = {
        "content": include_content,
        "config": include_config,
        "allow_sol": allow_sol,
        "submission_id": submission_id,
        "cf_key": codeforces.parse_source(cf_source) if cf_source else "",
    }
    batch = StandardizationBatch.objects.create(
        name=f"Chuẩn hóa {problem.code}",
        kind=BatchKind.AI_PIPELINE,
        config_version=VERSION,
        id_from=problem.pk,
        id_through=problem.pk,
        created_by=actor,
        budget_usd_cap=budget_cap,
        counters_json={"options": options},
        steps_json=[GUIDED_LUNA, *([STEP_CONTENT] if include_content else [])],
    )
    return batch, True


def set_progress(batch, phase, **updates):
    batch.refresh_from_db()
    batch.counters_json = {**batch.counters_json, "phase": phase, **updates}
    batch.save(update_fields=["counters_json"])


def tag_outcome(proposals, accepted):
    tags = next((p for p in proposals if p.field_group == FieldGroup.TAGS), None)
    if tags and tags.status == ProposalStatus.DRAFT:
        can_review = (
            accepted
            and bool(tags.payload_json["type_ids"])
            and not tags.payload_json["unresolved"]
        )
        transition(
            tags,
            (
                ProposalStatus.PENDING_REVIEW
                if can_review
                else ProposalStatus.NEEDS_CLARIFICATION
            ),
        )
    points = next((p for p in proposals if p.field_group == FieldGroup.POINTS), None)
    if points and points.status == ProposalStatus.DRAFT:
        evidence = points.evidence_json or {}
        can_review = (
            accepted
            and evidence.get("in_range")
            and not evidence.get("insufficient_evidence")
            and bool((evidence.get("reasoning") or "").strip())
        )
        transition(
            points,
            (
                ProposalStatus.PENDING_REVIEW
                if can_review
                else ProposalStatus.NEEDS_CLARIFICATION
            ),
        )


def failure_context(proposal, reason, detail):
    code = (proposal.payload_json or {}).get("cpp_code", "")
    files = ", ".join(detail.get("missing_files", []))
    diagnostic = detail.get("compile_error") or files
    contract = read_contract(proposal.problem, proposal.problem.description or "")
    diagnostic += "\n" + contract.prompt()
    if reason in ("WA", "partial_score"):
        diagnostic += (
            "\nNo hidden testcase content is provided. Recheck parsing and exact I/O first, "
            "then the invariant, complexity, overflow and edge cases. Construct an independent small "
            "counterexample before changing an algorithm; do not repeat the same claim without a correction."
        )
    return (
        f"Verification result: {reason}\n{diagnostic}\nPrevious code:\n{code[:14000]}"
    )


def judge_attempt(proposal, prechecked=None):
    if prechecked:
        return record_candidate(proposal, *prechecked)
    if current_full_verification(proposal):
        return proposal.verifications.order_by("-created_at", "-pk").first()
    return verify_solution_proposal(proposal)


def rule_proposal(problem, batch, classification, group, step, payload, diff, evidence):
    version = compute_source_hash(problem)
    snapshot = ensure_snapshot(problem, version, batch, classification)
    return StandardizationProposal.objects.get_or_create(
        dedupe_key=dedupe_key(problem.pk, version, group, step, repr(payload)),
        defaults={
            "snapshot": snapshot,
            "batch": batch,
            "problem": problem,
            "field_group": group,
            "step": step,
            "status": ProposalStatus.PENDING_REVIEW,
            "provider": "rules",
            "payload_json": payload,
            "diff_json": diff,
            "evidence_json": {**evidence, "guided_workflow": True},
        },
    )[0]


def prepare_metadata(problem, batch, classification, options):
    if options.get("config"):
        data = ProblemData.objects.filter(problem=problem).first()
        special = data and (
            data.use_ioi_signature
            or data.output_only
            or data.is_communication
            or data.interactive_judge
        )
        if special:
            set_progress(
                batch,
                "metadata",
                config_note="Bài dùng trình chấm đặc biệt, cần xác nhận cấu hình tương thích.",
            )
        else:
            try:
                config = config_profile.plan_config(
                    problem, config_profile.resolve_context()
                )
            except config_profile.ProfileResolutionError as exc:
                set_progress(batch, "metadata", config_note=str(exc))
            else:
                if not config.is_noop():
                    rule_proposal(
                        problem,
                        batch,
                        classification,
                        FieldGroup.CONFIG,
                        VERSION,
                        {"require_judge": True},
                        config.to_json(),
                        {"rules": config.rules_changed},
                    )
    if options.get("cf_key"):
        from requests import RequestException

        key = options["cf_key"]
        try:
            rating = codeforces.get_rating(key)
        except (RequestException, ValueError, KeyError):
            rating = None
        if rating is None:
            set_progress(
                batch,
                "metadata",
                points_note="Chưa lấy được rating chính thức từ Codeforces.",
            )
        elif rating != problem.points:
            source = f"https://codeforces.com/problemset/problem/{key}"
            rule_proposal(
                problem,
                batch,
                classification,
                FieldGroup.POINTS,
                VERSION,
                {
                    "points": rating,
                    "label": "Rating chính thức của bài Codeforces đã đối chiếu",
                },
                {"points_from": problem.points, "points_to": rating},
                {
                    "cf_source_url": source,
                    "official_rating": rating,
                    "source_confirmed_by": batch.created_by_id,
                    "statement_hash": compute_statement_hash(problem),
                },
            )


def guide(problem, batch):
    options = batch.counters_json["options"]
    source_hash = compute_source_hash(problem)
    classification = classify_problem(problem)
    plain = visible_text(classification.extracted_text).strip("# -_\n")
    if (
        classification.needs_source
        or not TITLE_ONLY.sub("", plain).strip()
        or (TITLE_ONLY.match(plain) and len(plain.split()) <= 10)
        or plain.casefold() == visible_text(problem.name).strip().casefold()
    ):
        set_progress(batch, "needs_source", reason="statement_missing")
        return

    prepare_metadata(problem, batch, classification, options)
    contract = read_contract(problem, classification.extracted_text)
    if contract.issues or contract.kind in ("function", "interactive", "output_only"):
        set_progress(
            batch,
            "needs_review",
            reason=(
                "grading_contract_conflict"
                if contract.issues
                else "special_judge_requires_review"
            ),
            contract=contract.as_json(),
        )
        return
    primary = getattr(settings, "STANDARDIZATION_GUIDED_MODEL", "openai/gpt-6-luna")
    editorial = _existing_solution(problem)
    rating_proposal = (
        StandardizationProposal.objects.filter(
            problem=problem,
            field_group=FieldGroup.POINTS,
            status=ProposalStatus.APPLIED,
            evidence_json__statement_hash=compute_statement_hash(problem),
        )
        .order_by("-pk")
        .first()
    )
    rated = rating_proposal and rating_proposal.payload_json.get("points") == float(
        problem.points
    )
    reuse_editorial = complete_editorial(editorial) and problem.types.exists() and rated
    description = problem.description or ""
    if options["content"] and format_v1.validate(description).label != "STRICT_OK":
        set_progress(batch, "content")
        fixed = format_v1.rule_fix_headings(description)
        if format_v1.can_auto_apply(description, fixed)[0]:
            rule_proposal(
                problem,
                batch,
                classification,
                FieldGroup.CONTENT,
                VERSION,
                {"description": fixed},
                {},
                {"heading_only": True, "validation": format_v1.validate(fixed).label},
            )
        else:
            result = run_step(
                STEP_CONTENT,
                problem,
                batch=batch,
                provider=get_provider("openrouter"),
                classification=classification,
                guided_content=True,
                model=primary,
                expected_source_hash=source_hash,
            )
            if result.error == "source_changed" or not batch_is_open(batch):
                set_progress(
                    batch, "needs_review", reason=result.error or result.skipped
                )
                return
    if reuse_editorial:
        set_progress(batch, "existing_solution", reason="editorial_and_tags_covered")
        return
    if not batch_is_open(batch):
        return

    set_progress(batch, "reference")
    fallback = getattr(
        settings, "STANDARDIZATION_GUIDED_FALLBACK_MODEL", "openai/gpt-6-sol"
    )
    reference, evidence = None, None
    attempted_references = []
    for candidate in references(problem, options["submission_id"], batch.created_by):
        if not batch_is_open(batch):
            return
        if missing_files(contract, candidate.source) or missing_named_files(
            classification.extracted_text, candidate.source
        ):
            attempted_references.append(
                {"kind": candidate.kind, "id": candidate.id, "reason": "io_mismatch"}
            )
            continue
        checked = check_candidate(problem, candidate.language, candidate.source)
        attempted_references.append(
            {
                "kind": candidate.kind,
                "id": candidate.id,
                "reason": checked[1].get("reason", ""),
            }
        )
        if checked[0] == VerificationLevel.CURRENT_TESTS_PASSED:
            reference, evidence = candidate, checked
            break
        if checked[0] == VerificationLevel.UNKNOWN:
            set_progress(
                batch,
                "needs_review",
                reason=checked[1]["reason"],
                reference_attempts=attempted_references,
            )
            return

    set_progress(
        batch,
        "reference",
        reference_status=(
            "verified"
            if reference
            else "not_verified" if attempted_references else "not_found"
        ),
        reference_attempts=attempted_references,
        has_existing_editorial=bool(editorial and editorial.content.strip()),
    )

    step, feedback = (GUIDED_REFERENCE if reference else GUIDED_LUNA), ""
    while batch_is_open(batch):
        model = fallback if step == GUIDED_SOL else primary
        set_progress(batch, step, model=model)
        provider = get_provider("openrouter")
        result = run_step(
            step,
            problem,
            batch=batch,
            provider=provider,
            classification=classification,
            reference=reference,
            model=model,
            feedback=feedback,
            expected_source_hash=source_hash,
        )
        if result.error or not result.proposals:
            if (
                step == GUIDED_LUNA
                and result.error
                and (
                    result.error == "provider:truncated"
                    or result.error.startswith("schema:")
                )
            ):
                step, feedback = (
                    GUIDED_REPAIR,
                    "Previous response was incomplete or invalid JSON. Return concise valid JSON.",
                )
                continue
            set_progress(batch, "needs_review", reason=result.error or result.skipped)
            return
        proposal = next(
            p for p in result.proposals if p.field_group == FieldGroup.SOLUTION
        )
        if proposal.status in (
            ProposalStatus.NEEDS_SOURCE,
            ProposalStatus.NEEDS_CLARIFICATION,
        ):
            if reference and evidence[1].get("judge_hash") == compute_judge_hash(
                problem, reference.language
            ):
                record_candidate(proposal, *evidence)
            tag_outcome(result.proposals, False)
            set_progress(
                batch,
                "needs_review",
                reason="source_clarification",
                proposal_id=proposal.pk,
            )
            return
        if proposal.status in (
            ProposalStatus.ERROR,
            ProposalStatus.REJECTED,
            ProposalStatus.REWORK,
            ProposalStatus.STALE,
            ProposalStatus.RESTORED,
        ):
            tag_outcome(result.proposals, False)
            set_progress(
                batch, "needs_review", reason=proposal.status, proposal_id=proposal.pk
            )
            return
        if (
            reference
            and evidence[1].get("judge_hash")
            and evidence[1]["judge_hash"]
            != compute_judge_hash(problem, reference.language)
        ):
            set_progress(
                batch, "needs_review", reason="tests_changed", proposal_id=proposal.pk
            )
            tag_outcome(result.proposals, False)
            return
        check = judge_attempt(proposal, evidence)
        reason = check.details_json.get("reason", "")
        accepted = check.level == VerificationLevel.CURRENT_TESTS_PASSED
        tag_outcome(result.proposals, accepted)
        if accepted:
            latest = {p.field_group: p for p in result.proposals}
            for item in batch.proposals.order_by("-pk"):
                latest.setdefault(item.field_group, item)
            held = [
                group
                for group, item in latest.items()
                if item.status
                in (
                    ProposalStatus.NEEDS_CLARIFICATION,
                    ProposalStatus.NEEDS_SOURCE,
                    ProposalStatus.ERROR,
                )
            ]
            set_progress(
                batch,
                "partial_ready" if held else "ready",
                reason=(
                    (
                        "content_needs_review"
                        if FieldGroup.CONTENT in held
                        else "metadata_needs_review"
                    )
                    if held
                    else "full_score"
                ),
                proposal_id=proposal.pk,
                held_groups=held,
            )
            return
        if step == GUIDED_LUNA and reason in REPAIRABLE:
            step = GUIDED_REPAIR
        elif (
            step == GUIDED_REPAIR
            and reason in REPAIRABLE
            and options["allow_sol"]
            and fallback != primary
        ):
            step = GUIDED_SOL
        else:
            set_progress(batch, "needs_review", reason=reason, proposal_id=proposal.pk)
            return
        feedback = failure_context(proposal, reason, check.details_json)


def run_queued(batch_id):
    if not StandardizationBatch.objects.filter(
        pk=batch_id,
        config_version=VERSION,
        status=BatchStatus.QUEUED,
    ).update(status=BatchStatus.RUNNING):
        return
    batch = StandardizationBatch.objects.select_related("created_by").get(pk=batch_id)
    try:
        guide(Problem.objects.get(pk=batch.id_from), batch)
    except Exception:
        set_progress(batch, "needs_review", reason="workflow_error")
        StandardizationBatch.objects.filter(
            pk=batch.pk, status=BatchStatus.RUNNING
        ).update(status=BatchStatus.FAILED, finished_at=timezone.now())
        raise
    finally:
        amount = (
            LLMUsageLedger.objects.filter(batch=batch).aggregate(total=Sum("cost_usd"))[
                "total"
            ]
            or 0
        )
        StandardizationBatch.objects.filter(pk=batch.pk).update(spent_usd=amount)
        StandardizationBatch.objects.filter(
            pk=batch.pk, status=BatchStatus.RUNNING
        ).update(
            status=BatchStatus.FINISHED,
            finished_at=timezone.now(),
            checkpoint_id=batch.id_from,
        )
        StandardizationBatch.objects.filter(
            pk=batch.pk, status=BatchStatus.CANCEL_REQUESTED
        ).update(
            status=BatchStatus.CANCELLED,
            finished_at=timezone.now(),
        )
        StandardizationBatch.objects.filter(
            pk=batch.pk, status=BatchStatus.PAUSING
        ).update(status=BatchStatus.PAUSED)


def cancel_run(batch):
    if StandardizationBatch.objects.filter(
        pk=batch.pk, status__in=(BatchStatus.QUEUED, BatchStatus.PAUSED)
    ).update(
        status=BatchStatus.CANCELLED,
        finished_at=timezone.now(),
    ):
        return
    StandardizationBatch.objects.filter(
        pk=batch.pk, status__in=(BatchStatus.RUNNING, BatchStatus.PAUSING)
    ).update(
        status=BatchStatus.CANCEL_REQUESTED,
    )
