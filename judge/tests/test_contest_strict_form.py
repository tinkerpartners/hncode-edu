"""ContestEditForm's handling of the strict-mode settings.

The case worth pinning: a POST that never rendered the Strict mode section must
leave it alone. Django reads a missing checkbox as False, so without that rule a
partial contest-edit POST would quietly switch proctoring off in the middle of a
live contest -- and nothing would say so.
"""

from django.test import TestCase, override_settings

from judge.forms import ContestEditForm
from judge.tests.strict_helpers import StrictContestMixin


@override_settings(LANGUAGE_CODE="en")
class ContestEditFormStrictTest(StrictContestMixin, TestCase):
    def base_post(self):
        contest = self.contest
        return {
            "key": contest.key,
            "name": contest.name,
            "authors": [str(p.id) for p in contest.authors.all()],
            "curators": [],
            "testers": [],
            "start_time": contest.start_time.isoformat(),
            "end_time": contest.end_time.isoformat(),
            "format_name": contest.format_name,
            "format_config": "{}",
            "is_visible": "on",
            "scoreboard_visibility": contest.scoreboard_visibility,
            "points_precision": str(contest.points_precision),
            "description": contest.description or "",
            "organizations": [],
            "private_contestants": [],
            "view_contest_scoreboard": [],
            "banned_users": [],
        }

    def bind(self, post):
        return ContestEditForm(post, instance=self.contest, user=self.author.user)

    def test_a_partial_post_leaves_strict_mode_alone(self):
        form = self.bind(self.base_post())

        self.assertTrue(form.is_valid(), form.errors)
        contest = form.save()

        self.assertTrue(contest.is_strict)
        self.assertTrue(contest.strict_autoban)
        self.assertEqual(contest.strict_violation_limit, 3)
        self.assertEqual(contest.strict_grace_seconds, 20)

    def test_the_strict_section_round_trips(self):
        post = dict(
            self.base_post(),
            is_strict="on",
            strict_violation_limit="7",
            strict_grace_seconds="45",
        )

        form = self.bind(post)

        self.assertTrue(form.is_valid(), form.errors)
        contest = form.save()
        self.assertTrue(contest.is_strict)
        self.assertEqual(contest.strict_violation_limit, 7)
        self.assertEqual(contest.strict_grace_seconds, 45)
        # Rendered but unchecked, so this one really is off.
        self.assertFalse(contest.strict_autoban)

    def test_turning_strict_mode_off_from_the_section_works(self):
        post = dict(
            self.base_post(),
            strict_violation_limit="3",
            strict_grace_seconds="20",
        )

        form = self.bind(post)

        self.assertTrue(form.is_valid(), form.errors)
        self.assertFalse(form.save().is_strict)

    def test_blank_numbers_keep_the_current_values(self):
        self.contest.strict_violation_limit = 9
        self.contest.strict_grace_seconds = 30
        self.contest.save()
        post = dict(
            self.base_post(),
            is_strict="on",
            strict_violation_limit="",
            strict_grace_seconds="",
        )

        form = self.bind(post)

        self.assertTrue(form.is_valid(), form.errors)
        contest = form.save()
        self.assertEqual(contest.strict_violation_limit, 9)
        self.assertEqual(contest.strict_grace_seconds, 30)

    def test_an_out_of_range_limit_is_rejected(self):
        post = dict(
            self.base_post(),
            is_strict="on",
            strict_violation_limit="0",
            strict_grace_seconds="20",
        )

        self.assertFalse(self.bind(post).is_valid())
