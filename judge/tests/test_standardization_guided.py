import json
from io import StringIO
from unittest import mock

from bs4 import BeautifulSoup
from django.contrib.auth import get_user_model
from django.core.management import call_command, CommandError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from judge.models import Language, Problem, ProblemGroup, ProblemType, Profile, Solution
from judge.models.problem_data import ProblemData, ProblemSolutionCode
from judge.models.standardization import StandardizationProposal
from judge.standardization import guided, prompts
from judge.standardization.verification import missing_named_files
from judge.standardization.enums import (
    BatchStatus,
    FieldGroup,
    ProposalStatus,
    VerificationLevel,
)
from llm_service.providers.base import LLMResponse, LLMUsage


class GuidedStandardizationTest(TestCase):
    fixtures = ["language_small"]

    def setUp(self):
        # These unit tests inject a fake judge/provider. Real archive/manifest
        # checks run, without mocks, in test_standardization_readiness.py.
        self.enterContext(
            mock.patch(
                "judge.standardization.readiness.test_data_issues", return_value=[]
            )
        )

    @classmethod
    def setUpTestData(cls):
        cls.language = Language.objects.get(key="CPP17")
        cls.user = get_user_model().objects.create_superuser(
            "guided-admin", "guided@example.com", "pw"
        )
        cls.actor = Profile.objects.create(user=cls.user, language=cls.language)
        cls.problem = Problem.objects.create(
            code="guided_problem",
            name="Guided test",
            description="Tính tổng hai số nguyên.\n\n### Input\n- Hai số nguyên.\n\n### Output\n- Tổng hai số.\n\n### Example\n- Không có.",
            group=ProblemGroup.objects.create(name="guided", full_name="Guided"),
            time_limit=1,
            memory_limit=262144,
            points=1,
        )
        cls.tag = ProblemType.objects.create(name="math", full_name="Math")

    def response(self, request):
        payload = {
            "summary": "Tính tổng.",
            "key_observations": [],
            "algorithm": "Cộng hai số nguyên.",
            "correctness": "Phép cộng cho tổng.",
            "complexity_time": "O(1)",
            "complexity_memory": "O(1)",
            "edge_cases": [],
            "subtasks": [],
            "language_key": "CPP17",
            "cpp_code": "int main(){}",
            "notes_on_existing": None,
            "confidence": "high",
            "tags": ["Math"],
            "needs_clarification": False,
            "clarification_reason": "",
            "warnings": [],
            "rating": 800,
            "rating_low": 800,
            "rating_high": 900,
            "rating_confidence": "high",
            "rating_reasoning": "Một phép cộng trực tiếp, thời gian hằng số.",
            "insufficient_evidence": False,
        }
        return LLMResponse(
            text=json.dumps(payload),
            provider="openrouter",
            model=request.model,
            usage=LLMUsage(input_tokens=1000, output_tokens=100),
        )

    def test_reference_uses_verified_code_and_existing_editorial(self):
        Solution.objects.create(
            problem=self.problem,
            content="Cộng từng số trong đúng thứ tự",
            publish_on=timezone.now(),
            is_public=False,
        )
        code = ProblemSolutionCode.objects.create(
            problem=self.problem,
            language=self.language,
            expected_result="AC",
            source_code="int main(){return 0;}",
        )
        provider = mock.Mock(name="openrouter")
        provider.name = "openrouter"
        provider.complete.side_effect = self.response
        batch, _ = guided.queue_run(self.problem, self.actor)
        checked = (VerificationLevel.CURRENT_TESTS_PASSED, {"reason": "full_score"}, {})
        with mock.patch.object(guided, "get_provider", return_value=provider):
            with mock.patch.object(
                guided, "check_candidate", return_value=checked
            ) as judge:
                guided.run_queued(batch.pk)
        judge.assert_called_once()
        provider.complete.assert_called_once()
        prompt = provider.complete.call_args.args[0].user
        self.assertIn("Cộng từng số", prompt)
        self.assertIn(code.source_code, prompt)
        draft = batch.proposals.get(field_group=FieldGroup.SOLUTION)
        self.assertEqual(draft.payload_json["cpp_code"], code.source_code)
        self.assertEqual(draft.evidence_json["reference"]["id"], code.pk)
        self.assertEqual(
            batch.proposals.get(field_group=FieldGroup.TAGS).status,
            ProposalStatus.PENDING_REVIEW,
        )
        batch.refresh_from_db()
        self.assertEqual(batch.counters_json["phase"], "ready")

    def test_author_reference_with_filename_macro_is_judged_without_literal_preflight(
        self,
    ):
        ProblemData.objects.create(
            problem=self.problem, fileio_input="x.inp", fileio_output="x.out"
        )
        source = '#define TASK "x"\nint main(){freopen(TASK ".inp","r",stdin);freopen(TASK ".out","w",stdout);}'
        ProblemSolutionCode.objects.create(
            problem=self.problem,
            language=self.language,
            expected_result="AC",
            source_code=source,
        )
        provider = mock.Mock(name="openrouter")
        provider.name = "openrouter"
        provider.complete.side_effect = self.response
        batch, _ = guided.queue_run(self.problem, self.actor)
        checked = (VerificationLevel.CURRENT_TESTS_PASSED, {"reason": "full_score"}, {})
        with mock.patch.object(
            guided, "get_provider", return_value=provider
        ), mock.patch.object(guided, "check_candidate", return_value=checked) as judge:
            guided.run_queued(batch.pk)
        judge.assert_called_once_with(
            self.problem, self.language, source, preflight_file_io=False
        )
        self.assertEqual(
            batch.proposals.get(field_group="solution").payload_json["cpp_code"], source
        )

    def test_existing_editorial_and_tags_skip_ai(self):
        Solution.objects.create(
            problem=self.problem,
            is_public=False,
            publish_on=timezone.now(),
            content="### Thuật toán\nCộng hai số.\n### Tính đúng\nPhép cộng chính xác.\n### Độ phức tạp\nHằng số thao tác.",
        )
        self.problem.types.add(self.tag)
        self.problem.points = 800
        self.problem._bypass_points_cap = True
        self.problem.save(update_fields=["points"])
        from judge.standardization.hashing import (
            compute_source_hash,
            compute_statement_hash,
        )

        snapshot = guided.ensure_snapshot(
            self.problem, compute_source_hash(self.problem)
        )
        StandardizationProposal.objects.create(
            problem=self.problem,
            snapshot=snapshot,
            field_group=FieldGroup.POINTS,
            step="already_rated",
            prompt_version="old",
            dedupe_key="already_rated",
            status=ProposalStatus.APPLIED,
            payload_json={"points": 800},
            evidence_json={"statement_hash": compute_statement_hash(self.problem)},
        )
        # Headings and tags alone are not an editorial certificate: preserve an
        # applied exact text only with a current full-judge verification.
        from hashlib import sha256
        from judge.models.standardization import StandardizationVerification

        certificate = StandardizationProposal.objects.create(
            problem=self.problem,
            snapshot=snapshot,
            field_group=FieldGroup.SOLUTION,
            step="certified_editorial",
            prompt_version="old",
            dedupe_key="certified_editorial",
            status=ProposalStatus.APPLIED,
            payload_json={
                "content": self.problem.solution.content,
                "cpp_code": "int main(){}",
                "language_key": "CPP17",
            },
        )
        StandardizationVerification.objects.create(
            proposal=certificate,
            level=VerificationLevel.CURRENT_TESTS_PASSED,
            test_config_hash=guided.compute_judge_hash(self.problem, self.language),
            details_json={
                "reason": "full_score",
                "source_hash": sha256(b"int main(){}").hexdigest(),
            },
        )
        batch, _ = guided.queue_run(self.problem, self.actor)
        with mock.patch.object(guided, "get_provider") as provider:
            guided.run_queued(batch.pk)
        provider.assert_not_called()
        batch.refresh_from_db()
        self.assertEqual(batch.counters_json["phase"], "existing_solution")

    def test_official_rating_uses_confirmed_id_without_ai(self):
        batch, _ = guided.queue_run(
            self.problem,
            self.actor,
            cf_source="https://codeforces.com/contest/4/problem/D",
        )
        with mock.patch.object(
            guided.codeforces, "get_rating", return_value=1700
        ) as lookup:
            guided.prepare_metadata(
                self.problem,
                batch,
                guided.classify_problem(self.problem),
                batch.counters_json["options"],
            )
        lookup.assert_called_once_with("4/D")
        proposal = batch.proposals.get(field_group=FieldGroup.POINTS)
        self.assertEqual(proposal.payload_json["points"], 1700)
        self.assertEqual(proposal.evidence_json["source_confirmed_by"], self.actor.pk)
        with self.assertRaises(ValueError):
            guided.codeforces.parse_source(
                "https://codeforces.com.example.org/problemset/problem/4/D"
            )

    def test_only_escalates_after_luna_repair_fails(self):
        provider = mock.Mock()
        provider.name = "openrouter"
        provider.complete.side_effect = self.response
        batch, _ = guided.queue_run(self.problem, self.actor, allow_sol=True)
        failed = mock.Mock(
            level=VerificationLevel.FAILED, details_json={"reason": "partial_score"}
        )
        passed = mock.Mock(
            level=VerificationLevel.CURRENT_TESTS_PASSED,
            details_json={"reason": "full_score"},
        )
        with mock.patch.object(guided, "get_provider", return_value=provider):
            with mock.patch.object(
                guided, "judge_attempt", side_effect=[failed, failed, passed]
            ):
                guided.run_queued(batch.pk)
        requests = [call.args[0] for call in provider.complete.call_args_list]
        self.assertEqual(
            [r.model for r in requests],
            ["openai/gpt-6-luna", "openai/gpt-6-luna", "openai/gpt-6-sol"],
        )
        self.assertIn("partial_score", requests[-1].user)
        tags = StandardizationProposal.objects.filter(
            batch=batch, field_group=FieldGroup.TAGS
        )
        self.assertEqual(tags.filter(status=ProposalStatus.PENDING_REVIEW).count(), 1)
        self.assertEqual(
            tags.filter(status=ProposalStatus.NEEDS_CLARIFICATION).count(), 2
        )

    def test_queue_request_is_idempotent_and_visible(self):
        self.client.force_login(self.user)
        url = reverse("internal_standardization_run", args=[self.problem.pk])
        with mock.patch("judge.tasks.standardization.run_guided.delay") as dispatch:
            with self.captureOnCommitCallbacks(execute=True):
                self.assertEqual(self.client.post(url, {"sol": "on"}).status_code, 302)
            with self.captureOnCommitCallbacks(execute=True):
                self.assertEqual(self.client.post(url).status_code, 302)
        dispatch.assert_called_once()
        batch_id = dispatch.call_args.args[0]
        page = self.client.get(reverse("internal_standardization_queue"))
        self.assertContains(page, self.problem.code)
        self.assertContains(page, "Lượt chạy gần đây")
        html = BeautifulSoup(page.content, "html.parser")
        self.assertIsNotNone(
            html.select_one(
                'form[action$="/standardization/run/"] input[name=csrfmiddlewaretoken]'
            )
        )
        run, is_new = guided.queue_run(self.problem, self.actor)
        self.assertFalse(is_new)
        self.assertEqual(run.pk, batch_id)
        guided.cancel_run(run)
        run.refresh_from_db()
        self.assertEqual(run.status, BatchStatus.CANCELLED)

    def test_known_file_protocol_flags_wrong_names_before_judge(self):
        self.assertEqual(
            missing_named_files(
                "Use TET.INP and TET.OUT.", 'freopen("tet.inp","r",stdin);'
            ),
            ["TET.INP", "TET.OUT"],
        )
        self.assertEqual(
            missing_named_files(
                "Use TET.INP and TET.OUT.",
                'ifstream in("TET.INP"); ofstream out("TET.OUT");',
            ),
            [],
        )

    def test_points_share_one_call_and_are_held_until_solution_passes(self):
        provider = mock.Mock(name="openrouter")
        provider.name = "openrouter"
        provider.complete.side_effect = self.response
        batch, _ = guided.queue_run(self.problem, self.actor)
        checked = mock.Mock(
            level=VerificationLevel.CURRENT_TESTS_PASSED,
            details_json={"reason": "full_score"},
        )
        with mock.patch.object(
            guided, "get_provider", return_value=provider
        ), mock.patch.object(guided, "judge_attempt", return_value=checked):
            guided.run_queued(batch.pk)
        provider.complete.assert_called_once()
        points = batch.proposals.get(field_group=FieldGroup.POINTS)
        self.assertEqual(points.payload_json["points"], 800)
        self.assertEqual(points.status, ProposalStatus.PENDING_REVIEW)
        self.assertEqual(points.evidence_json["rating_source"], "ai_estimate")
        self.assertIn(
            "simplest correct solution", provider.complete.call_args.args[0].system
        )

    def test_timeout_does_not_escalate_to_another_model(self):
        provider = mock.Mock(name="openrouter")
        provider.name = "openrouter"
        provider.complete.side_effect = self.response
        batch, _ = guided.queue_run(self.problem, self.actor)
        checked = mock.Mock(
            level=VerificationLevel.UNKNOWN, details_json={"reason": "timeout"}
        )
        with mock.patch.object(
            guided, "get_provider", return_value=provider
        ), mock.patch.object(guided, "judge_attempt", return_value=checked):
            guided.run_queued(batch.pk)
        provider.complete.assert_called_once()
        self.assertEqual(
            batch.proposals.get(field_group=FieldGroup.POINTS).status,
            ProposalStatus.NEEDS_CLARIFICATION,
        )

    def test_file_contract_reaches_prompt_and_generated_cpp_adapter(self):
        ProblemData.objects.create(
            problem=self.problem, fileio_input="PSNUM.INP", fileio_output="PSNUM.OUT"
        )
        provider = mock.Mock(name="openrouter")
        provider.name = "openrouter"
        provider.complete.side_effect = self.response
        batch, _ = guided.queue_run(self.problem, self.actor)
        checked = mock.Mock(
            level=VerificationLevel.CURRENT_TESTS_PASSED,
            details_json={"reason": "full_score"},
        )
        with mock.patch.object(
            guided, "get_provider", return_value=provider
        ), mock.patch.object(guided, "judge_attempt", return_value=checked):
            guided.run_queued(batch.pk)
        request = provider.complete.call_args.args[0]
        self.assertIn("PSNUM.INP", request.user)
        solution = batch.proposals.get(field_group=FieldGroup.SOLUTION)
        self.assertIn('std::freopen("PSNUM.OUT"', solution.payload_json["cpp_code"])
        self.assertEqual(solution.evidence_json["io_adapter"], "cpp_freopen_v1")

    def test_conflicting_io_does_not_spend_ai_or_invoke_judge(self):
        self.problem.description += "\nRead wrong.inp and write wrong.out."
        self.problem.save(update_fields=["description"])
        ProblemData.objects.create(
            problem=self.problem, fileio_input="PSNUM.INP", fileio_output="PSNUM.OUT"
        )
        batch, _ = guided.queue_run(self.problem, self.actor)
        with mock.patch.object(guided, "get_provider") as provider, mock.patch.object(
            guided, "judge_attempt"
        ) as judge:
            guided.run_queued(batch.pk)
        provider.assert_not_called()
        judge.assert_not_called()
        batch.refresh_from_db()
        self.assertEqual(batch.counters_json["reason"], "grading_contract_conflict")

    def test_missing_title_only_source_never_spends_ai(self):
        for text in ["SNT đẹp", "Quay thưởng", "# Đề bài\n![ảnh](/media/de.png)"]:
            self.problem.description = text
            self.problem.save(update_fields=["description"])
            batch, _ = guided.queue_run(self.problem, self.actor)
            with mock.patch.object(guided, "get_provider") as provider:
                guided.run_queued(batch.pk)
            provider.assert_not_called()
            batch.refresh_from_db()
            self.assertEqual(batch.counters_json["phase"], "needs_source")

    def test_explicit_function_statement_with_standard_judge_stops_before_ai(self):
        self.problem.description = (
            "Bạn cần cài đặt hàm play. Không được viết hàm `main`. "
            "Không đọc bàn phím hoặc in ra màn hình.\n```cpp\nvoid play(int n);\n```"
        )
        self.problem.save(update_fields=["description"])
        ProblemData.objects.create(problem=self.problem)
        batch, _ = guided.queue_run(self.problem, self.actor)
        with mock.patch.object(guided, "get_provider") as provider, mock.patch.object(
            guided, "judge_attempt"
        ) as judge:
            guided.run_queued(batch.pk)
        provider.assert_not_called()
        judge.assert_not_called()
        batch.refresh_from_db()
        self.assertEqual(batch.counters_json["reason"], "grading_contract_conflict")
        self.assertIn(
            "statement_judge_task_kind_conflict",
            batch.counters_json["contract"]["issues"],
        )

    def test_helper_functions_or_interactive_title_do_not_change_execution_kind(self):
        from judge.standardization.contracts import explicitly_function_only

        self.assertFalse(
            explicitly_function_only(
                "Nim tương tác. Tính và in kết quả. void helper(int n);"
            )
        )
        self.assertFalse(
            explicitly_function_only(
                "Do not write main twice; read the input and print its sum."
            )
        )
        self.assertTrue(
            explicitly_function_only(
                "Do not include a main function. Implement:\nint query(int x);"
            )
        )

    def test_function_only_statement_without_metadata_has_special_contract(self):
        from judge.standardization.contracts import read_contract

        contract = read_contract(
            self.problem, "Không được viết hàm main. void solve(int n);"
        )
        self.assertEqual(contract.kind, "function")
        self.assertEqual(contract.origin, "statement")
        self.assertFalse(contract.issues)

    def test_new_contract_conflict_blocks_previously_full_score_evidence(self):
        from hashlib import sha256
        from judge.models.standardization import StandardizationVerification
        from judge.standardization.verification import current_full_verification

        provider = mock.Mock(name="openrouter")
        provider.name = "openrouter"
        provider.complete.side_effect = self.response
        # Build a proposal directly, representing evidence from an older worker.
        from judge.standardization.steps import run_step, GUIDED_LUNA

        batch, _ = guided.queue_run(self.problem, self.actor)
        result = run_step(GUIDED_LUNA, self.problem, batch=batch, provider=provider)
        solution = next(
            p for p in result.proposals if p.field_group == FieldGroup.SOLUTION
        )
        self.problem.description = "Không được viết hàm main. void play(int n);"
        self.problem.save(update_fields=["description"])
        ProblemData.objects.create(problem=self.problem)
        solution.evidence_json["statement_hash"] = guided.compute_statement_hash(
            self.problem
        )
        solution.save(update_fields=["evidence_json"])
        StandardizationVerification.objects.create(
            proposal=solution,
            level=VerificationLevel.CURRENT_TESTS_PASSED,
            test_config_hash=guided.compute_judge_hash(self.problem, self.language),
            details_json={
                "reason": "full_score",
                "source_hash": sha256(
                    solution.payload_json["cpp_code"].encode()
                ).hexdigest(),
            },
        )
        self.assertFalse(current_full_verification(solution))

    def test_bad_rating_range_is_not_ready_even_when_code_passes(self):
        def bad_response(request):
            response = self.response(request)
            payload = json.loads(response.text)
            payload["rating_low"], payload["rating_high"] = 1800, 900
            response.text = json.dumps(payload)
            return response

        provider = mock.Mock(name="openrouter")
        provider.name = "openrouter"
        provider.complete.side_effect = bad_response
        batch, _ = guided.queue_run(self.problem, self.actor)
        checked = mock.Mock(
            level=VerificationLevel.CURRENT_TESTS_PASSED,
            details_json={"reason": "full_score"},
        )
        with mock.patch.object(
            guided, "get_provider", return_value=provider
        ), mock.patch.object(guided, "judge_attempt", return_value=checked):
            guided.run_queued(batch.pk)
        self.assertEqual(
            batch.proposals.get(field_group=FieldGroup.POINTS).status,
            ProposalStatus.NEEDS_CLARIFICATION,
        )

    def test_bulk_command_is_dry_run_unless_enqueued_and_preserves_active_runs(self):
        output = StringIO()
        with mock.patch("judge.tasks.standardization.run_guided.delay") as dispatch:
            call_command(
                "standardize_guided",
                "--actor",
                self.user.username,
                "--codes",
                self.problem.code,
                stdout=output,
            )
            dispatch.assert_not_called()
            self.assertEqual(json.loads(output.getvalue())["result"], "would_queue")
            self.assertFalse(guided.StandardizationBatch.objects.exists())
            with self.captureOnCommitCallbacks(execute=True):
                call_command(
                    "standardize_guided",
                    "--actor",
                    self.user.username,
                    "--codes",
                    self.problem.code,
                    "--enqueue",
                    stdout=StringIO(),
                )
            with self.captureOnCommitCallbacks(execute=True):
                call_command(
                    "standardize_guided",
                    "--actor",
                    self.user.username,
                    "--codes",
                    self.problem.code,
                    "--enqueue",
                    stdout=StringIO(),
                )
            dispatch.assert_called_once()
        for budget in ["NaN", "-1", "0", "100"]:
            with self.assertRaises(CommandError):
                call_command(
                    "standardize_guided",
                    "--actor",
                    self.user.username,
                    "--codes",
                    self.problem.code,
                    "--budget-per-problem",
                    budget,
                    stdout=StringIO(),
                )

    def test_unproven_rewrite_defaults_to_keeping_statement(self):
        self.assertFalse(guided.settings.STANDARDIZATION_GUIDED_AI_REWRITE)
        self.check_unproven_rewrite(allow_ai=False)

    def test_unproven_rewrite_can_request_ai_draft_when_enabled(self):
        with self.settings(STANDARDIZATION_GUIDED_AI_REWRITE=True):
            self.check_unproven_rewrite(allow_ai=True)

    def check_unproven_rewrite(self, allow_ai):
        from judge.tests.test_standardization_steps import CONTENT_PAYLOAD

        self.problem.description = "Cho hai số nguyên a và b, hãy tính và in ra tổng a + b.\nInput: Hai số. Output: Tổng."
        self.problem.save(update_fields=["description"])

        def response(request):
            if "RESTRUCTURE" in request.system:
                return LLMResponse(
                    text=json.dumps(
                        dict(CONTENT_PAYLOAD, warnings=["Giới hạn chưa rõ"])
                    ),
                    provider="openrouter",
                    model=request.model,
                    usage=LLMUsage(100, 100),
                )
            return self.response(request)

        provider = mock.Mock(name="openrouter")
        provider.name = "openrouter"
        provider.complete.side_effect = response
        batch, _ = guided.queue_run(self.problem, self.actor, include_content=True)
        checked = mock.Mock(
            level=VerificationLevel.CURRENT_TESTS_PASSED,
            details_json={"reason": "full_score"},
        )
        with mock.patch.object(
            guided, "get_provider", return_value=provider
        ), mock.patch.object(guided, "judge_attempt", return_value=checked):
            guided.run_queued(batch.pk)
        batch.refresh_from_db()
        self.assertEqual(batch.counters_json["phase"], "partial_ready")
        self.assertEqual(batch.counters_json["held_groups"], ["content"])
        content = batch.proposals.get(field_group=FieldGroup.CONTENT)
        self.assertEqual(content.status, ProposalStatus.NEEDS_CLARIFICATION)
        self.assertEqual(content.provider, "openrouter" if allow_ai else "rules")
        self.assertEqual(
            sum(
                "RESTRUCTURE" in call.args[0].system
                for call in provider.complete.call_args_list
            ),
            int(allow_ai),
        )
        if not allow_ai:
            self.assertEqual(
                content.payload_json["description"], self.problem.description
            )

    def test_auto_apply_rechecks_target_config_then_applies_solution_and_tags(self):
        from judge.models import Judge, Submission, SubmissionSource
        from judge.models.standardization import StandardizationPolicy
        from judge.standardization import policy as policy_mod
        from judge.tests.test_standardization_apply import ensure_languages

        ensure_languages()
        judge = Judge.objects.create(name="automation-judge", auth_key="test-key")
        for language in Language.objects.filter(key__in=guided.LANGUAGES):
            language.judges.add(judge)
        Solution.objects.create(
            problem=self.problem,
            content="Existing editorial to improve.",
            publish_on=timezone.now(),
            is_public=True,
        )
        provider = mock.Mock(name="openrouter")
        provider.name = "openrouter"
        provider.complete.side_effect = self.response

        def grade(problem, language, source, profile, **kwargs):
            submission = Submission.objects.create(
                problem=problem,
                user=profile,
                language=language,
                status="D",
                result="AC",
                points=problem.points,
                case_points=1,
                case_total=1,
                time=0.01,
                memory=1024,
                judged_on=judge,
            )
            SubmissionSource.objects.create(submission=submission, source=source)
            return submission

        policy = StandardizationPolicy.objects.create(
            version="automation-v2-test",
            name="Automation",
            is_enabled=True,
            groups_json=["config", "solution", "tags"],
            prompt_versions_json=[prompts.PROMPT_VERSION, guided.VERSION],
            daily_cap=10,
            sample_rate=1,
        )
        batch, _ = guided.queue_run(self.problem, self.actor, include_config=True)
        with self.settings(
            STANDARDIZATION_AUTHOR_USERNAME=self.user.username,
            STANDARDIZATION_GUIDED_AUTO_APPLY=True,
        ), mock.patch.object(guided, "get_provider", return_value=provider), mock.patch(
            "judge.standardization.verification.judge_and_wait", side_effect=grade
        ) as grading, mock.patch(
            "judge.standardization.verification.editorial_review_passes",
            return_value=True,
        ), mock.patch.object(
            policy_mod, "calibration", return_value=(100, 1.0)
        ):
            guided.run_queued(batch.pk)
            tally = policy_mod.sweep(policy)
            self.assertEqual(
                len(tally["applied"]), 0, tally
            )  # finalized automatically after the run
            self.assertEqual(grading.call_count, 2)
            self.assertEqual(policy_mod.sweep(policy)["applied"], [])
        self.assertEqual(
            batch.proposals.get(field_group="solution").status, ProposalStatus.APPLIED
        )
        self.assertEqual(
            batch.proposals.get(field_group="tags").status, ProposalStatus.APPLIED
        )
        self.assertEqual(
            batch.proposals.get(field_group="points").status,
            ProposalStatus.PENDING_REVIEW,
        )
        self.problem.refresh_from_db()
        self.assertEqual(self.problem.memory_limit, 1048576)
        self.assertFalse(self.problem.solution.is_public)
        provider.complete.assert_called_once()
