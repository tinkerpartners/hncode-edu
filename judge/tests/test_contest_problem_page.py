"""The in-contest problem page: /contest/<key>/problems/<code>.

Same page as the standalone problem view, plus a left column listing every
problem in the contest. Anonymous on a public contest -> 200; private
contest -> 403; problem outside the contest -> 404; participant -> 200 with
the whole contest listed and the problem list linking to the new route.
"""

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from judge.models import (
    Contest,
    ContestParticipation,
    ContestProblem,
    Language,
    Problem,
    ProblemGroup,
    Profile,
)


class ContestProblemPageTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.language, _ = Language.objects.get_or_create(
            key="PY3",
            defaults={
                "name": "Python 3",
                "short_name": "PY3",
                "common_name": "Python",
                "ace": "python",
                "pygments": "python3",
                "template": "",
            },
        )
        cls.group, _ = ProblemGroup.objects.get_or_create(
            name="contestpage", defaults={"full_name": "Contest page"}
        )

    def setUp(self):
        self.participant = self._profile("cpp_participant")

        now = timezone.now()
        self.contest = Contest.objects.create(
            key="cppcontest",
            name="Contest Page Contest",
            start_time=now - timezone.timedelta(minutes=10),
            end_time=now + timezone.timedelta(hours=1),
            is_visible=True,
        )
        self.first = self._make_problem("cpp1", "First Problem")
        self.second = self._make_problem("cpp2", "Second Problem")
        ContestProblem.objects.create(
            contest=self.contest, problem=self.first, points=60, order=0
        )
        ContestProblem.objects.create(
            contest=self.contest, problem=self.second, points=40, order=1
        )
        self.unrelated = self._make_problem("cpp3", "Unrelated Problem")

    def _profile(self, name):
        user = User.objects.create_user(name, f"{name}@x.com", "pw")
        profile, _ = Profile.objects.get_or_create(
            user=user, defaults={"language": self.language}
        )
        return profile

    def _make_problem(self, code, name):
        problem = Problem.objects.create(
            code=code,
            name=name,
            description="d",
            group=self.group,
            time_limit=1.0,
            memory_limit=65536,
            points=100.0,
            is_public=True,
            date="2020-01-01T00:00:00Z",
        )
        problem.allowed_languages.set(Language.objects.all())
        return problem

    def _join(self):
        participation = ContestParticipation.objects.create(
            contest=self.contest,
            user=self.participant,
            virtual=ContestParticipation.LIVE,
        )
        self.participant.current_contest = participation
        self.participant.save()
        self.client.force_login(self.participant.user)

    def _url(self, problem=None):
        return reverse(
            "contest_problem_detail",
            args=(self.contest.key, (problem or self.first).code),
        )

    def test_anonymous_sees_public_contest_problem(self):
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 200)
        self.assertIn("First Problem", response.content.decode())

    def test_private_contest_gets_403(self):
        self.contest.is_private = True
        self.contest.save()
        self.assertEqual(self.client.get(self._url()).status_code, 403)

    def test_problem_outside_the_contest_gets_404(self):
        self.assertEqual(self.client.get(self._url(self.unrelated)).status_code, 404)

    def test_nonexistent_contest_gets_404(self):
        url = reverse("contest_problem_detail", args=("nosuchcontest", "cpp1"))
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_participant_sees_the_problem_and_the_contest_sidebar(self):
        self._join()
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()

        # The problem page itself is still rendered.
        self.assertIn("First Problem", html)
        # The extra left column lists every problem in the contest...
        self.assertIn('class="left-sidebar"', html)
        self.assertIn("sidebar-problem", html)
        self.assertIn("Second Problem", html)
        self.assertIn(self._url(self.second), html)
        # ...and links back to the contest's problem list.
        self.assertIn(
            reverse("contest_problems", args=(self.contest.key,)), html
        )

    def test_title_names_the_contest_and_the_problem(self):
        self._join()
        response = self.client.get(self._url())
        self.assertEqual(
            response.context["title"], "Contest Page Contest - First Problem"
        )

    def test_current_problem_is_marked_active_in_the_sidebar(self):
        self._join()
        entries = self.client.get(self._url()).context["contest_sidebar_problems"]
        self.assertEqual(
            [(e["problem"].code, e["is_current"]) for e in entries],
            [("cpp1", True), ("cpp2", False)],
        )

    def test_participant_problem_list_links_to_the_in_contest_page(self):
        self._join()
        response = self.client.get(
            reverse("contest_problems", args=(self.contest.key,))
        )
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn(self._url(self.first), html)
        self.assertNotIn(
            'href="%s"' % reverse("problem_detail", args=[self.first.code]), html
        )
