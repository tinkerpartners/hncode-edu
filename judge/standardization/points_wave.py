"""Roll CF-style difficulty into ``Problem.points`` in bounded waves (PR-G).

A wave is a ``StandardizationBatch`` of kind POINTS_WAVE that applies a
bounded number of APPROVED POINTS proposals through ``apply_proposal`` and
records, in ``counters_json``, exactly which proposals it applied, skipped or
failed and which rescore jobs it created. That record is what makes a wave
auditable, resumable (re-running selects only what is still APPROVED) and
reversible (``rollback_wave`` restores the applied proposals and nothing else).

Points on public problems feed every leaderboard, so a wave is bracketed by
``snapshot_leaderboard`` before and ``compare_leaderboard`` after: the
snapshot is the before-state the owner can read, the comparison shows who
moved and by how much once the rescore queue has drained.

Nothing here talks to a model or a judge; it is pure ORM + the apply layer.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from judge.models import Problem, Profile
from judge.models.standardization import (
    StandardizationBatch,
    StandardizationProposal,
    StandardizationRescoreJob,
)
from judge.standardization.apply import (
    ContestBlockedError,
    ProposalStateError,
    StaleSourceError,
    RestoreConflictError,
    apply_proposal,
    restore_proposal,
)
from judge.standardization.enums import (
    BatchKind,
    BatchStatus,
    FieldGroup,
    ProposalStatus,
    RescoreStatus,
)

POLICY_VERSION = "points_wave_v1"
SNAPSHOT_VERSION = 1
SNAPSHOT_FIELDS = ("points", "performance_points", "problem_count")


class WaveError(Exception):
    pass


# --- leaderboard snapshot --------------------------------------------------


def _leaderboard_rows():
    """Every profile that holds any score. Unlisted profiles are kept (they
    still own points; the public ranking merely hides them) and flagged."""
    qs = (
        Profile.objects.filter(
            Q(points__gt=0) | Q(performance_points__gt=0) | Q(problem_count__gt=0)
        )
        .select_related("user")
        .order_by("-performance_points", "-problem_count", "id")
    )
    rows = []
    for rank, p in enumerate(qs.iterator(chunk_size=2000), start=1):
        rows.append(
            {
                "id": p.id,
                "username": p.user.username,
                "points": round(float(p.points), 2),
                "performance_points": round(float(p.performance_points), 2),
                "problem_count": p.problem_count,
                "is_unlisted": bool(p.is_unlisted),
                "rank": rank,
            }
        )
    return rows


def snapshot_leaderboard(path, *, label=""):
    """Dump Profile(id, points, performance_points, problem_count, rank) to a
    new JSON file. Refuses to overwrite: a snapshot is the one copy of the
    before-state, so clobbering it would destroy the rollback evidence."""
    path = Path(path)
    rows = _leaderboard_rows()
    doc = {
        "version": SNAPSHOT_VERSION,
        "label": label,
        "created_at": timezone.now().isoformat(),
        "count": len(rows),
        "profiles": rows,
    }
    with path.open("x", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False)
    return {"path": str(path), "profiles": len(rows), "created_at": doc["created_at"]}


def load_snapshot(path):
    with Path(path).open(encoding="utf-8") as fh:
        doc = json.load(fh)
    if doc.get("version") != SNAPSHOT_VERSION:
        raise WaveError(f"{path}: unsupported snapshot version {doc.get('version')!r}")
    return doc


def compare_leaderboard(before_path, *, top=20):
    """Compare a snapshot with the live leaderboard. Returns counts of changed
    profiles plus the biggest movers by points and by rank; the full per-profile
    delta list is included under ``changes`` so the caller can write it out."""
    before = {row["id"]: row for row in load_snapshot(before_path)["profiles"]}
    after = {row["id"]: row for row in _leaderboard_rows()}
    changes = []
    for pid in sorted(set(before) | set(after)):
        b, a = before.get(pid), after.get(pid)
        if b is None or a is None:
            changes.append(
                {
                    "id": pid,
                    "username": (a or b)["username"],
                    "status": "new" if b is None else "gone",
                }
            )
            continue
        delta = {f: round(a[f] - b[f], 2) for f in SNAPSHOT_FIELDS if a[f] != b[f]}
        if a["rank"] != b["rank"]:
            delta["rank"] = a["rank"] - b["rank"]
        if delta:
            changes.append(
                {
                    "id": pid,
                    "username": a["username"],
                    "is_unlisted": a["is_unlisted"],
                    "before": {f: b[f] for f in SNAPSHOT_FIELDS + ("rank",)},
                    "after": {f: a[f] for f in SNAPSHOT_FIELDS + ("rank",)},
                    "delta": delta,
                }
            )
    moved = [c for c in changes if "delta" in c]
    by_points = sorted(
        moved, key=lambda c: abs(c["delta"].get("points", 0)), reverse=True
    )
    by_rank = sorted(moved, key=lambda c: abs(c["delta"].get("rank", 0)), reverse=True)
    return {
        "before": str(before_path),
        "compared_at": timezone.now().isoformat(),
        "profiles_before": len(before),
        "profiles_after": len(after),
        "changed": len(moved),
        "rank_changed": sum(1 for c in moved if "rank" in c["delta"]),
        "top_by_points": by_points[:top],
        "top_by_rank": by_rank[:top],
        "changes": changes,
    }


# --- wave selection --------------------------------------------------------


def eligible_proposals(*, from_id=None, through_id=None, codes=None, limit=None):
    """APPROVED POINTS proposals, newest per problem, ordered by problem id.

    AUTO_ACCEPTED is deliberately excluded: a wave is the manual-approval
    rollout; auto-accepted points arrive with the policy layer (PR-H)."""
    qs = StandardizationProposal.objects.filter(
        field_group=FieldGroup.POINTS, status=ProposalStatus.APPROVED
    )
    if from_id is not None:
        qs = qs.filter(problem_id__gte=from_id)
    if through_id is not None:
        qs = qs.filter(problem_id__lte=through_id)
    if codes:
        qs = qs.filter(problem__code__in=list(codes))
    qs = qs.select_related("problem").order_by("problem_id", "-created_at", "-id")
    picked, seen = [], set()
    for proposal in qs.iterator(chunk_size=1000):
        if proposal.problem_id in seen:
            continue
        seen.add(proposal.problem_id)
        picked.append(proposal)
        if limit is not None and len(picked) >= limit:
            break
    return picked


def plan_wave(proposals):
    """Dry-run summary of what a wave over ``proposals`` would change."""
    rows = []
    for p in proposals:
        before = float((p.before_json or {}).get("points") or p.problem.points)
        after = float((p.payload_json or {}).get("points") or 0)
        rows.append(
            {
                "proposal": p.pk,
                "problem_id": p.problem_id,
                "code": p.problem.code,
                "is_public": p.problem.is_public,
                "points_from": before,
                "points_to": after,
                "delta": round(after - before, 2),
            }
        )
    return {
        "proposals": len(rows),
        "public_problems": sum(1 for r in rows if r["is_public"]),
        "id_from": min((r["problem_id"] for r in rows), default=0),
        "id_through": max((r["problem_id"] for r in rows), default=0),
        "rows": rows,
    }


# --- running a wave --------------------------------------------------------


@dataclass
class WaveResult:
    batch: StandardizationBatch
    applied: list = field(default_factory=list)
    stale: list = field(default_factory=list)
    blocked: list = field(default_factory=list)
    skipped: list = field(default_factory=list)
    errors: dict = field(default_factory=dict)
    rescore_jobs: list = field(default_factory=list)

    def counters(self):
        return {
            "applied": self.applied,
            "stale": self.stale,
            "blocked": self.blocked,
            "skipped": self.skipped,
            "errors": self.errors,
            "rescore_jobs": self.rescore_jobs,
            "applied_count": len(self.applied),
        }


def _wave_counters(batch):
    c = dict(batch.counters_json or {})
    for key in ("applied", "stale", "blocked", "skipped", "rescore_jobs", "restored"):
        c.setdefault(key, [])
    c.setdefault("errors", {})
    c.setdefault("restore_conflicts", {})
    return c


def run_wave(name, proposals, actor=None, *, snapshot_path=None, note=""):
    """Apply ``proposals`` as one POINTS_WAVE batch.

    Each proposal is applied in its own transaction via ``apply_proposal`` so
    one refusal (stale, contest) never rolls back its neighbours; refusals are
    recorded by the apply layer and tallied here. The batch row is updated
    after every proposal, so a crash leaves an accurate partial record.
    """
    if not proposals:
        raise WaveError("nothing to apply: no APPROVED points proposals selected")
    ids = [p.problem_id for p in proposals]
    batch = StandardizationBatch.objects.create(
        name=name or f"points wave {timezone.now():%Y-%m-%d %H:%M}",
        kind=BatchKind.POINTS_WAVE,
        id_from=min(ids),
        id_through=max(ids),
        status=BatchStatus.RUNNING,
        steps_json=["points_wave"],
        config_version=POLICY_VERSION,
        created_by=actor,
        counters_json={
            "selected": [p.pk for p in proposals],
            "snapshot": str(snapshot_path) if snapshot_path else "",
            "note": note,
        },
    )
    result = WaveResult(batch=batch)
    conditions = {"wave_batch": batch.pk, "snapshot": str(snapshot_path or "")}
    for proposal in proposals:
        try:
            applied = apply_proposal(
                proposal,
                actor,
                policy_version=POLICY_VERSION,
                conditions=conditions,
                note=note or f"points wave #{batch.pk}",
            )
        except StaleSourceError:
            result.stale.append(proposal.pk)
        except ContestBlockedError:
            result.blocked.append(proposal.pk)
        except ProposalStateError:
            result.skipped.append(proposal.pk)  # approved -> something else meanwhile
        except Exception as exc:  # noqa: BLE001 - tallied, batch continues
            result.errors[str(proposal.pk)] = f"{type(exc).__name__}: {exc}"[:500]
        else:
            result.applied.append(applied.pk)
            decision = (
                applied.decisions.filter(rescore_job__isnull=False)
                .order_by("-id")
                .first()
            )
            if decision is not None:
                result.rescore_jobs.append(decision.rescore_job_id)
        batch.counters_json = {**batch.counters_json, **result.counters()}
        batch.checkpoint_id = proposal.problem_id
        batch.save(update_fields=["counters_json", "checkpoint_id"])
    batch.status = BatchStatus.FINISHED if not result.errors else BatchStatus.FAILED
    batch.finished_at = timezone.now()
    batch.save(update_fields=["status", "finished_at"])
    return result


def rollback_wave(batch, actor=None, *, note=""):
    """Restore every proposal the wave applied. Proposals whose points were
    edited again afterwards are refused by ``restore_proposal`` and listed
    under ``restore_conflicts`` for manual handling."""
    if batch.kind != BatchKind.POINTS_WAVE:
        raise WaveError(f"batch #{batch.pk} is {batch.kind}, not a points wave")
    counters = _wave_counters(batch)
    already = set(counters["restored"])
    restored, conflicts = [], {}
    for pid in counters["applied"]:
        if pid in already:
            continue
        proposal = StandardizationProposal.objects.filter(pk=pid).first()
        if proposal is None or proposal.status != ProposalStatus.APPLIED:
            conflicts[str(pid)] = "not applied any more"
            continue
        try:
            restore_proposal(
                proposal, actor, note=note or f"rollback points wave #{batch.pk}"
            )
        except (RestoreConflictError, ContestBlockedError, ProposalStateError) as exc:
            conflicts[str(pid)] = f"{type(exc).__name__}: {exc}"[:500]
        else:
            restored.append(pid)
    with transaction.atomic():
        batch = StandardizationBatch.objects.select_for_update().get(pk=batch.pk)
        counters = _wave_counters(batch)
        counters["restored"] = sorted(set(counters["restored"]) | set(restored))
        counters["restore_conflicts"] = {**counters["restore_conflicts"], **conflicts}
        counters["rolled_back_at"] = timezone.now().isoformat()
        batch.counters_json = counters
        batch.status = BatchStatus.CANCELLED
        batch.save(update_fields=["counters_json", "status"])
    return {
        "batch": batch.pk,
        "restored": restored,
        "restore_conflicts": conflicts,
        "already_restored": sorted(already),
    }


def wave_status(batch):
    """Where a wave stands: apply tallies plus its rescore jobs by status."""
    counters = _wave_counters(batch)
    job_ids = counters["rescore_jobs"]
    jobs = StandardizationRescoreJob.objects.filter(pk__in=job_ids)
    by_status = {s: 0 for s, _ in RescoreStatus.choices}
    for status in jobs.values_list("status", flat=True):
        by_status[status] = by_status.get(status, 0) + 1
    # restores schedule their own jobs; count every pending job on the wave's
    # problems so "drained" means drained for both directions
    problem_ids = list(
        StandardizationProposal.objects.filter(pk__in=counters["applied"]).values_list(
            "problem_id", flat=True
        )
    )
    pending_on_problems = StandardizationRescoreJob.objects.filter(
        problem_id__in=problem_ids,
        status__in=(RescoreStatus.PENDING, RescoreStatus.RUNNING),
    ).count()
    return {
        "batch": batch.pk,
        "name": batch.name,
        "status": batch.status,
        "created_at": batch.created_at.isoformat(),
        "finished_at": batch.finished_at.isoformat() if batch.finished_at else None,
        "selected": len(counters.get("selected", [])),
        "applied": len(counters["applied"]),
        "stale": len(counters["stale"]),
        "blocked": len(counters["blocked"]),
        "skipped": len(counters["skipped"]),
        "errors": len(counters["errors"]),
        "restored": len(counters["restored"]),
        "restore_conflicts": len(counters["restore_conflicts"]),
        "rescore_jobs": by_status,
        "rescore_pending_on_problems": pending_on_problems,
        "rescore_drained": pending_on_problems == 0,
        "snapshot": counters.get("snapshot", ""),
    }


def wave_problems(batch):
    counters = _wave_counters(batch)
    return Problem.objects.filter(
        standardization_proposals__pk__in=counters["applied"]
    ).distinct()
