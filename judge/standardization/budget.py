"""Spend caps (spec F09 / AC16).

Caps are read from settings and compared with sums of ``LLMUsageLedger``
``cost_usd`` (rows with unknown cost count as 0 — Poe rows are unmeasured,
so caps only protect metered providers). When a cap is hit the current
batch is PAUSED and the step is skipped; there is never a silent model
downgrade. ``None`` disables a cap.
"""

from dataclasses import dataclass
from decimal import Decimal

from django.conf import settings
from django.db.models import Sum
from django.utils import timezone

from judge.models.standardization import LLMUsageLedger, StandardizationBatch
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
