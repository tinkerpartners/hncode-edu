"""The "Contests" sidebar on a problem page.

The sidebar lists the public contests a problem appears in, collapsing
everything past the fifth behind a "Show N more..." link. That link is the only
part of the page that interpolates a count into a translated string, and it is
the only part that more than five contests reaches — so it needs a problem with
six of them to exercise at all.
"""

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from judge.models import (
    Contest,
    ContestProblem,
    Language,
    Problem,
    ProblemGroup,
    Profile,
)
from judge.utils.contest_recommendation import (
    get_contests_for_problem,
    get_public_contests,
)


class ProblemContestSidebarTest(TestCase):
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
            name="sidebargroup", defaults={"full_name": "Sidebar"}
        )

    def setUp(self):
        user = User.objects.create_user("sidebaruser", "sidebar@x.com", "pw")
        self.profile, _ = Profile.objects.get_or_create(
            user=user, defaults={"language": self.language}
        )

        self.problem = Problem.objects.create(
            code="sidebarprob",
            name="Sidebar Problem",
            description="d",
            group=self.group,
            time_limit=1.0,
            memory_limit=65536,
            points=100.0,
            is_public=True,
            date="2020-01-01T00:00:00Z",
        )
        self.problem.allowed_languages.set(Language.objects.all())

        # Both lookups are cached globally, so a previous test's answer would
        # otherwise decide how many contests this problem appears in.
        get_public_contests.dirty()
        get_contests_for_problem.dirty(self.problem.id)

    def _add_to_contests(self, count):
        now = timezone.now()
        for i in range(count):
            contest = Contest.objects.create(
                key=f"sidebarcontest{i}",
                name=f"Sidebar Contest {i}",
                start_time=now - timezone.timedelta(days=count - i),
                end_time=now - timezone.timedelta(days=count - i - 1),
                is_visible=True,
            )
            ContestProblem.objects.create(
                contest=contest, problem=self.problem, points=10, order=0
            )
        get_public_contests.dirty()
        get_contests_for_problem.dirty(self.problem.id)

    def _url(self):
        return reverse("problem_detail", args=(self.problem.code,))

    def test_five_contests_render_without_the_more_link(self):
        self._add_to_contests(5)
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn("Sidebar Contest 0", html)
        self.assertNotIn("more-contests", html)

    def test_more_than_five_contests_render_the_collapsed_remainder(self):
        """Regression: this used to 500.

        The template read `_('Show %(count)d more...') % {'count': ...}`, but
        jinja2's i18n extension interpolates inside `_()` against the keyword
        arguments passed to it — none here — so it raised KeyError('count')
        before the template's own `%` ever ran. Any problem in six or more
        public contests took the whole page down with it.
        """
        self._add_to_contests(6)
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()

        # The count landed in the string rather than blowing up.
        self.assertIn("Show 1 more...", html)
        # The sixth contest is present, inside the collapsed list.
        self.assertIn("more-contests", html)
        self.assertIn("Sidebar Contest 5", html)
