"""Glue between providers and the standardization ledger.

``call_json`` is the one entry point AI steps use: it sends a request through
a provider, validates the answer against the step's JSON schema and writes
exactly one ``LLMUsageLedger`` row whatever happens (ok / error / unknown), so
spend is visible even for failed calls. ``record_usage`` is shared with the
batch result ingester.
"""

import logging

from django.db import transaction

from judge.models.standardization import LLMUsageLedger
from llm_service.providers import (
    LLMProviderError,
    LLMRequest,
    LLMResponse,
    get_provider,
)

logger = logging.getLogger(__name__)

LEDGER_OK = "ok"
LEDGER_ERROR = "error"
LEDGER_UNKNOWN = "unknown"  # usage not reported by the provider
LEDGER_EXPIRED = "expired"


class StepValidationError(Exception):
    """The provider answered, but not with the shape the step asked for."""


def validate_payload(payload, schema):
    import jsonschema

    try:
        jsonschema.validate(payload, schema)
    except jsonschema.ValidationError as exc:
        path = "/".join(str(p) for p in exc.absolute_path) or "$"
        raise StepValidationError(f"{path}: {exc.message[:200]}") from None
    return payload


def record_usage(
    response: LLMResponse | None,
    *,
    provider,
    model,
    step,
    status,
    problem=None,
    batch=None,
    proposal=None,
    request_id="",
    provider_batch_id="",
):
    usage = response.usage if response is not None else None
    fields = usage.as_ledger_fields() if usage is not None else {}
    if usage is not None and not usage.known and status == LEDGER_OK:
        status = LEDGER_UNKNOWN
    with transaction.atomic():
        return LLMUsageLedger.objects.create(
            provider=provider,
            model=model,
            step=step,
            problem=problem,
            batch=batch,
            proposal=proposal,
            request_id=(response.request_id if response is not None else request_id)[
                :120
            ],
            provider_batch_id=(
                provider_batch_id or (response.batch_id if response else "")
            )[:120],
            cost_usd=response.cost_usd if response is not None else None,
            status=status[:10],
            **fields,
        )


def call_json(request: LLMRequest, *, step, problem=None, batch=None, provider=None):
    """Run ``request`` synchronously and return ``(payload, ledger_row)``.

    Raises ``LLMProviderError`` (provider side) or ``StepValidationError``
    (answer shape). A ledger row is written in every case.
    """
    provider = provider if provider is not None else get_provider()
    model = request.model or getattr(provider, "default_model", "") or ""
    try:
        response = provider.complete(request)
    except LLMProviderError as exc:
        usage_response = getattr(exc, "usage_response", None)
        record_usage(
            usage_response,
            provider=provider.name,
            model=(usage_response.model if usage_response else model),
            step=step,
            status=LEDGER_ERROR,
            problem=problem,
            batch=batch,
        )
        raise
    try:
        payload = response.parsed_json()
        if request.schema is not None:
            validate_payload(payload, request.schema)
    except (LLMProviderError, StepValidationError):
        record_usage(
            response,
            provider=provider.name,
            model=response.model,
            step=step,
            status=LEDGER_ERROR,
            problem=problem,
            batch=batch,
        )
        raise
    ledger = record_usage(
        response,
        provider=provider.name,
        model=response.model,
        step=step,
        status=LEDGER_OK,
        problem=problem,
        batch=batch,
    )
    return payload, ledger
