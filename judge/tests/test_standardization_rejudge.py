"""Regression: scheduled sweeps must not repeatedly judge definitive failures."""

from datetime import timedelta
from hashlib import sha256
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from judge.models import Language, Problem, ProblemGroup
from judge.models.standardization import (
    ProblemSnapshot,
    StandardizationProposal,
    StandardizationVerification,
)
from judge.standardization import policy
from judge.standardization.enums import FieldGroup, ProposalStatus, VerificationLevel
from judge.standardization.hashing import compute_judge_hash, compute_source_hash
from judge.standardization.snapshot import capture_all


class ScheduledRejudgeTest(TestCase):
    fixtures = ["language_small"]

    def setUp(self):
        self.problem = Problem.objects.create(
            code="rejudge_loop",
            name="Rejudge",
            description="Tính tổng hai số.",
            group=ProblemGroup.objects.create(name="rejudge", full_name="Rejudge"),
            time_limit=1,
            memory_limit=262144,
            points=100,
        )
        snapshot = ProblemSnapshot.objects.create(
            problem=self.problem,
            source_hash=compute_source_hash(self.problem),
            before_json=capture_all(self.problem),
        )
        self.solution = StandardizationProposal.objects.create(
            problem=self.problem,
            snapshot=snapshot,
            field_group=FieldGroup.SOLUTION,
            step="guided_luna_v1",
            prompt_version="std_prompts_v3",
            dedupe_key="loop",
            status=ProposalStatus.PENDING_REVIEW,
            evidence_json={"guided": True},
            payload_json={"cpp_code": "int main(){}", "language_key": "CPP17"},
        )
        self.language = Language.objects.get(key="CPP17")
        self.version = compute_judge_hash(self.problem, self.language)
        self.source = sha256(b"int main(){}").hexdigest()
        self.record(
            VerificationLevel.CURRENT_TESTS_PASSED,
            version="previous-config",
            reason="full_score",
        )

    def record(self, level, *, version=None, source=None, reason="TLE", age=600):
        row = StandardizationVerification.objects.create(
            proposal=self.solution,
            level=level,
            test_config_hash=version or self.version,
            details_json={"source_hash": source or self.source, "reason": reason},
        )
        StandardizationVerification.objects.filter(pk=row.pk).update(
            created_at=timezone.now() - timedelta(seconds=age)
        )
        return row

    def run_refresh(self, proposal=None, side_effect=None):
        ev = policy.Evaluation("test", self.solution.pk, "solution")
        ev.fail("solution_verified", "old verification")
        with mock.patch.object(policy, "evaluate", return_value=ev), mock.patch(
            "judge.standardization.verification.verify_solution_proposal",
            side_effect=side_effect,
        ) as verify:
            policy.refresh_guided_verification(proposal or self.solution, mock.Mock())
        return verify

    def test_one_failed_rejudge_stops_later_sweeps_and_linked_tags(self):
        def failed(_):
            return self.record(VerificationLevel.FAILED)

        self.run_refresh(side_effect=failed).assert_called_once_with(self.solution)
        self.run_refresh().assert_not_called()
        self.run_refresh().assert_not_called()
        tags = StandardizationProposal.objects.create(
            problem=self.problem,
            snapshot=self.solution.snapshot,
            field_group=FieldGroup.TAGS,
            step="guided_luna_v1",
            prompt_version="std_prompts_v3",
            dedupe_key="loop-tags",
            evidence_json={"guided": True, "solution_proposal_id": self.solution.pk},
            status=ProposalStatus.PENDING_REVIEW,
        )
        self.run_refresh(tags).assert_not_called()
        self.solution.refresh_from_db()
        self.assertEqual(self.solution.status, ProposalStatus.PENDING_REVIEW)

    def test_changed_config_allows_one_new_attempt(self):
        self.record(VerificationLevel.FAILED)
        self.problem.time_limit = 2
        self.problem.save(update_fields=["time_limit"])
        self.solution = StandardizationProposal.objects.get(pk=self.solution.pk)
        self.run_refresh().assert_called_once()

    def test_changed_program_allows_new_attempt(self):
        self.record(VerificationLevel.FAILED)
        self.solution.payload_json["cpp_code"] = "int main(){return 0;}"
        self.solution.save(update_fields=["payload_json"])
        self.run_refresh().assert_called_once()

    def test_unknown_attempt_limit_is_scoped_to_current_code_and_config(self):
        for _ in range(3):
            self.record(
                VerificationLevel.UNKNOWN, version="previous-config", reason="timeout"
            )
        self.record(VerificationLevel.UNKNOWN, reason="timeout")
        self.run_refresh().assert_called_once()
        for _ in range(2):
            self.record(VerificationLevel.UNKNOWN, reason="timeout")
        self.run_refresh().assert_not_called()

    def test_unknown_respects_cooldown_and_does_not_retry_source_errors(self):
        self.record(VerificationLevel.UNKNOWN, reason="timeout", age=1)
        self.run_refresh().assert_not_called()
        self.record(VerificationLevel.UNKNOWN, reason="source_changed", age=0)
        self.run_refresh().assert_not_called()
