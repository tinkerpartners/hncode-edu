"""Regenerate init.yml from the database for problems imported from the HNOJ snapshot.

DO NOT run this expecting it to do much. On the tinhoctre.vn dataset it is almost entirely
a no-op by design, and that is the correct outcome. The reason is worth reading before
touching it.

On the old site, init.yml on disk -- not the ProblemTestCase rows -- is the authoritative
description of a problem's test data. Test archives were re-uploaded over the years without
the case rows being re-synced, so the rows drifted. Measured on the migrated snapshot:
558 of 704 problems have case rows naming files that are not in their own archive, and even
among the ones that do match, the row set is sometimes a strict subset of the archive.

That matters because ProblemDataCompiler.compile() is destructive on failure: it catches
ProblemDataError, writes the message to ProblemData.feedback, and *deletes* init.yml. It
does not raise. A naive regeneration pass over this dataset deletes init.yml for every stale
problem and silently drops test cases from the rest.

So this command validates first and only regenerates a problem when the database can
reproduce the archive exactly:

  * every case's input/output file must exist in the archive, and
  * the number of normal cases must equal the number of input files in the archive,
    so a regeneration cannot quietly shorten the test set.

Anything else is reported and left alone. As a second line of defence each original is
backed up before the compiler runs and restored if the compiler deletes it.
"""

import errno
import json
from zipfile import BadZipFile, ZipFile

from django.core.management.base import BaseCommand

from judge.models import Problem
from judge.models.problem_data import problem_data_storage
from judge.utils.problem_data import ProblemDataCompiler

BACKUP_SUFFIX = ".pre-import"
INPUT_EXTENSIONS = (".in", ".inp", ".txt")


class Command(BaseCommand):
    help = "Regenerate init.yml for HNOJ-imported problems, skipping any that would be damaged."

    def add_arguments(self, parser):
        parser.add_argument("--problems", required=True, help="path to problems.json")
        parser.add_argument(
            "--apply",
            action="store_true",
            help="actually regenerate; without it the command only reports",
        )
        parser.add_argument("--report", help="path to write the JSON result report")

    def handle(self, *args, **options):
        records = json.load(open(options["problems"], encoding="utf-8"))
        apply_changes = options["apply"]

        report = {
            "applied": bool(apply_changes),
            "regenerated": [],
            "unchanged": [],
            "eligible_not_applied": [],
            "skipped_not_flagged": [],
            "skipped_missing_problem": [],
            "skipped_no_archive": [],
            "skipped_stale_cases": [],
            "restored_after_compiler_deleted": [],
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

            data = problem.data_files
            names = self._archive_names(data)
            if names is None:
                report["skipped_no_archive"].append(code)
                continue

            cases = list(problem.cases.order_by("order"))
            reason = self._why_unsafe(cases, names)
            if reason:
                report["skipped_stale_cases"].append({"code": code, "reason": reason})
                continue

            if not apply_changes:
                report["eligible_not_applied"].append(code)
                continue

            changed = self._regenerate(problem, data, cases, sorted(names), report)
            (report["regenerated"] if changed else report["unchanged"]).append(code)

        self._summarise(report)
        if options["report"]:
            with open(options["report"], "w", encoding="utf-8") as fh:
                json.dump(report, fh, ensure_ascii=False, indent=1)
            self.stdout.write("report written to %s" % options["report"])

    def _archive_names(self, data):
        if not data.zipfile:
            return None
        try:
            return set(ZipFile(data.zipfile.path).namelist())
        except (BadZipFile, FileNotFoundError, ValueError):
            return None

    def _why_unsafe(self, cases, names):
        """Return None if regenerating is provably lossless, else why it is not."""
        normal = [case for case in cases if case.type == "C"]
        if not normal:
            return "no normal test cases in the database"

        for case in normal:
            if case.input_file and case.input_file not in names:
                return "case input %r is not in the archive" % case.input_file
            if case.output_file and case.output_file not in names:
                return "case output %r is not in the archive" % case.output_file

        archive_inputs = sum(
            1 for name in names if name.lower().endswith(INPUT_EXTENSIONS)
        )
        if archive_inputs and archive_inputs != len(normal):
            return "archive has %d input files but the database has %d cases" % (
                archive_inputs,
                len(normal),
            )
        return None

    def _regenerate(self, problem, data, cases, files, report):
        init_name = "%s/init.yml" % problem.code
        backup_name = init_name + BACKUP_SUFFIX
        before = b""
        if problem_data_storage.exists(init_name):
            with problem_data_storage.open(init_name, "rb") as fh:
                before = fh.read()
            if not problem_data_storage.exists(backup_name):
                problem_data_storage.save(backup_name, _Bytes(before))

        ProblemDataCompiler.generate(problem, data, cases, files)

        if not problem_data_storage.exists(init_name) and before:
            # compile() swallowed a ProblemDataError and deleted the file. Put it back.
            problem_data_storage.save(init_name, _Bytes(before))
            report["restored_after_compiler_deleted"].append(problem.code)
            return False

        after = b""
        if problem_data_storage.exists(init_name):
            with problem_data_storage.open(init_name, "rb") as fh:
                after = fh.read()
        if after == before:
            self._drop_backup(backup_name)
        return after != before

    def _drop_backup(self, backup_name):
        try:
            problem_data_storage.delete(backup_name)
        except OSError as exc:
            if exc.errno != errno.ENOENT:  # pragma: no cover - defensive
                raise

    def _summarise(self, report):
        for key in (
            "regenerated",
            "unchanged",
            "eligible_not_applied",
            "skipped_stale_cases",
            "skipped_no_archive",
            "skipped_not_flagged",
            "skipped_missing_problem",
            "restored_after_compiler_deleted",
        ):
            self.stdout.write("%-32s %d" % (key, len(report[key])))


class _Bytes:
    """Minimal file-like wrapper so Storage.save() can take an in-memory payload."""

    def __init__(self, payload):
        self._payload = payload

    def read(self, *args, **kwargs):
        return self._payload

    def chunks(self):
        yield self._payload

    def __len__(self):
        return len(self._payload)
