from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from judge.models import Language, Problem, ProblemGroup, Profile


class InlineSubmitFormTests(TestCase):
    """The problem detail page carries the submit form inline, so a solution can
    be submitted without navigating to /problem/<code>/submit."""

    fixtures = ["language_small"]

    @classmethod
    def setUpTestData(cls):
        cls.language = Language.objects.first()
        cls.user = User.objects.create_user("solver", "s@example.com", "password")
        cls.profile, _ = Profile.objects.get_or_create(
            user=cls.user, defaults={"language": cls.language}
        )
        cls.group, _ = ProblemGroup.objects.get_or_create(
            name="g", defaults={"full_name": "General"}
        )
        cls.problem = Problem.objects.create(
            code="inlinesubmit",
            name="Inline Submit",
            description="Add two numbers.",
            time_limit=1,
            memory_limit=65536,
            points=100,
            partial=False,
            group=cls.group,
            is_public=True,
            date="2020-01-01T00:00:00Z",
        )
        cls.problem.allowed_languages.set(Language.objects.all())

    def setUp(self):
        self.client = Client()

    def test_problem_page_renders_submit_form_for_authenticated_user(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("problem_detail", args=[self.problem.code]))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('id="submit-form"', html)
        self.assertIn('id="problem_submit"', html)

    def test_inline_form_posts_to_the_submit_view(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("problem_detail", args=[self.problem.code]))
        submit_url = reverse("problem_submit", args=[self.problem.code])
        self.assertIn('action="%s"' % submit_url, response.content.decode())

    def test_sidebar_button_jumps_to_the_inline_form(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("problem_detail", args=[self.problem.code]))
        self.assertIn('href="#submit-form"', response.content.decode())

    def test_anonymous_user_gets_no_inline_form(self):
        response = self.client.get(reverse("problem_detail", args=[self.problem.code]))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertNotIn('id="submit-form"', html)
        # ... and the sidebar still links out to the login-gated submit page.
        self.assertIn(
            'href="%s"' % reverse("problem_submit", args=[self.problem.code]), html
        )

    def test_dedicated_submit_page_still_renders(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("problem_submit", args=[self.problem.code]))
        self.assertEqual(response.status_code, 200)
        self.assertIn('id="problem_submit"', response.content.decode())
