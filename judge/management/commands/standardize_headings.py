"""Rule-based ``####``/``##`` -> ``###`` heading fix for statements.

    manage.py standardize_headings --report /reports/headings-dry.jsonl
    manage.py standardize_headings --apply --report /reports/headings-01.jsonl
    manage.py standardize_headings --apply --batch-id 7 --report ...

For every problem with a description, ``format_v1.rule_fix_headings`` rewrites
only the Input/Output/Example/Scoring heading lines. The fix is applied
without review *only* when ``can_auto_apply`` proves the change is
heading-only and the result validates as HNCode statement v1; it goes through
``apply_proposal`` as a CONTENT proposal (step ``heading_fix_v1``, policy
auto-accepted, service actor), so it is snapshotted, audited and restorable.
Statements that still fail validation are only reported (``needs_review``);
they are the AI pipeline's input, not this command's.
"""

import json
from collections import Counter

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from judge.models import Problem
from judge.models.standardization import (
    ProblemSnapshot,
    StandardizationBatch,
    StandardizationProposal,
)
from judge.standardization import format_v1
from judge.standardization.apply import (
    ContestBlockedError,
    StaleSourceError,
    apply_proposal,
    is_in_live_contest,
)
from judge.standardization.enums import (
    BatchKind,
    BatchStatus,
    FieldGroup,
    ProposalStatus,
)
from judge.standardization.hashing import compute_source_hash, dedupe_key
from judge.standardization.snapshot import capture_all

STEP = format_v1.HEADING_FIX_STEP


class Command(BaseCommand):
    help = "Normalise Input/Output/Example/Scoring headings to level 3 where that is provably all that changes."

    def add_arguments(self, parser):
        parser.add_argument("--from-id", type=int, default=0)
        parser.add_argument("--through-id", type=int)
        parser.add_argument("--batch-size", type=int, default=200)
        parser.add_argument("--report", required=True)
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--batch-id", type=int)
        parser.add_argument("--name", default="")

    def handle(self, *args, **options):
        if options["from_id"] < 0 or not 1 <= options["batch_size"] <= 1000:
            raise CommandError("from-id must be nonnegative, batch-size must be 1..1000")
        apply = options["apply"]
        batch = None
        last_id, upper = options["from_id"], options["through_id"]
        if options["batch_id"]:
            if not apply:
                raise CommandError("--batch-id only makes sense with --apply")
            batch = StandardizationBatch.objects.filter(pk=options["batch_id"]).first()
            if batch is None or batch.kind != BatchKind.HEADING_FIX:
                raise CommandError("no such heading-fix batch")
            if batch.status in (BatchStatus.FINISHED, BatchStatus.CANCELLED):
                raise CommandError(f"batch #{batch.pk} is {batch.status}")
            last_id, upper = batch.checkpoint_id, batch.id_through
            batch.status = BatchStatus.RUNNING
            batch.save(update_fields=["status"])
        if upper is None:
            upper = Problem.objects.order_by("-pk").values_list("pk", flat=True).first() or 0
        if apply and batch is None:
            batch = StandardizationBatch.objects.create(
                name=options["name"] or f"headings {timezone.now():%Y-%m-%d %H:%M}",
                kind=BatchKind.HEADING_FIX, id_from=last_id, id_through=upper,
                checkpoint_id=last_id, status=BatchStatus.RUNNING,
                config_version=format_v1.FORMAT_VERSION, steps_json=["headings"],
            )

        counters = Counter()
        try:
            report = open(options["report"], "x", encoding="utf-8")
        except FileExistsError:
            raise CommandError("Report exists. Choose a new path to keep the previous audit.")

        with report:
            while True:
                if batch is not None:
                    batch.refresh_from_db(fields=["status"])
                    if batch.status in (BatchStatus.PAUSING, BatchStatus.CANCEL_REQUESTED):
                        break
                chunk = list(
                    Problem.objects.filter(pk__gt=last_id, pk__lte=upper)
                    .order_by("pk")
                    .only("id", "code", "description", "is_public")[: options["batch_size"]]
                )
                if not chunk:
                    break
                for problem in chunk:
                    row = self.process(problem, batch, apply)
                    last_id = problem.pk
                    counters[row["result"]] += 1
                    report.write(json.dumps(row, ensure_ascii=False) + "\n")
                report.flush()
                if batch is not None:
                    batch.checkpoint_id = last_id
                    batch.counters_json = {"results": dict(counters)}
                    batch.save(update_fields=["checkpoint_id", "counters_json"])

        if batch is not None:
            batch.refresh_from_db(fields=["status"])
            if batch.status == BatchStatus.PAUSING:
                batch.status = BatchStatus.PAUSED
            elif batch.status == BatchStatus.CANCEL_REQUESTED:
                batch.status = BatchStatus.CANCELLED
            else:
                batch.status = BatchStatus.FINISHED
                batch.finished_at = timezone.now()
            batch.counters_json = {"results": dict(counters)}
            batch.save(update_fields=["status", "finished_at", "counters_json"])

        self.stdout.write(
            json.dumps(
                {
                    "mode": "apply" if apply else "dry-run",
                    "batch_id": batch.pk if batch else None,
                    "batch_status": batch.status if batch else None,
                    "last_id": last_id,
                    "through_id": upper,
                    "results": dict(counters),
                },
                ensure_ascii=False,
            )
        )

    def process(self, problem, batch, apply):
        before = problem.description or ""
        row = {"id": problem.pk, "code": problem.code, "public": problem.is_public}
        if not before.strip():
            row["result"] = "no_statement"
            return row
        current = format_v1.validate(before)
        if current.ok:
            row.update(result="already_v1", level=current.label)
            return row
        after = format_v1.rule_fix_headings(before)
        ok, reason = format_v1.can_auto_apply(before, after)
        row["validation_before"] = current.errors
        if not ok:
            row.update(result="needs_review", reason=reason)
            return row
        row["level_after"] = reason
        if is_in_live_contest(problem):
            row["result"] = "blocked_contest"
            if not apply:
                return row
        elif not apply:
            row["result"] = "would_apply"
            return row
        row["result"] = self.apply_one(problem, batch, after, row)
        return row

    def apply_one(self, problem, batch, after, row):
        problem = Problem.objects.get(pk=problem.pk)  # full row for snapshot/hash
        source_hash = compute_source_hash(problem)
        key = dedupe_key(problem.pk, source_hash, FieldGroup.CONTENT, STEP, format_v1.FORMAT_VERSION)
        with transaction.atomic():
            proposal = StandardizationProposal.objects.filter(dedupe_key=key).first()
            if proposal is None:
                snapshot = ProblemSnapshot.objects.create(
                    problem=problem, batch=batch, source_hash=source_hash,
                    before_json=capture_all(problem),
                )
                proposal = StandardizationProposal.objects.create(
                    snapshot=snapshot, batch=batch, problem=problem,
                    field_group=FieldGroup.CONTENT, step=STEP,
                    prompt_version=format_v1.FORMAT_VERSION, provider="rules",
                    dedupe_key=key, status=ProposalStatus.AUTO_ACCEPTED,
                    payload_json={"description": after},
                    diff_json={"headings_only": True},
                    evidence_json={"policy": STEP, "validation": "heading-only rewrite"},
                )
            elif proposal.status == ProposalStatus.APPLIED:
                row["proposal_id"] = proposal.pk
                return "already_applied"
            else:
                proposal.status = ProposalStatus.AUTO_ACCEPTED
                proposal.batch = batch
                proposal.save(update_fields=["status", "batch"])
        row["proposal_id"] = proposal.pk
        try:
            apply_proposal(proposal, actor=None, policy_version=STEP, conditions={"headings_only": True})
        except ContestBlockedError:
            return "blocked_contest"
        except StaleSourceError:
            return "stale"
        except Exception as exc:  # noqa: BLE001
            StandardizationProposal.objects.filter(pk=proposal.pk).update(
                status=ProposalStatus.ERROR,
                evidence_json={"error": f"{type(exc).__name__}: {exc}"[:1000]},
            )
            row["error"] = f"{type(exc).__name__}: {exc}"[:500]
            return "error"
        return "applied"
