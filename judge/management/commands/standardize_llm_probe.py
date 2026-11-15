"""Send one tiny structured request through a provider and print usage/cost.

Use after deploying a key: it proves credentials, model access, structured
outputs and the ledger write without touching any problem.
"""

import json

from django.core.management.base import BaseCommand, CommandError

from judge.standardization.llm import LEDGER_OK, call_json
from llm_service.providers import LLMProviderError, LLMRequest, get_provider

PROBE_SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "integer"},
        "model_note": {"type": "string"},
    },
    "required": ["answer", "model_note"],
    "additionalProperties": False,
}


class Command(BaseCommand):
    help = "Probe the configured LLM provider with one small JSON request."

    def add_arguments(self, parser):
        parser.add_argument("--provider", default=None, help="anthropic | poe")
        parser.add_argument("--model", default=None)
        parser.add_argument("--effort", default="low")

    def handle(self, *args, **options):
        try:
            provider = get_provider(options["provider"])
        except LLMProviderError as exc:
            raise CommandError(str(exc))
        request = LLMRequest(
            system="You are a terse assistant used for a connectivity probe.",
            user="What is 6 * 7? Put the number in `answer` and one short word in `model_note`.",
            schema=PROBE_SCHEMA,
            model=options["model"],
            max_tokens=2000,
            effort=options["effort"],
            custom_id="probe",
        )
        try:
            payload, ledger = call_json(request, step="probe", provider=provider)
        except LLMProviderError as exc:
            raise CommandError(f"provider error: {exc.code} {exc.detail}".strip())
        self.stdout.write(
            json.dumps(
                {
                    "provider": ledger.provider,
                    "model": ledger.model,
                    "payload": payload,
                    "usage": {
                        "input": ledger.input_tokens,
                        "output": ledger.output_tokens,
                        "cache_read": ledger.cache_read_tokens,
                        "cache_write": ledger.cache_write_tokens,
                    },
                    "cost_usd": (
                        str(ledger.cost_usd) if ledger.cost_usd is not None else None
                    ),
                    "ledger_id": ledger.id,
                    "status": ledger.status,
                    "ok": ledger.status == LEDGER_OK and payload.get("answer") == 42,
                },
                ensure_ascii=False,
            )
        )
