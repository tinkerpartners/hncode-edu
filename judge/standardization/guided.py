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
from judge.standardization.budget import batch_is_open
from judge.standardization.classify import classify_problem
from judge.standardization.contracts import read_contract
from judge.standardization.readiness import source_readiness, save_readiness
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
    current_full_row,
    has_full_score,
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
    from judge.standardization.reconciliation import update_metadata

    update_metadata(proposals, accepted)


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
        return current_full_row(proposal)
    return verify_solution_proposal(proposal)


def rule_proposal(problem, batch, classification, group, step, payload, diff, evidence):
    version = compute_source_hash(problem)
    snapshot = ensure_snapshot(problem, version, batch, classification)
    return StandardizationProposal.objects.get_or_create(
        dedupe_key=dedupe_key(
            problem.pk, version, group, step, repr(payload) + "|rules_v2"
        ),
        defaults={
            "snapshot": snapshot,
            "batch": batch,
            "problem": problem,
            "field_group": group,
            "step": step,
            "prompt_version": step,
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
    readiness = source_readiness(problem, classification=classification)
    save_readiness(problem, readiness, batch)
    if not readiness.ready:
        set_progress(
            batch,
            "needs_source",
            reason=readiness.reason,
            source_readiness=readiness.as_json(),
            contract=readiness.contract.as_json(),
        )
        return
    prepare_metadata(problem, batch, classification, options)
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
    certificate = (
        StandardizationProposal.objects.filter(
            problem=problem, field_group=FieldGroup.SOLUTION
        )
        .order_by("-created_at", "-pk")
        .first()
    )
    reuse_editorial = (
        complete_editorial(editorial)
        and problem.types.exists()
        and rated
        and certificate
        and certificate.status == ProposalStatus.APPLIED
        and certificate.payload_json.get("content") == editorial.content
        and current_full_verification(certificate, problem)
    )
    description = problem.description or ""
    if options["content"] and format_v1.validate(description).label != "STRICT_OK":
        set_progress(batch, "content")
        fixed, preservation = format_v1.preserve_format(description)
        if preservation:
            rule_proposal(
                problem,
                batch,
                classification,
                FieldGroup.CONTENT,
                VERSION,
                {"description": fixed},
                {},
                {
                    "preservation": preservation,
                    "validation": format_v1.validate(fixed).label,
                },
            )
        else:
            if getattr(settings, "STANDARDIZATION_GUIDED_AI_REWRITE", False):
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
            else:
                # Do not buy an unconstrained rewrite that cannot be proven
                # equivalent. Keep the source and continue other field groups.
                held = rule_proposal(
                    problem,
                    batch,
                    classification,
                    FieldGroup.CONTENT,
                    VERSION,
                    {"description": description},
                    {},
                    {"deferred_reason": "preservation_unproven"},
                )
                if held.status == ProposalStatus.PENDING_REVIEW:
                    held.status = ProposalStatus.NEEDS_CLARIFICATION
                    held.save(update_fields=["status"])
    if reuse_editorial:
        set_progress(batch, "existing_solution", reason="editorial_and_tags_covered")
        return
    if not batch_is_open(batch):
        return

    set_progress(batch, "reference")
    reference, evidence = None, None
    attempted_references = []
    for candidate in references(problem, options["submission_id"], batch.created_by):
        if not batch_is_open(batch):
            return
        # Author references may use macros/concatenated literals for filenames.
        # Only the real judge can decide whether their I/O is correct.
        checked = check_candidate(
            problem, candidate.language, candidate.source, preflight_file_io=False
        )
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

    generate_attempts(problem, batch, classification, source_hash, reference, evidence)


def generate_attempts(
    problem,
    batch,
    classification,
    source_hash,
    reference=None,
    evidence=None,
    *,
    step=None,
    feedback="",
):
    options = batch.counters_json["options"]
    primary = getattr(settings, "STANDARDIZATION_GUIDED_MODEL", "openai/gpt-6-luna")
    fallback = getattr(
        settings, "STANDARDIZATION_GUIDED_FALLBACK_MODEL", "openai/gpt-6-sol"
    )
    step = step or (GUIDED_REFERENCE if reference else GUIDED_LUNA)
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
                reason=(
                    "source_clarification"
                    if proposal.evidence_json.get("needs_clarification")
                    else "solution_quality_incomplete"
                ),
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


def resume_after_verification(row):
    """Claim at most the next original, budgeted repair stage after a late WA."""
    from judge.standardization.reconciliation import reconcile_verification

    if (
        row.level != VerificationLevel.FAILED
        or row.details_json.get("reason") not in REPAIRABLE
    ):
        return False
    if not reconcile_verification(row):
        return False
    with transaction.atomic():
        Problem.objects.select_for_update().get(pk=row.proposal.problem_id)
        batch = StandardizationBatch.objects.select_for_update().get(
            pk=row.proposal.batch_id
        )
        if batch.status != BatchStatus.FINISHED:
            return False
        proposal = StandardizationProposal.objects.get(pk=row.proposal_id)
        if (
            proposal.verifications.order_by("-created_at", "-pk").first().pk != row.pk
            or proposal.status != ProposalStatus.PENDING_REVIEW
            or proposal.reviewed_at
            or proposal.decisions.exists()
        ):
            return False
        primary = getattr(settings, "STANDARDIZATION_GUIDED_MODEL", "openai/gpt-6-luna")
        fallback = getattr(
            settings, "STANDARDIZATION_GUIDED_FALLBACK_MODEL", "openai/gpt-6-sol"
        )
        if proposal.step == GUIDED_LUNA:
            step = GUIDED_REPAIR
        elif (
            proposal.step == GUIDED_REPAIR
            and batch.counters_json["options"].get("allow_sol")
            and fallback != primary
        ):
            step = GUIDED_SOL
        else:
            return False
        if (
            batch.proposals.filter(step=step).exists()
            or StandardizationBatch.objects.filter(
                config_version=VERSION, id_from=proposal.problem_id, status__in=ACTIVE
            )
            .exclude(pk=batch.pk)
            .exists()
        ):
            return False
        # Only an explicit timeout in this workflow can reopen it automatically.
        # A manual recheck of an ordinary failure must not start a paid run.
        if (
            not row.details_json.get("submission_id")
            or not proposal.verifications.filter(
                details_json__reason="timeout",
                details_json__submission_id=row.details_json["submission_id"],
                details_json__source_hash=row.details_json.get("source_hash"),
                test_config_hash=row.test_config_hash,
            ).exists()
        ):
            return False
        batch.counters_json = {
            **batch.counters_json,
            "resume_verification": {
                "proposal_id": proposal.pk,
                "verification_id": row.pk,
                "step": step,
            },
        }
        batch.status = BatchStatus.QUEUED
        batch.save(update_fields=["status", "counters_json"])
        from judge.tasks.standardization import run_guided

        transaction.on_commit(lambda: run_guided.delay(batch.pk))
    return True


def run_queued(batch_id):
    if not StandardizationBatch.objects.filter(
        pk=batch_id,
        config_version=VERSION,
        status=BatchStatus.QUEUED,
    ).update(status=BatchStatus.RUNNING):
        return
    batch = StandardizationBatch.objects.select_related("created_by").get(pk=batch_id)
    try:
        problem = Problem.objects.get(pk=batch.id_from)
        resume = batch.counters_json.get("resume_verification")
        if resume:
            from judge.standardization.verification import _language_for

            proposal = batch.proposals.get(pk=resume["proposal_id"])
            row = proposal.verifications.order_by("-created_at", "-pk").first()
            if (
                not row
                or row.pk != resume["verification_id"]
                or proposal.evidence_json.get("statement_hash")
                != compute_statement_hash(problem)
                or row.test_config_hash
                != compute_judge_hash(problem, _language_for(proposal))
                or row.details_json.get("source_hash")
                != sha256(
                    proposal.payload_json.get("cpp_code", "").encode()
                ).hexdigest()
                or proposal.status != ProposalStatus.PENDING_REVIEW
                or proposal.decisions.exists()
            ):
                set_progress(batch, "needs_review", reason="resume_evidence_changed")
                return
            generate_attempts(
                problem,
                batch,
                classify_problem(problem),
                compute_source_hash(problem),
                step=resume["step"],
                feedback=failure_context(
                    proposal, row.details_json.get("reason"), row.details_json
                ),
            )
        else:
            guide(problem, batch)
    except Exception:
        set_progress(batch, "needs_review", reason="workflow_error")
        StandardizationBatch.objects.filter(
            pk=batch.pk, status=BatchStatus.RUNNING
        ).update(status=BatchStatus.FAILED, finished_at=timezone.now())
        raise
    finally:
        batch.refresh_from_db()
        from judge.standardization.routing import record_run_outcome

        record_run_outcome(batch)
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

        batch.refresh_from_db()
        if batch.status == BatchStatus.FINISHED:
            from judge.standardization.production import finalize_run

            finalize_run(batch)


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
