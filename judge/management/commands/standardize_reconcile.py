"""Repair workflow summaries from existing judge evidence, without AI or apply."""

import json

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from judge.models.standardization import StandardizationBatch
from judge.standardization.enums import BatchStatus, FieldGroup
from judge.standardization.guided import VERSION
from judge.standardization.reconciliation import reconcile_verification


class Command(BaseCommand):
    help = "Reconcile existing late results (dry-run by default); never call AI or apply proposals."

    def add_arguments(self, parser):
        parser.add_argument("--batch-ids", required=True)
        parser.add_argument("--limit", type=int, default=100)
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        try:
            ids = {int(value) for value in options["batch_ids"].split(",")}
        except ValueError:
            raise CommandError("--batch-ids must be comma-separated integers") from None
        if (
            not ids
            or min(ids) < 1
            or not 1 <= options["limit"] <= 1000
            or len(ids) > options["limit"]
        ):
            raise CommandError(
                "Select 1..limit positive batch IDs; limit must be <=1000"
            )
        batches = StandardizationBatch.objects.filter(
            pk__in=ids,
            config_version=VERSION,
            status__in=(BatchStatus.RUNNING, BatchStatus.FINISHED),
        ).order_by("pk")
        changed = 0
        for batch in batches:
            proposal = (
                batch.proposals.filter(field_group=FieldGroup.SOLUTION)
                .order_by("-pk")
                .first()
            )
            row = (
                proposal.verifications.order_by("-created_at", "-pk").first()
                if proposal
                else None
            )
            if row is None:
                continue
            with transaction.atomic():
                before = (
                    batch.counters_json,
                    list(
                        batch.proposals.order_by("pk").values_list(
                            "pk", "status", "evidence_json"
                        )
                    ),
                )
                eligible = reconcile_verification(row)
                batch.refresh_from_db()
                after = (
                    batch.counters_json,
                    list(
                        batch.proposals.order_by("pk").values_list(
                            "pk", "status", "evidence_json"
                        )
                    ),
                )
                different = before != after
                changed += different
                self.stdout.write(
                    json.dumps(
                        {
                            "batch": batch.pk,
                            "eligible": eligible,
                            "changed": different,
                            "phase": batch.counters_json.get("phase"),
                            "dry_run": not options["apply"],
                        }
                    )
                )
                if not options["apply"]:
                    transaction.set_rollback(True)
        self.stdout.write(
            json.dumps({"changed": changed, "dry_run": not options["apply"]})
        )
