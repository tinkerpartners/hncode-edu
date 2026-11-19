from unittest import mock

from django.test import TestCase, override_settings

from judge.models import (
    Judge,
    Language,
    Problem,
    ProblemGroup,
    Submission,
    SubmissionSource,
)
from judge.models.standardization import ProblemSnapshot, StandardizationProposal
from judge.review.submission_runner import JudgeTimeout
from judge.standardization import verification
from judge.models.problem_data import ProblemData
from judge.standardization.enums import FieldGroup, ProposalStatus, VerificationLevel
from judge.standardization.hashing import compute_source_hash
from judge.standardization.snapshot import capture_all
from judge.tests.test_standardization_apply import ensure_languages


class VerificationTest(TestCase):
    fixtures = ["language_small"]

    @classmethod
    def setUpTestData(cls):
        ensure_languages()
        cls.group = ProblemGroup.objects.create(name="stdv", full_name="std verify")
        cls.judge = Judge.objects.create(name="judge-x", auth_key="k")

    def setUp(self):
        self.problem = Problem.objects.create(
            code="stdv1",
            name="V",
            description="### Input\n### Output\n### Example\n",
            group=self.group,
            time_limit=1,
            memory_limit=1048576,
            points=100,
            is_public=True,
        )
        snapshot = ProblemSnapshot.objects.create(
            problem=self.problem,
            source_hash=compute_source_hash(self.problem),
            before_json=capture_all(self.problem),
        )
        self.proposal = StandardizationProposal.objects.create(
            snapshot=snapshot,
            problem=self.problem,
            field_group=FieldGroup.SOLUTION,
            step="draft_solution_v1",
            prompt_version="t",
            dedupe_key="k1",
            status=ProposalStatus.PENDING_REVIEW,
            payload_json={
                "content": "x",
                "cpp_code": "int main(){}",
                "language_key": "CPP17",
            },
        )

    def _submission(self, result, **kw):
        bot = verification.get_bot_profile()
        sub = Submission.objects.create(
            user=bot,
            problem=self.problem,
            language=Language.objects.get(key="CPP17"),
            status="D",
            result=result,
            points=kw.get("points", 100 if result == "AC" else 0),
            case_points=kw.get("case_points", 3 if result == "AC" else 0),
            case_total=3,
            time=0.01,
            memory=1000,
            judged_on=self.judge,
            error=kw.get("error"),
        )
        SubmissionSource.objects.create(submission=sub, source="int main(){}")
        return sub

    def test_bot_profile_is_inactive_singleton(self):
        a = verification.get_bot_profile()
        b = verification.get_bot_profile()
        self.assertEqual(a.pk, b.pk)
        self.assertFalse(a.user.is_active)
        self.assertFalse(a.user.has_usable_password())

    def test_ac_is_current_tests_passed_and_submission_deleted_by_default(self):
        sub = self._submission("AC")
        with mock.patch.object(verification, "judge_and_wait", return_value=sub) as jw:
            row = verification.verify_solution_proposal(self.proposal)
        self.assertEqual(row.level, VerificationLevel.CURRENT_TESTS_PASSED)
        self.assertEqual(row.verdict, "AC")
        self.assertEqual(row.judge_name, "judge-x")
        self.assertEqual(row.points, 100)
        self.assertEqual(len(row.test_config_hash), 64)
        self.assertEqual(row.details_json["submission_id"], sub.id)
        self.assertFalse(Submission.objects.filter(pk=sub.pk).exists())
        self.assertIsNone(row.submission_id)
        self.proposal.refresh_from_db()
        self.assertEqual(
            self.proposal.evidence_json["verification"], "current_tests_passed"
        )
        kwargs = jw.call_args.kwargs
        self.assertEqual(kwargs["judge_id"], None)
        self.assertEqual(jw.call_args.args[1].key, "CPP17")
        self.assertEqual(jw.call_args.args[2], "int main(){}")

    @override_settings(
        STANDARDIZATION_KEEP_VERIFICATION_SUBMISSIONS=True,
        STANDARDIZATION_JUDGE_ID="judge-x",
    )
    def test_ce_is_failed_and_submission_kept_when_configured(self):
        sub = self._submission("CE", error="expected ';'")
        with mock.patch.object(verification, "judge_and_wait", return_value=sub) as jw:
            row = verification.verify_solution_proposal(self.proposal)
        self.assertEqual(row.level, VerificationLevel.FAILED)
        self.assertEqual(row.details_json["compile_error"], "expected ';'")
        self.assertEqual(row.submission_id, sub.id)
        self.assertTrue(Submission.objects.filter(pk=sub.pk).exists())
        self.assertEqual(jw.call_args.kwargs["judge_id"], "judge-x")

    def test_wa_is_failed(self):
        sub = self._submission("WA", case_points=1)
        with mock.patch.object(verification, "judge_and_wait", return_value=sub):
            row = verification.verify_solution_proposal(self.proposal)
        self.assertEqual(row.level, VerificationLevel.FAILED)
        self.assertEqual(row.details_json["case_points"], 1)

    def test_partial_ac_is_not_a_full_pass(self):
        sub = self._submission("AC", case_points=1)
        with mock.patch.object(verification, "judge_and_wait", return_value=sub):
            row = verification.verify_solution_proposal(self.proposal)
        self.assertEqual(row.level, VerificationLevel.FAILED)
        self.assertEqual(row.details_json["reason"], "partial_score")

    def test_changed_grading_configuration_during_run_is_unknown(self):
        sub = self._submission("AC")
        with mock.patch.object(verification, "judge_and_wait", return_value=sub):
            with mock.patch.object(
                verification, "compute_judge_hash", side_effect=["before", "after"]
            ):
                row = verification.verify_solution_proposal(self.proposal)
        self.assertEqual(row.level, VerificationLevel.UNKNOWN)
        self.assertEqual(row.details_json["reason"], "tests_changed")
        self.assertEqual(row.test_config_hash, "before")

    def test_timeout_and_judge_errors_are_unknown_never_pass(self):
        with mock.patch.object(
            verification, "judge_and_wait", side_effect=JudgeTimeout("slow")
        ):
            row = verification.verify_solution_proposal(self.proposal)
        self.assertEqual(row.level, VerificationLevel.UNKNOWN)
        self.assertEqual(row.details_json["reason"], "timeout")
        with mock.patch.object(
            verification, "judge_and_wait", side_effect=RuntimeError("no judge")
        ):
            row = verification.verify_solution_proposal(self.proposal)
        self.assertEqual(row.level, VerificationLevel.UNKNOWN)
        self.assertEqual(row.details_json["reason"], "judge_error")
        self.assertEqual(
            verification.latest_level(self.proposal), VerificationLevel.UNKNOWN
        )

    def test_no_code_is_unknown_without_judging(self):
        self.proposal.payload_json = {"content": "x"}
        self.proposal.save()
        with mock.patch.object(verification, "judge_and_wait") as jw:
            row = verification.verify_solution_proposal(self.proposal)
        self.assertEqual(row.level, VerificationLevel.UNKNOWN)
        jw.assert_not_called()

    def test_database_file_protocol_is_checked_before_paid_repair_or_judging(self):
        ProblemData.objects.create(
            problem=self.problem, fileio_input="PSNUM.INP", fileio_output="PSNUM.OUT"
        )
        with mock.patch.object(verification, "judge_and_wait") as judge:
            result = verification.verify_solution_proposal(self.proposal)
        judge.assert_not_called()
        self.assertEqual(result.details_json["reason"], "io_mismatch")
        self.assertEqual(
            result.details_json["missing_files"], ["PSNUM.INP", "PSNUM.OUT"]
        )

    def test_timeout_keeps_job_identity_and_retry_reuses_completed_submission(self):
        sub = self._submission("AC")
        with mock.patch.object(
            verification, "judge_and_wait", side_effect=JudgeTimeout("waiting", sub.pk)
        ):
            pending = verification.verify_solution_proposal(self.proposal)
        self.assertEqual(pending.details_json["submission_id"], sub.pk)
        self.assertTrue(Submission.objects.filter(pk=sub.pk).exists())
        with mock.patch.object(verification, "judge_and_wait") as judge:
            done = verification.verify_solution_proposal(self.proposal)
        judge.assert_not_called()
        self.assertEqual(done.level, VerificationLevel.CURRENT_TESTS_PASSED)
        self.assertFalse(Submission.objects.filter(pk=sub.pk).exists())

    def test_author_macro_reference_is_checked_by_real_judge_on_verify(self):
        ProblemData.objects.create(
            problem=self.problem, fileio_input="x.inp", fileio_output="x.out"
        )
        source = '#define TASK "x"\nint main(){freopen(TASK ".inp","r",stdin);freopen(TASK ".out","w",stdout);}'
        self.proposal.payload_json["cpp_code"] = source
        self.proposal.evidence_json = {
            "guided": True,
            "reference": {"kind": "solution_code", "id": 1},
        }
        self.proposal.save()
        sub = self._submission("AC")
        with mock.patch.object(
            verification, "judge_and_wait", return_value=sub
        ) as judge:
            result = verification.verify_solution_proposal(self.proposal)
        judge.assert_called_once()
        self.assertEqual(result.level, VerificationLevel.CURRENT_TESTS_PASSED)

    def test_io_preflight_failure_is_bound_to_exact_program_and_judge(self):
        from hashlib import sha256

        ProblemData.objects.create(
            problem=self.problem, fileio_input="x.inp", fileio_output="x.out"
        )
        result = verification.verify_solution_proposal(self.proposal)
        self.assertEqual(result.details_json["reason"], "io_mismatch")
        self.assertEqual(
            result.test_config_hash,
            verification.compute_judge_hash(
                self.problem, Language.objects.get(key="CPP17")
            ),
        )
        self.assertEqual(
            result.details_json["source_hash"],
            sha256(self.proposal.payload_json["cpp_code"].encode()).hexdigest(),
        )

    def test_only_solution_group(self):
        self.proposal.field_group = FieldGroup.CONTENT
        self.proposal.save()
        with self.assertRaises(verification.VerificationError):
            verification.verify_solution_proposal(self.proposal)
