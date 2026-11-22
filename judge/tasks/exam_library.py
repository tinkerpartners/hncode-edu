import logging
from datetime import timedelta

from celery import shared_task
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from judge.models import ExamDeferredSubmission

logger = logging.getLogger(__name__)


@shared_task(ignore_result=True)
def dispatch_exam_submissions():
    """Queue accepted mock submissions when their respective clocks expire."""
    now = timezone.now()
    available = ExamDeferredSubmission.objects.filter(
        ready_at__lte=now, dispatched_at__isnull=True,
    ).filter(Q(claim_until__isnull=True) | Q(claim_until__lte=now))
    ids = list(available.order_by("ready_at", "pk").values_list("pk", flat=True)[:100])
    for pk in ids:
        with transaction.atomic():
            row = available.select_for_update().filter(pk=pk).first()
            if row is None:
                continue
            row.claim_until = timezone.now() + timedelta(minutes=5)
            row.save(update_fields=["claim_until"])
        try:
            submission = row.submission
            # A resumed task must not destroy results already written by a judge.
            if submission.status in ("QU", "IE") and not submission.judge():
                ExamDeferredSubmission.objects.filter(pk=pk).update(
                    claim_until=timezone.now() + timedelta(seconds=30)
                )
                continue
            ExamDeferredSubmission.objects.filter(pk=pk).update(dispatched_at=timezone.now())
        except Exception:
            logger.exception("Could not dispatch deferred exam submission %s", row.submission_id)
