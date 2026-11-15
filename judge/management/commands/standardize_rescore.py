"""Inspect or drain the standardization rescore queue.

manage.py standardize_rescore               # counts per status
manage.py standardize_rescore --drain       # kick the Celery drain chain
manage.py standardize_rescore --drain --sync --limit 50   # run inline, no worker
manage.py standardize_rescore --retry-failed
"""

import json

from django.core.management.base import BaseCommand
from django.db.models import Count

from judge.models.standardization import StandardizationRescoreJob
from judge.standardization.enums import RescoreStatus


class Command(BaseCommand):
    help = "Show or drain StandardizationRescoreJob rows."

    def add_arguments(self, parser):
        parser.add_argument("--drain", action="store_true")
        parser.add_argument(
            "--sync", action="store_true", help="run inline instead of via Celery"
        )
        parser.add_argument("--limit", type=int, help="jobs to claim in this drain")
        parser.add_argument("--retry-failed", action="store_true")

    def handle(self, *args, **options):
        if options["retry_failed"]:
            n = StandardizationRescoreJob.objects.filter(
                status=RescoreStatus.FAILED
            ).update(status=RescoreStatus.PENDING, error="")
            self.stdout.write(f"re-queued {n} failed job(s)")
        if options["drain"]:
            from judge.tasks.standardization import (
                drain_rescore_jobs,
                kick_rescore_drain,
            )

            if options["sync"]:
                result = drain_rescore_jobs(
                    limit=options["limit"], reschedule=False, sync=True
                )
                self.stdout.write(json.dumps(result))
            else:
                res = kick_rescore_drain()
                self.stdout.write(f"drain task queued: {res.id}")
        counts = dict(
            StandardizationRescoreJob.objects.values_list("status")
            .annotate(c=Count("id"))
            .values_list("status", "c")
        )
        self.stdout.write(json.dumps({"jobs": counts}))
