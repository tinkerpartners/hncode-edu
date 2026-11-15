"""Per-million-token list prices used to fill ``LLMUsageLedger.cost_usd``.

Prices are USD per million tokens as published on anthropic.com/pricing when
this table was written (2026-11). Cache writes are the 5-minute TTL rate
(1.25x input); batch requests are billed at half of every rate. Unknown
models yield ``None`` so the ledger shows "unknown", never a wrong number —
update this table when a model is added or repriced.
"""

from decimal import Decimal

from llm_service.providers.base import LLMUsage

MTOK = Decimal(1_000_000)

# model -> (input, output, cache_read, cache_write_5m)
ANTHROPIC_PRICES = {
    "claude-opus-5-5": ("4", "20", "0.20", "5"),
    "claude-opus-5": ("5", "25", "0.50", "6.25"),
    "claude-sonnet-5-5": ("2", "10", "0.10", "2.50"),
    "claude-sonnet-5": ("2", "10", "0.20", "2.50"),
    "claude-haiku-5-5": ("0.10", "0.50", "0.01", "0.125"),
    "claude-fable-5-2": ("8", "40", "0.20", "10"),
    "claude-fable-5-1": ("10", "50", "0.25", "12.50"),
}

BATCH_DISCOUNT = Decimal("0.5")


def price_table(model):
    row = ANTHROPIC_PRICES.get(model)
    if row is None:
        return None
    return tuple(Decimal(x) for x in row)


def estimate_cost(model, usage: LLMUsage, batch=False):
    """Return a Decimal USD cost, or None when the model or usage is unknown."""
    row = price_table(model)
    if row is None or not usage.known:
        return None
    inp, out, cread, cwrite = row
    total = (
        Decimal(usage.input_tokens) * inp
        + Decimal(usage.output_tokens) * out
        + Decimal(usage.cache_read_tokens or 0) * cread
        + Decimal(usage.cache_write_tokens or 0) * cwrite
    ) / MTOK
    if batch:
        total *= BATCH_DISCOUNT
    return total.quantize(Decimal("0.000001"))
