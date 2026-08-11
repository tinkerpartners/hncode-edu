"""Shared fixtures for the strict-contest tests.

Not named test_* on purpose, so unittest discovery does not try to run it.
"""

from django.contrib.auth.models import User
from django.utils import timezone

from judge.models import (
    Contest,
    ContestParticipation,
    ContestProblem,
    Judge,
    Language,
    Problem,
    ProblemGroup,
    Profile,
)


def make_language():
    language, _ = Language.objects.get_or_create(
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
    return language


def make_group():
    group, _ = ProblemGroup.objects.get_or_create(
        name="strictgroup", defaults={"full_name": "Strict group"}
    )
    return group


def make_online_judge(language):
    """ProblemSubmitForm only offers languages an online judge can run, so a
    submission test without a judge silently fails form validation instead of
    reaching the code under test."""
    judge, _ = Judge.objects.get_or_create(
        name="strict-test-judge",
        defaults={"auth_key": "k" * 16, "online": True},
    )
    if not judge.online:
        judge.online = True
        judge.save(update_fields=["online"])
    judge.runtimes.add(language)
    return judge


class StrictContestMixin:
    """A running strict contest with one problem and one live participant."""

    @classmethod
    def setUpTestData(cls):
        cls.language = make_language()
        cls.group = make_group()
        cls.judge = make_online_judge(cls.language)

    def setUp(self):
        now = timezone.now()
        self.contest = Contest.objects.create(
            key="strictc",
            name="Strict Contest",
            description="d",
            start_time=now - timezone.timedelta(minutes=10),
            end_time=now + timezone.timedelta(hours=2),
            is_visible=True,
            is_rated=False,
            is_strict=True,
            strict_violation_limit=3,
            strict_grace_seconds=20,
            strict_autoban=True,
        )
        self.problem = self.make_problem("strictp1", "Strict Problem")
        ContestProblem.objects.create(
            contest=self.contest, problem=self.problem, points=100, order=0
        )
        self.outside_problem = self.make_problem("strictp9", "Outside Problem")

        self.participant = self.make_profile("strictuser")
        self.author = self.make_profile("strictauthor")
        self.contest.authors.add(self.author)

    def make_profile(self, name):
        user = User.objects.create_user(name, "%s@x.invalid" % name, "pw")
        profile, _ = Profile.objects.get_or_create(
            user=user, defaults={"language": self.language}
        )
        return profile

    def make_problem(self, code, name):
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

    def join(self, profile=None, virtual=ContestParticipation.LIVE, armed=True):
        profile = profile or self.participant
        participation = ContestParticipation.objects.create(
            contest=self.contest,
            user=profile,
            virtual=virtual,
        )
        if armed:
            now = timezone.now()
            participation.strict_armed_at = now
            participation.strict_last_seen = now
            participation.save(
                update_fields=["strict_armed_at", "strict_last_seen"]
            )
        profile.current_contest = participation
        profile.save()
        return participation

    def login(self, profile=None):
        self.client.force_login((profile or self.participant).user)
