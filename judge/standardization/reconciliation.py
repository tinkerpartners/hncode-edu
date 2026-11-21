"""Reconcile late judge evidence without overriding independent or human holds."""

from django.db import transaction

from judge.models import Problem
from judge.models.standardization import StandardizationBatch, StandardizationProposal
from judge.standardization.enums import BatchStatus, FieldGroup, ProposalStatus
from judge.standardization.hashing import compute_statement_hash


def metadata_ready(proposal, accepted):
    payload, evidence = proposal.payload_json or {}, proposal.evidence_json or {}
    if proposal.field_group == FieldGroup.TAGS:
        return (
            accepted and bool(payload.get("type_ids")) and not payload.get("unresolved")
        )
    return (
        accepted
        and evidence.get("in_range")
        and not evidence.get("insufficient_evidence")
        and bool((evidence.get("reasoning") or "").strip())
    )


def update_metadata(proposals, accepted):
    for proposal in proposals:
        if proposal.field_group not in (FieldGroup.TAGS, FieldGroup.POINTS):
            continue
        with transaction.atomic():
            current = type(proposal).objects.select_for_update().get(pk=proposal.pk)
            _update_metadata(current, accepted)
            # The guide still holds instances returned before the judge ran.
            # Keep its summary in sync without saving those stale instances.
            proposal.status = current.status
            proposal.evidence_json = current.evidence_json


def _update_metadata(proposal, accepted):
    if proposal.reviewed_by_id or proposal.reviewed_at or proposal.decisions.exists():
        return
    evidence = dict(proposal.evidence_json or {})
    ready = metadata_ready(proposal, accepted)
    # An explicit marker lets a later AC release only the judge dependency.
    # Legacy guided proposals also carry the linked solution id; their
    # intrinsic tag/rating checks must still pass before release.
    releasable = (
        proposal.status == ProposalStatus.NEEDS_CLARIFICATION
        and evidence.get("guided")
        and evidence.get("solution_proposal_id")
        and evidence.get("judge_dependency", True)
    )
    if proposal.status == ProposalStatus.DRAFT or (releasable and ready):
        proposal.status = (
            ProposalStatus.PENDING_REVIEW
            if ready
            else ProposalStatus.NEEDS_CLARIFICATION
        )
        evidence["judge_dependency"] = not accepted
        proposal.evidence_json = evidence
        proposal.save(update_fields=["status", "evidence_json"])


def reconcile_verification(row):
    from judge.standardization.verification import current_full_verification

    proposal = row.proposal
    if not (proposal.evidence_json or {}).get("guided") or not proposal.batch_id:
        return False
    with transaction.atomic():
        # Same outer lock order as apply; do not hold a proposal lock while
        # acquiring the problem/batch lock.
        Problem.objects.select_for_update().get(pk=proposal.problem_id)
        batch = StandardizationBatch.objects.select_for_update().get(
            pk=proposal.batch_id
        )
        proposal = StandardizationProposal.objects.select_for_update().get(
            pk=proposal.pk
        )
        if batch.status not in (BatchStatus.RUNNING, BatchStatus.FINISHED):
            return False
        counters = dict(batch.counters_json or {})
        latest = (
            batch.proposals.filter(field_group=FieldGroup.SOLUTION)
            .order_by("-pk")
            .first()
        )
        if not latest or latest.pk != proposal.pk:
            return False
        if proposal.verifications.order_by("-created_at", "-pk").first().pk != row.pk:
            return False
        evidence = proposal.evidence_json or {}
        if evidence.get("statement_hash") != compute_statement_hash(proposal.problem):
            return False
        if (
            proposal.status not in (ProposalStatus.PENDING_REVIEW, ProposalStatus.DRAFT)
            or evidence.get("needs_clarification")
            or evidence.get("solution_quality_errors")
            or proposal.reviewed_at
            or proposal.decisions.exists()
        ):
            return False
        accepted = current_full_verification(proposal)
        # Recheck code/config identity for failures as well as passes. Old
        # verdicts remain audit evidence but cannot drive the current workflow.
        from hashlib import sha256
        from judge.standardization.hashing import compute_judge_hash
        from judge.standardization.verification import _language_for

        details = row.details_json or {}
        source = (proposal.payload_json or {}).get("cpp_code") or ""
        if details.get("source_hash") != sha256(
            source.encode()
        ).hexdigest() or row.test_config_hash != compute_judge_hash(
            proposal.problem, _language_for(proposal)
        ):
            return False
        siblings = [
            p
            for p in batch.proposals.select_for_update().filter(
                field_group__in=(FieldGroup.TAGS, FieldGroup.POINTS)
            )
            if (p.evidence_json or {}).get("solution_proposal_id") == proposal.pk
        ]
        update_metadata(siblings, accepted)
        latest_groups = {}
        for item in batch.proposals.order_by("-pk"):
            latest_groups.setdefault(item.field_group, item)
        held = [
            group
            for group, item in latest_groups.items()
            if item.status
            in (
                ProposalStatus.NEEDS_SOURCE,
                ProposalStatus.NEEDS_CLARIFICATION,
                ProposalStatus.ERROR,
                ProposalStatus.REJECTED,
                ProposalStatus.REWORK,
                ProposalStatus.STALE,
            )
        ]
        counters.update(
            phase=(
                ("partial_ready" if held else "ready") if accepted else "needs_review"
            ),
            reason=(
                (
                    (
                        "content_needs_review"
                        if FieldGroup.CONTENT in held
                        else "metadata_needs_review"
                    )
                    if held
                    else "full_score"
                )
                if accepted
                else details.get("reason", "judge_unavailable")
            ),
            proposal_id=proposal.pk,
            verification_id=row.pk,
            held_groups=held,
        )
        batch.counters_json = counters
        batch.save(update_fields=["counters_json"])
        if accepted and batch.status == BatchStatus.FINISHED:
            from judge.tasks.standardization import finalize_guided

            transaction.on_commit(lambda: finalize_guided.delay(batch.pk))
    return True
