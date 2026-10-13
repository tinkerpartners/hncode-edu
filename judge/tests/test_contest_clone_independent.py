"""A14 (#102): clone a contest without putting it in a group or a course.

Who may pick the Independent target: whoever may create a standalone contest
today, i.e. superusers and holders of judge.add_contest (the Django-admin
route is the only other way to create one).
"""

from datetime import timedelta
from unittest import mock

from django.contrib.auth.models import Permission, User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from judge.models import (
    Contest,
    ContestParticipation,
    ContestProblem,
    Course,
    CourseRole,
    Language,
    Organization,
    Problem,
    ProblemGroup,
    Profile,
)
from judge.models.course import CourseContest
from judge.models.quiz import Quiz


def make_profile(username, **kw):
    user = User.objects.create_user(username, f"{username}@x.com", "pw", **kw)
    profile, _ = Profile.objects.get_or_create(
        user=user, defaults={"language": Language.objects.first()}
    )
    return profile


class IndependentContestCloneTest(TestCase):
    fixtures = ["language_small"]

    @classmethod
    def setUpTestData(cls):
        cls.creator = make_profile(
            "a14_creator"
        )  # may create contests, no group/course
        cls.creator.user.user_permissions.add(
            Permission.objects.get(codename="add_contest")
        )
        cls.nobody = make_profile("a14_nobody")  # no rights at all
        cls.org_admin = make_profile("a14_orgadmin")  # group admin, no add_contest
        cls.teacher = make_profile("a14_teacher")
        cls.contestant = make_profile("a14_contestant")

        cls.org = Organization.objects.create(
            name="A14 Group",
            slug="a14-group",
            short_name="a14",
            about="",
            registrant=cls.org_admin,
        )
        cls.org.admins.add(cls.org_admin)
        cls.course = Course.objects.create(
            name="A14 Course",
            slug="a14-course",
            about="",
            is_public=False,
            is_open=False,
        )
        CourseRole.objects.create(course=cls.course, user=cls.teacher, role="TE")

        group, _ = ProblemGroup.objects.get_or_create(
            name="a14", defaults={"full_name": "A14"}
        )
        cls.problems = [
            Problem.objects.create(
                code=f"a14p{i}",
                name=f"P{i}",
                group=group,
                time_limit=1,
                memory_limit=65536,
                points=10,
                is_public=True,
            )
            for i in range(3)
        ]
        cls.quiz = Quiz.objects.create(code="a14quiz", title="A14 Quiz")

        now = timezone.now()
        # Public source, so anyone allowed to clone can open it. It carries a
        # participation and a course link that the copy must not inherit.
        cls.source = Contest.objects.create(
            key="a14src",
            name="A14 Source",
            is_visible=True,
            start_time=now - timedelta(days=5),
            end_time=now - timedelta(days=3),
        )
        cls.source.authors.add(cls.org_admin)
        # Problems out of creation order, a quiz row in the middle, custom points.
        for order, (problem, points) in enumerate(
            [(cls.problems[2], 30), (cls.problems[0], 10), (cls.problems[1], 20)]
        ):
            ContestProblem.objects.create(
                contest=cls.source, problem=problem, points=points, order=order * 2
            )
        ContestProblem.objects.create(
            contest=cls.source, quiz=cls.quiz, points=15, order=3
        )
        ContestParticipation.objects.create(
            contest=cls.source, user=cls.contestant, virtual=0
        )
        CourseContest.objects.create(
            course=cls.course, contest=cls.source, order=1, points=5
        )

        # A group-private source with private contestants, cloned by its admin.
        cls.private_source = Contest.objects.create(
            key="a14priv",
            name="A14 Private",
            is_visible=True,
            start_time=now - timedelta(days=5),
            end_time=now - timedelta(days=3),
            is_organization_private=True,
            is_private=True,
        )
        cls.private_source.organizations.add(cls.org)
        cls.private_source.private_contestants.add(cls.contestant)
        cls.private_source.authors.add(cls.org_admin)
        ContestProblem.objects.create(
            contest=cls.private_source, problem=cls.problems[0], points=10, order=0
        )

    def setUp(self):
        # get_admin_organization_ids is cached per profile id, and test runs
        # reuse ids; never trust a value left by another test.
        for profile in (self.creator, self.nobody, self.org_admin, self.teacher):
            Profile.get_admin_organization_ids.dirty(profile)

    def clone(self, profile, source=None, **data):
        self.client.force_login(profile.user)
        source = source or self.source
        return self.client.post(reverse("contest_clone", args=(source.key,)), data)

    def rows(self, contest):
        return list(
            contest.contest_problems.order_by("order").values_list(
                "problem_id", "quiz_id", "points", "order"
            )
        )

    # -- who may clone ----------------------------------------------------------

    def test_creator_without_group_or_course_can_clone(self):
        self.client.force_login(self.creator.user)
        r = self.client.get(reverse("contest_clone", args=(self.source.key,)))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'value="independent"')

    def test_user_without_any_target_cannot_clone(self):
        self.client.force_login(self.nobody.user)
        r = self.client.get(reverse("contest_clone", args=(self.source.key,)))
        # ContestMixin turns the Http404 into its "no such contest" page with
        # status 200 (see CLAUDE.md), so assert on the body: no clone form.
        self.assertNotContains(r, 'name="target_type"')
        self.assertNotContains(r, 'name="key"')
        r = self.clone(self.nobody, key="a14nobody", target_type="independent")
        self.assertFalse(Contest.objects.filter(key="a14nobody").exists())

    def test_independent_target_is_refused_without_the_right(self):
        self.client.force_login(self.org_admin.user)
        r = self.client.get(reverse("contest_clone", args=(self.source.key,)))
        self.assertNotContains(r, 'value="independent"')
        r = self.clone(self.org_admin, key="a14forged", target_type="independent")
        self.assertEqual(r.status_code, 200)  # form re-rendered with an error
        self.assertFalse(Contest.objects.filter(key="a14forged").exists())

    # -- what the copy contains ------------------------------------------------

    def test_independent_copy(self):
        before_rows = self.rows(self.source)
        r = self.clone(self.creator, key="a14copy", target_type="independent")
        self.assertEqual(r.status_code, 302)
        copy = Contest.objects.get(key="a14copy")
        # No container.
        self.assertEqual(copy.organizations.count(), 0)
        self.assertFalse(copy.is_organization_private)
        self.assertFalse(copy.is_in_course)
        self.assertFalse(CourseContest.objects.filter(contest=copy).exists())
        # No people or results from the source.
        self.assertEqual(copy.private_contestants.count(), 0)
        self.assertEqual(ContestParticipation.objects.filter(contest=copy).count(), 0)
        self.assertEqual(list(copy.authors.all()), [self.creator])
        # Draft state.
        self.assertFalse(copy.is_visible)
        self.assertFalse(copy.is_rated)
        # Same problems, quiz (by reference), order and points.
        self.assertEqual(self.rows(copy), before_rows)
        # Source untouched.
        self.assertEqual(self.rows(self.source), before_rows)
        self.assertEqual(
            ContestParticipation.objects.filter(contest=self.source).count(), 1
        )
        self.assertTrue(CourseContest.objects.filter(contest=self.source).exists())

    def test_failure_midway_leaves_nothing_behind(self):
        with mock.patch.object(
            ContestProblem.objects, "bulk_create", side_effect=RuntimeError("boom")
        ):
            with self.assertRaises(RuntimeError):
                self.clone(self.creator, key="a14broken", target_type="independent")
        self.assertFalse(Contest.objects.filter(key="a14broken").exists())

    # -- existing targets do not regress ----------------------------------------

    def test_group_target_still_works(self):
        r = self.clone(
            self.org_admin,
            key="a14group",
            target_type="organization",
            organization=str(self.org.id),
        )
        self.assertEqual(r.status_code, 302)
        copy = Contest.objects.get(key="a14group")
        self.assertEqual(list(copy.organizations.all()), [self.org])
        self.assertTrue(copy.is_organization_private)
        self.assertEqual(self.rows(copy), self.rows(self.source))

    def test_private_contestants_are_not_copied(self):
        r = self.clone(
            self.org_admin,
            source=self.private_source,
            key="a14privcopy",
            target_type="organization",
            organization=str(self.org.id),
        )
        self.assertEqual(r.status_code, 302)
        copy = Contest.objects.get(key="a14privcopy")
        self.assertEqual(copy.private_contestants.count(), 0)
        self.assertEqual(self.private_source.private_contestants.count(), 1)

    def test_course_target_still_works(self):
        r = self.clone(
            self.teacher,
            key="a14course",
            target_type="course",
            course=str(self.course.id),
        )
        self.assertEqual(r.status_code, 302)
        copy = Contest.objects.get(key="a14course")
        self.assertTrue(copy.is_in_course)
        self.assertTrue(
            CourseContest.objects.filter(contest=copy, course=self.course).exists()
        )
        self.assertEqual(copy.organizations.count(), 0)
