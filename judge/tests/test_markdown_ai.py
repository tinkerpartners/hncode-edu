from unittest.mock import patch
from uuid import uuid4

import fastapi_poe
import httpx
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse

from judge.models import Problem, ProblemGroup
from judge.tasks.llm import improve_markdown_task
from judge.tests.test_grades_live_refresh import make_profile
from llm_service.config import LLMConfig


class MarkdownAITest(TestCase):
    fixtures = ["language_small"]

    @classmethod
    def setUpTestData(cls):
        cls.owner = make_profile("markdown_owner")
        cls.other = make_profile("markdown_other")
        permission = Permission.objects.get(codename="use_ai_features")
        for profile in (cls.owner, cls.other):
            profile.user.user_permissions.add(permission)
        cls.problem = Problem.objects.create(
            code="markdownqa",
            name="QA Markdown",
            description="Đề gốc",
            group=ProblemGroup.objects.create(
                name="markdownqa", full_name="QA Markdown"
            ),
            points=1,
            time_limit=1,
            memory_limit=65536,
        )
        cls.problem.authors.add(cls.owner)

    def setUp(self):
        self.client.force_login(self.owner.user)
        self.config = LLMConfig()
        self.config.api_key = "fake-test-key"
        self.edit = reverse("problem_edit", args=[self.problem.code])
        self.status = reverse("task_status_ajax")

    def test_missing_key_does_not_enqueue(self):
        self.config.api_key = None
        with patch("llm_service.config._config", self.config), patch(
            "judge.views.problem.improve_markdown_task.delay"
        ) as enqueue:
            response = self.client.post(
                self.edit,
                {
                    "improve_markdown": "1",
                    "problem_code": self.problem.code,
                },
            )
        self.assertFalse(response.json()["success"])
        self.assertIn("POE_API_KEY", response.json()["error"])
        enqueue.assert_not_called()

    def test_markdown_draft_is_only_visible_to_requester(self):
        with patch("judge.tasks.llm.get_config", return_value=self.config), patch(
            "ai_features.problem_tag_service.get_config", return_value=self.config
        ), patch(
            "fastapi_poe.get_bot_response_sync",
            return_value=[
                fastapi_poe.PartialResponse(
                    text="### Input\n- Hai số\n### Output\n- Tổng"
                )
            ],
        ):
            payload = improve_markdown_task.run(
                self.problem.pk, requested_by=self.owner.user_id
            )
        task_id = str(uuid4())
        with patch("judge.views.tasks.AsyncResult") as result:
            result.return_value.state = "SUCCESS"
            result.return_value.result = payload
            response = self.client.get(self.status, {"id": task_id})
            self.assertEqual(response.status_code, 200)
            self.assertIn("Hai số", response.json()["improved_markdown"])
            self.assertNotIn("_markdown_owner", response.json())
            for user in (self.other.user, None):
                self.client.logout()
                if user:
                    self.client.force_login(user)
                self.assertEqual(
                    self.client.get(self.status, {"id": task_id}).status_code, 404
                )
        self.problem.refresh_from_db()
        self.assertEqual(self.problem.description, "Đề gốc")

    def test_provider_errors_use_safe_codes(self):
        request = httpx.Request("POST", "https://api.poe.com/bot/example")
        for status, code in ((401, "credentials"), (429, "quota"), (500, "provider")):
            error = httpx.HTTPStatusError(
                "fake-test-key must not leak",
                request=request,
                response=httpx.Response(status, request=request),
            )
            self.check_provider_error(error, code)
        self.check_provider_error(TimeoutError("fake-test-key"), "timeout")

    def check_provider_error(self, error, code):
        with self.subTest(code=code), patch(
            "judge.tasks.llm.get_config", return_value=self.config
        ), patch(
            "ai_features.problem_tag_service.get_config", return_value=self.config
        ), patch(
            "fastapi_poe.get_bot_response_sync", side_effect=error
        ), patch(
            "ai_features.markdown_improver.time.sleep"
        ):
            result = improve_markdown_task.run(
                self.problem.pk, requested_by=self.owner.user_id
            )
        self.assertEqual(result["error_code"], code)
        self.assertNotIn("fake-test-key", repr(result))

    def test_markdown_retries_transient_timeout_then_succeeds(self):
        from ai_features.markdown_improver import MarkdownImprover

        improver = MarkdownImprover("test-key", sleep_time=0)
        with patch(
            "fastapi_poe.get_bot_response_sync",
            side_effect=[
                TimeoutError("private provider response"),
                [fastapi_poe.PartialResponse(text="### Input\n- Hai số")],
            ],
        ) as provider:
            result = improver.improve_markdown("Đề gốc", max_retries=3)
        self.assertTrue(result["success"])
        self.assertEqual(provider.call_count, 2)

    def test_markdown_does_not_retry_credentials_or_quota(self):
        from ai_features.markdown_improver import MarkdownImprover
        from llm_service.llm_api import PoeRequestError

        for code in ("credentials", "quota"):
            with self.subTest(code=code):
                improver = MarkdownImprover("test-key", sleep_time=0)
                with patch.object(
                    improver.llm_service,
                    "call_llm_with_files",
                    side_effect=PoeRequestError(code),
                ) as provider, self.assertRaises(PoeRequestError):
                    improver.improve_markdown("Đề gốc", max_retries=3)
                self.assertEqual(provider.call_count, 1)
