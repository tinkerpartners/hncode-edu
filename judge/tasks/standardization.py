"""Celery tasks for the standardization flow.

``drain_rescore_jobs`` is the throttle between a bulk points/partial change
and ``rescore_problem``: it claims at most ``STANDARDIZATION_RESCORE_PER_MINUTE``
PENDING ``StandardizationRescoreJob`` rows, runs them, and re-schedules itself
a minute later while work remains. It holds a cache lock so a beat entry and a
command-kicked chain cannot both drain at once. ``rescore_problem_std`` wraps
``rescore_problem`` and then refreshes the problem's stats, which the stock
task does not do.
"""

import logging

from celery import shared_task
from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from judge.models.standardization import StandardizationRescoreJob
from judge.standardization.enums import RescoreStatus
from judge.tasks.submission import rescore_problem, update_problem_stats

__all__ = (
    "drain_rescore_jobs",
    "rescore_problem_std",
    "kick_rescore_drain",
    "run_ai_step",
    "verify_solution",
    "poll_llm_batches",
    "run_auto_apply_policies",
)

logger = logging.getLogger(__name__)

DRAIN_LOCK_KEY = "standardization:rescore-drain-lock"
DRAIN_LOCK_SECONDS = 110
DRAIN_INTERVAL_SECONDS = 60


def _queue_kwargs():
    queue = getattr(settings, "STANDARDIZATION_CELERY_QUEUE", None)
    return {"queue": queue} if queue else {}


def kick_rescore_drain(countdown=0):
    """Start (or nudge) the self-rescheduling drain chain."""
    return drain_rescore_jobs.apply_async(countdown=countdown, **_queue_kwargs())


@shared_task(bind=True)
def rescore_problem_std(self, job_id):
    job = StandardizationRescoreJob.objects.select_related("problem").get(pk=job_id)
    try:
        # .apply() runs the task inline with a proper request/task id, so its
        # Progress updates have somewhere to go and exceptions propagate
        # (throw=True). Read ``.result`` rather than ``.get()``: inside a worker
        # Celery refuses ``EagerResult.get()`` with "Never call result.get()
        # within a task!", which marked every job FAILED on the first dev run.
        rescored = rescore_problem.apply(args=[job.problem_id], throw=True).result
        update_problem_stats.apply(args=[job.problem_id], throw=True)
    except (
        Exception
    ) as exc:  # noqa: BLE001 - recorded on the job, never swallowed silently
        logger.exception(
            "standardization rescore failed for problem %s", job.problem_id
        )
        job.status = RescoreStatus.FAILED
        job.error = f"{type(exc).__name__}: {exc}"[:2000]
        job.finished_at = timezone.now()
        job.save(update_fields=["status", "error", "finished_at"])
        return {"job": job_id, "status": job.status}
    job.status = RescoreStatus.DONE
    job.error = ""
    job.finished_at = timezone.now()
    job.save(update_fields=["status", "error", "finished_at"])
    return {"job": job_id, "status": job.status, "rescored": rescored}


@shared_task(bind=True)
def drain_rescore_jobs(self, limit=None, reschedule=True, sync=False):
    """Claim up to ``limit`` PENDING jobs and run them.

    ``sync=True`` runs each job inline (tests, management command); otherwise
    each job is dispatched as its own task so a slow problem does not block the
    drain. Returns a summary dict.
    """
    limit = limit or getattr(settings, "STANDARDIZATION_RESCORE_PER_MINUTE", 20)
    if not cache.add(DRAIN_LOCK_KEY, timezone.now().isoformat(), DRAIN_LOCK_SECONDS):
        return {"claimed": 0, "skipped": "locked"}
    try:
        with transaction.atomic():
            jobs = list(
                StandardizationRescoreJob.objects.select_for_update(skip_locked=True)
                .filter(status=RescoreStatus.PENDING)
                .order_by("enqueued_at")[:limit]
            )
            now = timezone.now()
            for job in jobs:
                job.status = RescoreStatus.RUNNING
                job.started_at = now
                job.attempts += 1
                job.task_id = self.request.id or ""
                job.save(update_fields=["status", "started_at", "attempts", "task_id"])
        for job in jobs:
            if sync:
                rescore_problem_std(job.pk)
            else:
                rescore_problem_std.apply_async(args=[job.pk], **_queue_kwargs())
        remaining = StandardizationRescoreJob.objects.filter(
            status=RescoreStatus.PENDING
        ).count()
    finally:
        cache.delete(DRAIN_LOCK_KEY)
    if reschedule and remaining:
        kick_rescore_drain(countdown=DRAIN_INTERVAL_SECONDS)
    return {"claimed": len(jobs), "remaining": remaining}


@shared_task(bind=True)
def run_ai_step(self, problem_id, step, batch_id=None, provider_name=None):
    """Run one AI step for one problem (PR-E). Returns a summary dict."""
    from judge.models import Problem
    from judge.models.standardization import StandardizationBatch
    from judge.standardization.steps import run_step
    from llm_service.providers import get_provider

    problem = Problem.objects.get(pk=problem_id)
    batch = (
        StandardizationBatch.objects.filter(pk=batch_id).first() if batch_id else None
    )
    result = run_step(step, problem, batch=batch, provider=get_provider(provider_name))
    return {
        "problem": problem_id,
        "step": step,
        "proposals": [p.pk for p in result.proposals],
        "statuses": result.statuses,
        "skipped": result.skipped,
        "error": result.error,
    }


@shared_task
def run_guided(batch_id):
    from judge.standardization.guided import run_queued

    run_queued(batch_id)


@shared_task(bind=True)
def verify_solution(self, proposal_id):
    """Judge a SOLUTION proposal's reference code and record the level."""
    from judge.models.standardization import StandardizationProposal
    from judge.standardization.verification import verify_solution_proposal

    proposal = StandardizationProposal.objects.select_related("problem").get(
        pk=proposal_id
    )
    row = verify_solution_proposal(proposal)
    return {"proposal": proposal_id, "level": row.level, "verification": row.pk}


@shared_task(bind=True)
def poll_llm_batches(self):
    """Beat/manual: ingest every provider batch that has ended (PR-F)."""
    from judge.standardization.batches import poll_all

    return poll_all()


@shared_task(bind=True)
def run_auto_apply_policies(self, limit=None):
    """Beat/manual: sweep every enabled, un-halted auto-apply policy (PR-H)."""
    from judge.standardization.policy import sweep_all

    limit = limit or getattr(settings, "STANDARDIZATION_POLICY_SWEEP_LIMIT", 200)
    return sweep_all(limit=limit)
