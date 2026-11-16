"""Message Batches runner for the 20k run (spec F09, AC16, AC17).

``submit_step_batches`` prepares every selected problem for one step (same
``prepare`` as the sync path), submits the requests in provider batches of
``STANDARDIZATION_LLM_BATCH_CHUNK`` with ``custom_id`` = the step's primary
dedupe key, and leaves a DRAFT proposal per field group pointing at the
provider batch. ``poll_job`` / ``poll_all`` fetch ended batches and turn
each result into the same proposals the sync path would create:

* succeeded   -> validate -> finalize (PENDING_REVIEW / NEEDS_CLARIFICATION)
* errored invalid_request / refusal / truncated -> ERROR
* other errors, expired, canceled -> re-queued once (DRAFT, pending_batch=false),
  then ERROR
* result for a CANCELLED standardization batch -> stays DRAFT with late=true,
  never finalized (AC17)
* source changed since submission -> STALE

Every result writes a ledger row (batch pricing comes from the provider).
"""

import logging
from collections import Counter

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from judge.models import Problem
from judge.models.standardization import LLMBatchJob, StandardizationProposal
from judge.standardization import steps
from judge.standardization.budget import (
    BudgetExceeded,
    batch_is_open,
    check_budget,
    pause_batch,
)
from judge.standardization.enums import BatchStatus, ProposalStatus
from judge.standardization.hashing import compute_source_hash, dedupe_key
from judge.standardization.llm import (
    LEDGER_ERROR,
    LEDGER_EXPIRED,
    LEDGER_OK,
    StepValidationError,
    record_usage,
    validate_payload,
)
from llm_service.providers import LLMProviderError, get_provider

logger = logging.getLogger(__name__)

MAX_REQUEUE = 1
FINAL_ERROR_CODES = ("invalid_request", "refusal", "truncated", "invalid_json")


class _StoredClassification:
    """Shim so ``finalize`` can read the classification saved at submit time."""

    def __init__(self, data):
        self.data = data or {}
        self.source_kinds = self.data.get("source_kinds", [])
        self.quality_flags = self.data.get("quality_flags", [])
        self.primary_source = self.data.get("primary_source", "")
        self.extracted_text = ""

    def as_json(self):
        return self.data


def _chunk_size(chunk):
    return chunk or getattr(settings, "STANDARDIZATION_LLM_BATCH_CHUNK", 2000)


def submit_step_batches(batch, step, problems, provider=None, chunk=None):
    """Submit one step for ``problems`` inside ``batch``; returns a summary."""
    provider = provider or get_provider()
    if not provider.supports_batches:
        raise LLMProviderError("config", f"{provider.name} has no batch API")
    chunk = _chunk_size(chunk)
    counters = Counter()
    jobs = []
    pending = []

    def flush():
        if not pending:
            return True
        try:
            check_budget(batch=batch)
        except BudgetExceeded as exc:
            with transaction.atomic():
                pause_batch(batch, str(exc))
            counters["budget"] += len(pending)
            pending.clear()
            return False
        provider_batch_id = provider.submit_batch([p.request for p in pending])
        logger.info(
            "submitted provider batch %s (%d requests)", provider_batch_id, len(pending)
        )
        with transaction.atomic():
            job = LLMBatchJob.objects.create(
                provider=provider.name,
                provider_batch_id=provider_batch_id,
                step=step,
                batch=batch,
                request_count=len(pending),
            )
            for prepared in pending:
                steps.create_drafts(prepared, batch, provider_batch_id)
        jobs.append(job.pk)
        counters["submitted"] += len(pending)
        pending.clear()
        return True

    for problem in problems:
        if not batch_is_open(batch):
            counters["batch_closed"] += 1
            break
        prepared = steps.prepare(step, problem, batch)
        if prepared.result is not None:
            counters[prepared.result.skipped or "done"] += 1
            continue
        pending.append(prepared)
        if len(pending) >= chunk and not flush():
            break
    flush()
    return {
        "batch_id": batch.pk,
        "step": step,
        "jobs": jobs,
        "counters": dict(counters),
    }


def _rebuild_prepared(primary):
    problem = Problem.objects.select_related("group").get(pk=primary.problem_id)
    step = primary.step
    keys = {
        group: dedupe_key(
            problem.pk,
            primary.snapshot.source_hash,
            group,
            step,
            primary.prompt_version,
        )
        for group in steps.STEP_GROUPS[step]
    }
    prepared = steps.Prepared(
        step=step, problem=problem, source_hash=primary.snapshot.source_hash, keys=keys
    )
    prepared.snapshot = primary.snapshot
    prepared.classification = _StoredClassification(
        (primary.evidence_json or {}).get("classification")
    )
    drafts = {}
    for proposal in StandardizationProposal.objects.filter(
        dedupe_key__in=list(keys.values())
    ):
        if proposal.status == ProposalStatus.DRAFT:
            drafts[proposal.field_group] = proposal
        else:
            prepared.existing[proposal.field_group] = proposal
    return prepared, drafts


def _mark_drafts(drafts, status=None, **evidence_updates):
    for draft in drafts.values():
        evidence = dict(draft.evidence_json or {})
        evidence.update(evidence_updates)
        draft.evidence_json = evidence
        if status is not None and status != draft.status:
            steps.transition(draft, status, save=False)
        draft.save()


def _requeue_or_fail(prepared, drafts, batch, code, ledger):
    attempts = (
        max((d.evidence_json or {}).get("requeue", 0) for d in drafts.values())
        if drafts
        else 0
    )
    if code in FINAL_ERROR_CODES or attempts >= MAX_REQUEUE:
        steps.fail(prepared, f"provider:{code}", batch, ledger)
        return "error"
    _mark_drafts(drafts, pending_batch=False, requeue=attempts + 1, last_error=code)
    return "requeued"


def ingest_result(job, result, provider):
    """Apply one batch result; returns an outcome label for the counters."""
    primary = (
        StandardizationProposal.objects.select_related("problem", "snapshot", "batch")
        .filter(dedupe_key=result.custom_id)
        .first()
    )
    if primary is None:
        logger.warning(
            "batch %s: no proposal for custom_id %s",
            job.provider_batch_id,
            result.custom_id,
        )
        return "orphan"
    if primary.status != ProposalStatus.DRAFT or not (primary.evidence_json or {}).get(
        "pending_batch"
    ):
        return "already_final"
    prepared, drafts = _rebuild_prepared(primary)
    problem, step, batch = prepared.problem, prepared.step, job.batch
    response = result.response
    ledger_status = (
        LEDGER_OK
        if result.status == "succeeded"
        else (
            LEDGER_EXPIRED if result.status in ("expired", "canceled") else LEDGER_ERROR
        )
    )
    ledger = record_usage(
        response,
        provider=provider.name,
        model=(response.model if response else ""),
        step=step,
        status=ledger_status,
        problem=problem,
        batch=batch,
        proposal=primary,
        provider_batch_id=job.provider_batch_id,
    )
    with transaction.atomic():
        if batch.status in (BatchStatus.CANCELLED, BatchStatus.CANCEL_REQUESTED):
            _mark_drafts(drafts, late=True, pending_batch=False, result=result.status)
            return "late"
        if compute_source_hash(problem) != prepared.source_hash:
            _mark_drafts(
                drafts, ProposalStatus.STALE, pending_batch=False, result=result.status
            )
            return "stale"
        if result.status != "succeeded":
            code = result.error.code if result.error else result.status
            return _requeue_or_fail(prepared, drafts, batch, code, ledger)
        try:
            payload = response.parsed_json()
            validate_payload(payload, steps.STEP_SCHEMAS[step])
        except (LLMProviderError, StepValidationError) as exc:
            ledger.status = LEDGER_ERROR
            ledger.save(update_fields=["status"])
            steps.fail(prepared, f"schema:{exc}", batch, ledger)
            return "schema_error"
        steps.finalize(prepared, payload, ledger, batch)
        return "finalized"


def poll_job(job, provider=None):
    provider = provider or get_provider(job.provider)
    status = provider.poll_batch(job.provider_batch_id)
    job.status = status.status[:20]
    if not status.ended:
        job.save(update_fields=["status"])
        return {
            "job": job.pk,
            "ended": False,
            "status": status.status,
            "counts": status.counts,
        }
    counters = Counter()
    for result in provider.iter_batch_results(job.provider_batch_id):
        try:
            counters[ingest_result(job, result, provider)] += 1
        except Exception:  # noqa: BLE001 - one bad result must not block the rest
            logger.exception(
                "batch %s: failed to ingest %s", job.provider_batch_id, result.custom_id
            )
            counters["ingest_error"] += 1
    job.ended_at = timezone.now()
    job.results_ingested = True
    job.status = "ended"
    job.save(update_fields=["status", "ended_at", "results_ingested"])
    return {
        "job": job.pk,
        "ended": True,
        "status": "ended",
        "counts": status.counts,
        "ingested": dict(counters),
    }


def poll_all(provider=None):
    out = []
    for job in LLMBatchJob.objects.filter(results_ingested=False).order_by(
        "submitted_at"
    ):
        try:
            out.append(poll_job(job, provider))
        except LLMProviderError as exc:
            logger.warning(
                "poll of batch %s failed (%s)", job.provider_batch_id, exc.code
            )
            out.append({"job": job.pk, "ended": False, "error": exc.code})
    return out
