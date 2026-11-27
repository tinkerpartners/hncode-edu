"""IDE layout controls: two or three columns, a foldable statement with its
own text size, and a way back to the rankings from the contest IDE."""

from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse

from judge.models import Contest
from judge.tests.strict_helpers import StrictContestMixin


@override_settings(LANGUAGE_CODE="en")
class IDELayoutTest(StrictContestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.contest.is_strict = False
        self.contest.save()

    def contest_ide(self):
        return self.client.get(
            reverse("contest_ide", args=(self.contest.key, self.problem.code))
        )

    def test_practice_ide_has_the_layout_controls(self):
        self.login()
        html = self.client.get(
            reverse("problem_ide", args=[self.outside_problem.code])
        ).content.decode()
        for control in (
            "ide-layout-toggle",
            "ide-left-toggle",
            "ide-statement-font-inc",
            "ide-statement-font-dec",
        ):
            self.assertIn(f'id="{control}"', html)
        # Outside a contest there are no rankings to go back to.
        self.assertNotIn('id="ide-ranking"', html)

    def test_contest_ide_links_to_the_rankings(self):
        self.join()
        self.login()
        response = self.contest_ide()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="ide-ranking"')
        self.assertContains(
            response, reverse("contest_ranking", args=(self.contest.key,))
        )

    def test_no_rankings_link_without_scoreboard_access(self):
        self.join()
        self.login()
        with patch.object(Contest, "can_see_own_scoreboard", return_value=False):
            response = self.contest_ide()
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'id="ide-ranking"')
