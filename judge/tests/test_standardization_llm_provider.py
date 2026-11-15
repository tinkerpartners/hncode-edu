import json
from decimal import Decimal
from io import StringIO
from types import SimpleNamespace
from unittest import mock

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase

from judge.models.standardization import LLMUsageLedger
from judge.standardization.llm import StepValidationError, call_json
from llm_service import pricing
from llm_service.providers import (
    LLMProviderError,
    LLMRequest,
    LLMUsage,
    get_provider,
)
from llm_service.providers.anthropic_api import AnthropicProvider, map_exception
from llm_service.providers.poe import JSON_SUFFIX, PoeProvider

SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "integer"}},
    "required": ["answer"],
    "additionalProperties": False,
}


def fake_message(text, stop_reason="end_turn", model="claude-opus-5-5", **usage):
    base = {"input_tokens": 1000, "output_tokens": 100}
    base.update(usage)
    return SimpleNamespace(
        id="msg_1",
        _request_id="req_1",
        model=model,
        stop_reason=stop_reason,
        content=[SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(**base),
    )


class FakeMessages:
    def __init__(self, result):
        self.result = result
        self.calls = []
        self.batches = SimpleNamespace(
            create=self._batch_create, retrieve=self._retrieve, results=self._results
        )
        self.batch_payload = None
        self.batch_results = []

    def create(self, **params):
        self.calls.append(params)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result

    def _batch_create(self, requests):
        self.batch_payload = requests
        return SimpleNamespace(id="msgbatch_1", processing_status="in_progress")

    def _retrieve(self, batch_id):
        return SimpleNamespace(
            processing_status="ended",
            request_counts=SimpleNamespace(
                processing=0, succeeded=1, errored=1, canceled=0, expired=1
            ),
        )

    def _results(self, batch_id):
        return iter(self.batch_results)


def provider_with(result):
    messages = FakeMessages(result)
    return (
        AnthropicProvider(api_key="k", client=SimpleNamespace(messages=messages)),
        messages,
    )


class PricingTest(SimpleTestCase):
    def test_opus_cost_and_batch_discount(self):
        usage = LLMUsage(
            1_000_000, 100_000, cache_read_tokens=500_000, cache_write_tokens=0
        )
        self.assertEqual(
            pricing.estimate_cost("claude-opus-5-5", usage), Decimal("6.100000")
        )  # 4 + 2 + 0.10
        self.assertEqual(
            pricing.estimate_cost("claude-opus-5-5", usage, batch=True),
            Decimal("3.050000"),
        )

    def test_unknown_model_or_usage_is_none(self):
        self.assertIsNone(pricing.estimate_cost("gpt-x", LLMUsage(1, 1)))
        self.assertIsNone(pricing.estimate_cost("claude-opus-5-5", LLMUsage()))


class AnthropicProviderTest(SimpleTestCase):
    def test_params_use_structured_output_and_cache_without_batch_forbidden_keys(self):
        provider, _ = provider_with(fake_message("{}"))
        params = provider._params(
            LLMRequest(
                system="S", user="U", schema=SCHEMA, effort="low", max_tokens=500
            )
        )
        self.assertEqual(params["model"], "claude-opus-5-5")
        self.assertEqual(params["max_tokens"], 500)
        self.assertEqual(params["system"][0]["cache_control"], {"type": "ephemeral"})
        self.assertEqual(params["output_config"]["effort"], "low")
        self.assertEqual(params["output_config"]["format"]["type"], "json_schema")
        self.assertEqual(params["output_config"]["format"]["schema"], SCHEMA)
        for forbidden in ("stream", "speed", "fallbacks", "thinking"):
            self.assertNotIn(forbidden, params)

    def test_complete_returns_text_usage_and_cost(self):
        provider, messages = provider_with(
            fake_message(
                '{"answer": 42}',
                cache_read_input_tokens=800,
                cache_creation_input_tokens=0,
            )
        )
        response = provider.complete(LLMRequest(system="S", user="U", schema=SCHEMA))
        self.assertEqual(response.parsed_json(), {"answer": 42})
        self.assertEqual(response.usage.cache_read_tokens, 800)
        self.assertEqual(response.request_id, "req_1")
        self.assertEqual(
            response.cost_usd, Decimal("0.006160")
        )  # 1000*4 + 100*20 + 800*0.2 per MTok
        self.assertEqual(len(messages.calls), 1)

    def test_refusal_and_truncation_are_errors_that_keep_usage(self):
        for stop, code in (("refusal", "refusal"), ("max_tokens", "truncated")):
            provider, _ = provider_with(fake_message("partial", stop_reason=stop))
            with self.assertRaises(LLMProviderError) as ctx:
                provider.complete(LLMRequest(system="S", user="U"))
            self.assertEqual(ctx.exception.code, code)
            self.assertEqual(ctx.exception.usage_response.usage.input_tokens, 1000)

    def test_sdk_exceptions_map_to_codes(self):
        import anthropic
        import httpx

        def status_exc(cls, status):
            request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
            response = httpx.Response(status, request=request)
            return cls("boom", response=response, body=None)

        self.assertEqual(
            map_exception(status_exc(anthropic.AuthenticationError, 401)).code,
            "credentials",
        )
        self.assertEqual(
            map_exception(status_exc(anthropic.RateLimitError, 429)).code, "quota"
        )
        self.assertEqual(
            map_exception(status_exc(anthropic.BadRequestError, 400)).code,
            "invalid_request",
        )
        self.assertEqual(
            map_exception(status_exc(anthropic.InternalServerError, 529)).code,
            "provider",
        )
        self.assertTrue(
            map_exception(status_exc(anthropic.InternalServerError, 529)).retryable
        )
        self.assertEqual(
            map_exception(
                anthropic.APIConnectionError(request=httpx.Request("POST", "https://x"))
            ).code,
            "timeout",
        )
        provider, _ = provider_with(status_exc(anthropic.RateLimitError, 429))
        with self.assertRaises(LLMProviderError) as ctx:
            provider.complete(LLMRequest(system="S", user="U"))
        self.assertEqual(ctx.exception.code, "quota")

    def test_missing_key_is_a_config_error(self):
        with self.settings(ANTHROPIC_API_KEY=None):
            provider = AnthropicProvider()
            with self.assertRaises(LLMProviderError) as ctx:
                provider.complete(LLMRequest(system="S", user="U"))
            self.assertEqual(ctx.exception.code, "config")

    def test_batch_submit_requires_unique_custom_ids(self):
        provider, messages = provider_with(None)
        reqs = [
            LLMRequest(system="S", user=f"U{i}", schema=SCHEMA, custom_id=f"k{i}")
            for i in range(2)
        ]
        self.assertEqual(provider.submit_batch(reqs), "msgbatch_1")
        self.assertEqual([r["custom_id"] for r in messages.batch_payload], ["k0", "k1"])
        self.assertEqual(
            messages.batch_payload[0]["params"]["output_config"]["format"]["schema"],
            SCHEMA,
        )
        with self.assertRaises(LLMProviderError):
            provider.submit_batch([LLMRequest(system="S", user="U")])
        with self.assertRaises(LLMProviderError):
            provider.submit_batch([reqs[0], reqs[0]])
        status = provider.poll_batch("msgbatch_1")
        self.assertTrue(status.ended)
        self.assertEqual(status.counts["succeeded"], 1)

    def test_batch_results_are_keyed_by_custom_id_and_half_price(self):
        provider, messages = provider_with(None)
        messages.batch_results = [
            SimpleNamespace(
                custom_id="k0",
                result=SimpleNamespace(
                    type="succeeded", message=fake_message('{"answer": 1}')
                ),
            ),
            SimpleNamespace(
                custom_id="k1",
                result=SimpleNamespace(
                    type="errored", error=SimpleNamespace(type="invalid_request")
                ),
            ),
            SimpleNamespace(custom_id="k2", result=SimpleNamespace(type="expired")),
        ]
        reqs = {
            f"k{i}": LLMRequest(system="S", user="U", custom_id=f"k{i}")
            for i in range(3)
        }
        results = list(provider.iter_batch_results("msgbatch_1", reqs))
        self.assertEqual(
            [r.status for r in results], ["succeeded", "errored", "expired"]
        )
        ok = results[0].response
        self.assertEqual(ok.batch_id, "msgbatch_1")
        self.assertEqual(ok.custom_id, "k0")
        self.assertEqual(ok.cost_usd, Decimal("0.003000"))  # (4000+2000)/1e6 * 0.5
        self.assertEqual(results[1].error.code, "invalid_request")
        self.assertFalse(results[1].error.retryable)
        self.assertTrue(results[2].error.retryable)


class PoeProviderTest(SimpleTestCase):
    def test_json_suffix_and_unknown_usage(self):
        service = mock.Mock()
        service.bot_name = "Claude-Opus-4.7"
        service.call_llm.return_value = '```json\n{"answer": 7}\n```'
        provider = PoeProvider(service=service)
        response = provider.complete(LLMRequest(system="S", user="U", schema=SCHEMA))
        self.assertEqual(response.parsed_json(), {"answer": 7})
        self.assertIsNone(response.cost_usd)
        self.assertFalse(response.usage.known)
        system_sent = service.call_llm.call_args.kwargs["system_prompt"]
        self.assertTrue(system_sent.startswith("S" + JSON_SUFFIX))

    def test_poe_error_codes_pass_through(self):
        from llm_service.llm_api import PoeRequestError

        service = mock.Mock()
        service.call_llm.side_effect = PoeRequestError("quota")
        with self.assertRaises(LLMProviderError) as ctx:
            PoeProvider(service=service).complete(LLMRequest(system="S", user="U"))
        self.assertEqual(ctx.exception.code, "quota")

    def test_get_provider_names(self):
        with self.settings(STANDARDIZATION_LLM_PROVIDER="poe"):
            self.assertIsInstance(get_provider(), PoeProvider)
        self.assertIsInstance(get_provider("anthropic"), AnthropicProvider)
        with self.assertRaises(LLMProviderError):
            get_provider("gpt")


class CallJsonLedgerTest(TestCase):
    def test_ok_call_writes_one_ok_row(self):
        provider, _ = provider_with(fake_message('{"answer": 42}'))
        payload, ledger = call_json(
            LLMRequest(system="S", user="U", schema=SCHEMA),
            step="probe",
            provider=provider,
        )
        self.assertEqual(payload, {"answer": 42})
        self.assertEqual(ledger.status, "ok")
        self.assertEqual(ledger.input_tokens, 1000)
        self.assertEqual(ledger.request_id, "req_1")
        self.assertEqual(LLMUsageLedger.objects.count(), 1)

    def test_schema_violation_is_recorded_as_error(self):
        provider, _ = provider_with(fake_message('{"answer": "forty-two"}'))
        with self.assertRaises(StepValidationError):
            call_json(
                LLMRequest(system="S", user="U", schema=SCHEMA),
                step="probe",
                provider=provider,
            )
        row = LLMUsageLedger.objects.get()
        self.assertEqual(row.status, "error")
        self.assertEqual(row.output_tokens, 100)  # still charged

    def test_refusal_is_recorded_with_usage(self):
        provider, _ = provider_with(fake_message("no", stop_reason="refusal"))
        with self.assertRaises(LLMProviderError):
            call_json(LLMRequest(system="S", user="U"), step="probe", provider=provider)
        row = LLMUsageLedger.objects.get()
        self.assertEqual((row.status, row.input_tokens), ("error", 1000))

    def test_poe_rows_are_unknown_not_free(self):
        service = mock.Mock()
        service.bot_name = "bot"
        service.call_llm.return_value = '{"answer": 1}'
        _, ledger = call_json(
            LLMRequest(system="S", user="U", schema=SCHEMA),
            step="probe",
            provider=PoeProvider(service=service),
        )
        self.assertEqual(ledger.status, "unknown")
        self.assertIsNone(ledger.cost_usd)

    def test_probe_command(self):
        provider, _ = provider_with(fake_message('{"answer": 42, "model_note": "ok"}'))
        out = StringIO()
        with mock.patch(
            "judge.management.commands.standardize_llm_probe.get_provider",
            return_value=provider,
        ):
            call_command("standardize_llm_probe", stdout=out)
        result = json.loads(out.getvalue())
        self.assertTrue(result["ok"])
        self.assertEqual(result["cost_usd"], "0.006000")
