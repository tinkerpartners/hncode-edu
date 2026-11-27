"""Printable problem and editorial pages ("View as PDF")."""

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from judge.models import ProblemData, Profile, Solution
from judge.tests.strict_helpers import StrictContestMixin


@override_settings(LANGUAGE_CODE="en")
class PrintDocumentTest(StrictContestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.problem = self.outside_problem
        self.problem.description = (
            "Compute $a + b$.\n\n<details><summary>Hint</summary>Add.</details>"
        )
        self.problem.save()

    def solution(self, public=True):
        return Solution.objects.create(
            problem=self.problem,
            is_public=public,
            publish_on=timezone.now() - timezone.timedelta(days=1),
            content="Use **addition**.",
        )

    def test_problem_page_has_logo_title_and_limits(self):
        ProblemData.objects.create(
            problem=self.problem, fileio_input="SUM.INP", fileio_output="SUM.OUT"
        )
        self.login()
        response = self.client.get(reverse("problem_raw", args=[self.problem.code]))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('class="hn-print-logo"', html)
        self.assertIn("print-document.css", html)
        self.assertIn('<h1 class="hn-print-title">Outside Problem</h1>', html)
        for value in ("1.0s", "64M", "SUM.INP", "SUM.OUT"):
            self.assertIn(f"<dd>{value}</dd>", html)
        self.assertIn("Compute", html)

    def test_editorial_page_offers_a_pdf(self):
        self.solution()
        self.login()
        response = self.client.get(
            reverse("problem_editorial", args=[self.problem.code])
        )
        self.assertContains(response, 'id="pdf_button"')
        self.assertContains(
            response, reverse("problem_editorial_raw", args=[self.problem.code])
        )

    def test_printable_editorial(self):
        self.solution()
        self.login()
        response = self.client.get(
            reverse("problem_editorial_raw", args=[self.problem.code])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="hn-print-logo"')
        self.assertContains(response, "<strong>addition</strong>", html=True)

    def test_private_editorial_is_not_printable_for_others(self):
        self.solution(public=False)
        self.login()
        url = reverse("problem_editorial_raw", args=[self.problem.code])
        self.assertEqual(self.client.get(url).status_code, 404)
        admin = User.objects.create_superuser("printadmin", "pa@x.invalid", "pw")
        Profile.objects.get_or_create(user=admin, defaults={"language": self.language})
        self.client.force_login(admin)
        self.assertEqual(self.client.get(url).status_code, 200)
