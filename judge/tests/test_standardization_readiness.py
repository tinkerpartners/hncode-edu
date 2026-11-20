import itertools
from hashlib import sha256
from unittest import mock
from zipfile import ZipFile

from django.test import TestCase, SimpleTestCase

from judge.models.standardization import (
    StandardizationDisposition as Route,
    StandardizationBatch,
    StandardizationVerification,
)
from judge.standardization import format_v1, guided, policy, readiness, verification
from judge.standardization.enums import (
    AuditVerdict,
    FieldGroup,
    ProposalStatus,
    VerificationLevel,
)
from judge.standardization.hashing import compute_judge_hash
from judge.standardization.integrity import source_examples
from judge.tests import test_standardization_policy as policy_tests
from judge.tests.standardization_fixtures import install_test_data


class ReadinessTest(TestCase):
    fixtures = ["language_small"]
    _seq = itertools.count()
    make_problem = policy_tests.PolicyTest.make_problem
    proposal = policy_tests.PolicyTest.proposal
    tags_proposal = policy_tests.PolicyTest.tags_proposal
    make_policy = policy_tests.PolicyTest.policy

    @classmethod
    def setUpTestData(cls):
        policy_tests.PolicyTest.setUpTestData.__func__(cls)

    def setUp(self):
        self.problem = self.make_problem("readiness")
        self.folder = install_test_data(self, self.problem)

    def test_valid_manifest_without_samples_or_ac_reference_is_ready(self):
        self.assertTrue(readiness.source_readiness(self.problem).ready)

    def test_missing_manifest_and_missing_case_are_parked(self):
        (self.folder / "init.yml").unlink()
        self.assertEqual(
            readiness.source_readiness(self.problem).reason, "test_manifest_missing"
        )
        install_test_data(self, self.problem)
        (self.folder / "1.out").unlink()
        self.assertEqual(
            readiness.source_readiness(self.problem).reason, "test_files_missing"
        )

    def test_pretests_alone_do_not_count_as_full_tests(self):
        (self.folder / "init.yml").write_text(
            "pretest_test_cases: [{in: 1.in, out: 1.out}]\n"
        )
        self.assertEqual(
            readiness.source_readiness(self.problem).reason, "full_tests_missing"
        )

    def test_archive_members_and_custom_checker_are_checked(self):
        (self.folder / "init.yml").write_text(
            "archive: tests.zip\nchecker: {name: testlib, args: {files: checker.cpp}}\ntest_cases: [{in: a.in, out: a.out}]\n"
        )
        with ZipFile(self.folder / "tests.zip", "w") as archive:
            archive.writestr("a.in", "1 2")
            archive.writestr("a.out", "3")
        self.assertEqual(
            readiness.source_readiness(self.problem).reason,
            "checker_missing_or_invalid",
        )
        (self.folder / "checker.cpp").write_text("int main(){}")
        self.assertTrue(readiness.source_readiness(self.problem).ready)
        with ZipFile(self.folder / "tests.zip", "w") as archive:
            archive.writestr("a.in", "1 2")
        self.assertEqual(
            readiness.source_readiness(self.problem).reason, "test_files_missing"
        )

    def test_unsafe_manifest_and_zero_score_are_refused(self):
        for text, expected in [
            (
                "archive: ../secret.zip\ntest_cases: [{in: a, out: b}]",
                "unsafe_test_path",
            ),
            (
                "test_cases: [{in: 1.in, out: 1.out, points: 0}]",
                "full_test_score_missing",
            ),
            ("test_cases: [{batched: []}]", "invalid_test_cases"),
        ]:
            with self.subTest(text=text):
                (self.folder / "init.yml").write_text(text)
                self.assertEqual(
                    readiness.source_readiness(self.problem).reason, expected
                )

    def test_semantic_parking_survives_metadata_edits_but_not_source_change(self):
        report = readiness.source_readiness(self.problem)
        report.issues = ["source_clarification"]
        readiness.save_readiness(self.problem, report)
        self.problem.points = 1800
        self.problem.save(update_fields=["points"])
        self.problem.types.add(self.tag_dp)
        self.assertTrue(readiness.source_readiness(self.problem).reused_parking)
        self.problem.description += " Hai số có trị tuyệt đối không quá 1000."
        self.problem.save(update_fields=["description"])
        self.assertTrue(readiness.source_readiness(self.problem).ready)

    def test_missing_data_stops_before_metadata_or_ai_and_leaves_problem_untouched(
        self,
    ):
        original_points = self.problem.points
        (self.folder / "init.yml").unlink()
        batch, _ = guided.queue_run(
            self.problem, self.reviewer, include_config=True, include_content=True
        )
        with mock.patch.object(guided, "get_provider") as provider, mock.patch.object(
            guided, "prepare_metadata"
        ) as metadata:
            guided.run_queued(batch.pk)
        provider.assert_not_called()
        metadata.assert_not_called()
        self.assertFalse(batch.proposals.exists())
        self.assertEqual(
            Route.objects.get(problem=self.problem, field_group="").state,
            Route.State.DEFERRED_SOURCE,
        )
        self.problem.refresh_from_db()
        self.assertEqual(self.problem.points, original_points)

    def test_every_auto_group_is_blocked_by_missing_tests(self):
        proposal = self.tags_proposal(self.problem)
        pol = self.make_policy([FieldGroup.TAGS])
        (self.folder / "1.out").unlink()
        ev, applied = policy.auto_apply(proposal, pol)
        self.assertIsNone(applied)
        self.assertIn("source", {k for k, (ok, _) in ev.conditions.items() if not ok})

    def test_newer_held_draft_blocks_old_pending_across_prompt_and_batch_scopes(self):
        old = self.tags_proposal(self.problem)
        pol = self.make_policy(
            [FieldGroup.TAGS], prompt_versions_json=[old.prompt_version]
        )
        new = self.tags_proposal(self.problem)
        new.status, new.prompt_version = ProposalStatus.NEEDS_CLARIFICATION, "different"
        new.save(update_fields=["status", "prompt_version"])
        self.assertEqual(policy.candidates(pol), [])
        self.assertFalse(policy.evaluate(old, pol).passed)
        self.assertIsNone(policy.auto_apply(old, pol)[1])

    def test_exhausted_ai_budget_does_not_block_apply_and_tags_are_additive(self):
        extra = policy_tests.ProblemType.objects.create(
            name="teacher", full_name="Teacher"
        )
        self.problem.types.add(extra)
        proposal = self.tags_proposal(self.problem)
        proposal.evidence_json["guided_workflow"] = True
        proposal.batch = StandardizationBatch.objects.create(
            name="no more AI",
            kind="ai_pipeline",
            status="finished",
            budget_usd_cap=0,
            id_through=self.problem.pk,
        )
        proposal.save(update_fields=["batch", "evidence_json"])
        pol = self.make_policy(
            [FieldGroup.TAGS], prompt_versions_json=[proposal.prompt_version]
        )
        with self.settings(STANDARDIZATION_GUIDED_AUTO_APPLY=True):
            ev, applied = policy.auto_apply(proposal, pol)
        self.assertTrue(ev.passed, ev.reasons)
        self.assertIsNotNone(applied)
        self.assertEqual(
            set(self.problem.types.values_list("pk", flat=True)),
            {extra.pk, self.tag_dp.pk},
        )
        self.assertEqual(
            Route.objects.get(problem=self.problem, field_group="").state,
            Route.State.AUTO_PARTIAL,
        )

    def test_critical_audit_stops_next_direct_apply_without_minimum_sample(self):
        prop = self.tags_proposal(self.problem)
        pol = self.make_policy([FieldGroup.TAGS], audit_min_samples=100, sample_rate=1)
        _, applied = policy.auto_apply(prop, pol)
        self.assertIsNotNone(applied)
        _, stopped = policy.record_audit(
            applied.audit_sample, AuditVerdict.WRONG, self.reviewer, critical=True
        )
        self.assertTrue(stopped)
        next_prop = self.tags_proposal(self.make_problem("next_critical"))
        self.assertIsNone(policy.auto_apply(next_prop, pol)[1])

    def test_audit_backlog_blocks_without_hiding_or_deleting_proposals(self):
        prop = self.tags_proposal(self.problem)
        pol = self.make_policy(
            [FieldGroup.TAGS], thresholds_json={"audit_max_pending": 1}, sample_rate=1
        )
        self.assertIsNotNone(policy.auto_apply(prop, pol)[1])
        next_prop = self.tags_proposal(self.make_problem("backlog"))
        self.assertIsNone(policy.auto_apply(next_prop, pol)[1])
        next_prop.refresh_from_db()
        self.assertEqual(next_prop.status, ProposalStatus.PENDING_REVIEW)

    def test_information_check_does_not_hide_ac_but_new_hard_failure_does(self):
        proposal = self.proposal(
            self.problem,
            FieldGroup.SOLUTION,
            {"cpp_code": "int main(){}", "language_key": "CPP17"},
        )
        identity = {"source_hash": sha256(b"int main(){}").hexdigest()}
        version = compute_judge_hash(
            self.problem, policy_tests.Language.objects.get(key="CPP17")
        )
        StandardizationVerification.objects.create(
            proposal=proposal,
            level=VerificationLevel.CURRENT_TESTS_PASSED,
            test_config_hash=version,
            details_json={**identity, "reason": "full_score", "kind": "full_judge"},
        )
        StandardizationVerification.objects.create(
            proposal=proposal,
            level=VerificationLevel.UNKNOWN,
            test_config_hash=version,
            details_json={
                **identity,
                "reason": "not_applicable",
                "kind": "editorial_consistency",
            },
        )
        self.assertTrue(verification.current_full_verification(proposal))
        StandardizationVerification.objects.create(
            proposal=proposal,
            level=VerificationLevel.FAILED,
            test_config_hash=version,
            details_json={
                **identity,
                "reason": "counterexample",
                "kind": "independent_oracle",
            },
        )
        self.assertFalse(verification.current_full_verification(proposal))

    def test_editorial_certificate_is_bound_to_exact_text_code_and_current_tests(self):
        from django.contrib.auth import get_user_model
        from judge.models import Profile

        actor = Profile.objects.create(
            user=get_user_model().objects.create_superuser(
                "certifier", "c@example.org", "pw"
            ),
            language=self.lang,
        )
        proposal = self.proposal(
            self.problem,
            FieldGroup.SOLUTION,
            {
                "content": "Cộng hai số.",
                "cpp_code": "int main(){}",
                "language_key": "CPP17",
            },
        )
        version = compute_judge_hash(
            self.problem, policy_tests.Language.objects.get(key="CPP17")
        )
        StandardizationVerification.objects.create(
            proposal=proposal,
            level=VerificationLevel.CURRENT_TESTS_PASSED,
            test_config_hash=version,
            details_json={
                "reason": "full_score",
                "source_hash": sha256(b"int main(){}").hexdigest(),
            },
        )
        self.assertFalse(verification.editorial_review_passes(proposal))
        verification.record_editorial_review(proposal, actor)
        self.assertTrue(verification.editorial_review_passes(proposal))
        self.assertTrue(verification.current_full_verification(proposal))
        proposal.payload_json["content"] = "Một lời giải khác."
        proposal.save(update_fields=["payload_json"])
        self.assertFalse(verification.editorial_review_passes(proposal))

    def test_high_confidence_ai_tag_stays_deferred_without_calibration(self):
        prop = self.tags_proposal(self.problem)
        prop.evidence_json["guided"] = True
        prop.save(update_fields=["evidence_json"])
        pol = self.make_policy([FieldGroup.TAGS])
        ev = policy.evaluate(prop, pol)
        self.assertFalse(ev.conditions["calibrated"][0])

    def test_repeated_reviews_of_one_proposal_are_not_multiple_calibration_samples(
        self,
    ):
        from judge.models.standardization import StandardizationDecision
        from judge.standardization.enums import DecisionAction

        prop = self.tags_proposal(self.problem)
        for _ in range(3):
            StandardizationDecision.objects.create(
                proposal=prop, action=DecisionAction.APPROVE, actor=self.reviewer
            )
        self.assertEqual(
            policy.calibration(FieldGroup.TAGS, prop.prompt_version), (1, 1.0)
        )
        StandardizationDecision.objects.create(
            proposal=prop, action=DecisionAction.REJECT, actor=self.reviewer
        )
        self.assertEqual(
            policy.calibration(FieldGroup.TAGS, prop.prompt_version), (1, 0.0)
        )
        next_version = self.tags_proposal(self.problem)
        StandardizationDecision.objects.create(
            proposal=next_version, action=DecisionAction.APPROVE, actor=self.reviewer
        )
        self.assertEqual(
            policy.calibration(FieldGroup.TAGS, prop.prompt_version), (1, 1.0)
        )

    def test_source_readiness_does_not_erase_partial_completion(self):
        report = readiness.source_readiness(self.problem)
        row = readiness.save_readiness(self.problem, report)
        row.state = Route.State.AUTO_PARTIAL
        row.save(update_fields=["state"])
        self.assertEqual(
            readiness.save_readiness(self.problem, report).state,
            Route.State.AUTO_PARTIAL,
        )

    def test_tampered_rule_output_cannot_bypass_preservation(self):
        prop = self.proposal(
            self.problem,
            FieldGroup.CONTENT,
            {"description": format_v1.render(policy_tests.CONTENT_PAYLOAD)},
        )
        prop.provider = "rules"
        prop.evidence_json = {"preservation": {"body": "copied"}}
        prop.save(update_fields=["provider", "evidence_json"])
        ev = policy.evaluate(prop, self.make_policy([FieldGroup.CONTENT]))
        self.assertFalse(ev.conditions["validators"][0])

    def test_heading_only_apply_is_partial_not_complete_content(self):
        self.problem.description += (
            "\n## Input\nHai số.\n## Output\nTổng.\n## Example\nĐề gốc chưa có ví dụ."
        )
        self.problem.save(update_fields=["description"])
        fixed, proof = format_v1.preserve_format(self.problem.description)
        prop = guided.rule_proposal(
            self.problem,
            None,
            readiness.classify_problem(self.problem),
            FieldGroup.CONTENT,
            guided.VERSION,
            {"description": fixed},
            {},
            {"preservation": proof},
        )
        pol = self.make_policy(
            [FieldGroup.CONTENT], prompt_versions_json=[guided.VERSION]
        )
        with self.settings(STANDARDIZATION_GUIDED_AUTO_APPLY=True):
            ev, applied = policy.auto_apply(prop, pol)
        self.assertTrue(applied, ev.reasons)
        self.assertEqual(
            Route.objects.get(problem=self.problem, field_group="content").state,
            Route.State.AUTO_PARTIAL,
        )

    def test_legacy_ai_steps_cannot_bypass_calibration_or_delete_teacher_tags(self):
        from judge.standardization.apply import apply_proposal, review_proposal
        from judge.standardization.enums import DecisionAction

        extra = policy_tests.ProblemType.objects.create(
            name="teacher_old", full_name="Teacher old"
        )
        self.problem.types.add(extra)
        prop = self.tags_proposal(self.problem)
        prop.provider = "openrouter"  # legacy step, no guided flags
        prop.save(update_fields=["provider"])
        pol = self.make_policy(
            [FieldGroup.TAGS], prompt_versions_json=[prop.prompt_version]
        )
        self.assertFalse(policy.evaluate(prop, pol).conditions["calibrated"][0])
        review_proposal(prop, self.reviewer, DecisionAction.APPROVE)
        apply_proposal(prop, self.reviewer)
        self.assertEqual(
            set(self.problem.types.values_list("pk", flat=True)),
            {extra.pk, self.tag_dp.pk},
        )


class PreservingFormatTest(SimpleTestCase):
    def test_byte_sensitive_samples_preserve_indentation_blank_lines_and_spaces(self):
        source = "Cho bài toán.\n## Dữ liệu vào\nCác ký tự.\n## Kết quả\nGiữ nguyên.\n## Ví dụ\n```sample\n  A  \n\n  B\n\n```\n```sample\n  C\n```"
        before = source_examples(source)
        self.assertEqual(before[0]["input"], "  A  \n\n  B\n")
        fixed, proof = format_v1.preserve_format(source)
        self.assertTrue(proof)
        self.assertEqual(format_v1.validate(fixed).label, "STRICT_OK")
        self.assertEqual(source_examples(fixed), before)
        self.assertTrue(format_v1.preservation_proof(source, fixed))
        self.assertFalse(
            format_v1.preservation_proof(source, fixed.replace("Các ký tự.", "Các số."))
        )

    def test_headings_inside_code_and_tables_images_are_not_rewritten(self):
        source = "![hình](a.png)\n| a | b |\n| - | - |\n## Input\n```cpp\n## Output\n```\n## Output\nMột số.\n## Example\nĐề chưa có ví dụ."
        fixed, proof = format_v1.preserve_format(source)
        self.assertTrue(proof)
        self.assertIn("```cpp\n## Output\n```", fixed)
        self.assertIn("| a | b |", fixed)
        self.assertIn("![hình](a.png)", fixed)

    def test_ambiguous_duplicate_sections_are_refused(self):
        source = "## Input\nA\n## Dữ liệu vào\nB\n## Output\nC\n## Example\nD"
        self.assertEqual(format_v1.preserve_format(source), (source, {}))
