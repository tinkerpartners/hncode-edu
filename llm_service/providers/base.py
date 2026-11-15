"""Provider-neutral request/response types.

A ``LLMRequest`` carries everything a step wants to say; a provider turns it
into its own wire format. ``LLMResponse.usage`` is what the provider actually
reported — ``None`` fields mean "unknown", never 0, so the ledger can tell a
free call from an unmeasured one.
"""

import json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Iterator, List, Optional


class LLMProviderError(Exception):
    """A provider failure with a stable, loggable ``code``.

    Codes: ``credentials``, ``quota``, ``timeout``, ``invalid_request``,
    ``refusal``, ``truncated``, ``provider``, ``config``. The raw provider
    response is never attached; ``detail`` is a short safe string.
    """

    RETRYABLE = ("quota", "timeout", "provider")

    def __init__(self, code, detail=""):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)

    @property
    def retryable(self):
        return self.code in self.RETRYABLE


@dataclass
class LLMRequest:
    system: str
    user: str
    schema: Optional[dict] = None  # JSON schema the answer must satisfy
    model: Optional[str] = None  # provider default when None
    max_tokens: int = 16000
    effort: Optional[str] = None  # low | medium | high | xhigh | max
    custom_id: str = ""  # dedupe_key; required for batches
    cache_system: bool = True  # mark the system prompt as cacheable
    metadata: dict = field(default_factory=dict)  # free-form, never sent


@dataclass
class LLMUsage:
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    cache_read_tokens: Optional[int] = None
    cache_write_tokens: Optional[int] = None

    @property
    def known(self):
        return self.input_tokens is not None and self.output_tokens is not None

    def as_ledger_fields(self):
        return {
            "input_tokens": self.input_tokens or 0,
            "output_tokens": self.output_tokens or 0,
            "cache_read_tokens": self.cache_read_tokens or 0,
            "cache_write_tokens": self.cache_write_tokens or 0,
        }


@dataclass
class LLMResponse:
    text: str
    provider: str
    model: str
    usage: LLMUsage = field(default_factory=LLMUsage)
    cost_usd: Optional[Decimal] = None  # None = unknown (e.g. Poe)
    request_id: str = ""
    stop_reason: str = ""
    custom_id: str = ""
    batch_id: str = ""

    def parsed_json(self):
        """Parse ``text`` as JSON, tolerating a ```json fence. Raises
        ``LLMProviderError('invalid_json')`` when it is not JSON."""
        text = self.text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else ""
            if text.rstrip().endswith("```"):
                text = text.rstrip()[:-3]
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise LLMProviderError("invalid_json", str(exc)[:200]) from None


@dataclass
class BatchStatus:
    batch_id: str
    ended: bool
    status: str
    counts: dict = field(default_factory=dict)


class LLMProvider:
    """Interface every provider implements."""

    name = "base"
    supports_batches = False

    def complete(self, request: LLMRequest) -> LLMResponse:
        raise NotImplementedError

    def submit_batch(self, requests: List[LLMRequest]) -> str:
        raise LLMProviderError("config", f"{self.name} does not support batches")

    def poll_batch(self, batch_id: str) -> BatchStatus:
        raise LLMProviderError("config", f"{self.name} does not support batches")

    def iter_batch_results(self, batch_id: str) -> Iterator["BatchResult"]:
        raise LLMProviderError("config", f"{self.name} does not support batches")


@dataclass
class BatchResult:
    custom_id: str
    status: str  # succeeded | errored | canceled | expired
    response: Optional[LLMResponse] = None
    error: Optional[LLMProviderError] = None
