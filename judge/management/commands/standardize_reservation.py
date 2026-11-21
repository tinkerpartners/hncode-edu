"""Resolve an outstanding charge after an operator checks provider billing."""

from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from judge.models import Profile
from judge.models.standardization import (
    StandardizationBudgetLock,
    StandardizationReservation,
    LLMUsageLedger,
)
from judge.standardization.permissions import can_apply_standardization

MIN_HELD_AGE = timedelta(hours=1)


class Command(BaseCommand):
    help = "Confirm an actual provider charge; preserves resolution evidence. Never retries AI."

    def add_arguments(self, parser):
        parser.add_argument("--id", required=True, type=int)
        parser.add_argument("--actual-usd", required=True)
        parser.add_argument("--actor", required=True)
        parser.add_argument("--note", required=True)
        parser.add_argument(
            "--confirm-worker-stopped",
            action="store_true",
            help="For a held reservation at least one hour old, confirm the call can no longer complete.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        actor = (
            Profile.objects.select_related("user")
            .filter(user__username=options["actor"])
            .first()
        )
        if not actor or not can_apply_standardization(actor.user):
            raise CommandError("Actor needs apply_standardization permission")
        note = options["note"].strip()
        if len(note) < 10:
            raise CommandError("Record the provider billing evidence in --note")
        try:
            actual = Decimal(options["actual_usd"])
            if (
                not actual.is_finite()
                or not 0 <= actual < 1000000
                or actual != actual.quantize(Decimal("0.000001"))
            ):
                raise ValueError
        except (InvalidOperation, ValueError):
            raise CommandError(
                "Use a finite nonnegative amount with up to six decimals"
            ) from None
        StandardizationBudgetLock.objects.select_for_update().get(key="global")
        row = (
            StandardizationReservation.objects.select_for_update()
            .filter(pk=options["id"])
            .first()
        )
        if row is None:
            raise CommandError("Reservation not found")
        now = timezone.now()
        if row.state == "held":
            if now - row.created_at < MIN_HELD_AGE:
                raise CommandError("A held reservation must be at least one hour old")
            if not options["confirm_worker_stopped"]:
                raise CommandError(
                    "Confirm the worker/request has stopped and record provider billing evidence"
                )
        elif row.state not in ("uncertain", "overrun"):
            raise CommandError("Reservation has already been resolved")
        ledger = row.ledger
        if ledger:
            ledger = LLMUsageLedger.objects.select_for_update().get(pk=ledger.pk)
            if ledger.cost_usd is not None and ledger.cost_usd != actual:
                raise CommandError(
                    "Amount disagrees with the provider cost already recorded"
                )
            ledger.cost_usd = actual
            ledger.save(update_fields=["cost_usd"])
        else:
            ledger = LLMUsageLedger.objects.create(
                problem=row.problem,
                batch=row.batch,
                provider="billing_resolution",
                model="unknown",
                step="manual_billing_resolution",
                cost_usd=actual,
                status="resolved",
            )
        row.resolution_json = {
            "actor_id": actor.pk,
            "note": note[:2000],
            "previous_state": row.state,
            "actual_usd": str(actual),
            "at": now.isoformat(),
        }
        if row.state == "held":
            row.resolution_json["worker_stop_confirmed"] = True
        row.ledger, row.state, row.settled_at = ledger, "settled", now
        row.save(update_fields=["ledger", "state", "settled_at", "resolution_json"])
        self.stdout.write(f"Resolved reservation {row.pk}; no AI request dispatched.")
