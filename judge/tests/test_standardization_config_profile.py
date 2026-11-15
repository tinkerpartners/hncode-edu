from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.utils import timezone

from judge.forms import ProblemEditForm
from judge.models import Judge, Language, Problem, ProblemGroup, Profile
from judge.models.problem import LanguageLimit, Solution
from judge.standardization import config_profile
from judge.standardization.config_profile import (
    REQUIRED_LANGUAGE_KEYS,
    TARGET_MEMORY_KB,
    ConfigDiff,
    ProfileResolutionError,
    apply_config_diff,
    plan_config,
    resolve_context,
    revert_config_diff,
)


def ensure_languages():
    for key in REQUIRED_LANGUAGE_KEYS:
        Language.objects.get_or_create(
            key=key,
            defaults={
                "name": key,
                "short_name": key,
                "common_name": key,
                "ace": "c_cpp",
                "pygments": "cpp",
                "template": "",
            },
        )


@override_settings(STANDARDIZATION_AUTHOR_USERNAME="MrTee")
class ResolveContextTest(TestCase):
    fixtures = ["language_small"]

    def setUp(self):
        ensure_languages()

    def make_mrtee(self, username="MrTee"):
        user = User.objects.create_user(username, f"{username}@x.com", "x")
        return Profile.objects.create(user=user, language=Language.get_python3())

    def test_missing_author_blocks(self):
        with self.assertRaises(ProfileResolutionError):
            resolve_context(require_judge=False)

    def test_missing_runtime_blocks(self):
        self.make_mrtee()
        Language.objects.filter(key="PYPY3").delete()
        with self.assertRaises(ProfileResolutionError) as ctx:
            resolve_context(require_judge=False)
        self.assertIn("PYPY3", str(ctx.exception))

    def test_requires_unblocked_judge(self):
        self.make_mrtee()
        with self.assertRaises(ProfileResolutionError):
            resolve_context(require_judge=True)
        judge = Judge.objects.create(name="std-judge", auth_key="k" * 32)
        judge.runtimes.set(Language.objects.filter(key__in=REQUIRED_LANGUAGE_KEYS))
        ctx = resolve_context(require_judge=True)
        self.assertEqual(set(ctx.language_ids), set(REQUIRED_LANGUAGE_KEYS))

    def test_resolves(self):
        mrtee = self.make_mrtee()
        ctx = resolve_context(require_judge=False)
        self.assertEqual(ctx.author_profile_id, mrtee.id)
        self.assertEqual(ctx.target_memory_kb, 1048576)


@override_settings(STANDARDIZATION_AUTHOR_USERNAME="MrTee")
class PlanApplyConfigTest(TestCase):
    fixtures = ["language_small"]

    @classmethod
    def setUpTestData(cls):
        ensure_languages()
        cls.group = ProblemGroup.objects.create(name="stdp", full_name="std plan")
        user = User.objects.create_user("MrTee", "m@x.com", "x")
        cls.mrtee = Profile.objects.create(user=user, language=Language.get_python3())
        other = User.objects.create_user("origauthor", "o@x.com", "x")
        cls.other = Profile.objects.create(user=other, language=Language.get_python3())

    def setUp(self):
        self.ctx = resolve_context(require_judge=False)
        self.problem = Problem.objects.create(
            code="stdplan1",
            name="Plan",
            description="x",
            group=self.group,
            time_limit=1,
            memory_limit=262144,
            points=100,
            partial=False,
            is_public=True,
        )
        self.pas = Language.objects.get(key="PAS")
        self.problem.allowed_languages.add(self.pas)
        self.problem.authors.add(self.other)
        self.limit = LanguageLimit.objects.create(
            problem=self.problem, language=self.pas, time_limit=3, memory_limit=65536
        )
        Solution.objects.create(
            problem=self.problem,
            content="sol",
            is_public=True,
            publish_on=timezone.now(),
        )

    def test_plan_is_pure_and_complete(self):
        before = {
            "langs": set(self.problem.allowed_languages.values_list("id", flat=True)),
            "partial": self.problem.partial,
        }
        diff = plan_config(self.problem, self.ctx)
        self.assertEqual(
            diff.rules_changed,
            ["partial", "languages", "memory", "authors", "solution_private"],
        )
        self.assertEqual(diff.memory_to, TARGET_MEMORY_KB)
        self.assertEqual(len(diff.add_language_ids), 4)
        self.assertEqual(diff.add_author_ids, [self.mrtee.id])
        self.assertEqual(diff.language_limit_updates[0]["memory_to"], TARGET_MEMORY_KB)
        self.problem.refresh_from_db()
        self.assertEqual(before["partial"], self.problem.partial)
        self.assertEqual(
            before["langs"],
            set(self.problem.allowed_languages.values_list("id", flat=True)),
        )

    def test_apply_keeps_old_data_and_sets_defaults(self):
        diff = plan_config(self.problem, self.ctx)
        apply_config_diff(self.problem, diff)
        self.problem = Problem.objects.get(pk=self.problem.pk)
        self.assertTrue(self.problem.partial)
        self.assertEqual(self.problem.memory_limit, 1048576)
        keys = set(self.problem.allowed_languages.values_list("key", flat=True))
        self.assertTrue(set(REQUIRED_LANGUAGE_KEYS) <= keys)
        self.assertIn("PAS", keys)  # old language kept
        authors = set(self.problem.authors.values_list("id", flat=True))
        self.assertEqual(authors, {self.mrtee.id, self.other.id})
        self.limit.refresh_from_db()
        self.assertEqual(self.limit.memory_limit, 1048576)
        self.assertEqual(self.limit.time_limit, 3)  # per-language TL untouched
        self.assertFalse(Solution.objects.get(problem=self.problem).is_public)
        self.assertEqual(self.problem.points, 100)  # untouched

    def test_apply_is_idempotent(self):
        apply_config_diff(self.problem, plan_config(self.problem, self.ctx))
        self.problem = Problem.objects.get(pk=self.problem.pk)
        second = plan_config(self.problem, self.ctx)
        self.assertTrue(second.is_noop(), second.rules_changed)

    def test_memory_already_larger_is_kept(self):
        self.problem.memory_limit = 1048576
        self.problem.save()
        diff = plan_config(self.problem, self.ctx)
        self.assertNotIn("memory_from_change", diff.rules_changed)
        self.assertEqual(diff.memory_to, 1048576)

    def test_private_problem_points_not_clamped_by_apply(self):
        self.problem.is_public = False
        self.problem._bypass_points_cap = True
        self.problem.points = 1500
        self.problem.save()
        apply_config_diff(self.problem, plan_config(self.problem, self.ctx))
        self.assertEqual(Problem.objects.get(pk=self.problem.pk).points, 1500)

    def test_edit_form_reopens_as_1024_mb(self):
        apply_config_diff(self.problem, plan_config(self.problem, self.ctx))
        self.problem = Problem.objects.get(pk=self.problem.pk)
        # ProblemEdit.get_form_kwargs shows the limit in MB when divisible by 1024
        self.assertEqual(self.problem.memory_limit % 1024, 0)
        self.assertEqual(self.problem.memory_limit // 1024, 1024)
        form = ProblemEditForm(
            data={"memory_limit": 1024, "memory_unit": "MB"},
            instance=self.problem,
            profile=self.mrtee,
        )
        form.is_valid()  # clean() runs regardless of other field errors
        self.assertEqual(form.cleaned_data.get("memory_limit"), 1048576)

    def test_revert_never_republishes_solution(self):
        diff = plan_config(self.problem, self.ctx)
        apply_config_diff(self.problem, diff)
        restored = ConfigDiff.from_json(diff.to_json())
        revert_config_diff(self.problem, restored)
        self.problem = Problem.objects.get(pk=self.problem.pk)
        self.assertFalse(self.problem.partial)
        self.assertEqual(self.problem.memory_limit, 262144)
        self.assertEqual(
            set(self.problem.allowed_languages.values_list("key", flat=True)), {"PAS"}
        )
        self.assertEqual(
            set(self.problem.authors.values_list("id", flat=True)), {self.other.id}
        )
        self.limit.refresh_from_db()
        self.assertEqual(self.limit.memory_limit, 65536)
        self.assertFalse(Solution.objects.get(problem=self.problem).is_public)

    def test_diff_json_roundtrip(self):
        diff = plan_config(self.problem, self.ctx)
        data = diff.to_json()
        self.assertIn("rules_changed", data)
        self.assertEqual(ConfigDiff.from_json(data), diff)

    def test_version_constant(self):
        self.assertEqual(config_profile.CONFIG_PROFILE_VERSION, "mechanical_config_v1")
