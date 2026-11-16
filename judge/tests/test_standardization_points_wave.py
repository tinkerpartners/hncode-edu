import json
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.management import CommandError, call_command
from django.test import TestCase

from judge.models import Language, Problem, ProblemGroup, Profile, Submission
from judge.models.standardization import (
    ProblemSnapshot,
    StandardizationBatch,
    StandardizationDecision,
    StandardizationProposal,
    StandardizationRescoreJob,
)
from judge.standardization import points_wave
from judge.standardization.enums import (
    BatchKind,
    BatchStatus,
    DecisionAction,
    FieldGroup,
    ProposalStatus,
    RescoreStatus,
)
from judge.standardization.hashing import compute_source_hash, dedupe_key
from judge.standardization.snapshot import capture_all
from judge.tasks.standardization import DRAIN_LOCK_KEY, drain_rescore_jobs


class PointsWaveTest(TestCase):
    fixtures = ["language_small"]

    @classmethod
    def setUpTestData(cls):
        cls.group = ProblemGroup.objects.create(name="stdw", full_name="std wave")
        cls.lang = Language.get_python3()
        cls.solver = Profile.objects.create(
            user=User.objects.create_user("wavesolver", "w@x.com", "x"),
            language=cls.lang,
        )
        cls.reviewer = Profile.objects.create(
            user=User.objects.create_user("wavereviewer", "r@x.com", "x"),
            language=cls.lang,
        )

    def setUp(self):
        cache.delete(DRAIN_LOCK_KEY)

    def make_problem(self, code, points=100, public=True, solved=True):
        p = Problem.objects.create(
            code=code,
            name=code,
            description="x",
            group=self.group,
            time_limit=1,
            memory_limit=262144,
            points=points,
            partial=True,
            is_public=public,
        )
        if solved:
            Submission.objects.create(
                problem=p,
                user=self.solver,
                language=self.lang,
                points=points,
                case_points=1,
                case_total=1,
                status="D",
                result="AC",
            )
        return p

    def proposal(self, problem, points, status=ProposalStatus.APPROVED, step="tp"):
        problem = Problem.objects.get(pk=problem.pk)
        snap = ProblemSnapshot.objects.create(
            problem=problem,
            source_hash=compute_source_hash(problem),
            before_json=capture_all(problem),
        )
        return StandardizationProposal.objects.create(
            snapshot=snap,
            problem=problem,
            field_group=FieldGroup.POINTS,
            step=step,
            dedupe_key=dedupe_key(
                problem.id, snap.source_hash, FieldGroup.POINTS, step, str(points)
            ),
            status=status,
            payload_json={"points": points},
        )

    # --- snapshot ---------------------------------------------------------

    def test_snapshot_ranks_and_refuses_overwrite(self):
        self.solver.points, self.solver.performance_points = 100, 90
        self.solver.problem_count = 1
        self.solver.save()
        with TemporaryDirectory() as tmp:
            path = Path(tmp, "lb.json")
            info = points_wave.snapshot_leaderboard(path, label="t")
            self.assertEqual(info["profiles"], 1)  # reviewer holds no score
            doc = json.loads(path.read_text(encoding="utf-8"))
            row = doc["profiles"][0]
            self.assertEqual(
                (row["username"], row["points"], row["rank"]), ("wavesolver", 100.0, 1)
            )
            with self.assertRaises(FileExistsError):
                points_wave.snapshot_leaderboard(path)

    # --- selection --------------------------------------------------------

    def test_eligible_picks_newest_approved_points_per_problem(self):
        a, b, c = (self.make_problem(f"stdw{i}") for i in "abc")
        old = self.proposal(a, 1200, step="old")
        new = self.proposal(a, 1300, step="new")
        self.proposal(b, 1400, status=ProposalStatus.PENDING_REVIEW)
        self.proposal(c, 1500, status=ProposalStatus.AUTO_ACCEPTED)
        picked = points_wave.eligible_proposals()
        self.assertEqual([p.pk for p in picked], [new.pk])
        self.assertNotIn(old.pk, [p.pk for p in picked])
        self.assertEqual(points_wave.eligible_proposals(through_id=a.pk - 1), [])
        self.assertEqual(len(points_wave.eligible_proposals(codes=["stdwa"])), 1)
        plan = points_wave.plan_wave(picked)
        self.assertEqual(plan["proposals"], 1)
        self.assertEqual(plan["rows"][0]["points_to"], 1300.0)
        self.assertEqual(plan["rows"][0]["points_from"], 100.0)

    # --- run / rollback ---------------------------------------------------

    def test_run_wave_applies_tallies_and_is_reversible(self):
        a, b, c = (self.make_problem(f"stdw{i}") for i in "abc")
        pa, pb, pc = (self.proposal(p, 1500) for p in (a, b, c))
        # c's points edited by hand after the draft -> stale, not applied
        c.points = 7
        c._bypass_points_cap = True
        c.save()

        result = points_wave.run_wave(
            "w1", [pa, pb, pc], self.reviewer, snapshot_path="/tmp/x.json"
        )
        batch = result.batch
        self.assertEqual(batch.kind, BatchKind.POINTS_WAVE)
        self.assertEqual(batch.status, BatchStatus.FINISHED)
        self.assertEqual((batch.id_from, batch.id_through), (a.pk, c.pk))
        self.assertEqual(sorted(result.applied), sorted([pa.pk, pb.pk]))
        self.assertEqual(result.stale, [pc.pk])
        self.assertEqual(result.errors, {})
        self.assertEqual(len(result.rescore_jobs), 2)
        self.assertEqual(Problem.objects.get(pk=a.pk).points, 1500)
        self.assertEqual(Problem.objects.get(pk=c.pk).points, 7)
        self.assertEqual(
            StandardizationProposal.objects.get(pk=pc.pk).status, ProposalStatus.STALE
        )
        d = StandardizationDecision.objects.get(
            proposal=pa, action=DecisionAction.APPLY
        )
        self.assertEqual(d.conditions_json["wave_batch"], batch.pk)
        self.assertEqual(d.policy_version, points_wave.POLICY_VERSION)
        self.assertEqual(batch.counters_json["applied_count"], 2)
        self.assertEqual(batch.counters_json["snapshot"], "/tmp/x.json")

        # re-running selects nothing: applied / stale are no longer APPROVED
        self.assertEqual(points_wave.eligible_proposals(), [])
        with self.assertRaises(points_wave.WaveError):
            points_wave.run_wave("w2", [], None)

        status = points_wave.wave_status(batch)
        self.assertEqual((status["applied"], status["stale"]), (2, 1))
        self.assertEqual(status["rescore_jobs"][RescoreStatus.PENDING], 2)
        self.assertFalse(status["rescore_drained"])

        # rollback restores both, and a second rollback has nothing left
        out = points_wave.rollback_wave(batch, self.reviewer)
        self.assertEqual(sorted(out["restored"]), sorted([pa.pk, pb.pk]))
        self.assertEqual(out["restore_conflicts"], {})
        self.assertEqual(Problem.objects.get(pk=a.pk).points, 100)
        self.assertEqual(
            StandardizationProposal.objects.get(pk=pa.pk).status,
            ProposalStatus.RESTORED,
        )
        batch.refresh_from_db()
        self.assertEqual(batch.status, BatchStatus.CANCELLED)
        again = points_wave.rollback_wave(batch)
        self.assertEqual(again["restored"], [])
        self.assertEqual(sorted(again["already_restored"]), sorted([pa.pk, pb.pk]))

    def test_rollback_refuses_hand_edited_points(self):
        a = self.make_problem("stdwx")
        pa = self.proposal(a, 1500)
        batch = points_wave.run_wave("w", [pa], None, snapshot_path=None).batch
        a = Problem.objects.get(pk=a.pk)
        a.points = 1234
        a._bypass_points_cap = True
        a.save()
        out = points_wave.rollback_wave(batch)
        self.assertEqual(out["restored"], [])
        self.assertIn(str(pa.pk), out["restore_conflicts"])
        self.assertEqual(Problem.objects.get(pk=a.pk).points, 1234)
        with self.assertRaises(points_wave.WaveError):
            points_wave.rollback_wave(
                StandardizationBatch.objects.create(
                    name="x", kind=BatchKind.HEADING_FIX, id_through=1
                )
            )

    # --- leaderboard before/after -------------------------------------------

    def test_compare_shows_movement_after_drain(self):
        a = self.make_problem("stdwc")
        self.solver.calculate_points()
        self.solver.refresh_from_db()
        self.assertEqual(self.solver.points, 100)
        pa = self.proposal(a, 1500)
        with TemporaryDirectory() as tmp:
            before = Path(tmp, "before.json")
            points_wave.snapshot_leaderboard(before)
            result = points_wave.run_wave("w", [pa], None, snapshot_path=before)
            drain_rescore_jobs(limit=10, reschedule=False, sync=True)
            self.assertTrue(points_wave.wave_status(result.batch)["rescore_drained"])
            self.assertEqual(Submission.objects.get(problem=a).points, 1500)
            report = points_wave.compare_leaderboard(before)
        self.assertEqual(report["changed"], 1)
        mover = report["top_by_points"][0]
        self.assertEqual(mover["username"], "wavesolver")
        self.assertEqual(mover["delta"]["points"], 1400.0)
        self.assertNotIn("rank", mover["delta"])  # still the only ranked profile

    # --- command --------------------------------------------------------------

    def run_cmd(self, *args):
        out = StringIO()
        call_command("standardize_points_wave", *args, stdout=out)
        lines = out.getvalue().strip().splitlines()
        return json.loads(lines[-1]), lines

    def test_command_end_to_end(self):
        a = self.make_problem("stdwcmd")
        pa = self.proposal(a, 1500)
        with TemporaryDirectory() as tmp:
            plan, lines = self.run_cmd("plan", "--limit", "5")
            self.assertEqual(plan["proposals"], 1)
            self.assertIn("stdwcmd", lines[0])
            with self.assertRaises(CommandError):  # snapshot is mandatory
                self.run_cmd("run", "--name", "w", "--no-drain")
            with self.assertRaises(CommandError):
                self.run_cmd(
                    "run", "--actor", "nobody", "--skip-snapshot", "--no-drain"
                )
            snap = str(Path(tmp, "lb.json"))
            summary, _ = self.run_cmd(
                "run",
                "--name",
                "w",
                "--snapshot",
                snap,
                "--actor",
                "wavereviewer",
                "--no-drain",
            )
            self.assertEqual((summary["applied"], summary["rescore_jobs"]), (1, 1))
            self.assertEqual(summary["status"], BatchStatus.FINISHED)
            self.assertTrue(Path(snap).exists())
            batch_id = str(summary["batch"])
            status, _ = self.run_cmd("status", "--batch-id", batch_id)
            self.assertEqual(status["applied"], 1)
            self.assertEqual(status["snapshot"], snap)
            diff = str(Path(tmp, "diff.json"))
            cmp_, _ = self.run_cmd("compare", "--before", snap, "--out", diff)
            self.assertEqual(cmp_["out"], diff)
            self.assertIn("changed", cmp_)
            self.assertEqual(
                StandardizationProposal.objects.get(pk=pa.pk).applied_by, self.reviewer
            )
            rb, _ = self.run_cmd("rollback", "--batch-id", batch_id, "--no-drain")
            self.assertEqual(rb["restored"], [pa.pk])
            self.assertEqual(Problem.objects.get(pk=a.pk).points, 100)
            with self.assertRaises(CommandError):
                self.run_cmd("status", "--batch-id", "999999")
        self.assertEqual(
            StandardizationRescoreJob.objects.filter(problem=a).count(), 2
        )  # apply target + restore target
