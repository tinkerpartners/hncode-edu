"""Apply the five mechanical configuration rules to the problem bank.

    manage.py standardize_config --report /reports/config-dry.jsonl            # dry run
    manage.py standardize_config --apply --report /reports/config-apply.jsonl  # apply
    manage.py standardize_config --apply --batch-id 3 --report ...             # resume

Every problem in the id range gets a ``plan_config`` diff. In a dry run the
diff is only written to the JSONL report. With ``--apply`` each non-empty diff
becomes a CONFIG proposal (``AUTO_ACCEPTED`` by policy
``mechanical_config_v1``, service actor) applied through
``judge.standardization.apply.apply_proposal`` -- so it gets a snapshot, a
reversion, an audit row, a contest guard and a de-duplicated rescore job like
any reviewed change. The batch checkpoints after every chunk; a crashed run
resumes with ``--batch-id``. One failing problem never stops the run.
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
from judge.standardization import config_profile
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

STEP = config_profile.CONFIG_PROFILE_VERSION


class Command(BaseCommand):
    help = "Apply the fixed configuration rules (partial, languages, 1024 MB, author, private solutions)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--from-id", type=int, default=0, help="first problem id (exclusive)"
        )
        parser.add_argument(
            "--through-id",
            type=int,
            help="last problem id (inclusive); default: max id at start",
        )
        parser.add_argument("--batch-size", type=int, default=200)
        parser.add_argument(
            "--report", required=True, help="JSONL report path (must not exist)"
        )
        parser.add_argument(
            "--apply", action="store_true", help="write changes; default is a dry run"
        )
        parser.add_argument(
            "--batch-id", type=int, help="resume an existing batch from its checkpoint"
        )
        parser.add_argument("--name", default="", help="batch name")
        parser.add_argument(
            "--no-require-judge",
            action="store_true",
            help="do not require an unblocked judge for each required runtime",
        )
        parser.add_argument(
            "--drain",
            action="store_true",
            help="after applying, start the rescore drain chain (needs a running celery worker)",
        )

    def handle(self, *args, **options):
        if options["from_id"] < 0 or not 1 <= options["batch_size"] <= 1000:
            raise CommandError(
                "from-id must be nonnegative, batch-size must be 1..1000"
            )
        require_judge = not options["no_require_judge"]
        try:
            ctx = config_profile.resolve_context(require_judge=require_judge)
        except config_profile.ProfileResolutionError as exc:
            raise CommandError(f"cannot resolve the fixed rules: {exc}")

        apply = options["apply"]
        batch = None
        last_id = options["from_id"]
        upper = options["through_id"]
        if options["batch_id"]:
            if not apply:
                raise CommandError("--batch-id only makes sense with --apply")
            batch = StandardizationBatch.objects.filter(pk=options["batch_id"]).first()
            if batch is None or batch.kind != BatchKind.MECHANICAL_CONFIG:
                raise CommandError("no such mechanical-config batch")
            if batch.status in (BatchStatus.FINISHED, BatchStatus.CANCELLED):
                raise CommandError(f"batch #{batch.pk} is {batch.status}")
            last_id, upper = batch.checkpoint_id, batch.id_through
            batch.status = BatchStatus.RUNNING
            batch.save(update_fields=["status"])
        if upper is None:
            upper = (
                Problem.objects.order_by("-pk").values_list("pk", flat=True).first()
                or 0
            )
        if apply and batch is None:
            batch = StandardizationBatch.objects.create(
                name=options["name"] or f"config {timezone.now():%Y-%m-%d %H:%M}",
                kind=BatchKind.MECHANICAL_CONFIG,
                id_from=last_id,
                id_through=upper,
                checkpoint_id=last_id,
                status=BatchStatus.RUNNING,
                config_version=STEP,
                steps_json=["config"],
            )

        counters = Counter()
        rules = Counter()
        try:
            report = open(options["report"], "x", encoding="utf-8")
        except FileExistsError:
            raise CommandError(
                "Report exists. Choose a new path to keep the previous audit."
            )

        with report:
            while True:
                if batch is not None:
                    batch.refresh_from_db(fields=["status"])
                    if batch.status in (
                        BatchStatus.PAUSING,
                        BatchStatus.CANCEL_REQUESTED,
                    ):
                        break
                chunk = list(
                    Problem.objects.filter(pk__gt=last_id, pk__lte=upper)
                    .order_by("pk")
                    .prefetch_related("allowed_languages", "authors", "language_limits")
                    .select_related("solution")[: options["batch_size"]]
                )
                if not chunk:
                    break
                for problem in chunk:
                    row = self.process(problem, ctx, batch, apply, require_judge)
                    last_id = problem.pk
                    counters[row["result"]] += 1
                    for rule in row["rules_changed"]:
                        rules[rule] += 1
                    report.write(json.dumps(row, ensure_ascii=False) + "\n")
                report.flush()
                if batch is not None:
                    batch.checkpoint_id = last_id
                    batch.counters_json = {
                        "results": dict(counters),
                        "rules": dict(rules),
                    }
                    batch.save(update_fields=["checkpoint_id", "counters_json"])

        if batch is not None:
            batch.refresh_from_db(fields=["status"])
            if batch.status == BatchStatus.PAUSING:
                batch.status = BatchStatus.PAUSED
            elif batch.status == BatchStatus.CANCEL_REQUESTED:
                batch.status = BatchStatus.CANCELLED
            else:  # the loop only exits on pause/cancel or when the range is exhausted
                batch.status = BatchStatus.FINISHED
                batch.finished_at = timezone.now()
            batch.counters_json = {"results": dict(counters), "rules": dict(rules)}
            batch.save(update_fields=["status", "finished_at", "counters_json"])
            if options["drain"] and counters["applied"]:
                from judge.tasks.standardization import kick_rescore_drain

                kick_rescore_drain()

        self.stdout.write(
            json.dumps(
                {
                    "mode": "apply" if apply else "dry-run",
                    "batch_id": batch.pk if batch else None,
                    "batch_status": batch.status if batch else None,
                    "last_id": last_id,
                    "through_id": upper,
                    "results": dict(counters),
                    "rules": dict(rules),
                },
                ensure_ascii=False,
            )
        )

    def process(self, problem, ctx, batch, apply, require_judge):
        row = {
            "id": problem.pk,
            "code": problem.code,
            "public": problem.is_public,
            "rules_changed": [],
            "result": "noop",
            "in_live_contest": False,
        }
        try:
            diff = config_profile.plan_config(problem, ctx)
        except Exception as exc:  # noqa: BLE001 - one bad row must not stop the run
            row.update(result="error", error=f"{type(exc).__name__}: {exc}"[:500])
            return row
        row["rules_changed"] = diff.rules_changed
        row["before"] = {
            "partial": diff.partial_from,
            "memory_limit": diff.memory_from,
            "add_language_ids": diff.add_language_ids,
            "add_author_ids": diff.add_author_ids,
            "language_limit_updates": diff.language_limit_updates,
            "solution_make_private": diff.solution_make_private,
        }
        row["in_live_contest"] = is_in_live_contest(problem)
        if diff.is_noop():
            return row
        if not apply:
            row["result"] = (
                "blocked_contest" if row["in_live_contest"] else "would_apply"
            )
            return row
        row["result"] = self.apply_one(problem, batch, diff, require_judge, row)
        return row

    def apply_one(self, problem, batch, diff, require_judge, row):
        source_hash = compute_source_hash(problem)
        key = dedupe_key(problem.pk, source_hash, FieldGroup.CONFIG, STEP, "")
        with transaction.atomic():
            proposal = StandardizationProposal.objects.filter(dedupe_key=key).first()
            if proposal is None:
                snapshot = ProblemSnapshot.objects.create(
                    problem=problem,
                    batch=batch,
                    source_hash=source_hash,
                    before_json=capture_all(problem),
                )
                proposal = StandardizationProposal.objects.create(
                    snapshot=snapshot,
                    batch=batch,
                    problem=problem,
                    field_group=FieldGroup.CONFIG,
                    step=STEP,
                    provider="rules",
                    dedupe_key=key,
                    status=ProposalStatus.AUTO_ACCEPTED,
                    payload_json={"require_judge": require_judge},
                    diff_json=diff.to_json(),
                    evidence_json={"policy": STEP, "rules": diff.rules_changed},
                )
            elif proposal.status == ProposalStatus.APPLIED:
                row["proposal_id"] = proposal.pk
                return "already_applied"
            else:
                # earlier attempt was blocked or errored: retry under the same policy
                proposal.status = ProposalStatus.AUTO_ACCEPTED
                proposal.batch = batch
                proposal.save(update_fields=["status", "batch"])
        row["proposal_id"] = proposal.pk
        try:
            apply_proposal(
                proposal,
                actor=None,
                policy_version=STEP,
                conditions={
                    "rules": diff.rules_changed,
                    "require_judge": require_judge,
                },
            )
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
