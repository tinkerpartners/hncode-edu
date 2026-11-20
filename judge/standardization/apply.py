"""Apply and restore standardization proposals (spec F10).

Every write goes through this module so that one place enforces:

* only APPROVED / AUTO_ACCEPTED proposals are applied;
* a conflict check per field group against the snapshot (someone edited the
  same group since the proposal was drafted -> STALE, never a silent overwrite);
* no apply while the problem is in a running contest;
* ORM ``save()`` / m2m ``.add()`` so cache signals fire, ``_bypass_points_cap``
  so a save never clamps points on a private problem, ``reversion`` history;
* an append-only ``StandardizationDecision`` row with before/after state;
* derived-score sync via ``StandardizationRescoreJob`` when points/partial
  changed, scheduled ``on_commit`` and never run inside the transaction.

Restore writes the snapshot's before-state back through the same group
appliers, refuses when the group no longer matches the applied after-state,
and never re-publishes a Solution.
"""

import logging
from dataclasses import dataclass, field
from datetime import timedelta

import reversion
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from judge.models import ContestProblem, Problem
from judge.models.problem import Solution
from judge.models.standardization import (
    StandardizationDecision,
    StandardizationProposal,
)
from judge.standardization import config_profile
from judge.standardization.enums import (
    APPLICABLE_STATUSES,
    PROPOSAL_TRANSITIONS,
    ActorKind,
    DecisionAction,
    FieldGroup,
    ProposalStatus,
)
from judge.standardization.rescore import schedule_rescore
from judge.standardization.snapshot import capture_group

logger = logging.getLogger(__name__)


class StandardizationError(Exception):
    pass


class ProposalStateError(StandardizationError):
    pass


class StaleSourceError(StandardizationError):
    pass


class ContestBlockedError(StandardizationError):
    pass


class RestoreConflictError(StandardizationError):
    pass


@dataclass
class ApplyResult:
    diff: dict = field(default_factory=dict)
    needs_rescore: bool = False
    rescore_reason: str = ""


def transition(proposal, to_status, *, save=True):
    allowed = PROPOSAL_TRANSITIONS.get(ProposalStatus(proposal.status), set())
    if ProposalStatus(to_status) not in allowed:
        raise ProposalStateError(
            f"proposal #{proposal.pk}: {proposal.status} -> {to_status} is not allowed"
        )
    proposal.status = to_status
    if save:
        proposal.save(update_fields=["status"])
    return proposal


def is_in_live_contest(problem, at=None):
    """True when the problem is in a contest that is running *and* started
    recently. Year-long "practice" contests (hundreds on hncode) are open
    windows, not exams; without the start bound they would block their
    problems forever. ``STANDARDIZATION_CONTEST_BLOCK_MAX_DAYS`` (default 7)
    is the longest window still treated as an exam."""
    at = at or timezone.now()
    max_days = getattr(settings, "STANDARDIZATION_CONTEST_BLOCK_MAX_DAYS", 7)
    return ContestProblem.objects.filter(
        problem=problem,
        contest__start_time__lte=at,
        contest__start_time__gte=at - timedelta(days=max_days),
        contest__end_time__gte=at,
    ).exists()


def _actor_fields(actor):
    if actor is None:
        return {"actor": None, "actor_kind": ActorKind.SERVICE}
    return {"actor": actor, "actor_kind": ActorKind.HUMAN}


def _record(proposal, action, actor, **extra):
    return StandardizationDecision.objects.create(
        proposal=proposal, action=action, **_actor_fields(actor), **extra
    )


# --- per-group appliers ---------------------------------------------------


def _save_problem(problem):
    problem._bypass_points_cap = True
    problem.save()


def _apply_content(problem, payload, proposal):
    problem.description = payload["description"]
    _save_problem(problem)
    return ApplyResult(diff={"description": True})


def _apply_solution(problem, payload, proposal):
    if (proposal.evidence_json or {}).get("guided"):
        from judge.standardization.verification import current_full_verification

        if not current_full_verification(proposal, problem):
            raise StandardizationError(
                "Cần chấm đạt đủ điểm trên bộ test hiện hành trước khi áp dụng lời giải."
            )
    try:
        solution = problem.solution
        created = False
    except Exception:  # RelatedObjectDoesNotExist
        solution = Solution(problem=problem, publish_on=timezone.now())
        created = True
    if "content" in payload:
        solution.content = payload["content"]
    solution.is_public = False  # spec §2: always private
    solution.save()
    return ApplyResult(diff={"created": created, "is_public": False})


def _apply_tags(problem, payload, proposal):
    evidence = proposal.evidence_json or {}
    if evidence.get("guided") and evidence.get("solution_proposal_id"):
        from judge.standardization.verification import current_full_verification

        solution = StandardizationProposal.objects.filter(
            pk=evidence["solution_proposal_id"],
            problem=problem,
            field_group=FieldGroup.SOLUTION,
        ).first()
        if not solution or not current_full_verification(solution, problem):
            raise StandardizationError(
                "Cần chấm lại lời giải làm căn cứ cho bộ tag này."
            )
    target = set(int(x) for x in payload["type_ids"])
    current = set(problem.types.values_list("id", flat=True))
    if (
        evidence.get("guided_workflow")
        or evidence.get("guided")
        or proposal.provider not in ("", "rules")
    ):
        target |= current  # AI must never delete a teacher's existing tags.
    added, removed = sorted(target - current), sorted(current - target)
    if added:
        problem.types.add(*added)
    if removed:
        problem.types.remove(*removed)
    return ApplyResult(diff={"added": added, "removed": removed})


def _apply_points(problem, payload, proposal):
    evidence = proposal.evidence_json or {}
    if evidence.get("guided") and evidence.get("solution_proposal_id"):
        from judge.standardization.verification import current_full_verification

        solution = StandardizationProposal.objects.filter(
            pk=evidence["solution_proposal_id"],
            problem=problem,
            field_group=FieldGroup.SOLUTION,
        ).first()
        if not solution or not current_full_verification(solution, problem):
            raise StandardizationError(
                "Cần chấm lại lời giải làm căn cứ cho độ khó này."
            )
    new = float(payload["points"])
    old = float(problem.points)
    problem.points = new
    _save_problem(problem)
    return ApplyResult(
        diff={"points_from": old, "points_to": new},
        needs_rescore=old != new,
        rescore_reason="points",
    )


def _apply_config(problem, payload, proposal):
    ctx = config_profile.resolve_context(
        require_judge=bool(payload.get("require_judge", True))
    )
    diff = config_profile.plan_config(problem, ctx)
    config_profile.apply_config_diff(problem, diff)
    return ApplyResult(
        diff=diff.to_json(),
        needs_rescore=diff.partial_changed,
        rescore_reason="partial",
    )


APPLIERS = {
    FieldGroup.CONTENT: _apply_content,
    FieldGroup.SOLUTION: _apply_solution,
    FieldGroup.TAGS: _apply_tags,
    FieldGroup.POINTS: _apply_points,
    FieldGroup.CONFIG: _apply_config,
}

# CONFIG is recomputed from the live state at apply time (monotone rules), so
# it has no before-state to conflict with. Every other group must still match
# what the proposal was drafted against.
CONFLICT_CHECKED_GROUPS = {
    FieldGroup.CONTENT,
    FieldGroup.SOLUTION,
    FieldGroup.TAGS,
    FieldGroup.POINTS,
}


def check_conflict(problem, proposal):
    """Return (ok, current_state). ok=False means the group changed since snapshot."""
    group = FieldGroup(proposal.field_group)
    current = capture_group(problem, group)
    if group not in CONFLICT_CHECKED_GROUPS:
        return True, current
    before = proposal.before_json
    if (
        group == FieldGroup.SOLUTION
        and before.get("is_public") is True
        and current.get("is_public") is False
    ):
        # The mechanical config step only makes Solutions private. It must
        # not invalidate an otherwise unchanged editorial from the same snapshot.
        config = StandardizationProposal.objects.filter(
            problem=problem,
            snapshot=proposal.snapshot,
            field_group=FieldGroup.CONFIG,
            status=ProposalStatus.APPLIED,
        ).exists()
        if config and {**before, "is_public": False} == current:
            return True, current
    return current == proposal.before_json, current


@dataclass
class _Refusal:
    """A refusal found inside the apply transaction. Recorded in its own
    transaction afterwards, so the audit row survives the rollback."""

    exc: Exception
    action: str
    new_status: str | None = None
    conflict: dict = field(default_factory=dict)
    note: str = ""


def _refuse(proposal, actor, refusal):
    with transaction.atomic():
        proposal = StandardizationProposal.objects.select_for_update().get(
            pk=proposal.pk
        )
        if refusal.new_status is not None:
            transition(proposal, refusal.new_status)
        _record(
            proposal,
            refusal.action,
            actor,
            conflict_json=refusal.conflict,
            note=refusal.note,
        )
    raise refusal.exc


def _precheck(problem, proposal):
    """Return a _Refusal or None. Runs under the row locks of the caller."""
    group = FieldGroup(proposal.field_group)
    ok, current = check_conflict(problem, proposal)
    if not ok:
        return (
            _Refusal(
                exc=StaleSourceError(
                    f"proposal #{proposal.pk}: {group} changed since snapshot"
                ),
                action=DecisionAction.CONFLICT,
                new_status=ProposalStatus.STALE,
                conflict={"expected": proposal.before_json, "current": current},
                note="group changed since snapshot",
            ),
            current,
        )
    if is_in_live_contest(problem):
        return (
            _Refusal(
                exc=ContestBlockedError(
                    f"problem {problem.code} is in a running contest"
                ),
                action=DecisionAction.BLOCKED,
                new_status=ProposalStatus.BLOCKED_CONTEST,
                note="problem is in a running contest",
            ),
            current,
        )
    return None, current


def apply_proposal(
    proposal, actor=None, *, policy_version="", conditions=None, note=""
):
    """Apply one proposal. Raises on refusal; the refusal is also recorded."""
    refusal = None
    with transaction.atomic(), reversion.create_revision():
        problem = Problem.objects.select_for_update().get(pk=proposal.problem_id)
        proposal = (
            StandardizationProposal.objects.select_for_update()
            .select_related("snapshot")
            .get(pk=proposal.pk)
        )
        group = FieldGroup(proposal.field_group)

        if proposal.status not in APPLICABLE_STATUSES:
            raise ProposalStateError(
                f"proposal #{proposal.pk} is {proposal.status}, not approved"
            )

        refusal, current = _precheck(problem, proposal)
        if refusal is not None:
            transaction.set_rollback(True)
        else:
            return _do_apply(
                problem,
                proposal,
                group,
                current,
                actor,
                policy_version=policy_version,
                conditions=conditions,
                note=note,
            )
    _refuse(proposal, actor, refusal)


def _do_apply(
    problem, proposal, group, current, actor, *, policy_version, conditions, note
):
    before = current
    result = APPLIERS[group](problem, proposal.payload_json or {}, proposal)
    problem.refresh_from_db()
    after = capture_group(problem, group)

    if actor is not None:
        reversion.set_user(actor.user)
    reversion.set_comment(
        f"standardization {group}/{proposal.step} proposal #{proposal.pk}"
    )

    now = timezone.now()
    proposal.status = ProposalStatus.APPLIED
    proposal.applied_at = now
    proposal.applied_by = actor
    proposal.after_json = after
    if result.diff:
        proposal.diff_json = result.diff
    proposal.save(
        update_fields=["status", "applied_at", "applied_by", "after_json", "diff_json"]
    )

    decision = _record(
        proposal,
        DecisionAction.APPLY,
        actor,
        policy_version=policy_version,
        conditions_json=conditions or {},
        before_json=before,
        after_json=after,
        note=note,
    )

    if result.needs_rescore:
        job = schedule_rescore(problem, reason=result.rescore_reason)
        if job is not None:
            decision.rescore_job = job
            decision.save(update_fields=["rescore_job"])
    return proposal


def _restore_payload(proposal):
    group = FieldGroup(proposal.field_group)
    before = proposal.before_json
    if group == FieldGroup.CONTENT:
        return {"description": before.get("description", "")}
    if group == FieldGroup.SOLUTION:
        if not before.get("exists"):
            return None  # we created it; delete on restore
        return {"content": before.get("content", "")}
    if group == FieldGroup.TAGS:
        return {"type_ids": before.get("type_ids", [])}
    if group == FieldGroup.POINTS:
        return {"points": before.get("points")}
    return None


def restore_proposal(proposal, actor=None, *, note=""):
    """Put the group back to its snapshot before-state. Never re-publishes a Solution."""
    refusal = None
    with transaction.atomic(), reversion.create_revision():
        problem = Problem.objects.select_for_update().get(pk=proposal.problem_id)
        proposal = (
            StandardizationProposal.objects.select_for_update()
            .select_related("snapshot")
            .get(pk=proposal.pk)
        )
        group = FieldGroup(proposal.field_group)
        if proposal.status != ProposalStatus.APPLIED:
            raise ProposalStateError(f"proposal #{proposal.pk} is not applied")

        current = capture_group(problem, group)
        if current != proposal.after_json:
            refusal = _Refusal(
                exc=RestoreConflictError(
                    f"proposal #{proposal.pk}: group changed since apply"
                ),
                action=DecisionAction.CONFLICT,
                conflict={"expected": proposal.after_json, "current": current},
                note="group changed since apply; manual restore needed",
            )
        elif is_in_live_contest(problem):
            refusal = _Refusal(
                exc=ContestBlockedError(
                    f"problem {problem.code} is in a running contest"
                ),
                action=DecisionAction.BLOCKED,
                note="problem is in a running contest",
            )
        if refusal is not None:
            transaction.set_rollback(True)
        else:
            return _do_restore(problem, proposal, group, current, actor, note=note)
    _refuse(proposal, actor, refusal)


def _do_restore(problem, proposal, group, current, actor, *, note):
    needs_rescore, reason = False, ""
    if group == FieldGroup.CONFIG:
        diff = config_profile.ConfigDiff.from_json(proposal.diff_json)
        config_profile.revert_config_diff(problem, diff)
        needs_rescore, reason = diff.partial_changed, "partial"
    elif group == FieldGroup.SOLUTION and _restore_payload(proposal) is None:
        Solution.objects.filter(problem=problem).delete()
    else:
        payload = _restore_payload(proposal)
        result = APPLIERS[group](problem, payload, proposal)
        needs_rescore, reason = result.needs_rescore, result.rescore_reason

    problem.refresh_from_db()
    after = capture_group(problem, group)
    if actor is not None:
        reversion.set_user(actor.user)
    reversion.set_comment(f"standardization restore proposal #{proposal.pk}")

    proposal.status = ProposalStatus.RESTORED
    proposal.save(update_fields=["status"])
    decision = _record(
        proposal,
        DecisionAction.RESTORE,
        actor,
        before_json=current,
        after_json=after,
        note=note,
    )
    if needs_rescore:
        job = schedule_rescore(problem, reason=reason)
        if job is not None:
            decision.rescore_job = job
            decision.save(update_fields=["rescore_job"])
    return proposal


def review_proposal(proposal, actor, action, note=""):
    """Human review: approve / reject / rework. Returns the proposal."""
    mapping = {
        DecisionAction.APPROVE: ProposalStatus.APPROVED,
        DecisionAction.REJECT: ProposalStatus.REJECTED,
        DecisionAction.REWORK: ProposalStatus.REWORK,
    }
    action = DecisionAction(action)
    if action not in mapping:
        raise ProposalStateError(f"{action} is not a review action")
    with transaction.atomic():
        proposal = StandardizationProposal.objects.select_for_update().get(
            pk=proposal.pk
        )
        transition(proposal, mapping[action], save=False)
        proposal.reviewed_by = actor
        proposal.reviewed_at = timezone.now()
        proposal.review_note = note
        proposal.save(
            update_fields=["status", "reviewed_by", "reviewed_at", "review_note"]
        )
        _record(proposal, action, actor, note=note)
    return proposal
