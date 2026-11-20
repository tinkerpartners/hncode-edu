"""Resolve an uncertain charge only after an operator checks provider billing."""

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


class Command(BaseCommand):
    help = "Confirm an actual provider charge; preserves resolution evidence. Never retries AI."

    def add_arguments(self, parser):
        parser.add_argument("--id", required=True, type=int)
        parser.add_argument("--actual-usd", required=True)
        parser.add_argument("--actor", required=True)
        parser.add_argument("--note", required=True)

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
        row = StandardizationReservation.objects.select_for_update().get(
            pk=options["id"]
        )
        if row.state not in ("uncertain", "overrun"):
            raise CommandError(
                "Only uncertain/overrun charges may be confirmed; held calls may still be running"
            )
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
            "at": timezone.now().isoformat(),
        }
        row.ledger, row.state, row.settled_at = ledger, "settled", timezone.now()
        row.save(update_fields=["ledger", "state", "settled_at", "resolution_json"])
        self.stdout.write(f"Resolved reservation {row.pk}; no AI request dispatched.")
