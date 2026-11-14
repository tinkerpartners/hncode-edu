import json
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib.auth.models import User
from django.core.management import call_command
from django.db import connection
from django.test import SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from judge.models import Language, Problem, ProblemGroup, ProblemTranslation, Profile
from judge.utils.problem_inventory import (
    content_inventory,
    inventory_queryset,
    inventory_rows,
)


class ContentInventoryTest(SimpleTestCase):
    def test_empty_and_placeholder(self):
        self.assertEqual(content_inventory("")["state"], "missing")
        self.assertEqual(content_inventory("TODO")["state"], "placeholder")

    def test_asset_only_statement_is_not_missing(self):
        image = content_inventory("![Đề](/media/qa.png)")
        self.assertEqual(image["state"], "asset_review")
        self.assertEqual(image["sources"], ["image"])
        pdf = content_inventory("", "qa.pdf")
        self.assertEqual(pdf["state"], "asset_review")
        self.assertEqual(pdf["pdf_kind"], "uninspected")
        self.assertEqual(
            content_inventory("", attachments=["statement.pdf"])["state"],
            "asset_review",
        )

    def test_headings_inside_samples_do_not_complete_a_statement(self):
        text = "Mô tả\n```sample\n### Input\n### Output\n### Example\n```"
        result = content_inventory(text)
        self.assertFalse(any(result["headings"].values()))
        self.assertEqual(result["state"], "format_review")

    def test_mixed_html_pdf_and_text(self):
        result = content_inventory(
            '<p>Mô tả</p><img src="/media/a.png"><a href="https://example.com/a.pdf">PDF</a>'
        )
        self.assertEqual(
            set(result["sources"]), {"html", "image", "pdf", "external_link"}
        )


class ProblemInventoryTest(TestCase):
    fixtures = ["language_small"]

    @classmethod
    def setUpTestData(cls):
        cls.group = ProblemGroup.objects.create(
            name="inventory", full_name="QA inventory"
        )
        cls.first = Problem.objects.create(
            code="inventory1",
            name="QA empty",
            description="",
            group=cls.group,
            points=1,
            time_limit=1,
            memory_limit=65536,
        )
        cls.second = Problem.objects.create(
            code="inventory2",
            name="QA translated",
            description="",
            group=cls.group,
            points=1,
            time_limit=1,
            memory_limit=65536,
        )
        ProblemTranslation.objects.create(
            problem=cls.second,
            language="en",
            name="QA translated",
            description="### Input\nHai số\n### Output\nTổng\n### Example",
        )

    def test_translation_and_query_count(self):
        with CaptureQueriesContext(connection) as queries:
            rows = list(inventory_rows(inventory_queryset()))
        self.assertEqual(
            [r["statement"]["state"] for r in rows], ["missing", "translation_review"]
        )
        self.assertLessEqual(len(queries), 12)
        self.assertNotIn("description", rows[1])
        self.assertEqual(rows[0]["submission_count"], 0)

    def test_export_resumes_without_database_writes(self):
        with TemporaryDirectory() as folder:
            log = StringIO()
            path = Path(folder) / "first.jsonl"
            call_command(
                "inventory_problems",
                output=str(path),
                through_id=self.first.pk,
                stdout=log,
            )
            first = [
                json.loads(line)
                for line in path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual([r["id"] for r in first], [self.first.pk])
            path = Path(folder) / "rest.jsonl"
            call_command(
                "inventory_problems",
                output=str(path),
                after_id=self.first.pk,
                batch_size=1,
                stdout=StringIO(),
            )
            rest = [
                json.loads(line)
                for line in path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual([r["id"] for r in rest], [self.second.pk])
            self.assertEqual(Problem.objects.count(), 2)
            self.assertEqual(Problem.objects.get(pk=self.first.pk).description, "")

    def test_only_admin_can_see_private_inventory(self):
        url = reverse("internal_problem_inventory")
        self.assertEqual(self.client.get(url).status_code, 302)
        ordinary = User.objects.create_user("inventoryuser")
        Profile.objects.create(user=ordinary, language=Language.objects.first())
        self.client.force_login(ordinary)
        self.assertEqual(self.client.get(url).status_code, 403)
        admin = User.objects.create_superuser("inventoryadmin", password="test")
        Profile.objects.create(user=admin, language=Language.objects.first())
        self.client.force_login(admin)
        response = self.client.get(url, {"q": "inventory1"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertContains(response, self.first.code)
        self.assertNotContains(response, self.second.code)
