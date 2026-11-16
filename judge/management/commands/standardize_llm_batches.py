"""Submit and ingest Message Batches for one AI step.

manage.py standardize_llm_batches submit --name "content wave 1" --from-id 1 --through-id 5000 --step content
manage.py standardize_llm_batches submit --batch-id 7 --codes a,b,c --step tags_points
manage.py standardize_llm_batches poll            # all jobs not yet ingested
manage.py standardize_llm_batches poll --job-id 3
manage.py standardize_llm_batches status
"""

import json

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from judge.models import Problem
from judge.models.standardization import LLMBatchJob, StandardizationBatch
from judge.standardization import batches
from judge.standardization.enums import BatchKind, BatchStatus
from judge.standardization.steps import ALL_STEPS
from llm_service.providers import LLMProviderError, get_provider

STEP_ALIASES = {
    "content": ALL_STEPS[0],
    "tags_points": ALL_STEPS[1],
    "solution": ALL_STEPS[2],
}


class Command(BaseCommand):
    help = "Submit / poll provider Message Batches for standardization AI steps."

    def add_arguments(self, parser):
        parser.add_argument("action", choices=["submit", "poll", "status"])
        parser.add_argument("--step", default="content")
        parser.add_argument("--batch-id", type=int)
        parser.add_argument("--name", default="")
        parser.add_argument("--codes")
        parser.add_argument("--ids")
        parser.add_argument("--from-id", type=int)
        parser.add_argument("--through-id", type=int)
        parser.add_argument("--limit", type=int, default=100000)
        parser.add_argument("--chunk", type=int)
        parser.add_argument("--provider", default=None)
        parser.add_argument("--job-id", type=int)

    def handle(self, *args, **options):
        action = options["action"]
        if action == "status":
            rows = [
                {
                    "job": j.pk,
                    "provider_batch_id": j.provider_batch_id,
                    "step": j.step,
                    "batch": j.batch_id,
                    "requests": j.request_count,
                    "status": j.status,
                    "ingested": j.results_ingested,
                    "submitted_at": j.submitted_at.isoformat(),
                }
                for j in LLMBatchJob.objects.order_by("-submitted_at")[:50]
            ]
            self.stdout.write(json.dumps(rows, ensure_ascii=False, indent=1))
            return
        try:
            provider = get_provider(options["provider"])
        except LLMProviderError as exc:
            raise CommandError(str(exc))
        if action == "poll":
            if options["job_id"]:
                job = LLMBatchJob.objects.filter(pk=options["job_id"]).first()
                if job is None:
                    raise CommandError("no such job")
                out = [batches.poll_job(job, provider)]
            else:
                out = batches.poll_all(provider)
            self.stdout.write(json.dumps(out, ensure_ascii=False))
            return
        step = STEP_ALIASES.get(options["step"], options["step"])
        if step not in ALL_STEPS:
            raise CommandError(f"unknown step {options['step']!r}")
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
                name=options["name"]
                or f"ai-batch {step} {timezone.now():%Y-%m-%d %H:%M}",
                kind=BatchKind.AI_PIPELINE,
                id_from=min(p.pk for p in problems),
                id_through=max(p.pk for p in problems),
                status=BatchStatus.RUNNING,
                steps_json=[step],
                config_version=provider.name,
            )
        try:
            summary = batches.submit_step_batches(
                batch, step, problems, provider, options["chunk"]
            )
        except LLMProviderError as exc:
            raise CommandError(f"provider error: {exc}")
        self.stdout.write(json.dumps(summary, ensure_ascii=False))

    def _problems(self, options):
        qs = Problem.objects.order_by("id")
        if options["codes"]:
            codes = [c.strip() for c in options["codes"].split(",") if c.strip()]
            return list(qs.filter(code__in=codes))
        if options["ids"]:
            return list(
                qs.filter(
                    id__in=[int(x) for x in options["ids"].split(",") if x.strip()]
                )
            )
        if options["from_id"] is not None or options["through_id"] is not None:
            if options["from_id"] is not None:
                qs = qs.filter(id__gte=options["from_id"])
            if options["through_id"] is not None:
                qs = qs.filter(id__lte=options["through_id"])
            return list(qs[: options["limit"]])
        raise CommandError(
            "select problems with --codes, --ids or --from-id/--through-id"
        )
