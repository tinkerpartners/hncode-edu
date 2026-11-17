"""Provider abstraction for the standardization pipeline.

``get_provider(name)`` returns a configured ``LLMProvider``. Steps never
import a concrete provider; they ask for one by name (or the configured
default) so Poe, the direct Anthropic API and OpenRouter are interchangeable
per step.
"""

from django.conf import settings

from llm_service.providers.base import (
    LLMProvider,
    LLMProviderError,
    LLMRequest,
    LLMResponse,
    LLMUsage,
)

__all__ = (
    "LLMProvider",
    "LLMProviderError",
    "LLMRequest",
    "LLMResponse",
    "LLMUsage",
    "get_provider",
    "PROVIDER_NAMES",
)

PROVIDER_NAMES = ("anthropic", "poe", "openrouter")


def get_provider(name=None) -> LLMProvider:
    name = name or getattr(settings, "STANDARDIZATION_LLM_PROVIDER", "anthropic")
    if name == "anthropic":
        from llm_service.providers.anthropic_api import AnthropicProvider

        return AnthropicProvider()
    if name == "poe":
        from llm_service.providers.poe import PoeProvider

        return PoeProvider()
    if name == "openrouter":
        from llm_service.providers.openrouter import OpenRouterProvider

        return OpenRouterProvider()
    raise LLMProviderError("config", f"unknown LLM provider {name!r}")
