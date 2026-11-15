"""Queue derived-score syncs without hammering Celery or MariaDB.

``schedule_rescore`` records intent in ``StandardizationRescoreJob``; a
throttled beat task (PR-B, ``judge.tasks.standardization.drain_rescore_jobs``)
runs ``rescore_problem`` for a bounded number of PENDING jobs per minute.
The unique (problem, target_source_hash) pair means that re-applying the same
result never enqueues a second job.
"""

from django.db import IntegrityError, transaction

from judge.models import Submission
from judge.models.standardization import StandardizationRescoreJob
from judge.standardization.enums import RescoreStatus
from judge.standardization.hashing import compute_source_hash


def schedule_rescore(problem, reason=""):
    """Create a PENDING job for the problem's current state, or return the
    existing one. Returns None when the problem has no submissions (nothing to
    sync; update_stats alone is not worth a job)."""
    if not Submission.objects.filter(problem=problem).exists():
        return None
    target = compute_source_hash(problem)
    try:
        with transaction.atomic():
            job, _ = StandardizationRescoreJob.objects.get_or_create(
                problem=problem,
                target_source_hash=target,
                defaults={"reason": reason, "status": RescoreStatus.PENDING},
            )
    except IntegrityError:
        job = StandardizationRescoreJob.objects.get(
            problem=problem, target_source_hash=target
        )
    if job.status == RescoreStatus.FAILED:
        job.status = RescoreStatus.PENDING
        job.reason = reason or job.reason
        job.save(update_fields=["status", "reason"])
    return job
