"""Direct Anthropic API provider (Messages + Message Batches).

Design notes (see docs/PROBLEM_STANDARDIZATION.md):

* The ``anthropic`` SDK is imported lazily so the site runs without the
  package until a step actually needs it.
* Thinking is left at the model default (always on for Claude Opus 5.5);
  depth is controlled through ``output_config.effort``.
* ``request.schema`` becomes ``output_config.format`` (structured outputs),
  so the first text block is guaranteed valid JSON when the model finishes
  normally. ``stop_reason`` ``max_tokens`` / ``refusal`` are surfaced as
  provider errors instead of half-parsed drafts.
* Batch requests cannot carry ``stream``, ``speed`` or ``fallbacks``; this
  provider sets none of them anywhere, so one ``_params`` builder serves both
  paths.
* Only the error *code* is logged; raw provider responses are not stored.
"""

import logging

from django.conf import settings

from llm_service.pricing import estimate_cost
from llm_service.providers.base import (
    BatchResult,
    BatchStatus,
    LLMProvider,
    LLMProviderError,
    LLMRequest,
    LLMResponse,
    LLMUsage,
)

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-opus-5-5"


def _sdk():
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise LLMProviderError(
            "config", "the 'anthropic' package is not installed"
        ) from exc
    return anthropic


def map_exception(exc):
    """Translate an SDK exception into an ``LLMProviderError``."""
    anthropic = _sdk()
    if isinstance(exc, LLMProviderError):
        return exc
    if isinstance(
        exc, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)
    ):
        return LLMProviderError("credentials")
    if isinstance(exc, anthropic.RateLimitError):
        return LLMProviderError("quota", "rate limited")
    if isinstance(exc, anthropic.BadRequestError):
        return LLMProviderError(
            "invalid_request", str(getattr(exc, "message", ""))[:200]
        )
    if isinstance(exc, anthropic.APITimeoutError):
        return LLMProviderError("timeout")
    if isinstance(exc, anthropic.APIStatusError):
        status = getattr(exc, "status_code", 0) or 0
        if status == 402:
            return LLMProviderError("quota", "billing")
        return LLMProviderError("provider", f"http {status}")
    if isinstance(exc, anthropic.APIConnectionError):
        return LLMProviderError("timeout", "connection")
    return LLMProviderError("provider", type(exc).__name__)


def usage_from(usage_obj) -> LLMUsage:
    if usage_obj is None:
        return LLMUsage()
    return LLMUsage(
        input_tokens=getattr(usage_obj, "input_tokens", None),
        output_tokens=getattr(usage_obj, "output_tokens", None),
        cache_read_tokens=getattr(usage_obj, "cache_read_input_tokens", None) or 0,
        cache_write_tokens=getattr(usage_obj, "cache_creation_input_tokens", None) or 0,
    )


class AnthropicProvider(LLMProvider):
    name = "anthropic"
    supports_batches = True

    def __init__(self, api_key=None, model=None, client=None):
        self.api_key = api_key or getattr(settings, "ANTHROPIC_API_KEY", None)
        self.default_model = model or getattr(
            settings, "STANDARDIZATION_ANTHROPIC_MODEL", DEFAULT_MODEL
        )
        self.default_effort = getattr(settings, "STANDARDIZATION_LLM_EFFORT", "medium")
        self.timeout = getattr(settings, "STANDARDIZATION_LLM_TIMEOUT", 300)
        self._client = client

    @property
    def client(self):
        if self._client is None:
            if not self.api_key:
                raise LLMProviderError("config", "ANTHROPIC_API_KEY is not set")
            anthropic = _sdk()
            self._client = anthropic.Anthropic(
                api_key=self.api_key, timeout=self.timeout, max_retries=2
            )
        return self._client

    # -- request building ---------------------------------------------------

    def _params(self, request: LLMRequest) -> dict:
        system_block = {"type": "text", "text": request.system}
        if request.cache_system:
            system_block["cache_control"] = {"type": "ephemeral"}
        output_config = {"effort": request.effort or self.default_effort}
        if request.schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": request.schema}
        return {
            "model": request.model or self.default_model,
            "max_tokens": request.max_tokens,
            "system": [system_block],
            "messages": [{"role": "user", "content": request.user}],
            "output_config": output_config,
        }

    def _response(self, message, request: LLMRequest, batch_id="") -> LLMResponse:
        text = "".join(
            getattr(block, "text", "")
            for block in getattr(message, "content", []) or []
            if getattr(block, "type", "") == "text"
        )
        usage = usage_from(getattr(message, "usage", None))
        model = getattr(message, "model", None) or request.model or self.default_model
        stop_reason = getattr(message, "stop_reason", "") or ""
        response = LLMResponse(
            text=text,
            provider=self.name,
            model=model,
            usage=usage,
            cost_usd=estimate_cost(model, usage, batch=bool(batch_id)),
            request_id=getattr(message, "_request_id", "")
            or getattr(message, "id", "")
            or "",
            stop_reason=stop_reason,
            custom_id=request.custom_id,
            batch_id=batch_id,
        )
        if stop_reason == "refusal":
            raise _WithUsage(LLMProviderError("refusal"), response)
        if stop_reason == "max_tokens":
            raise _WithUsage(LLMProviderError("truncated", "max_tokens"), response)
        return response

    # -- single calls -------------------------------------------------------

    def complete(self, request: LLMRequest) -> LLMResponse:
        try:
            message = self.client.messages.create(**self._params(request))
        except Exception as exc:  # noqa: BLE001 - mapped to a code below
            err = map_exception(exc)
            logger.warning("anthropic request failed (%s)", err.code)
            raise err from None
        try:
            return self._response(message, request)
        except _WithUsage as wrapped:
            wrapped.error.usage_response = wrapped.response
            raise wrapped.error from None

    # -- batches ------------------------------------------------------------

    def submit_batch(self, requests) -> str:
        if not requests:
            raise LLMProviderError("invalid_request", "empty batch")
        seen = set()
        payload = []
        for request in requests:
            if not request.custom_id:
                raise LLMProviderError(
                    "invalid_request", "batch request without custom_id"
                )
            if request.custom_id in seen:
                raise LLMProviderError(
                    "invalid_request", f"duplicate custom_id {request.custom_id}"
                )
            seen.add(request.custom_id)
            payload.append(
                {"custom_id": request.custom_id, "params": self._params(request)}
            )
        try:
            batch = self.client.messages.batches.create(requests=payload)
        except Exception as exc:  # noqa: BLE001
            err = map_exception(exc)
            logger.warning("anthropic batch submit failed (%s)", err.code)
            raise err from None
        return batch.id

    def poll_batch(self, batch_id) -> BatchStatus:
        try:
            batch = self.client.messages.batches.retrieve(batch_id)
        except Exception as exc:  # noqa: BLE001
            raise map_exception(exc) from None
        counts = getattr(batch, "request_counts", None)
        return BatchStatus(
            batch_id=batch_id,
            ended=batch.processing_status == "ended",
            status=batch.processing_status,
            counts=(
                {
                    k: getattr(counts, k, 0)
                    for k in (
                        "processing",
                        "succeeded",
                        "errored",
                        "canceled",
                        "expired",
                    )
                }
                if counts is not None
                else {}
            ),
        )

    def iter_batch_results(self, batch_id, requests_by_id=None):
        """Yield ``BatchResult`` per request. ``requests_by_id`` maps
        custom_id -> LLMRequest so responses carry the model/custom_id even
        when the result omits them."""
        requests_by_id = requests_by_id or {}
        try:
            stream = self.client.messages.batches.results(batch_id)
        except Exception as exc:  # noqa: BLE001
            raise map_exception(exc) from None
        for item in stream:
            custom_id = item.custom_id
            request = requests_by_id.get(custom_id) or LLMRequest(
                system="", user="", custom_id=custom_id
            )
            kind = item.result.type
            if kind == "succeeded":
                try:
                    yield BatchResult(
                        custom_id,
                        "succeeded",
                        self._response(item.result.message, request, batch_id),
                    )
                except _WithUsage as wrapped:
                    wrapped.error.usage_response = wrapped.response
                    yield BatchResult(
                        custom_id, "errored", wrapped.response, wrapped.error
                    )
            elif kind == "errored":
                etype = getattr(getattr(item.result, "error", None), "type", "") or ""
                code = "invalid_request" if etype == "invalid_request" else "provider"
                yield BatchResult(
                    custom_id, "errored", None, LLMProviderError(code, etype)
                )
            else:  # canceled | expired
                yield BatchResult(
                    custom_id, kind, None, LLMProviderError("provider", kind)
                )


class _WithUsage(Exception):
    """Internal: carry the usage of a refused/truncated answer so it is still
    charged to the ledger."""

    def __init__(self, error, response):
        self.error = error
        self.response = response
        super().__init__(error.code)


__all__ = ("AnthropicProvider", "DEFAULT_MODEL", "map_exception", "usage_from")
