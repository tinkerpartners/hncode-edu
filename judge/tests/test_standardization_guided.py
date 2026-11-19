import json
from unittest import mock

from bs4 import BeautifulSoup
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from judge.models import Language, Problem, ProblemGroup, ProblemType, Profile, Solution
from judge.models.problem_data import ProblemSolutionCode
from judge.models.standardization import StandardizationProposal
from judge.standardization import guided
from judge.standardization.enums import (
    BatchStatus,
    FieldGroup,
    ProposalStatus,
    VerificationLevel,
)
from llm_service.providers.base import LLMResponse, LLMUsage


class GuidedStandardizationTest(TestCase):
    fixtures = ["language_small"]

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

    def test_existing_editorial_and_tags_skip_ai(self):
        Solution.objects.create(
            problem=self.problem,
            is_public=False,
            publish_on=timezone.now(),
            content="### Thuật toán\nCộng hai số.\n### Tính đúng\nPhép cộng chính xác.\n### Độ phức tạp\nHằng số thao tác.",
        )
        self.problem.types.add(self.tag)
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
            guided.missing_named_files(
                "Use TET.INP and TET.OUT.", 'freopen("tet.inp","r",stdin);'
            ),
            ["TET.INP", "TET.OUT"],
        )
        self.assertEqual(
            guided.missing_named_files(
                "Use TET.INP and TET.OUT.",
                'ifstream in("TET.INP"); ofstream out("TET.OUT");',
            ),
            [],
        )
