from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from judge.models import Language, Problem, ProblemGroup, ProblemType, Profile
from judge.models.problem import LanguageLimit, Solution
from judge.standardization.hashing import compute_source_hash, dedupe_key
from judge.standardization.snapshot import capture_all, capture_group
from judge.standardization.enums import FieldGroup


class SourceHashTest(TestCase):
    fixtures = ["language_small"]

    @classmethod
    def setUpTestData(cls):
        cls.group = ProblemGroup.objects.create(name="stdh", full_name="std hash")
        cls.problem = Problem.objects.create(
            code="stdhash1",
            name="Hash",
            description="Đề bài.",
            group=cls.group,
            time_limit=1,
            memory_limit=262144,
            points=100,
        )
        user = User.objects.create_user("stdauthor", "a@a.com", "x")
        cls.profile = Profile.objects.create(user=user, language=Language.get_python3())

    def setUp(self):
        self.base = compute_source_hash(self.problem)

    def assertDirty(self):
        self.assertNotEqual(self.base, compute_source_hash(self.problem))

    def test_authors_dirty(self):
        self.problem.authors.add(self.profile)
        self.assertDirty()

    def test_languages_dirty(self):
        self.problem.allowed_languages.add(Language.objects.get(key="CPP17"))
        self.assertDirty()

    def test_types_dirty(self):
        t = ProblemType.objects.create(name="dp", full_name="Dynamic programming")
        self.problem.types.add(t)
        self.assertDirty()

    def test_language_limit_dirty(self):
        LanguageLimit.objects.create(
            problem=self.problem,
            language=Language.objects.get(key="PY3"),
            time_limit=2,
            memory_limit=65536,
        )
        self.assertDirty()

    def test_solution_dirty(self):
        Solution.objects.create(
            problem=self.problem,
            content="hint",
            is_public=True,
            publish_on=timezone.now(),
        )
        self.assertDirty()
        after_create = compute_source_hash(self.problem)
        sol = Solution.objects.get(problem=self.problem)
        sol.is_public = False
        sol.save()
        self.problem = Problem.objects.get(pk=self.problem.pk)
        self.assertNotEqual(after_create, compute_source_hash(self.problem))

    def test_description_points_partial_dirty(self):
        self.problem.description = "Đề khác."
        self.problem.save()
        self.assertDirty()

    def test_stable_when_unchanged(self):
        self.assertEqual(
            self.base, compute_source_hash(Problem.objects.get(pk=self.problem.pk))
        )

    def test_dedupe_key_changes_with_inputs(self):
        a = dedupe_key(1, "h", FieldGroup.CONFIG, "config_v1", "")
        self.assertNotEqual(a, dedupe_key(1, "h", FieldGroup.CONTENT, "config_v1", ""))
        self.assertNotEqual(a, dedupe_key(1, "h2", FieldGroup.CONFIG, "config_v1", ""))
        self.assertNotEqual(a, dedupe_key(1, "h", FieldGroup.CONFIG, "config_v1", "p2"))
        self.assertEqual(len(a), 64)


class CaptureGroupTest(TestCase):
    fixtures = ["language_small"]

    def test_capture_all_has_every_group(self):
        group = ProblemGroup.objects.create(name="stdc", full_name="std capture")
        problem = Problem.objects.create(
            code="stdcap1",
            name="Cap",
            description="x",
            group=group,
            time_limit=1,
            memory_limit=262144,
            points=1,
            partial=False,
        )
        state = capture_all(problem)
        self.assertEqual(set(state), {g.value for g in FieldGroup})
        self.assertEqual(state["config"]["memory_limit"], 262144)
        self.assertFalse(state["config"]["partial"])
        self.assertEqual(state["solution"], {"exists": False})
        self.assertEqual(capture_group(problem, FieldGroup.POINTS), {"points": 1.0})
