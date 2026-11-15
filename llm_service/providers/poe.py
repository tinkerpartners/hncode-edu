"""Poe provider: wraps the existing ``LLMService`` so standardization steps
can run on the Poe key when no direct Anthropic key is configured.

Poe reports no token usage, so ``usage`` is unknown and ``cost_usd`` is
``None``; the ledger shows these rows as unmeasured. ``schema`` is enforced
client-side only (the JSON is validated by the caller), and the system
prompt gets an explicit "answer with JSON only" suffix. No batch support.
"""

import logging

from django.conf import settings

from llm_service.providers.base import (
    LLMProvider,
    LLMProviderError,
    LLMRequest,
    LLMResponse,
    LLMUsage,
)

logger = logging.getLogger(__name__)

JSON_SUFFIX = (
    "\n\nReply with a single JSON object only — no prose, no Markdown fence. "
    "It must validate against this JSON schema:\n"
)


class PoeProvider(LLMProvider):
    name = "poe"
    supports_batches = False

    def __init__(self, service=None, bot_name=None):
        self._service = service
        self.bot_name = bot_name or getattr(
            settings, "STANDARDIZATION_POE_BOT_NAME", None
        )

    @property
    def service(self):
        if self._service is None:
            from llm_service.config import get_config
            from llm_service.llm_api import LLMService

            try:
                config = get_config()
            except ValueError as exc:
                raise LLMProviderError("config", str(exc)) from None
            self._service = LLMService(
                api_key=config.api_key,
                bot_name=self.bot_name or config.get_bot_name_for_markdown(),
                timeout=getattr(settings, "STANDARDIZATION_LLM_TIMEOUT", 300),
                raise_provider_errors=True,
            )
        return self._service

    def complete(self, request: LLMRequest) -> LLMResponse:
        import json

        from llm_service.llm_api import PoeRequestError

        system = request.system
        if request.schema is not None:
            system += JSON_SUFFIX + json.dumps(request.schema, ensure_ascii=False)
        try:
            text = self.service.call_llm(request.user, system_prompt=system)
        except PoeRequestError as exc:
            raise LLMProviderError(exc.code) from None
        except Exception as exc:  # noqa: BLE001
            logger.warning("poe request failed (%s)", type(exc).__name__)
            raise LLMProviderError("provider", type(exc).__name__) from None
        if not text:
            raise LLMProviderError("provider", "empty response")
        return LLMResponse(
            text=text,
            provider=self.name,
            model=self.service.bot_name,
            usage=LLMUsage(),
            cost_usd=None,
            custom_id=request.custom_id,
        )


__all__ = ("PoeProvider",)
