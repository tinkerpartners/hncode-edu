"""Run AI steps for a set of problems (the pilot runner).

    manage.py standardize_run --codes aplusb,threepoint --steps content,tags_points
    manage.py standardize_run --ids 12,34 --steps all --verify --report /reports/pilot.jsonl
    manage.py standardize_run --from-id 100 --through-id 200 --limit 50 --async

Synchronous by default (one model call at a time, results printed as they
land) so a 50-problem pilot can be watched. ``--async`` queues one Celery
task per (problem, step) instead. ``--verify`` judges every SOLUTION
proposal that was created. Nothing is applied.
"""

import json
from collections import Counter
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from judge.models import Problem
from judge.models.standardization import StandardizationBatch
from judge.standardization.enums import BatchKind, BatchStatus, FieldGroup
from judge.standardization.steps import (
    ALL_STEPS,
    STEP_CONTENT,
    STEP_SOLUTION,
    STEP_TAGS_POINTS,
    run_step,
)
from llm_service.providers import LLMProviderError, get_provider

STEP_ALIASES = {
    "content": STEP_CONTENT,
    "tags_points": STEP_TAGS_POINTS,
    "tags": STEP_TAGS_POINTS,
    "points": STEP_TAGS_POINTS,
    "solution": STEP_SOLUTION,
}


class Command(BaseCommand):
    help = "Run standardization AI steps for selected problems (drafts only)."

    def add_arguments(self, parser):
        parser.add_argument("--codes", help="comma-separated problem codes")
        parser.add_argument("--ids", help="comma-separated problem ids")
        parser.add_argument("--from-id", type=int)
        parser.add_argument("--through-id", type=int)
        parser.add_argument("--limit", type=int, default=50)
        parser.add_argument(
            "--steps",
            default="content,tags_points",
            help="content,tags_points,solution or all",
        )
        parser.add_argument("--provider", default=None)
        parser.add_argument(
            "--verify", action="store_true", help="judge SOLUTION proposals"
        )
        parser.add_argument("--async", dest="run_async", action="store_true")
        parser.add_argument("--name", default="")
        parser.add_argument("--batch-id", type=int)
        parser.add_argument("--report", help="JSONL file (one line per problem/step)")

    def _steps(self, raw):
        if raw.strip() == "all":
            return list(ALL_STEPS)
        steps = []
        for token in raw.split(","):
            token = token.strip()
            if not token:
                continue
            if token not in STEP_ALIASES and token not in ALL_STEPS:
                raise CommandError(f"unknown step {token!r}")
            step = STEP_ALIASES.get(token, token)
            if step not in steps:
                steps.append(step)
        if not steps:
            raise CommandError("no steps selected")
        return steps

    def _problems(self, options):
        qs = Problem.objects.order_by("id")
        if options["codes"]:
            codes = [c.strip() for c in options["codes"].split(",") if c.strip()]
            qs = qs.filter(code__in=codes)
            missing = set(codes) - set(qs.values_list("code", flat=True))
            if missing:
                raise CommandError(f"unknown codes: {', '.join(sorted(missing))}")
            return list(qs)
        if options["ids"]:
            ids = [int(x) for x in options["ids"].split(",") if x.strip()]
            return list(qs.filter(id__in=ids))
        if options["from_id"] is not None or options["through_id"] is not None:
            if options["from_id"] is not None:
                qs = qs.filter(id__gte=options["from_id"])
            if options["through_id"] is not None:
                qs = qs.filter(id__lte=options["through_id"])
            return list(qs[: options["limit"]])
        raise CommandError(
            "select problems with --codes, --ids or --from-id/--through-id"
        )

    def handle(self, *args, **options):
        steps = self._steps(options["steps"])
        problems = self._problems(options)
        if not problems:
            raise CommandError("no problems matched")
        if options["batch_id"]:
            batch = StandardizationBatch.objects.filter(
                pk=options["batch_id"], kind=BatchKind.AI_PIPELINE
            ).first()
            if batch is None:
                raise CommandError("no such AI batch")
        else:
            batch = StandardizationBatch.objects.create(
                name=options["name"] or f"ai-pilot {timezone.now():%Y-%m-%d %H:%M}",
                kind=BatchKind.AI_PIPELINE,
                id_from=min(p.pk for p in problems),
                id_through=max(p.pk for p in problems),
                status=BatchStatus.RUNNING,
                steps_json=steps,
                config_version=f"{options['provider'] or 'default'}",
            )
        report = None
        if options["report"]:
            path = Path(options["report"])
            path.parent.mkdir(parents=True, exist_ok=True)
            report = path.open("x", encoding="utf-8")
        counters = Counter()
        try:
            if options["run_async"]:
                from judge.tasks.standardization import run_ai_step

                for problem in problems:
                    for step in steps:
                        run_ai_step.apply_async(
                            args=[problem.pk, step],
                            kwargs={
                                "batch_id": batch.pk,
                                "provider_name": options["provider"],
                            },
                        )
                        counters["queued"] += 1
                self.stdout.write(
                    json.dumps({"batch_id": batch.pk, "queued": counters["queued"]})
                )
                return
            try:
                provider = get_provider(options["provider"])
            except LLMProviderError as exc:
                raise CommandError(str(exc))
            for problem in problems:
                for step in steps:
                    result = run_step(step, problem, batch=batch, provider=provider)
                    row = {
                        "id": problem.pk,
                        "code": problem.code,
                        "step": step,
                        "proposals": [p.pk for p in result.proposals],
                        "statuses": result.statuses,
                        "skipped": result.skipped,
                        "error": result.error,
                        "cost_usd": [
                            str(p.cost_usd)
                            for p in result.proposals
                            if p.cost_usd is not None
                        ][:1],
                    }
                    counters[
                        result.skipped or result.error.split(":")[0] or "drafted"
                    ] += 1
                    if options["verify"] and step == STEP_SOLUTION:
                        from judge.standardization.verification import (
                            verify_solution_proposal,
                        )

                        for proposal in result.proposals:
                            if proposal.field_group == FieldGroup.SOLUTION and (
                                proposal.payload_json or {}
                            ).get("cpp_code"):
                                verification = verify_solution_proposal(proposal)
                                row.setdefault("verification", []).append(
                                    verification.level
                                )
                                counters[f"verify:{verification.level}"] += 1
                    self.stdout.write(json.dumps(row, ensure_ascii=False))
                    if report:
                        report.write(json.dumps(row, ensure_ascii=False) + "\n")
                        report.flush()
                    batch.checkpoint_id = problem.pk
                    batch.counters_json = dict(counters)
                    batch.save(update_fields=["checkpoint_id", "counters_json"])
        finally:
            if report:
                report.close()
        batch.status = BatchStatus.FINISHED
        batch.finished_at = timezone.now()
        batch.counters_json = dict(counters)
        batch.save(update_fields=["status", "finished_at", "counters_json"])
        self.stdout.write(
            json.dumps(
                {"batch_id": batch.pk, "results": dict(counters)}, ensure_ascii=False
            )
        )
