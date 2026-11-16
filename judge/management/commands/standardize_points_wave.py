"""Roll approved CF-style points into Problem.points in bounded waves (PR-G).

manage.py standardize_points_wave snapshot --out ~/reports/leaderboard-before.json
manage.py standardize_points_wave plan [--limit 200] [--from-id A --through-id B] [--codes a,b]
manage.py standardize_points_wave run --name "wave 1" --limit 200 --snapshot ~/reports/lb-w1.json [--actor user]
manage.py standardize_points_wave status --batch-id 12
manage.py standardize_points_wave compare --before ~/reports/lb-w1.json [--out ~/reports/lb-w1-diff.json]
manage.py standardize_points_wave rollback --batch-id 12 [--actor user]

``run`` requires a snapshot path (or ``--skip-snapshot``), applies through the
audited apply layer, then kicks the rescore drain unless ``--no-drain``. Waves
are meant to be 100-300 proposals; the default limit is 200.
"""

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from judge.models import Profile
from judge.models.standardization import StandardizationBatch
from judge.standardization import points_wave
from judge.standardization.enums import BatchKind

ACTIONS = ("snapshot", "plan", "run", "status", "compare", "rollback")


class Command(BaseCommand):
    help = "Snapshot leaderboards and apply approved POINTS proposals in waves."

    def add_arguments(self, parser):
        parser.add_argument("action", choices=ACTIONS)
        parser.add_argument(
            "--out", help="snapshot/compare output file (created, never overwritten)"
        )
        parser.add_argument("--before", help="snapshot file for compare")
        parser.add_argument(
            "--snapshot", help="leaderboard snapshot to write before a run"
        )
        parser.add_argument("--skip-snapshot", action="store_true")
        parser.add_argument("--name", default="")
        parser.add_argument("--note", default="")
        parser.add_argument("--limit", type=int, default=200)
        parser.add_argument("--from-id", type=int)
        parser.add_argument("--through-id", type=int)
        parser.add_argument("--codes", help="comma-separated problem codes")
        parser.add_argument("--batch-id", type=int)
        parser.add_argument("--actor", help="username recorded as the human actor")
        parser.add_argument("--no-drain", action="store_true")
        parser.add_argument("--top", type=int, default=20)

    def _actor(self, options):
        if not options["actor"]:
            return None
        actor = Profile.objects.filter(user__username=options["actor"]).first()
        if actor is None:
            raise CommandError(f"no profile for username {options['actor']!r}")
        return actor

    def _batch(self, options):
        if not options["batch_id"]:
            raise CommandError("--batch-id is required")
        batch = StandardizationBatch.objects.filter(pk=options["batch_id"]).first()
        if batch is None:
            raise CommandError("no such batch")
        if batch.kind != BatchKind.POINTS_WAVE:
            raise CommandError(f"batch #{batch.pk} is {batch.kind}, not a points wave")
        return batch

    def _selection(self, options):
        codes = (
            [c.strip() for c in options["codes"].split(",")]
            if options["codes"]
            else None
        )
        return points_wave.eligible_proposals(
            from_id=options["from_id"],
            through_id=options["through_id"],
            codes=codes,
            limit=options["limit"],
        )

    def _dump(self, obj):
        self.stdout.write(json.dumps(obj, ensure_ascii=False, default=str))

    def handle(self, *args, **options):
        action = options["action"]
        try:
            getattr(self, f"do_{action}")(options)
        except (points_wave.WaveError, FileExistsError, FileNotFoundError) as exc:
            raise CommandError(str(exc))

    def do_snapshot(self, options):
        if not options["out"]:
            raise CommandError("--out is required")
        self._dump(
            points_wave.snapshot_leaderboard(options["out"], label=options["name"])
        )

    def do_plan(self, options):
        plan = points_wave.plan_wave(self._selection(options))
        for row in plan["rows"]:
            self.stdout.write(
                f"  #{row['proposal']:<7} {row['code']:<24} {row['points_from']:>7g} -> "
                f"{row['points_to']:>7g}{'  public' if row['is_public'] else ''}"
            )
        self._dump({k: v for k, v in plan.items() if k != "rows"})

    def do_run(self, options):
        actor = self._actor(options)
        proposals = self._selection(options)
        if not proposals:
            raise CommandError(
                "nothing to apply: no APPROVED points proposals selected"
            )
        snapshot = None
        if options["snapshot"]:
            snapshot = Path(options["snapshot"])
            snap = points_wave.snapshot_leaderboard(snapshot, label=options["name"])
            self.stdout.write(
                f"snapshot: {snap['profiles']} profiles -> {snap['path']}"
            )
        elif not options["skip_snapshot"]:
            raise CommandError(
                "a leaderboard snapshot is required before a wave: pass --snapshot PATH "
                "(or --skip-snapshot if one was taken already)"
            )
        result = points_wave.run_wave(
            options["name"],
            proposals,
            actor,
            snapshot_path=snapshot,
            note=options["note"],
        )
        summary = {
            "batch": result.batch.pk,
            "status": result.batch.status,
            "selected": len(proposals),
            "applied": len(result.applied),
            "stale": len(result.stale),
            "blocked": len(result.blocked),
            "skipped": len(result.skipped),
            "errors": result.errors,
            "rescore_jobs": len(result.rescore_jobs),
        }
        if result.rescore_jobs and not options["no_drain"]:
            from judge.tasks.standardization import kick_rescore_drain

            summary["drain_task"] = kick_rescore_drain().id
        self._dump(summary)

    def do_status(self, options):
        self._dump(points_wave.wave_status(self._batch(options)))

    def do_compare(self, options):
        if not options["before"]:
            raise CommandError("--before is required")
        report = points_wave.compare_leaderboard(options["before"], top=options["top"])
        if options["out"]:
            with Path(options["out"]).open("x", encoding="utf-8") as fh:
                json.dump(report, fh, ensure_ascii=False)
            report["out"] = options["out"]
        self._dump({k: v for k, v in report.items() if k != "changes"})

    def do_rollback(self, options):
        batch = self._batch(options)
        result = points_wave.rollback_wave(
            batch, self._actor(options), note=options["note"]
        )
        if result["restored"] and not options["no_drain"]:
            from judge.tasks.standardization import kick_rescore_drain

            result["drain_task"] = kick_rescore_drain().id
        self._dump(result)
