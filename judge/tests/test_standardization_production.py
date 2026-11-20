import itertools
import json
from decimal import Decimal
from hashlib import sha256
from unittest import mock

from django.test import TestCase, TransactionTestCase, override_settings

from judge.models import Language
from judge.models.problem_data import ProblemSolutionCode
from judge.models.standardization import (
    StandardizationBatch,
    StandardizationReservation,
    StandardizationVerification,
    LLMUsageLedger,
    StandardizationDisposition as Route,
)
from judge.standardization import budget, guided, policy, independent_review as review
from judge.standardization.enums import FieldGroup, ProposalStatus, VerificationLevel
from judge.standardization.hashing import compute_judge_hash, compute_statement_hash
from judge.standardization.llm import call_json
from judge.standardization.production import finalize_run
from judge.standardization.routing import refresh_routes
from judge.tests import test_standardization_policy as helpers
from llm_service.providers import LLMRequest, LLMResponse, LLMUsage, LLMProviderError


class ProductionTest(TestCase):
    fixtures = ["language_small"]
    _seq = itertools.count()
    make_problem = helpers.PolicyTest.make_problem
    proposal = helpers.PolicyTest.proposal
    tags_proposal = helpers.PolicyTest.tags_proposal
    make_policy = helpers.PolicyTest.policy

    @classmethod
    def setUpTestData(cls):
        helpers.PolicyTest.setUpTestData.__func__(cls)

    def setUp(self):
        self.problem = self.make_problem("prod")
        self.batch = StandardizationBatch.objects.create(
            name="production",
            kind="ai_pipeline",
            config_version=guided.VERSION,
            id_from=self.problem.pk,
            id_through=self.problem.pk,
            status="finished",
            budget_usd_cap=1,
            counters_json={"options": {}},
        )
        self.code = "int main(){return 0;}"
        self.language = Language.objects.get(key="CPP17")
        self.author = ProblemSolutionCode.objects.create(
            problem=self.problem,
            language=self.language,
            expected_result="AC",
            source_code=self.code,
        )
        self.solution = self.proposal(
            self.problem,
            FieldGroup.SOLUTION,
            {
                "content": "Cộng hai số và in tổng.\n```cpp\n" + self.code + "\n```",
                "cpp_code": self.code,
                "language_key": "CPP17",
                "structured": {
                    "summary": "Cộng hai số.",
                    "algorithm": "Cộng và in tổng.",
                    "correctness": "Phép cộng cho tổng.",
                    "complexity_time": "O(1)",
                    "complexity_memory": "O(1)",
                },
            },
            {
                "guided": True,
                "guided_workflow": True,
                "statement_hash": compute_statement_hash(self.problem),
                "reference": {
                    "kind": "solution_code",
                    "id": self.author.pk,
                    "language": "CPP17",
                    "source_hash": sha256(self.code.encode()).hexdigest(),
                },
            },
        )
        self.solution.model, self.solution.provider, self.solution.batch = (
            "writer",
            "openrouter",
            self.batch,
        )
        self.solution.save(update_fields=["model", "provider", "batch"])
        self.tags = self.tags_proposal(self.problem)
        self.tags.evidence_json.update(
            guided=True, guided_workflow=True, solution_proposal_id=self.solution.pk
        )
        self.tags.batch = self.batch
        self.tags.save(update_fields=["evidence_json", "batch"])
        self.points = self.proposal(
            self.problem,
            FieldGroup.POINTS,
            {"points": 800, "range": [800, 900], "confidence": "high"},
            {
                "guided": True,
                "guided_workflow": True,
                "solution_proposal_id": self.solution.pk,
                "confidence": "high",
                "reasoning": "Một phép cộng.",
                "in_range": True,
            },
        )
        self.points.batch = self.batch
        self.tags.snapshot = self.solution.snapshot
        self.tags.save(update_fields=["snapshot"])
        self.points.snapshot = self.solution.snapshot
        self.points.save(update_fields=["batch", "snapshot"])
        self.full = StandardizationVerification.objects.create(
            proposal=self.solution,
            level=VerificationLevel.CURRENT_TESTS_PASSED,
            test_config_hash=compute_judge_hash(self.problem, self.language),
            details_json={
                "kind": "full_judge",
                "reason": "full_score",
                "source_hash": sha256(self.code.encode()).hexdigest(),
            },
        )
        self.pol = self.make_policy(
            [FieldGroup.SOLUTION, FieldGroup.TAGS, FieldGroup.POINTS],
            prompt_versions_json=[self.solution.prompt_version],
            sample_rate=1,
            thresholds_json={
                "reference_review": {
                    "enabled": True,
                    "reviewer_models": ["critic"],
                    "max_rating": 1400,
                }
            },
        )
        self.reply = {
            "checks": {k: True for k in review.CHECKS},
            "editorial_reason": "Hướng dẫn mô tả đúng code và yêu cầu đã xác định.",
            "approved_tags": ["DP"],
            "tags_reason": "Đối chiếu kỹ thuật chính trong mã tham khảo.",
            "rating_low": 800,
            "rating_high": 900,
            "rating_reason": "Thao tác trực tiếp theo dữ liệu vào.",
            "issues": [],
        }
        self.provider = mock.Mock(name="reviewer")
        self.provider.name = "openrouter"
        self.provider.complete.side_effect = lambda request: LLMResponse(
            text=json.dumps(self.reply),
            provider="openrouter",
            model=request.model,
            usage=LLMUsage(100, 100),
            cost_usd=Decimal("0.001"),
        )
        self.enterContext(
            override_settings(
                STANDARDIZATION_INDEPENDENT_REVIEW=True,
                STANDARDIZATION_REVIEW_MODEL="critic",
                STANDARDIZATION_RESERVATIONS_ENABLED=True,
                STANDARDIZATION_REQUEST_USD_CEILINGS={"openrouter/critic": "0.01"},
                STANDARDIZATION_GUIDED_AUTO_APPLY=True,
            )
        )

    def test_reference_full_judge_and_critic_allow_each_group_without_fixed_review_count(
        self,
    ):
        outcome = review.run_review(self.solution, provider=self.provider)
        self.assertIn("verification", outcome)
        for prop in (self.solution, self.tags, self.points):
            ev = policy.evaluate(prop, self.pol)
            self.assertTrue(ev.passed, ev.reasons)
        for prop in (self.solution, self.tags, self.points):
            ev, applied = policy.auto_apply(prop, self.pol)
            self.assertIsNotNone(applied, ev.reasons)
        self.problem.refresh_from_db()
        self.assertEqual(self.problem.points, 800)
        self.assertFalse(self.problem.solution.is_public)
        self.assertEqual(
            review.run_review(self.solution, provider=self.provider)["deduplicated"],
            True,
        )
        self.provider.complete.assert_called_once()

    def test_critic_approval_alone_or_changed_author_reference_never_suffices(self):
        review.run_review(self.solution, provider=self.provider)
        self.full.delete()
        self.assertFalse(
            review.review_allows(self.solution, FieldGroup.SOLUTION, self.pol)
        )
        self.assertFalse(policy.evaluate(self.solution, self.pol).passed)

    def test_critic_cannot_certify_itself_or_use_unapproved_model(self):
        with self.settings(STANDARDIZATION_REVIEW_MODEL="writer"):
            self.assertEqual(
                review.run_review(self.solution, provider=self.provider)["skipped"],
                "independent_model_required",
            )
        self.provider.complete.assert_not_called()
        review.run_review(self.solution, provider=self.provider)
        self.pol.thresholds_json["reference_review"]["reviewer_models"] = ["another"]
        self.assertFalse(
            review.review_allows(self.solution, FieldGroup.SOLUTION, self.pol)
        )

    def test_difficult_or_wide_rating_remains_manual(self):
        self.reply["rating_low"], self.reply["rating_high"] = 1400, 1800
        review.run_review(self.solution, provider=self.provider)
        self.assertFalse(
            review.review_allows(self.solution, FieldGroup.SOLUTION, self.pol)
        )
        self.assertFalse(review.review_allows(self.points, FieldGroup.POINTS, self.pol))

    def test_writer_marks_hard_even_if_critic_calls_it_easy_stays_outside_profile(self):
        self.points.payload_json.update(points=2400, range=[2300, 2500])
        self.points.save(update_fields=["payload_json"])
        review.run_review(self.solution, provider=self.provider)
        self.assertFalse(
            review.review_allows(self.solution, FieldGroup.SOLUTION, self.pol)
        )

    def test_rating_disagreement_does_not_block_verified_editorial(self):
        self.reply["rating_low"], self.reply["rating_high"] = 1000, 1100
        review.run_review(self.solution, provider=self.provider)
        self.assertTrue(
            review.review_allows(self.solution, FieldGroup.SOLUTION, self.pol)
        )
        self.assertFalse(review.review_allows(self.points, FieldGroup.POINTS, self.pol))

    def test_tag_issue_is_field_specific(self):
        self.reply["issues"] = [
            {"group": "tags", "reason": "Đây là phép cộng, không dùng quy hoạch động."}
        ]
        review.run_review(self.solution, provider=self.provider)
        self.assertFalse(review.review_allows(self.tags, FieldGroup.TAGS, self.pol))
        self.assertTrue(
            review.review_allows(self.solution, FieldGroup.SOLUTION, self.pol)
        )

    def test_changed_payload_code_tests_and_newer_metadata_invalidate_review(self):
        review.run_review(self.solution, provider=self.provider)
        self.points.payload_json["range"] = [800, 1200]
        self.points.save(update_fields=["payload_json"])
        self.assertFalse(
            review.review_allows(self.solution, FieldGroup.SOLUTION, self.pol)
        )
        self.assertFalse(review.review_allows(self.points, FieldGroup.POINTS, self.pol))

    def test_source_issue_blocks_whole_problem_and_has_separate_route(self):
        self.reply["issues"] = [
            {"group": "source", "reason": "Không rõ miền giá trị được phép."}
        ]
        review.run_review(self.solution, provider=self.provider)
        ev = policy.evaluate(self.tags, self.pol)
        self.assertFalse(ev.conditions["source"][0])
        row = refresh_routes(self.problem, self.batch)
        self.assertEqual(row.state, Route.State.DEFERRED_SOURCE)
        self.assertEqual(row.details_json["category"], "source")

    def test_review_response_while_source_is_edited_is_not_certified(self):
        original = self.provider.complete.side_effect

        def changed(request):
            self.problem.description += " Thay đổi miền dữ liệu."
            self.problem.save(update_fields=["description"])
            return original(request)

        self.provider.complete.side_effect = changed
        self.assertEqual(
            review.run_review(self.solution, provider=self.provider)["skipped"],
            "evidence_changed",
        )
        self.assertFalse(
            review.review_allows(self.solution, FieldGroup.SOLUTION, self.pol)
        )

    def test_failed_paid_review_is_not_repeated_on_every_sweep(self):
        self.provider.complete.side_effect = LLMProviderError("timeout")
        self.assertEqual(
            review.run_review(self.solution, provider=self.provider)["error"],
            "review:timeout",
        )
        self.assertTrue(
            review.run_review(self.solution, provider=self.provider)["deduplicated"]
        )
        self.provider.complete.assert_called_once()
        self.assertEqual(StandardizationReservation.objects.get().state, "uncertain")

    def test_finalize_applies_automatically_then_materializes_problem_lists(self):
        with mock.patch.object(review, "get_provider", return_value=self.provider):
            result = finalize_run(self.batch)
        self.assertEqual(sum(bool(r.get("applied")) for r in result["results"]), 3)
        self.assertEqual(
            Route.objects.get(problem=self.problem, field_group="").state,
            Route.State.AUTO_PARTIAL,
        )
        self.assertFalse(finalize_run(self.batch)["results"])
        self.assertEqual(self.provider.complete.call_count, 1)

    def test_nonpending_fields_are_also_listed_by_problem_type(self):
        self.tags.status = ProposalStatus.NEEDS_CLARIFICATION
        self.tags.save(update_fields=["status"])
        refresh_routes(self.problem, self.batch)
        row = Route.objects.get(problem=self.problem, field_group="tags")
        self.assertEqual(row.details_json["category"], "tags")

    def test_judge_timeout_stays_in_infrastructure_list_not_manual_content_review(self):
        self.batch.counters_json = {"phase": "needs_review", "reason": "timeout"}
        self.batch.save(update_fields=["counters_json"])
        refresh_routes(self.problem, self.batch)
        for group in ("solution", "tags", "points"):
            row = Route.objects.get(problem=self.problem, field_group=group)
            self.assertEqual(row.state, Route.State.RETRY_INFRA)
            self.assertEqual(row.details_json["category"], "infrastructure")

    def test_finalizer_replaces_unprovable_ai_rewrite_with_preserving_rules_without_ai(
        self,
    ):
        self.problem.description += (
            "\n## Input\nHai số.\n## Output\nTổng.\n## Example\nĐề gốc chưa có ví dụ."
        )
        self.problem.save(update_fields=["description"])
        old = self.proposal(
            self.problem,
            FieldGroup.CONTENT,
            {"description": "Bản AI chưa đủ chứng cứ."},
            status=ProposalStatus.NEEDS_CLARIFICATION,
        )
        old.batch, old.provider = self.batch, "openrouter"
        old.save(update_fields=["batch", "provider"])
        self.pol.groups_json = ["content"]
        self.pol.prompt_versions_json = [old.prompt_version, guided.VERSION]
        self.pol.save(update_fields=["groups_json", "prompt_versions_json"])
        result = finalize_run(self.batch)
        self.assertEqual(sum(bool(r.get("applied")) for r in result["results"]), 1)
        self.provider.complete.assert_not_called()
        self.problem.refresh_from_db()
        self.assertIn("### Input", self.problem.description)

    def test_finalizer_preserves_human_hold_instead_of_replacing_it(self):
        from django.utils import timezone

        self.problem.description += (
            "\n## Input\nHai số.\n## Output\nTổng.\n## Example\nĐề gốc chưa có ví dụ."
        )
        self.problem.save(update_fields=["description"])
        old = self.proposal(
            self.problem,
            FieldGroup.CONTENT,
            {"description": "Chưa rõ."},
            status=ProposalStatus.NEEDS_CLARIFICATION,
        )
        old.batch, old.reviewed_at = self.batch, timezone.now()
        old.save(update_fields=["batch", "reviewed_at"])
        self.pol.groups_json = ["content"]
        self.pol.prompt_versions_json = [old.prompt_version, guided.VERSION]
        self.pol.save(update_fields=["groups_json", "prompt_versions_json"])
        before = self.problem.description
        self.assertFalse(finalize_run(self.batch)["results"])
        self.problem.refresh_from_db()
        self.assertEqual(self.problem.description, before)

    def test_finalizer_does_not_touch_results_while_another_run_is_active(self):
        StandardizationBatch.objects.create(
            name="new run",
            kind="ai_pipeline",
            config_version=guided.VERSION,
            id_from=self.problem.pk,
            id_through=self.problem.pk,
            status="queued",
        )
        self.assertEqual(finalize_run(self.batch)["skipped"], "newer_run_active")
        self.provider.complete.assert_not_called()

    def test_reference_profile_requires_sampling_and_stops_on_first_audit_error(self):
        from judge.standardization.enums import AuditVerdict

        review.run_review(self.solution, provider=self.provider)
        self.pol.sample_rate = 0
        self.assertFalse(
            review.review_allows(self.solution, FieldGroup.SOLUTION, self.pol)
        )
        self.pol.sample_rate = 1
        ev, applied = policy.auto_apply(self.solution, self.pol)
        self.assertIsNotNone(applied, ev.reasons)
        _, halted = policy.record_audit(
            applied.audit_sample, AuditVerdict.WRONG, self.reviewer
        )
        self.assertTrue(halted)

    def test_new_test_data_invalidates_review_and_source_change_during_critic_is_billed_once(
        self,
    ):
        review.run_review(self.solution, provider=self.provider)
        from pathlib import Path

        (Path(self._std_test_root) / self.problem.code / "1.out").write_text(
            "another output\n"
        )
        self.assertFalse(
            review.review_allows(self.solution, FieldGroup.SOLUTION, self.pol)
        )

    def test_billing_resolution_requires_permission_and_records_evidence(self):
        from django.core.management import call_command, CommandError
        from django.contrib.auth.models import Permission

        row = budget.reserve_request(
            LLMRequest("system", "user", model="critic"),
            "openrouter",
            "critic",
            self.problem,
            self.batch,
        )
        budget.settle_request(row)
        with self.assertRaises(CommandError):
            call_command(
                "standardize_reservation",
                id=row.pk,
                actual_usd="0.001",
                actor=self.reviewer.user.username,
                note="Checked actual provider billing",
            )
        self.reviewer.user.user_permissions.add(
            Permission.objects.get(codename="apply_standardization")
        )
        call_command(
            "standardize_reservation",
            id=row.pk,
            actual_usd="0.001",
            actor=self.reviewer.user.username,
            note="Checked actual provider billing",
        )
        row.refresh_from_db()
        self.assertEqual(row.state, "settled")
        self.assertEqual(row.ledger.cost_usd, Decimal("0.001"))
        self.assertEqual(row.resolution_json["actor_id"], self.reviewer.pk)


@override_settings(
    STANDARDIZATION_RESERVATIONS_ENABLED=True,
    STANDARDIZATION_REQUEST_USD_CEILINGS={"fake/model": "0.06"},
    STANDARDIZATION_BUDGET_USD_PER_DAY=Decimal("0.10"),
    STANDARDIZATION_BUDGET_USD_PER_PROBLEM=None,
)
class ReservationTest(TestCase):
    def test_inflight_requests_share_global_admission_and_known_cost_releases_hold(
        self,
    ):
        first = budget.reserve_request(LLMRequest("A", "B"), "fake", "model")
        with self.assertRaises(budget.BudgetExceeded):
            budget.reserve_request(LLMRequest("C", "D"), "fake", "model")
        ledger = LLMUsageLedger.objects.create(
            provider="fake", model="model", step="test", cost_usd=Decimal("0.01")
        )
        budget.settle_request(first, ledger)
        self.assertIsNotNone(
            budget.reserve_request(LLMRequest("C", "D"), "fake", "model")
        )

    def test_duplicate_inflight_is_refused_even_without_spend_caps(self):
        budget.reserve_request(LLMRequest("A", "B"), "fake", "model")
        with self.settings(STANDARDIZATION_BUDGET_USD_PER_DAY=None):
            with self.assertRaises(budget.BudgetExceeded) as exc:
                budget.reserve_request(LLMRequest("A", "B"), "fake", "model")
        self.assertEqual(exc.exception.scope, "request_in_flight")

    def test_unknown_cost_is_not_freed_and_overrun_stops_future_requests(self):
        hold = budget.reserve_request(LLMRequest("A", "B"), "fake", "model")
        budget.settle_request(hold)
        hold.refresh_from_db()
        self.assertEqual(hold.state, "uncertain")
        with self.assertRaises(budget.BudgetExceeded):
            budget.reserve_request(LLMRequest("C", "D"), "fake", "model")
        known = LLMUsageLedger.objects.create(
            provider="fake", model="model", step="test", cost_usd=Decimal("0.07")
        )
        budget.settle_request(hold, known)
        with self.assertRaises(budget.BudgetExceeded) as exc:
            budget.check_budget()
        self.assertEqual(exc.exception.scope, "reservation_overrun")

    def test_unknown_price_model_is_blocked_before_provider_call(self):
        provider = mock.Mock(name="unknown")
        provider.name = "fake"
        with self.assertRaises(budget.BudgetExceeded):
            call_json(
                LLMRequest("A", "B", model="unknown"), step="test", provider=provider
            )
        provider.complete.assert_not_called()


@override_settings(
    STANDARDIZATION_RESERVATIONS_ENABLED=True,
    STANDARDIZATION_REQUEST_USD_CEILINGS={"fake/model": "0.06"},
    STANDARDIZATION_BUDGET_USD_PER_DAY=Decimal("0.10"),
)
class ReservationConcurrencyTest(TransactionTestCase):
    def test_two_database_connections_cannot_admit_more_than_remaining_budget(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from django.db import connections
        from judge.models.standardization import StandardizationBudgetLock

        StandardizationBudgetLock.objects.create(key="global")
        barrier = Barrier(2)

        def reserve(number):
            connections.close_all()
            try:
                barrier.wait(timeout=10)
                budget.reserve_request(
                    LLMRequest("same system", str(number)), "fake", "model"
                )
                return "admitted"
            except budget.BudgetExceeded:
                return "held"
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(reserve, [1, 2]))
        self.assertCountEqual(outcomes, ["admitted", "held"])
        self.assertEqual(StandardizationReservation.objects.count(), 1)
