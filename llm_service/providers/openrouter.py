"""OpenRouter provider (OpenAI-compatible ``chat/completions`` over ``requests``).

Design notes (see docs/PROBLEM_STANDARDIZATION.md):

* No SDK: one ``POST {base_url}/chat/completions`` per request with the
  ``requests`` package already in ``requirements.txt``. ``requests`` is
  imported lazily so importing this module never fails at startup.
* ``request.schema`` becomes ``response_format`` ``json_schema`` with
  ``strict: true`` and ``provider.require_parameters`` so OpenRouter only
  routes to endpoints that honour the schema. The step schemas use only
  strict-safe keywords (``type``, ``properties``, ``required``,
  ``additionalProperties``, ``enum``, ``items``, ``description``). If a future
  schema adds ``minLength``/``pattern``/``format``/``minimum``, OpenRouter
  answers 400 (``invalid_request``) — add a stripper then, not now.
* Reasoning depth goes through ``reasoning.effort`` (same vocabulary as
  ``LLMRequest.effort``); ``reasoning.exclude`` keeps the thinking out of the
  returned content so ``parsed_json`` sees only the answer.
* The system prompt carries ``cache_control`` (explicit breakpoint needed for
  Anthropic models; OpenAI and DeepSeek cache automatically).
* ``usage.cost`` is reported by OpenRouter on every response (credits = USD at
  list price) and is stored as ``cost_usd`` directly; no price table lookup.
  ``model`` is the model actually served, which may differ from the request.
* ``finish_reason`` ``length`` / ``content_filter`` are surfaced as provider
  errors (``truncated`` / ``refusal``) that still carry usage for the ledger.
* OpenRouter has **no batch API**; ``supports_batches`` stays ``False`` and
  bulk runs use ``standardize_run --async`` (Celery fan-out).
* Transport: 2 retries on 408/429/5xx and connection errors, honouring
  ``Retry-After`` (capped at 30 s). 400/401/402/403 are never retried.
* Only the error *code* is logged; raw provider responses are not stored.
"""

import logging
import re
import time
from decimal import Decimal, InvalidOperation

from django.conf import settings

from llm_service.providers.base import (
    LLMProvider,
    LLMProviderError,
    LLMRequest,
    LLMResponse,
    LLMUsage,
)

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "openai/gpt-6-luna"
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
MAX_RETRIES = 2
RETRY_STATUSES = (408, 429, 500, 502, 503, 504)
MAX_RETRY_AFTER = 30.0

_sleep = time.sleep  # module attribute so tests can patch it


def _requests():
    try:
        import requests
    except ImportError:  # pragma: no cover - requests is a hard dependency
        raise LLMProviderError("config", "the 'requests' package is not installed")
    return requests


def _schema_name(custom_id):
    """OpenAI requires ``^[a-zA-Z0-9_-]{1,64}$``; dedupe keys contain ``:``/``.``."""
    return re.sub(r"[^A-Za-z0-9_-]", "_", custom_id or "")[:64] or "step"


def usage_from(usage):
    """Map an OpenRouter ``usage`` dict to ``LLMUsage`` (None = unknown)."""
    if not isinstance(usage, dict):
        return LLMUsage()
    details = usage.get("prompt_tokens_details") or {}
    return LLMUsage(
        input_tokens=usage.get("prompt_tokens"),
        output_tokens=usage.get("completion_tokens"),
        cache_read_tokens=details.get("cached_tokens") or 0,
        cache_write_tokens=details.get("cache_write_tokens") or 0,
    )


def cost_from(usage):
    """``usage.cost`` as a Decimal USD (6 places), or None when absent.

    With BYOK keys ``cost`` can be 0 while ``cost_details.upstream_inference_cost``
    holds the real charge, so that is used as a fallback.
    """
    if not isinstance(usage, dict):
        return None
    value = usage.get("cost")
    if not value:
        value = (usage.get("cost_details") or {}).get("upstream_inference_cost")
    if value is None:
        return None
    try:
        return Decimal(str(value)).quantize(Decimal("0.000001"))
    except (InvalidOperation, ValueError):
        return None


def _error_of(body):
    err = body.get("error") if isinstance(body, dict) else None
    return err if isinstance(err, dict) else {}


def map_status(status, body=None):
    """Map an HTTP status (+ optional error body) to an ``LLMProviderError``."""
    err = _error_of(body)
    message = str(err.get("message") or "")[:200]
    metadata = err.get("metadata") if isinstance(err.get("metadata"), dict) else {}
    if status == 400:
        return LLMProviderError("invalid_request", message)
    if status == 401:
        return LLMProviderError("credentials")
    if status == 402:
        return LLMProviderError("quota", "billing")
    if status == 403:
        if metadata.get("reasons") or re.search(
            r"moderat|refus|guardrail|content_policy", message, re.I
        ):
            return LLMProviderError("refusal", "moderation")
        return LLMProviderError("credentials", message)
    if status == 408:
        return LLMProviderError("timeout")
    if status == 429:
        return LLMProviderError("quota", "rate limited")
    return LLMProviderError("provider", f"http {status}")


def _retry_after(headers, attempt):
    raw = None
    try:
        raw = headers.get("Retry-After")
    except Exception:  # noqa: BLE001 - headers may be any mapping-like
        raw = None
    try:
        delay = float(raw) if raw is not None else float(2**attempt)
    except (TypeError, ValueError):
        delay = float(2**attempt)
    return min(max(delay, 0.0), MAX_RETRY_AFTER)


class OpenRouterProvider(LLMProvider):
    name = "openrouter"
    supports_batches = False

    def __init__(self, api_key=None, model=None, session=None):
        self.api_key = api_key or getattr(settings, "OPENROUTER_API_KEY", None)
        self.default_model = model or getattr(
            settings, "STANDARDIZATION_OPENROUTER_MODEL", DEFAULT_MODEL
        )
        self.default_effort = getattr(settings, "STANDARDIZATION_LLM_EFFORT", "medium")
        self.timeout = getattr(settings, "STANDARDIZATION_LLM_TIMEOUT", 300)
        self.base_url = (
            getattr(settings, "STANDARDIZATION_OPENROUTER_BASE_URL", None)
            or DEFAULT_BASE_URL
        ).rstrip("/")
        self._session = session

    @property
    def session(self):
        if self._session is None:
            if not self.api_key:
                raise LLMProviderError("config", "OPENROUTER_API_KEY is not set")
            self._session = _requests().Session()
        return self._session

    @property
    def url(self):
        return f"{self.base_url}/chat/completions"

    # -- request building ---------------------------------------------------

    def _headers(self):
        if not self.api_key:
            raise LLMProviderError("config", "OPENROUTER_API_KEY is not set")
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        title = getattr(settings, "SITE_NAME", None)
        domain = getattr(settings, "SITE_DOMAIN", None)
        if title:
            headers["X-Title"] = str(title)
        if domain:
            headers["HTTP-Referer"] = f"https://{domain}"
        return headers

    def _params(self, request: LLMRequest) -> dict:
        system_part = {"type": "text", "text": request.system}
        if request.cache_system:
            system_part["cache_control"] = {"type": "ephemeral"}
        body = {
            "model": request.model or self.default_model,
            "messages": [
                {"role": "system", "content": [system_part]},
                {"role": "user", "content": request.user},
            ],
            "max_tokens": request.max_tokens,
            "reasoning": {
                "effort": request.effort or self.default_effort,
                "exclude": True,
            },
        }
        if request.schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": _schema_name(request.custom_id),
                    "strict": True,
                    "schema": request.schema,
                },
            }
            body["provider"] = {"require_parameters": True}
        if request.custom_id:
            body["user"] = request.custom_id[:128]
        return body

    # -- response handling --------------------------------------------------

    @staticmethod
    def _text_of(message):
        content = (message or {}).get("content")
        if isinstance(content, list):
            return "".join(
                part.get("text", "")
                for part in content
                if isinstance(part, dict) and part.get("type", "text") == "text"
            )
        return content or ""

    def _response(self, data, request: LLMRequest) -> LLMResponse:
        if not isinstance(data, dict):
            raise LLMProviderError("provider", "bad json")
        choices = data.get("choices") or []
        top_error = _error_of(data)
        if top_error and not choices:
            raise map_status(_status_of(top_error, 502), data)
        if not choices:
            raise LLMProviderError("provider", "no choices")
        choice = choices[0] or {}
        usage_dict = data.get("usage")
        usage = usage_from(usage_dict)
        model = data.get("model") or request.model or self.default_model
        finish = choice.get("finish_reason") or ""
        response = LLMResponse(
            text=self._text_of(choice.get("message")),
            provider=self.name,
            model=model,
            usage=usage,
            cost_usd=cost_from(usage_dict),
            request_id=str(data.get("id") or "")[:120],
            stop_reason=finish,
            custom_id=request.custom_id,
        )
        if response.cost_usd is None and usage.known:
            logger.warning("openrouter response carried usage but no cost")
        choice_error = _error_of(choice)
        if choice_error or finish == "error":
            detail = str(
                (choice_error.get("metadata") or {}).get("error_type")
                or choice.get("native_finish_reason")
                or "error"
            )[:60]
            raise _WithUsage(LLMProviderError("provider", detail), response)
        if finish == "length":
            raise _WithUsage(LLMProviderError("truncated", "length"), response)
        if finish == "content_filter":
            raise _WithUsage(LLMProviderError("refusal", "content_filter"), response)
        if not response.text.strip():
            raise _WithUsage(LLMProviderError("provider", "empty response"), response)
        return response

    # -- transport ----------------------------------------------------------

    def _post(self, body):
        requests = _requests()
        headers = self._headers()
        for attempt in range(MAX_RETRIES + 1):
            try:
                resp = self.session.post(
                    self.url, json=body, headers=headers, timeout=self.timeout
                )
            except requests.Timeout:
                err = LLMProviderError("timeout")
            except requests.ConnectionError:
                err = LLMProviderError("timeout", "connection")
            except requests.RequestException as exc:
                err = LLMProviderError("provider", type(exc).__name__)
            else:
                status = getattr(resp, "status_code", 0)
                if status < 400:
                    try:
                        return resp.json()
                    except ValueError:
                        raise LLMProviderError("provider", "bad json") from None
                err = map_status(status, _safe_json(resp))
                if status not in RETRY_STATUSES or attempt == MAX_RETRIES:
                    raise err
                _sleep(_retry_after(getattr(resp, "headers", {}) or {}, attempt))
                continue
            if attempt == MAX_RETRIES:
                raise err
            _sleep(float(2**attempt))
        raise err  # pragma: no cover - loop always returns or raises

    def complete(self, request: LLMRequest) -> LLMResponse:
        try:
            data = self._post(self._params(request))
        except LLMProviderError as err:
            logger.warning("openrouter request failed (%s)", err.code)
            raise
        try:
            return self._response(data, request)
        except _WithUsage as wrapped:
            wrapped.error.usage_response = wrapped.response
            raise wrapped.error from None


def _safe_json(resp):
    try:
        body = resp.json()
    except Exception:  # noqa: BLE001 - non-JSON error bodies are common
        return None
    return body if isinstance(body, dict) else None


def _status_of(error, default):
    code = error.get("code")
    return code if isinstance(code, int) and 100 <= code <= 599 else default


class _WithUsage(Exception):
    """Internal: carry the usage of a refused/truncated answer so it is still
    charged to the ledger."""

    def __init__(self, error, response):
        self.error = error
        self.response = response
        super().__init__(error.code)


__all__ = (
    "OpenRouterProvider",
    "DEFAULT_MODEL",
    "DEFAULT_BASE_URL",
    "map_status",
    "usage_from",
    "cost_from",
)
