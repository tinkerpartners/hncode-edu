from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from judge.models import Problem, ProblemType, Profile
from judge.tests.strict_helpers import make_group, make_language


class ProblemTagTypesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser("tag_admin", password="pass")
        Profile.objects.get_or_create(
            user=cls.admin, defaults={"language": make_language()}
        )
        cls.dp = ProblemType.objects.create(name="tagdp", full_name="DP")
        cls.graph = ProblemType.objects.create(name="taggraph", full_name="Graph")
        cls.problem = Problem.objects.create(
            code="tagtypes",
            name="Tag",
            group=make_group(),
            time_limit=1,
            memory_limit=65536,
            points=1,
            is_public=True,
        )

    def setUp(self):
        self.problem.types.set([self.dp])
        self.client.force_login(self.admin)

    def tag(self, **data):
        with patch("judge.views.internal.rescore_problem"):
            response = self.client.post(
                reverse("internal_problem_tag"),
                {"problem_code": self.problem.code, **data},
            )
        return response.json()["success"], set(self.problem.types.all())

    def test_empty_selection_keeps_types(self):
        self.assertEqual(self.tag(points=5), (True, {self.dp}))
        self.assertEqual(self.tag(types=""), (True, {self.dp}))
        self.problem.refresh_from_db()
        self.assertEqual(self.problem.points, 5)

    def test_selection_replaces_types(self):
        self.assertEqual(self.tag(types=[self.graph.pk]), (True, {self.graph}))

    def test_types_are_cleared_only_when_asked(self):
        self.assertEqual(self.tag(clear_types="1"), (True, set()))

    def test_unknown_types_are_rejected(self):
        self.assertEqual(self.tag(types=[99999999]), (False, {self.dp}))
