"""Spend caps (spec F09 / AC16).

Caps are read from settings and compared with sums of ``LLMUsageLedger``
``cost_usd`` (rows with unknown cost count as 0 — Poe rows are unmeasured,
so caps only protect metered providers). When a cap is hit the current
batch is PAUSED and the step is skipped; there is never a silent model
downgrade. ``None`` disables a cap.
"""

from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
import json

from django.conf import settings
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from judge.models.standardization import LLMUsageLedger, StandardizationBatch
from judge.models.standardization import (
    StandardizationBudgetLock,
    StandardizationReservation,
)
from judge.standardization.enums import BatchStatus

ZERO = Decimal("0")


class BudgetExceeded(Exception):
    def __init__(self, scope, spent, cap):
        self.scope, self.spent, self.cap = scope, spent, cap
        super().__init__(f"{scope} budget exceeded: spent {spent} of {cap} USD")


@dataclass
class BudgetStatus:
    problem: Decimal
    batch: Decimal
    day: Decimal
    total: Decimal

    def as_json(self):
        return {k: str(v) for k, v in self.__dict__.items()}


def _cap(name):
    value = getattr(settings, f"STANDARDIZATION_BUDGET_USD_PER_{name}", None)
    return None if value is None else Decimal(str(value))


def _sum(qs):
    return qs.aggregate(s=Sum("cost_usd"))["s"] or ZERO


def spent(problem=None, batch=None):
    rows = LLMUsageLedger.objects.all()
    now = timezone.now()
    today = timezone.localdate(now) if timezone.is_aware(now) else now.date()
    return BudgetStatus(
        problem=_sum(rows.filter(problem=problem)) if problem is not None else ZERO,
        batch=_sum(rows.filter(batch=batch)) if batch is not None else ZERO,
        day=_sum(rows.filter(created_at__date=today)),
        total=_sum(rows),
    )


def check_budget(problem=None, batch=None):
    """Raise ``BudgetExceeded`` for the first cap already reached."""
    if StandardizationReservation.objects.filter(state="overrun").exists():
        raise BudgetExceeded("reservation_overrun", ZERO, ZERO)
    status = spent(problem, batch)
    for scope in ("PROBLEM", "BATCH", "DAY", "TOTAL"):
        cap = _cap(scope)
        if scope == "BATCH" and batch is not None and batch.budget_usd_cap is not None:
            cap = (
                min(cap, batch.budget_usd_cap)
                if cap is not None
                else batch.budget_usd_cap
            )
        if cap is None:
            continue
        if scope == "PROBLEM" and problem is None:
            continue
        if scope == "BATCH" and batch is None:
            continue
        value = getattr(status, scope.lower())
        if value >= cap:
            raise BudgetExceeded(scope.lower(), value, cap)
    return status


def pause_batch(batch, reason):
    """Move a running batch to PAUSED and note why (idempotent)."""
    if batch is None:
        return
    batch = StandardizationBatch.objects.select_for_update().get(pk=batch.pk)
    if batch.status in (BatchStatus.RUNNING, BatchStatus.QUEUED, BatchStatus.PAUSING):
        batch.status = BatchStatus.PAUSED
    counters = dict(batch.counters_json or {})
    counters["paused_reason"] = reason
    batch.counters_json = counters
    batch.save(update_fields=["status", "counters_json"])


def batch_is_open(batch):
    """False when steps must not spend on this batch (paused/cancelled/...)."""
    if batch is None:
        return True
    batch.refresh_from_db(fields=["status"])
    return batch.status in (BatchStatus.RUNNING, BatchStatus.QUEUED)


@transaction.atomic
def reserve_request(request, provider, model, problem=None, batch=None):
    """Reserve before a paid call, counting in-flight and unknown-cost work.

    Ceilings are an admission estimate, not a provider-enforced invoice limit.
    Unknown failures stay held; never expire them into free budget automatically.
    """
    if not getattr(settings, "STANDARDIZATION_RESERVATIONS_ENABLED", False):
        return None
    configured = getattr(settings, "STANDARDIZATION_REQUEST_USD_CEILINGS", {})
    try:
        amount = Decimal(str(configured[f"{provider}/{model}"]))
        if (
            not amount.is_finite()
            or not Decimal("0.000001") <= amount < Decimal("1000000")
            or amount != amount.quantize(Decimal("0.000001"))
        ):
            raise ValueError
    except (KeyError, ValueError, ArithmeticError):
        raise BudgetExceeded("reservation_config", ZERO, ZERO) from None
    # get_or_create's unique PK handles two workers bootstrapping together.
    StandardizationBudgetLock.objects.get_or_create(key="global")
    StandardizationBudgetLock.objects.select_for_update().get(key="global")
    status = check_budget(problem, batch)
    identity = json.dumps(
        [
            provider,
            model,
            request.system,
            request.user,
            request.schema,
            request.max_tokens,
            request.effort,
        ],
        sort_keys=True,
    )
    key = sha256(identity.encode()).hexdigest()
    held = StandardizationReservation.objects.filter(state__in=("held", "uncertain"))
    if held.filter(request_key=key, problem=problem).exists():
        raise BudgetExceeded("request_in_flight", ZERO, amount)
    for scope in ("PROBLEM", "BATCH", "DAY", "TOTAL"):
        if (scope == "PROBLEM" and problem is None) or (
            scope == "BATCH" and batch is None
        ):
            continue
        cap = _cap(scope)
        if scope == "BATCH" and batch.budget_usd_cap is not None:
            cap = (
                min(cap, batch.budget_usd_cap)
                if cap is not None
                else batch.budget_usd_cap
            )
        if cap is None:
            continue
        pending = held
        if scope == "PROBLEM":
            pending = pending.filter(problem=problem)
        elif scope == "BATCH":
            pending = pending.filter(batch=batch)
        # All unresolved older calls also count against today's allowance;
        # crossing midnight must not silently free unknown spend.
        reserved = pending.aggregate(s=Sum("amount"))["s"] or ZERO
        consumed = getattr(status, scope.lower()) + reserved
        if consumed + amount > cap:
            raise BudgetExceeded(scope.lower(), consumed + amount, cap)
    return StandardizationReservation.objects.create(
        problem=problem, batch=batch, request_key=key, amount=amount
    )


@transaction.atomic
def settle_request(reservation, ledger=None):
    if reservation is None:
        return
    StandardizationBudgetLock.objects.select_for_update().get(key="global")
    row = StandardizationReservation.objects.select_for_update().get(pk=reservation.pk)
    if row.state == "settled":
        return  # idempotent settlement must not erase an operator resolution
    row.ledger = ledger
    cost = ledger.cost_usd if ledger else None
    row.state = (
        "overrun"
        if cost is not None and (cost < 0 or cost > row.amount)
        else "settled" if cost is not None else "uncertain"
    )
    row.settled_at = timezone.now()
    row.save(update_fields=["ledger", "state", "settled_at"])
