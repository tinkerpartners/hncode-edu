"""Bounded bulk queue for the evidence-guided pipeline, dry-run by default."""

import json
from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from judge.management.commands.standardize_run import Command as PilotCommand
from judge.models import Profile
from judge.models.standardization import StandardizationBatch
from judge.standardization import guided, permissions


class Command(BaseCommand):
    help = "Plan or enqueue the guided pipeline in bounded ID chunks; no AI calls without --enqueue."

    def add_arguments(self, parser):
        parser.add_argument("--actor", required=True)
        selection = parser.add_mutually_exclusive_group(required=True)
        selection.add_argument("--codes")
        selection.add_argument("--ids")
        selection.add_argument("--from-id", type=int)
        parser.add_argument("--through-id", type=int)
        parser.add_argument("--limit", type=int, default=100)
        parser.add_argument("--budget-per-problem", default="0.10")
        parser.add_argument("--no-sol", action="store_true")
        parser.add_argument("--enqueue", action="store_true")

    def handle(self, *args, **options):
        if not 1 <= options["limit"] <= 10000:
            raise CommandError("--limit must be between 1 and 10000")
        if options["through_id"] and (
            options["from_id"] is None or options["through_id"] < options["from_id"]
        ):
            raise CommandError("--through-id must follow --from-id")
        try:
            cap = Decimal(options["budget_per_problem"])
            if not cap.is_finite() or not Decimal("0.001") <= cap <= Decimal("10"):
                raise ValueError
        except (InvalidOperation, ValueError):
            raise CommandError(
                "--budget-per-problem must be between 0.001 and 10 USD"
            ) from None
        actor = (
            Profile.objects.select_related("user")
            .filter(user__username=options["actor"])
            .first()
        )
        if not actor or not permissions.can_review_standardization(actor.user):
            raise CommandError("actor needs standardization review permission")
        try:
            problems = PilotCommand()._problems(options)
        except (TypeError, ValueError) as exc:
            raise CommandError("invalid problem selection") from exc
        if len(problems) > options["limit"]:
            raise CommandError("selection exceeds --limit; use bounded ID ranges")
        if any(
            not permissions.can_view_problem_source(actor.user, p) for p in problems
        ):
            raise CommandError("actor cannot access all selected sources")
        from judge.tasks.standardization import run_guided

        for problem in problems:
            row = {
                "id": problem.pk,
                "code": problem.code,
                "result": "would_queue",
                "cap_usd": str(cap),
            }
            if options["enqueue"]:
                with transaction.atomic():
                    batch, created = guided.queue_run(
                        problem,
                        actor,
                        include_content=True,
                        include_config=True,
                        allow_sol=not options["no_sol"],
                        budget_cap=cap,
                    )
                    row.update(
                        batch=batch.pk, result="queued" if created else "already_active"
                    )
                    if created:

                        def dispatch(run_id=batch.pk):
                            try:
                                run_guided.delay(run_id)
                            except Exception:
                                StandardizationBatch.objects.filter(
                                    pk=run_id, status="queued"
                                ).update(status="failed")
                                raise CommandError(
                                    f"worker dispatch failed for batch {run_id}"
                                ) from None

                        transaction.on_commit(dispatch)
            self.stdout.write(json.dumps(row, ensure_ascii=False))
