from hashlib import sha256
from unittest import mock

from django.test import TestCase, SimpleTestCase

from judge.models.standardization import StandardizationProposal
from judge.standardization import guided, verification
from judge.standardization.enums import (
    BatchStatus,
    FieldGroup,
    ProposalStatus,
    VerificationLevel,
)
from judge.standardization.hashing import compute_judge_hash
from judge.standardization.integrity import preserve_source_examples, source_examples
from judge.tests import test_standardization_guided as guided_tests


class LateVerificationTest(TestCase):
    fixtures = ["language_small"]
    response = guided_tests.GuidedStandardizationTest.response

    @classmethod
    def setUpTestData(cls):
        guided_tests.GuidedStandardizationTest.setUpTestData.__func__(cls)

    def setUp(self):
        self.enterContext(
            mock.patch(
                "judge.standardization.readiness.test_data_issues", return_value=[]
            )
        )
        provider = mock.Mock(name="provider")
        provider.name = "openrouter"
        provider.complete.side_effect = self.response
        self.batch, _ = guided.queue_run(self.problem, self.actor)
        timeout = mock.Mock(
            level=VerificationLevel.UNKNOWN, details_json={"reason": "timeout"}
        )
        with mock.patch.object(
            guided, "get_provider", return_value=provider
        ), mock.patch.object(guided, "judge_attempt", return_value=timeout):
            guided.run_queued(self.batch.pk)
        self.solution = self.batch.proposals.get(field_group=FieldGroup.SOLUTION)
        self.tags = self.batch.proposals.get(field_group=FieldGroup.TAGS)
        self.points = self.batch.proposals.get(field_group=FieldGroup.POINTS)

    def record(self, reason="full_score", **details):
        level = {
            "full_score": VerificationLevel.CURRENT_TESTS_PASSED,
            "timeout": VerificationLevel.UNKNOWN,
        }.get(reason, VerificationLevel.FAILED)
        return verification.record_candidate(
            self.solution,
            level,
            {
                "reason": reason,
                "source_hash": sha256(
                    self.solution.payload_json["cpp_code"].encode()
                ).hexdigest(),
                **details,
            },
            {"test_config_hash": compute_judge_hash(self.problem, self.language)},
        )

    def test_late_ac_releases_judge_holds_and_updates_finished_batch(self):
        self.record()
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.status, BatchStatus.FINISHED)
        self.assertEqual(self.batch.counters_json["phase"], "ready")
        self.assertEqual(self.batch.counters_json["reason"], "full_score")
        for item in (self.tags, self.points):
            item.refresh_from_db()
            self.assertEqual(item.status, ProposalStatus.PENDING_REVIEW)
        self.assertEqual(self.batch.usage.count(), 1)

    def test_content_and_intrinsic_rating_holds_survive_ac(self):
        self.points.evidence_json["insufficient_evidence"] = True
        self.points.save()
        content = StandardizationProposal.objects.create(
            problem=self.problem,
            batch=self.batch,
            snapshot=self.solution.snapshot,
            field_group=FieldGroup.CONTENT,
            step="content",
            dedupe_key="held-content",
            status=ProposalStatus.NEEDS_CLARIFICATION,
        )
        self.record()
        content.refresh_from_db()
        self.points.refresh_from_db()
        self.batch.refresh_from_db()
        self.assertEqual(content.status, ProposalStatus.NEEDS_CLARIFICATION)
        self.assertEqual(self.points.status, ProposalStatus.NEEDS_CLARIFICATION)
        self.assertEqual(self.batch.counters_json["phase"], "partial_ready")

    def test_manual_rejection_is_not_released(self):
        self.tags.status = ProposalStatus.REJECTED
        self.tags.save()
        self.record()
        self.tags.refresh_from_db()
        self.assertEqual(self.tags.status, ProposalStatus.REJECTED)

    def test_stale_in_memory_draft_does_not_overwrite_new_database_status(self):
        self.tags.status = ProposalStatus.DRAFT
        StandardizationProposal.objects.filter(pk=self.tags.pk).update(
            status=ProposalStatus.REJECTED
        )
        guided.tag_outcome([self.tags], True)
        self.assertEqual(self.tags.status, ProposalStatus.REJECTED)
        self.tags.refresh_from_db()
        self.assertEqual(self.tags.status, ProposalStatus.REJECTED)

    def test_legacy_timeout_hold_can_be_reconciled(self):
        self.tags.evidence_json.pop("judge_dependency")
        self.tags.save()
        self.record()
        self.tags.refresh_from_db()
        self.assertEqual(self.tags.status, ProposalStatus.PENDING_REVIEW)

    def test_changed_source_code_or_cancelled_batch_does_not_release(self):
        for variant in ("source", "code", "cancelled"):
            with self.subTest(variant=variant):
                if variant == "source":
                    self.solution.evidence_json["statement_hash"] = "old"
                    self.solution.save()
                elif variant == "code":
                    self.solution.evidence_json["statement_hash"] = (
                        guided.compute_statement_hash(self.problem)
                    )
                    self.solution.save()
                else:
                    self.batch.status = BatchStatus.CANCELLED
                    self.batch.save()
                self.record(**({"source_hash": "old"} if variant == "code" else {}))
                self.tags.refresh_from_db()
                self.assertEqual(self.tags.status, ProposalStatus.NEEDS_CLARIFICATION)

    def test_old_attempt_cannot_overwrite_new_attempt(self):
        StandardizationProposal.objects.create(
            problem=self.problem,
            batch=self.batch,
            snapshot=self.solution.snapshot,
            field_group=FieldGroup.SOLUTION,
            step="repair",
            dedupe_key="new-attempt",
        )
        self.record()
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.counters_json["reason"], "timeout")

    def test_late_wa_updates_reason_without_releasing_tags(self):
        self.record("WA")
        self.batch.refresh_from_db()
        self.tags.refresh_from_db()
        self.assertEqual(self.batch.counters_json["reason"], "WA")
        self.assertEqual(self.tags.status, ProposalStatus.NEEDS_CLARIFICATION)

    def test_late_failure_claims_one_repair_and_keeps_original_budget(self):
        from decimal import Decimal

        self.batch.budget_usd_cap = Decimal("0.25")
        self.batch.save(update_fields=["budget_usd_cap"])
        self.record("timeout", submission_id=123)
        row = self.record("WA", submission_id=123)
        with mock.patch("judge.tasks.standardization.run_guided.delay") as dispatch:
            with self.captureOnCommitCallbacks(execute=True):
                self.assertTrue(guided.resume_after_verification(row))
                self.assertFalse(guided.resume_after_verification(row))
        dispatch.assert_called_once_with(self.batch.pk)
        self.batch.refresh_from_db()
        self.assertEqual(
            self.batch.counters_json["resume_verification"]["step"],
            guided.GUIDED_REPAIR,
        )
        self.assertEqual(self.batch.budget_usd_cap, Decimal("0.25"))
        self.assertEqual(self.batch.spent_usd, Decimal("0"))

    def test_manual_failure_without_original_timeout_never_starts_paid_repair(self):
        row = self.record("WA")
        with mock.patch("judge.tasks.standardization.run_guided.delay") as dispatch:
            self.assertFalse(guided.resume_after_verification(row))
        dispatch.assert_not_called()

    def test_resumed_run_starts_at_repair_without_repeating_primary_call(self):
        self.record("timeout", submission_id=123)
        row = self.record("WA", submission_id=123)
        self.assertTrue(guided.resume_after_verification(row))
        provider = mock.Mock()
        provider.name = "openrouter"
        provider.complete.side_effect = self.response
        passed = mock.Mock(
            level=VerificationLevel.CURRENT_TESTS_PASSED,
            details_json={"reason": "full_score"},
        )
        with mock.patch.object(
            guided, "get_provider", return_value=provider
        ), mock.patch.object(guided, "judge_attempt", return_value=passed):
            guided.run_queued(self.batch.pk)
        provider.complete.assert_called_once()
        self.assertIn("WA", provider.complete.call_args.args[0].user)
        self.assertEqual(
            self.batch.proposals.filter(step=guided.GUIDED_REPAIR).count(), 3
        )
        self.assertEqual(
            self.batch.proposals.filter(step=guided.GUIDED_LUNA).count(), 3
        )

    def test_source_edit_after_resume_claim_stops_before_ai(self):
        self.record("timeout", submission_id=123)
        row = self.record("WA", submission_id=123)
        self.assertTrue(guided.resume_after_verification(row))
        self.problem.description += "\nChanged source"
        self.problem.save(update_fields=["description"])
        with mock.patch.object(guided, "get_provider") as provider:
            guided.run_queued(self.batch.pk)
        provider.assert_not_called()
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.counters_json["reason"], "resume_evidence_changed")

    def test_other_submission_failure_does_not_reopen_an_old_timeout(self):
        self.record("timeout", submission_id=123)
        row = self.record("WA", submission_id=124)
        self.assertFalse(guided.resume_after_verification(row))

    def test_reconcile_command_is_dry_run_then_idempotent_apply(self):
        import json
        from io import StringIO
        from django.core.management import call_command

        self.record()
        self.batch.refresh_from_db()
        self.batch.counters_json.update(phase="needs_review", reason="timeout")
        self.batch.save()
        for apply, expected in (
            (False, "timeout"),
            (True, "full_score"),
            (True, "full_score"),
        ):
            output = StringIO()
            call_command(
                "standardize_reconcile",
                batch_ids=str(self.batch.pk),
                apply=apply,
                stdout=output,
            )
            self.batch.refresh_from_db()
            self.assertEqual(self.batch.counters_json["reason"], expected)
        self.assertEqual(json.loads(output.getvalue().splitlines()[-1])["changed"], 0)

    def test_pending_poll_has_bounded_retries_and_never_submits_again(self):
        from judge.models import Submission
        from judge.tasks.standardization import poll_guided_verification

        submission = Submission.objects.create(
            user=self.actor, problem=self.problem, language=self.language, status="QU"
        )
        row = self.record(
            "timeout",
            submission_id=submission.pk,
            judge_hash=compute_judge_hash(self.problem, self.language),
        )
        with self.settings(
            STANDARDIZATION_GUIDED_POLL_MAX_ATTEMPTS=20
        ), mock.patch.object(
            poll_guided_verification, "apply_async"
        ) as dispatch, mock.patch.object(
            verification, "verify_solution_proposal"
        ) as verify:
            poll_guided_verification(row.pk, attempt=18)
            dispatch.assert_called_once_with(args=[row.pk, 19], countdown=30)
            dispatch.reset_mock()
            stopped = poll_guided_verification(row.pk, attempt=19)
            dispatch.assert_not_called()
        self.assertEqual(stopped["skipped"], "judge_poll_exhausted")
        verify.assert_not_called()
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.counters_json["reason"], "judge_poll_exhausted")
        latest = self.solution.verifications.get(pk=stopped["verification"])
        self.assertEqual(latest.details_json["submission_id"], submission.pk)
        with mock.patch.object(
            verification,
            "check_candidate",
            return_value=(VerificationLevel.UNKNOWN, {"reason": "judge_error"}, {}),
        ) as judge:
            verification.verify_solution_proposal(self.solution, reuse_only=True)
        self.assertEqual(judge.call_args.kwargs["reuse_submission_id"], submission.pk)

    def test_reuse_only_does_not_submit_after_evidence_changed(self):
        self.record("timeout", submission_id=123, judge_hash="old")
        with mock.patch.object(verification, "check_candidate") as judge:
            row = verification.verify_solution_proposal(self.solution, reuse_only=True)
        judge.assert_not_called()
        self.assertEqual(row.details_json["reason"], "pending_evidence_changed")

    def test_failure_dispatches_continuation_only_after_commit(self):
        with mock.patch(
            "judge.tasks.standardization.resume_guided_verification.delay"
        ) as dispatch:
            with self.captureOnCommitCallbacks(execute=True):
                row = self.record("WA")
                dispatch.assert_not_called()
            dispatch.assert_called_once_with(row.pk)

    def test_missing_pending_submission_is_reported_without_resubmitting(self):
        from judge.tasks.standardization import poll_guided_verification

        row = self.record("timeout", submission_id=987654321)
        with mock.patch.object(verification, "check_candidate") as judge:
            result = poll_guided_verification(row.pk)
        judge.assert_not_called()
        self.assertEqual(result["skipped"], "pending_submission_missing")
        self.batch.refresh_from_db()
        self.assertEqual(
            self.batch.counters_json["reason"], "pending_submission_missing"
        )


class SourceExamplesTest(SimpleTestCase):
    source = "### Example\n```sample\n1 2  \n3\n```\n```sample\n6 \n```\n"

    def test_whitespace_is_restored_without_relaxing_checker(self):
        payload, issues, restored = preserve_source_examples(
            self.source,
            {"examples": [{"input": "1 2\n3", "output": "6", "note": "Ghi chú"}]},
        )
        self.assertTrue(restored)
        self.assertFalse(issues)
        self.assertEqual(payload["examples"][0]["input"], "1 2  \n3")
        self.assertEqual(payload["examples"][0]["output"], "6 ")

    def test_wrong_answer_or_missing_example_is_preserved_but_held(self):
        for examples in ([], [{"input": "1 2\n3", "output": "7"}]):
            payload, issues, restored = preserve_source_examples(
                self.source, {"examples": examples}
            )
            self.assertTrue(restored)
            self.assertIn("model_changed_source_examples", issues)
            self.assertEqual(payload["examples"][0]["output"], "6 ")

    def test_unpaired_source_is_not_guessed(self):
        payload = {"examples": []}
        self.assertEqual(
            preserve_source_examples("```sample\n1\n```", payload), (payload, [], False)
        )

    def test_html_samples_are_preserved(self):
        source = "<pre>1 2  </pre><pre>3 </pre>"
        payload, issues, _ = preserve_source_examples(
            source, {"examples": [{"input": "1 2", "output": "3"}]}
        )
        self.assertFalse(issues)
        self.assertEqual(
            source_examples(source)[0]["input"], payload["examples"][0]["input"]
        )
