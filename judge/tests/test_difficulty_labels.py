from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils.translation import gettext

from judge.models import Problem, Profile
from judge.tests.strict_helpers import make_group, make_language


class DifficultyLabelTests(TestCase):
    def test_management_pages_call_problem_points_difficulty(self):
        cache.clear()
        user = User.objects.create_superuser("difficulty_admin", password="pass")
        profile, _ = Profile.objects.get_or_create(
            user=user, defaults={"language": make_language()}
        )
        problem = Problem.objects.create(
            code="difficultylabel",
            name="Label",
            group=make_group(),
            description="",
            time_limit=1,
            memory_limit=65536,
            points=1200,
            is_public=True,
        )
        problem.authors.add(profile)
        self.client.force_login(user)
        for url in (
            reverse("admin:judge_problem_changelist"),
            reverse("admin:judge_problem_change", args=[problem.pk]),
            reverse("problem_edit", args=[problem.code]),
            reverse("problem_add"),
            reverse("internal_problem_queue"),
            reverse("user_problems", args=[user.username]),
        ):
            with self.subTest(url=url):
                response = self.client.get(url, follow=True)
                self.assertContains(response, gettext("Difficulty"))
