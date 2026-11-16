import json
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import TestCase, override_settings

from judge.models import Problem, ProblemGroup, ProblemType
from judge.models.standardization import (
    LLMBatchJob,
    LLMUsageLedger,
    StandardizationBatch,
    StandardizationProposal,
)
from judge.standardization import batches, budget, steps
from judge.standardization.enums import (
    BatchKind,
    BatchStatus,
    ProposalStatus,
)
from judge.tests.test_standardization_apply import ensure_languages
from judge.tests.test_standardization_steps import (
    CONTENT_PAYLOAD,
    STATEMENT,
    TAGS_PAYLOAD,
    FakeProvider,
)
from llm_service.providers import LLMProviderError, LLMResponse, LLMUsage
from llm_service.providers.base import BatchResult, BatchStatus as ProviderBatchStatus


class FakeBatchProvider(FakeProvider):
    name = "fakebatch"
    supports_batches = True

    def __init__(self, payloads):
        super().__init__(payloads)
        self.submitted = []  # list of request lists
        self.results = {}  # provider_batch_id -> list[BatchResult]
        self.ended = set()

    def submit_batch(self, requests):
        self.submitted.append(list(requests))
        return f"msgbatch_{len(self.submitted)}"

    def poll_batch(self, batch_id):
        ended = batch_id in self.ended
        return ProviderBatchStatus(
            batch_id,
            ended,
            "ended" if ended else "in_progress",
            {"processing": 0 if ended else 1},
        )

    def iter_batch_results(self, batch_id, requests_by_id=None):
        return iter(self.results.get(batch_id, []))

    def ok(self, custom_id, payload, model="fake-model"):
        return BatchResult(
            custom_id,
            "succeeded",
            LLMResponse(
                text=self._json.dumps(payload),
                provider=self.name,
                model=model,
                usage=LLMUsage(1000, 100),
                cost_usd=Decimal("0.002"),
                request_id="r",
                custom_id=custom_id,
                batch_id="b",
            ),
        )


class BudgetTest(TestCase):
    fixtures = ["language_small"]

    @classmethod
    def setUpTestData(cls):
        ensure_languages()
        cls.group = ProblemGroup.objects.create(name="stdbg", full_name="std budget")
        cls.problem = Problem.objects.create(
            code="bg1",
            name="bg1",
            description=STATEMENT,
            group=cls.group,
            time_limit=1,
            memory_limit=1048576,
            points=100,
        )
        cls.batch = StandardizationBatch.objects.create(
            name="b",
            kind=BatchKind.AI_PIPELINE,
            id_from=1,
            id_through=9,
            status=BatchStatus.RUNNING,
        )

    def spend(self, amount, **kw):
        LLMUsageLedger.objects.create(
            provider="x", model="m", step="s", cost_usd=Decimal(amount), **kw
        )

    @override_settings(
        STANDARDIZATION_BUDGET_USD_PER_PROBLEM=1,
        STANDARDIZATION_BUDGET_USD_PER_DAY=5,
        STANDARDIZATION_BUDGET_USD_TOTAL=None,
    )
    def test_caps_from_ledger_sums(self):
        self.spend("0.6", problem=self.problem)
        status = budget.check_budget(problem=self.problem, batch=self.batch)
        self.assertEqual(status.problem, Decimal("0.6"))
        self.spend("0.5", problem=self.problem)
        with self.assertRaises(budget.BudgetExceeded) as ctx:
            budget.check_budget(problem=self.problem)
        self.assertEqual(ctx.exception.scope, "problem")
        # another problem is fine until the day cap
        other = Problem.objects.create(
            code="bg2",
            name="bg2",
            description="x",
            group=self.group,
            time_limit=1,
            memory_limit=1,
            points=1,
        )
        budget.check_budget(problem=other)
        self.spend("4")  # unattributed spend today
        with self.assertRaises(budget.BudgetExceeded) as ctx:
            budget.check_budget(problem=other)
        self.assertEqual(ctx.exception.scope, "day")
        # unknown-cost rows do not count
        LLMUsageLedger.objects.create(
            provider="poe", model="m", step="s", cost_usd=None
        )
        self.assertEqual(budget.spent().total, Decimal("5.1"))

    @override_settings(STANDARDIZATION_BUDGET_USD_PER_BATCH=Decimal("0.001"))
    def test_run_step_pauses_batch_when_cap_hit(self):
        self.spend("0.001", batch=self.batch)
        provider = FakeProvider({"content": CONTENT_PAYLOAD})
        result = steps.run_step(
            steps.STEP_CONTENT, self.problem, batch=self.batch, provider=provider
        )
        self.assertEqual(result.skipped, "budget")
        self.assertEqual(result.error, "budget:batch")
        self.assertEqual(provider.calls, [])
        self.assertFalse(StandardizationProposal.objects.exists())
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.status, BatchStatus.PAUSED)
        self.assertIn(
            "batch budget exceeded", self.batch.counters_json["paused_reason"]
        )
        # paused batch: later steps are skipped without spending
        result = steps.run_step(
            steps.STEP_CONTENT, self.problem, batch=self.batch, provider=provider
        )
        self.assertEqual(result.skipped, "batch_paused")
        self.assertEqual(provider.calls, [])


class BatchRunnerTest(TestCase):
    fixtures = ["language_small"]

    @classmethod
    def setUpTestData(cls):
        ensure_languages()
        cls.group = ProblemGroup.objects.create(name="stdbt", full_name="std batches")
        ProblemType.objects.create(name="implementation", full_name="implementation")
        ProblemType.objects.create(name="math", full_name="math")
        cls.problems = [
            Problem.objects.create(
                code=f"bt{i}",
                name=f"bt{i}",
                description=STATEMENT,
                group=cls.group,
                time_limit=1,
                memory_limit=1048576,
                points=100,
            )
            for i in range(3)
        ]
        cls.empty = Problem.objects.create(
            code="bt_empty",
            name="e",
            description="",
            group=cls.group,
            time_limit=1,
            memory_limit=1,
            points=1,
        )

    def setUp(self):
        self.batch = StandardizationBatch.objects.create(
            name="wave",
            kind=BatchKind.AI_PIPELINE,
            id_from=1,
            id_through=99,
            status=BatchStatus.RUNNING,
        )
        self.provider = FakeBatchProvider(
            {"content": CONTENT_PAYLOAD, "tags": TAGS_PAYLOAD}
        )

    def submit(self, step=steps.STEP_CONTENT, problems=None, chunk=2):
        return batches.submit_step_batches(
            self.batch,
            step,
            problems or self.problems + [self.empty],
            self.provider,
            chunk=chunk,
        )

    def test_submit_chunks_creates_jobs_and_drafts(self):
        summary = self.submit()
        self.assertEqual(summary["counters"], {"submitted": 3, "needs_source": 1})
        self.assertEqual(len(summary["jobs"]), 2)  # chunk=2 -> 2 + 1
        self.assertEqual([len(r) for r in self.provider.submitted], [2, 1])
        jobs = list(LLMBatchJob.objects.order_by("pk"))
        self.assertEqual([j.request_count for j in jobs], [2, 1])
        self.assertEqual(jobs[0].provider_batch_id, "msgbatch_1")
        drafts = StandardizationProposal.objects.filter(status=ProposalStatus.DRAFT)
        self.assertEqual(drafts.count(), 3)
        d = drafts.get(problem=self.problems[0])
        self.assertTrue(d.evidence_json["pending_batch"])
        self.assertEqual(d.evidence_json["provider_batch_id"], "msgbatch_1")
        self.assertEqual(d.dedupe_key, self.provider.submitted[0][0].custom_id)
        self.assertEqual(
            StandardizationProposal.objects.get(problem=self.empty).status,
            ProposalStatus.NEEDS_SOURCE,
        )
        # resubmitting: everything pending -> nothing new
        again = self.submit()
        self.assertEqual(again["counters"], {"pending_batch": 3, "dedupe": 1})
        self.assertEqual(len(self.provider.submitted), 2)
        # the sync runner also refuses to double-spend on a pending draft
        r = steps.run_step(steps.STEP_CONTENT, self.problems[0], provider=self.provider)
        self.assertEqual(r.skipped, "pending_batch")

    def test_poll_ingests_success_error_and_requeue(self):
        self.submit(chunk=10)
        job = LLMBatchJob.objects.get()
        reqs = self.provider.submitted[0]
        k0, k1, k2 = (r.custom_id for r in reqs)
        # not ended yet
        out = batches.poll_job(job, self.provider)
        self.assertFalse(out["ended"])
        job.refresh_from_db()
        self.assertEqual(job.status, "in_progress")
        self.provider.ended.add("msgbatch_1")
        self.provider.results["msgbatch_1"] = [
            self.provider.ok(k0, CONTENT_PAYLOAD),
            BatchResult(
                k1, "errored", None, LLMProviderError("invalid_request", "bad")
            ),
            BatchResult(k2, "expired", None, LLMProviderError("provider", "expired")),
            BatchResult("nobody", "succeeded", None, None),
        ]
        out = batches.poll_job(job, self.provider)
        self.assertTrue(out["ended"])
        self.assertEqual(
            out["ingested"], {"finalized": 1, "error": 1, "requeued": 1, "orphan": 1}
        )
        job.refresh_from_db()
        self.assertTrue(job.results_ingested)
        self.assertIsNotNone(job.ended_at)
        p0 = StandardizationProposal.objects.get(dedupe_key=k0)
        self.assertEqual(p0.status, ProposalStatus.PENDING_REVIEW)
        self.assertEqual(p0.model, "fake-model")
        self.assertEqual(p0.cost_usd, Decimal("0.002"))
        self.assertEqual(
            p0.payload_json["description"][:20], CONTENT_PAYLOAD["statement"][:20]
        )
        self.assertEqual(p0.evidence_json["validation"], "STRICT_OK")
        self.assertNotIn("pending_batch", p0.evidence_json)
        ledger = LLMUsageLedger.objects.get(proposal=p0)
        self.assertEqual(
            (ledger.status, ledger.provider_batch_id, ledger.input_tokens),
            ("ok", "msgbatch_1", 1000),
        )
        p1 = StandardizationProposal.objects.get(dedupe_key=k1)
        self.assertEqual(p1.status, ProposalStatus.ERROR)
        self.assertEqual(p1.evidence_json["error"], "provider:invalid_request")
        p2 = StandardizationProposal.objects.get(dedupe_key=k2)
        self.assertEqual(p2.status, ProposalStatus.DRAFT)
        self.assertFalse(p2.evidence_json["pending_batch"])
        self.assertEqual(p2.evidence_json["requeue"], 1)
        self.assertEqual(LLMUsageLedger.objects.filter(status="expired").count(), 1)
        # re-submit picks up only the re-queued one
        again = self.submit(chunk=10)
        self.assertEqual(again["counters"]["submitted"], 1)
        self.assertEqual(self.provider.submitted[1][0].custom_id, k2)
        p2.refresh_from_db()
        self.assertTrue(p2.evidence_json["pending_batch"])
        # a second expiry is final
        job2 = LLMBatchJob.objects.get(provider_batch_id="msgbatch_2")
        self.provider.ended.add("msgbatch_2")
        self.provider.results["msgbatch_2"] = [
            BatchResult(k2, "expired", None, LLMProviderError("provider", "expired"))
        ]
        out = batches.poll_job(job2, self.provider)
        self.assertEqual(out["ingested"], {"error": 1})
        p2.refresh_from_db()
        self.assertEqual(p2.status, ProposalStatus.ERROR)

    def test_tags_points_batch_fills_both_drafts(self):
        self.submit(step=steps.STEP_TAGS_POINTS, problems=[self.problems[0]], chunk=10)
        drafts = StandardizationProposal.objects.filter(status=ProposalStatus.DRAFT)
        self.assertEqual(
            set(drafts.values_list("field_group", flat=True)), {"tags", "points"}
        )
        key = self.provider.submitted[0][0].custom_id
        self.assertEqual(drafts.get(field_group="points").dedupe_key, key)
        job = LLMBatchJob.objects.get()
        self.provider.ended.add("msgbatch_1")
        self.provider.results["msgbatch_1"] = [self.provider.ok(key, TAGS_PAYLOAD)]
        out = batches.poll_job(job, self.provider)
        self.assertEqual(out["ingested"], {"finalized": 1})
        by_group = {
            p.field_group: p
            for p in StandardizationProposal.objects.filter(problem=self.problems[0])
        }
        self.assertEqual(by_group["tags"].status, ProposalStatus.PENDING_REVIEW)
        self.assertEqual(
            by_group["tags"].payload_json["names"], ["implementation", "math"]
        )
        self.assertEqual(by_group["points"].payload_json["points"], 800)
        self.assertEqual(LLMUsageLedger.objects.count(), 1)

    def test_late_result_for_cancelled_batch_is_never_finalized(self):
        self.submit(problems=[self.problems[0]], chunk=10)
        key = self.provider.submitted[0][0].custom_id
        self.batch.status = BatchStatus.CANCELLED
        self.batch.save()
        job = LLMBatchJob.objects.get()
        self.provider.ended.add("msgbatch_1")
        self.provider.results["msgbatch_1"] = [self.provider.ok(key, CONTENT_PAYLOAD)]
        out = batches.poll_job(job, self.provider)
        self.assertEqual(out["ingested"], {"late": 1})
        p = StandardizationProposal.objects.get(dedupe_key=key)
        self.assertEqual(p.status, ProposalStatus.DRAFT)
        self.assertTrue(p.evidence_json["late"])
        self.assertEqual(p.payload_json, {})
        self.assertEqual(
            LLMUsageLedger.objects.get().cost_usd, Decimal("0.002")
        )  # still charged

    def test_changed_source_marks_stale(self):
        self.submit(problems=[self.problems[1]], chunk=10)
        key = self.provider.submitted[0][0].custom_id
        self.problems[1].description += "\nsửa"
        self.problems[1].save()
        job = LLMBatchJob.objects.get()
        self.provider.ended.add("msgbatch_1")
        self.provider.results["msgbatch_1"] = [self.provider.ok(key, CONTENT_PAYLOAD)]
        out = batches.poll_job(job, self.provider)
        self.assertEqual(out["ingested"], {"stale": 1})
        self.assertEqual(
            StandardizationProposal.objects.get(dedupe_key=key).status,
            ProposalStatus.STALE,
        )

    def test_schema_violation_in_batch_result_is_error(self):
        self.submit(problems=[self.problems[2]], chunk=10)
        key = self.provider.submitted[0][0].custom_id
        bad = dict(CONTENT_PAYLOAD)
        del bad["examples"]
        job = LLMBatchJob.objects.get()
        self.provider.ended.add("msgbatch_1")
        self.provider.results["msgbatch_1"] = [self.provider.ok(key, bad)]
        out = batches.poll_job(job, self.provider)
        self.assertEqual(out["ingested"], {"schema_error": 1})
        p = StandardizationProposal.objects.get(dedupe_key=key)
        self.assertEqual(p.status, ProposalStatus.ERROR)
        self.assertTrue(p.evidence_json["error"].startswith("schema:"))
        self.assertEqual(LLMUsageLedger.objects.get().status, "error")

    @override_settings(STANDARDIZATION_BUDGET_USD_PER_BATCH=Decimal("0.001"))
    def test_submit_stops_and_pauses_on_budget(self):
        LLMUsageLedger.objects.create(
            provider="x",
            model="m",
            step="s",
            cost_usd=Decimal("0.001"),
            batch=self.batch,
        )
        summary = self.submit(chunk=10)
        self.assertEqual(summary["counters"], {"budget": 3, "needs_source": 1})
        self.assertEqual(self.provider.submitted, [])
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.status, BatchStatus.PAUSED)
        self.assertFalse(
            StandardizationProposal.objects.filter(status=ProposalStatus.DRAFT).exists()
        )

    def test_provider_without_batches_is_refused(self):
        with self.assertRaises(LLMProviderError):
            batches.submit_step_batches(
                self.batch, steps.STEP_CONTENT, self.problems, FakeProvider({})
            )

    def test_command_submit_poll_status(self):
        out = StringIO()
        with mock.patch(
            "judge.management.commands.standardize_llm_batches.get_provider",
            return_value=self.provider,
        ):
            call_command(
                "standardize_llm_batches",
                "submit",
                "--step",
                "content",
                "--codes",
                "bt0,bt1",
                "--name",
                "cmd wave",
                stdout=out,
            )
            summary = json.loads(out.getvalue().splitlines()[-1])
            self.assertEqual(summary["counters"], {"submitted": 2})
            batch = StandardizationBatch.objects.get(pk=summary["batch_id"])
            self.assertEqual(
                (batch.name, batch.kind, batch.steps_json),
                ("cmd wave", "ai_pipeline", [steps.STEP_CONTENT]),
            )
            self.provider.ended.add("msgbatch_1")
            self.provider.results["msgbatch_1"] = [
                self.provider.ok(r.custom_id, CONTENT_PAYLOAD)
                for r in self.provider.submitted[0]
            ]
            out = StringIO()
            call_command("standardize_llm_batches", "poll", stdout=out)
            self.assertEqual(
                json.loads(out.getvalue())[0]["ingested"], {"finalized": 2}
            )
            out = StringIO()
            call_command("standardize_llm_batches", "status", stdout=out)
            self.assertTrue(json.loads(out.getvalue())[0]["ingested"])

    def test_poll_task_registered(self):
        from judge.tasks.standardization import poll_llm_batches

        with mock.patch(
            "judge.standardization.batches.poll_all", return_value=[]
        ) as pa:
            self.assertEqual(poll_llm_batches(), [])
            pa.assert_called_once()
