"""A15 (#103): the first-AC mark on the contest ranking is decided server-side.

Owner decisions (2026-10-13): first submission whose current verdict is AC,
ordered by submission time then submission id; a rejudge moves the mark.
"""

from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from judge.models import (
    Contest,
    ContestParticipation,
    ContestProblem,
    ContestSubmission,
    Language,
    Problem,
    ProblemGroup,
    Profile,
    Submission,
)
from judge.utils.first_solve import get_first_solve_urls, get_first_solves


def make_profile(username, **kw):
    user = User.objects.create_user(username, f"{username}@x.com", "pw", **kw)
    profile, _ = Profile.objects.get_or_create(
        user=user, defaults={"language": Language.objects.get(key="PY3")}
    )
    return profile


class FirstSolveTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        Language.objects.get_or_create(
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
        cls.lang = Language.objects.get(key="PY3")
        group, _ = ProblemGroup.objects.get_or_create(
            name="a15", defaults={"full_name": "A15"}
        )
        cls.editor = make_profile("a15_editor", is_superuser=True, is_staff=True)
        cls.viewer = make_profile("a15_viewer")
        names = ["alice", "bob", "carol", "dave", "virt"]
        cls.p = {n: make_profile(f"a15_{n}") for n in names}

        cls.start = timezone.now() - timedelta(hours=5)
        cls.contest = Contest.objects.create(
            key="a15",
            name="A15",
            is_visible=True,
            format_name="vnoj",
            start_time=cls.start,
            end_time=cls.start + timedelta(hours=3),
        )
        cls.contest.authors.add(cls.editor)
        cls.cp = {}
        for i, code in enumerate(
            ["mixed", "partial", "virtual", "tie", "frozen", "hidden"]
        ):
            problem = Problem.objects.create(
                code=f"a15{code}",
                name=code,
                group=group,
                time_limit=1,
                memory_limit=65536,
                points=100,
                is_public=True,
                partial=True,
            )
            cls.cp[code] = ContestProblem.objects.create(
                contest=cls.contest,
                problem=problem,
                points=100,
                order=i,
                is_result_hidden=(code == "hidden"),
            )
        cls.part = {}
        for n in names:
            cls.part[n] = ContestParticipation.objects.create(
                contest=cls.contest,
                user=cls.p[n],
                real_start=cls.start,
                virtual=1 if n == "virt" else 0,
                is_disqualified=(n == "dave"),
            )

        cls.subs = {}

        def sub(who, code, minutes, result, points):
            s = Submission.objects.create(
                user=cls.p[who],
                problem=cls.cp[code].problem,
                language=cls.lang,
                contest_object=cls.contest,
                status="D",
                result=result,
                points=points,
                case_points=points,
                case_total=100,
            )
            Submission.objects.filter(id=s.id).update(
                date=cls.start + timedelta(minutes=minutes)
            )
            ContestSubmission.objects.create(
                submission=s,
                problem=cls.cp[code],
                participation=cls.part[who],
                points=points,
            )
            cls.subs[(who, code)] = s
            return s

        # mixed: alice partial first (highest partial ever), carol AC before bob.
        sub("alice", "mixed", 5, "WA", 90)
        sub("bob", "mixed", 30, "AC", 100)
        sub("carol", "mixed", 20, "AC", 100)
        # partial: nobody AC, only partial scores.
        sub("alice", "partial", 10, "WA", 80)
        sub("bob", "partial", 15, "WA", 50)
        # virtual: the virtual and the disqualified participant AC first.
        sub("virt", "virtual", 1, "AC", 100)
        sub("dave", "virtual", 2, "AC", 100)
        sub("bob", "virtual", 40, "AC", 100)
        # tie: same second; the lower submission id wins.
        sub("alice", "tie", 50, "AC", 100)
        sub("bob", "tie", 50, "AC", 100)
        # frozen: the only AC (bob, 70 min) falls after a 1h freeze.
        sub("alice", "frozen", 10, "WA", 10)
        sub("bob", "frozen", 70, "AC", 100)
        # hidden: result hidden from non-editors.
        sub("carol", "hidden", 25, "AC", 100)

        # Build the ranking cells the way the judge does after grading.
        for participation in cls.part.values():
            cls.contest.format.update_participation(participation)

    def first(self, user=None, show_final=False):
        user = user or self.viewer.user
        firsts = get_first_solves(self.contest, user, show_final)
        by_code = {cp.problem.code[3:]: cp.id for cp in self.cp.values()}
        names = {p.id: n for n, p in self.part.items()}
        return {code: names.get(firsts.get(cpid)) for code, cpid in by_code.items()}

    # -- the rule ------------------------------------------------------------------

    def test_first_ac_by_time(self):
        self.assertEqual(self.first()["mixed"], "carol")

    def test_no_ac_means_no_mark_even_with_partial_scores(self):
        self.assertIsNone(self.first()["partial"])

    def test_virtual_and_disqualified_do_not_count(self):
        self.assertEqual(self.first()["virtual"], "bob")

    def test_tie_goes_to_the_lower_submission_id(self):
        self.assertEqual(self.first()["tie"], "alice")

    def test_rejudge_moves_the_mark(self):
        Submission.objects.filter(id=self.subs[("carol", "mixed")].id).update(
            result="WA"
        )
        self.assertEqual(self.first()["mixed"], "bob")

    def test_freeze_hides_post_freeze_acs_outside_the_final_ranking(self):
        Contest.objects.filter(id=self.contest.id).update(
            freeze_after=timedelta(hours=1)
        )
        self.contest.refresh_from_db()
        self.assertIsNone(self.first()["frozen"])
        self.assertEqual(self.first(show_final=True)["frozen"], "bob")

    def test_hidden_results_are_not_marked_for_non_editors(self):
        self.assertIsNone(self.first()["hidden"])
        self.assertEqual(self.first(user=self.editor.user)["hidden"], "carol")

    def test_out_of_contest_time_does_not_count(self):
        Submission.objects.filter(id=self.subs[("carol", "mixed")].id).update(
            date=self.contest.end_time + timedelta(minutes=1)
        )
        self.assertEqual(self.first()["mixed"], "bob")

    # -- the page ----------------------------------------------------------------

    def ranking(self, **params):
        self.client.force_login(self.viewer.user)
        r = self.client.get(
            reverse("contest_ranking", args=(self.contest.key,)), params
        )
        self.assertEqual(r.status_code, 200)
        import re

        m = re.search(r'data-first-solves="([^"]*)"', r.content.decode())
        self.assertIsNotNone(m, "table has no data-first-solves attribute")
        return m.group(1).split()

    def test_marks_do_not_depend_on_filter_or_page(self):
        expected = get_first_solve_urls(self.contest, self.viewer.user)
        self.assertTrue(expected)
        self.assertEqual(self.ranking(), expected)
        self.assertEqual(
            self.ranking(search="a15_alice"), expected
        )  # carol filtered out
        self.assertEqual(self.ranking(ajax="1"), expected)  # live refresh fragment
        self.assertEqual(self.ranking(ajax="1", search="a15_bob"), expected)

    def test_marked_urls_are_the_cells_rendered_by_the_format(self):
        self.client.force_login(self.viewer.user)
        html = self.client.get(
            reverse("contest_ranking", args=(self.contest.key,))
        ).content.decode()
        for url in get_first_solve_urls(self.contest, self.viewer.user):
            self.assertIn(f'data-featherlight="{url}"', html)

    def test_scores_are_unchanged(self):
        before = list(
            ContestParticipation.objects.order_by("id").values_list("score", "cumtime")
        )
        get_first_solves(self.contest, self.viewer.user)
        self.ranking()
        after = list(
            ContestParticipation.objects.order_by("id").values_list("score", "cumtime")
        )
        self.assertEqual(before, after)
