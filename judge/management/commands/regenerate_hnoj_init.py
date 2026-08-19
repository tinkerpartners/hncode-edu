"""Regenerate init.yml for problems imported from the HNOJ snapshot.

The import copies each problem directory verbatim from the old site, so every init.yml on
disk is still the VNOJ-flavoured one. Regenerating from the converted ProblemData rows is
what makes the checker conversion real, and keeps disk and database in agreement so a later
admin edit of the test data does not silently rewrite a working checker.

Only problems the exporter flagged `data.regenerate_init` are touched. Two groups are
deliberately skipped, because regenerating them would destroy working problems:

  * problems with zero ProblemTestCase rows (24 of them) -- a regenerate would emit an
    empty test_cases list;
  * the one themis-checker problem (colmap), whose protocol has no LQDOJ equivalent.

Every original is backed up next to itself before being replaced, and the command reports
which files actually changed.
"""

import json
import os
from zipfile import BadZipFile, ZipFile

from django.core.management.base import BaseCommand, CommandError

from judge.models import Problem
from judge.models.problem_data import problem_data_storage
from judge.utils.problem_data import ProblemDataCompiler

BACKUP_SUFFIX = ".pre-import"


class Command(BaseCommand):
    help = "Regenerate init.yml for HNOJ-imported problems flagged for regeneration."

    def add_arguments(self, parser):
        parser.add_argument("--problems", required=True, help="path to problems.json")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="report what would change without writing",
        )
        parser.add_argument("--report", help="path to write the JSON result report")

    def handle(self, *args, **options):
        records = json.load(open(options["problems"], encoding="utf-8"))
        dry_run = options["dry_run"]

        report = {
            "dry_run": bool(dry_run),
            "regenerated": [],
            "unchanged": [],
            "skipped_not_flagged": [],
            "skipped_missing_problem": [],
            "failed": [],
        }

        for rec in records:
            code = rec["code"]
            data_rec = rec.get("data")
            if not data_rec or not data_rec.get("regenerate_init"):
                report["skipped_not_flagged"].append(code)
                continue

            problem = Problem.objects.filter(code=code).first()
            if problem is None or not hasattr(problem, "data_files"):
                report["skipped_missing_problem"].append(code)
                continue

            try:
                changed = self._regenerate(problem, dry_run)
            except Exception as exc:  # noqa: BLE001 - recorded per problem
                report["failed"].append({"code": code, "error": repr(exc)})
                self.stderr.write(self.style.ERROR("%s failed: %r" % (code, exc)))
                continue

            (report["regenerated"] if changed else report["unchanged"]).append(code)

        self.stdout.write(
            "regenerated=%d unchanged=%d skipped=%d failed=%d"
            % (
                len(report["regenerated"]),
                len(report["unchanged"]),
                len(report["skipped_not_flagged"]) + len(report["skipped_missing_problem"]),
                len(report["failed"]),
            )
        )
        for failure in report["failed"][:10]:
            self.stdout.write(self.style.ERROR("  %s" % (failure,)))

        if options["report"]:
            with open(options["report"], "w", encoding="utf-8") as fh:
                json.dump(report, fh, ensure_ascii=False, indent=1)
            self.stdout.write("report written to %s" % options["report"])

    def _regenerate(self, problem, dry_run):
        data = problem.data_files
        init_name = "%s/init.yml" % problem.code
        before = b""
        if problem_data_storage.exists(init_name):
            with problem_data_storage.open(init_name, "rb") as fh:
                before = fh.read()

        if dry_run:
            # compile() writes; there is no side-effect-free rendering path, so a dry run
            # reports intent only rather than pretending to diff.
            return bool(before)

        if before:
            backup = init_name + BACKUP_SUFFIX
            if not problem_data_storage.exists(backup):
                problem_data_storage.save(backup, _Bytes(before))

        files = []
        if data.zipfile:
            try:
                files = ZipFile(data.zipfile.path).namelist()
            except (BadZipFile, FileNotFoundError) as exc:
                raise CommandError("unreadable zip for %s: %r" % (problem.code, exc))

        ProblemDataCompiler.generate(
            problem, data, problem.cases.order_by("order"), files
        )

        after = b""
        if problem_data_storage.exists(init_name):
            with problem_data_storage.open(init_name, "rb") as fh:
                after = fh.read()
        return after != before


class _Bytes:
    """Minimal file-like wrapper so Storage.save() can take an in-memory backup."""

    def __init__(self, payload):
        self._payload = payload

    def read(self, *args, **kwargs):
        return self._payload

    def chunks(self):
        yield self._payload

    def __len__(self):
        return len(self._payload)
