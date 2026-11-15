import json
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib.auth.models import User
from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from judge.models import (
    Contest,
    ContestProblem,
    Language,
    Problem,
    ProblemGroup,
    Profile,
    Submission,
)
from judge.models.problem import LanguageLimit, Solution
from judge.models.standardization import (
    StandardizationBatch,
    StandardizationProposal,
    StandardizationRescoreJob,
)
from judge.standardization.config_profile import REQUIRED_LANGUAGE_KEYS
from judge.standardization.enums import BatchStatus, ProposalStatus


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


def read_jsonl(path):
    return [
        json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines()
    ]


@override_settings(STANDARDIZATION_AUTHOR_USERNAME="MrTee")
class StandardizeConfigCommandTest(TestCase):
    fixtures = ["language_small"]

    @classmethod
    def setUpTestData(cls):
        ensure_languages()
        cls.group = ProblemGroup.objects.create(name="stdcmd", full_name="std cmd")
        cls.lang = Language.get_python3()
        cls.mrtee = Profile.objects.create(
            user=User.objects.create_user("MrTee", "m@x.com", "x"), language=cls.lang
        )
        cls.student = Profile.objects.create(
            user=User.objects.create_user("stdstudent", "s@x.com", "x"),
            language=cls.lang,
        )

    def make_problem(self, code, **kwargs):
        defaults = dict(
            name=code,
            description="x",
            group=self.group,
            time_limit=1,
            memory_limit=262144,
            points=100,
            partial=False,
            is_public=True,
        )
        defaults.update(kwargs)
        problem = Problem.objects.create(code=code, **defaults)
        problem.allowed_languages.add(Language.objects.get(key="PAS"))
        return problem

    def run_cmd(self, tmp, name, *args):
        out = StringIO()
        call_command(
            "standardize_config",
            "--report",
            str(Path(tmp) / name),
            "--no-require-judge",
            *args,
            stdout=out,
        )
        return json.loads(out.getvalue()), read_jsonl(Path(tmp) / name)

    def test_dry_run_writes_report_and_changes_nothing(self):
        p = self.make_problem("stdcmd1")
        Solution.objects.create(
            problem=p, content="s", is_public=True, publish_on=timezone.now()
        )
        with TemporaryDirectory() as tmp:
            summary, rows = self.run_cmd(tmp, "dry.jsonl")
        self.assertEqual(summary["mode"], "dry-run")
        self.assertIsNone(summary["batch_id"])
        self.assertEqual(summary["results"], {"would_apply": 1})
        self.assertEqual(
            rows[0]["rules_changed"],
            ["partial", "languages", "memory", "authors", "solution_private"],
        )
        p.refresh_from_db()
        self.assertFalse(p.partial)
        self.assertEqual(p.memory_limit, 262144)
        self.assertTrue(Solution.objects.get(problem=p).is_public)
        self.assertFalse(StandardizationBatch.objects.exists())

    def test_apply_then_rerun_is_noop(self):
        p = self.make_problem("stdcmd2")
        LanguageLimit.objects.create(
            problem=p,
            language=Language.objects.get(key="PAS"),
            time_limit=2,
            memory_limit=65536,
        )
        with TemporaryDirectory() as tmp:
            summary, rows = self.run_cmd(tmp, "apply.jsonl", "--apply", "--name", "t")
            self.assertEqual(summary["results"], {"applied": 1})
            self.assertEqual(summary["batch_status"], BatchStatus.FINISHED)
            batch = StandardizationBatch.objects.get(pk=summary["batch_id"])
            self.assertEqual(batch.checkpoint_id, p.pk)
            self.assertEqual(batch.counters_json["results"], {"applied": 1})
            self.assertEqual(batch.counters_json["rules"]["memory"], 1)
            p.refresh_from_db()
            self.assertTrue(p.partial)
            self.assertEqual(p.memory_limit, 1048576)
            self.assertEqual(LanguageLimit.objects.get(problem=p).memory_limit, 1048576)
            self.assertTrue(
                set(REQUIRED_LANGUAGE_KEYS)
                <= set(p.allowed_languages.values_list("key", flat=True))
            )
            self.assertIn("PAS", p.allowed_languages.values_list("key", flat=True))
            self.assertIn(self.mrtee.id, p.authors.values_list("id", flat=True))
            proposal = StandardizationProposal.objects.get(problem=p)
            self.assertEqual(proposal.status, ProposalStatus.APPLIED)
            self.assertEqual(proposal.provider, "rules")
            self.assertIsNone(proposal.created_by)
            decision = proposal.decisions.get(action="apply")
            self.assertEqual(decision.actor_kind, "service")
            self.assertEqual(decision.policy_version, "mechanical_config_v1")

            summary2, rows2 = self.run_cmd(tmp, "again.jsonl", "--apply")
        self.assertEqual(summary2["results"], {"noop": 1})
        self.assertEqual(StandardizationProposal.objects.filter(problem=p).count(), 1)

    def test_live_contest_is_skipped_and_counted(self):
        p = self.make_problem("stdcmd3")
        now = timezone.now()
        contest = Contest.objects.create(
            key="stdcmdc",
            name="C",
            description="d",
            start_time=now - timezone.timedelta(minutes=1),
            end_time=now + timezone.timedelta(hours=1),
        )
        ContestProblem.objects.create(contest=contest, problem=p, points=100, order=0)
        with TemporaryDirectory() as tmp:
            summary, rows = self.run_cmd(tmp, "c.jsonl", "--apply")
        self.assertEqual(summary["results"], {"blocked_contest": 1})
        self.assertTrue(rows[0]["in_live_contest"])
        p.refresh_from_db()
        self.assertFalse(p.partial)
        proposal = StandardizationProposal.objects.get(problem=p)
        self.assertEqual(proposal.status, ProposalStatus.BLOCKED_CONTEST)
        # after the contest ends a rerun applies the same proposal
        contest.end_time = now - timezone.timedelta(seconds=1)
        contest.save()
        with TemporaryDirectory() as tmp:
            summary, _ = self.run_cmd(tmp, "c2.jsonl", "--apply")
        self.assertEqual(summary["results"], {"applied": 1})
        proposal.refresh_from_db()
        self.assertEqual(proposal.status, ProposalStatus.APPLIED)
        self.assertEqual(StandardizationProposal.objects.filter(problem=p).count(), 1)

    def test_partial_flip_schedules_one_rescore_job_only_with_submissions(self):
        with_subs = self.make_problem("stdcmd4")
        Submission.objects.create(
            problem=with_subs,
            user=self.student,
            language=self.lang,
            points=0,
            case_points=1,
            case_total=2,
            status="D",
            result="WA",
        )
        self.make_problem("stdcmd5")
        with TemporaryDirectory() as tmp:
            summary, _ = self.run_cmd(tmp, "r.jsonl", "--apply")
        self.assertEqual(summary["results"], {"applied": 2})
        jobs = StandardizationRescoreJob.objects.all()
        self.assertEqual(jobs.count(), 1)
        self.assertEqual(jobs[0].problem, with_subs)
        self.assertEqual(jobs[0].reason, "partial")

    def test_resume_from_checkpoint_and_id_range(self):
        a = self.make_problem("stdcmd6")
        b = self.make_problem("stdcmd7")
        with TemporaryDirectory() as tmp:
            summary, rows = self.run_cmd(
                tmp,
                "first.jsonl",
                "--apply",
                "--through-id",
                str(a.pk),
                "--batch-size",
                "1",
            )
            self.assertEqual([r["id"] for r in rows], [a.pk])
            batch = StandardizationBatch.objects.get(pk=summary["batch_id"])
            self.assertEqual(batch.status, BatchStatus.FINISHED)
            with self.assertRaises(CommandError):
                self.run_cmd(tmp, "x.jsonl", "--apply", "--batch-id", str(batch.pk))
            # simulate a crash: batch left RUNNING with a checkpoint
            batch.status = BatchStatus.RUNNING
            batch.id_through = b.pk
            batch.save()
            summary2, rows2 = self.run_cmd(
                tmp, "resume.jsonl", "--apply", "--batch-id", str(batch.pk)
            )
        self.assertEqual([r["id"] for r in rows2], [b.pk])
        self.assertEqual(summary2["batch_id"], batch.pk)
        self.assertEqual(summary2["batch_status"], BatchStatus.FINISHED)
        b.refresh_from_db()
        self.assertTrue(b.partial)

    def test_report_must_not_exist(self):
        self.make_problem("stdcmd8")
        with TemporaryDirectory() as tmp:
            Path(tmp, "dup.jsonl").write_text("")
            with self.assertRaises(CommandError):
                self.run_cmd(tmp, "dup.jsonl")

    def test_missing_author_aborts_before_any_write(self):
        self.make_problem("stdcmd9")
        with override_settings(STANDARDIZATION_AUTHOR_USERNAME="nobody-here"):
            with TemporaryDirectory() as tmp:
                with self.assertRaises(CommandError):
                    self.run_cmd(tmp, "a.jsonl", "--apply")
        self.assertFalse(StandardizationBatch.objects.exists())

    def test_private_problem_points_survive(self):
        p = self.make_problem("stdcmd10", is_public=False)
        p._bypass_points_cap = True
        p.points = 1700
        p.save()
        with TemporaryDirectory() as tmp:
            self.run_cmd(tmp, "p.jsonl", "--apply")
        p.refresh_from_db()
        self.assertEqual(p.points, 1700)
        self.assertTrue(p.partial)
