"""Import a problem + contest snapshot exported from the old HNOJ (VNOJ-fork) tinhoctre.vn.

The old site (AWS EC2, github.com/hnojedu/HNOJ, judge migration leaf 0179) and this codebase
(LQDOJ fork, leaf 0265) diverged around judge.0150 and share no migration lineage, so a
database restore is impossible. This command consumes the JSON produced by the workspace
exporter (tinhoctre/migration/export/) and rebuilds the content through the ORM.

Scope is deliberately content-only: problems and contests. Users, submissions, contest
participations, organizations and ratings are NOT imported, so authorship collapses onto a
single placeholder profile and organization-privacy cannot be expressed.

Idempotent: keyed on Problem.code / Contest.key, so a failed run is simply re-runnable and a
later delta export reuses the same command.
"""

import json
from datetime import timedelta, timezone as dt_timezone

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils.dateparse import parse_datetime

from judge.models import (
    Contest,
    ContestProblem,
    Language,
    Problem,
    ProblemData,
    ProblemGroup,
    ProblemTestCase,
    ProblemType,
    Profile,
)

# Scalar Problem fields copied straight across.
PROBLEM_SCALARS = (
    "name",
    "description",
    "summary",
    "points",
    "partial",
    "time_limit",
    "memory_limit",
    "short_circuit",
    "is_public",
    "is_organization_private",
    "is_manually_managed",
    "og_image",
)

CONTEST_SCALARS = (
    "name",
    "description",
    "summary",
    "format_name",
    "is_visible",
    "is_private",
    "is_organization_private",
    "is_rated",
    "rating_floor",
    "rating_ceiling",
    "rate_all",
    "run_pretests_only",
    "scoreboard_visibility",
    "use_clarifications",
    "points_precision",
    "problem_label_script",
    "access_code",
    "hide_problem_tags",
    "og_image",
    "logo_override_image",
)

# ProblemData FileFields whose target file is already on disk under DMOJ_PROBLEM_DATA_ROOT.
# Assigning .name records the path without re-uploading anything.
DATA_FILE_FIELDS = (
    "zipfile",
    "generator",
    "custom_checker",
    "custom_checker_cpp",
    "interactive_judge",
)


def _dt(value):
    """Parse an exported naive datetime string. The source DB runs USE_TZ, so it is UTC."""
    if not value:
        return None
    parsed = parse_datetime(value)
    if parsed is None:
        raise CommandError("unparseable datetime %r" % (value,))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt_timezone.utc)
    return parsed


class Command(BaseCommand):
    help = "Import problems and contests from an HNOJ snapshot export."

    def add_arguments(self, parser):
        parser.add_argument("--problems", required=True, help="path to problems.json")
        parser.add_argument("--contests", required=True, help="path to contests.json")
        parser.add_argument(
            "--author",
            required=True,
            help="username that becomes the placeholder author of every imported object",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="validate and report, roll back without writing",
        )
        parser.add_argument(
            "--limit", type=int, default=0, help="only process the first N of each (0 = all)"
        )
        parser.add_argument("--report", help="path to write the JSON result report")

    def handle(self, *args, **options):
        problems = json.load(open(options["problems"], encoding="utf-8"))
        contests = json.load(open(options["contests"], encoding="utf-8"))
        if options["limit"]:
            problems = problems[: options["limit"]]
            contests = contests[: options["limit"]]

        try:
            author = Profile.objects.get(user__username=options["author"])
        except Profile.DoesNotExist:
            raise CommandError("no profile for username %r" % options["author"])

        self._preflight_languages(problems)

        report = {
            "dry_run": bool(options["dry_run"]),
            "author": options["author"],
            "problems": {"created": [], "updated": [], "failed": []},
            "contests": {"created": [], "updated": [], "failed": []},
            "counts": {},
        }

        # One outer transaction so --dry-run can roll the whole thing back, and so a crash
        # mid-run cannot leave contests referencing half-imported problems.
        try:
            with transaction.atomic():
                for rec in problems:
                    self._import_problem(rec, author, report)
                for rec in contests:
                    self._import_contest(rec, author, report)

                report["counts"] = {
                    "problems_total": Problem.objects.count(),
                    "contests_total": Contest.objects.count(),
                    "contest_problems_total": ContestProblem.objects.count(),
                    "testcases_total": ProblemTestCase.objects.count(),
                }
                if options["dry_run"]:
                    raise _Rollback()
        except _Rollback:
            self.stdout.write(self.style.WARNING("dry run: rolled back, nothing written"))

        self._summarise(report)
        if options["report"]:
            with open(options["report"], "w", encoding="utf-8") as fh:
                json.dump(report, fh, ensure_ascii=False, indent=1)
            self.stdout.write("report written to %s" % options["report"])

    # ------------------------------------------------------------------ preflight

    def _preflight_languages(self, problems):
        """Abort before writing anything if the snapshot references a missing Language.

        Importing with a language silently absent would leave problems submittable in fewer
        languages than intended, with nothing in the logs to say so.
        """
        wanted = set()
        for rec in problems:
            wanted.update(rec["allowed_languages"])
        have = set(Language.objects.values_list("key", flat=True))
        missing = sorted(wanted - have)
        if missing:
            raise CommandError(
                "these languages are referenced by the snapshot but do not exist on this "
                "site: %s -- create them first" % ", ".join(missing)
            )

    # ------------------------------------------------------------------ problems

    def _import_problem(self, rec, author, report):
        code = rec["code"]
        try:
            with transaction.atomic():
                existed = Problem.objects.filter(code=code).exists()
                problem = Problem.objects.filter(code=code).first() or Problem(code=code)

                for field in PROBLEM_SCALARS:
                    setattr(problem, field, rec[field])
                problem.date = _dt(rec["date"])
                problem.group = self._group(rec["group"])
                if rec["pdf"]:
                    problem.pdf_description.name = rec["pdf"]["dest_rel"]
                # Problem.save() clamps points to 1 on any non-public or org-private problem.
                # 671 of the 704 imported problems are non-public, so without this every
                # imported point value would silently collapse to 1.
                problem._bypass_points_cap = True
                problem.save()

                problem.types.set([self._type(name) for name in rec["types"]])
                problem.allowed_languages.set(
                    Language.objects.filter(key__in=rec["allowed_languages"])
                )
                problem.authors.set([author])

                if rec["data"]:
                    self._import_problem_data(problem, rec)

            report["problems"]["updated" if existed else "created"].append(code)
        except Exception as exc:  # noqa: BLE001 - recorded per object, surfaced in the report
            report["problems"]["failed"].append({"code": code, "error": repr(exc)})
            self.stderr.write(self.style.ERROR("problem %s failed: %r" % (code, exc)))

    def _import_problem_data(self, problem, rec):
        src = rec["data"]
        data = ProblemData.objects.filter(problem=problem).first() or ProblemData(
            problem=problem
        )
        data.checker = src["checker"]
        data.checker_args = src["checker_args"]
        data.output_limit = src["output_limit"]
        data.output_prefix = src["output_prefix"]
        data.feedback = src["feedback"]
        data.output_only = src["output_only"]
        for field in DATA_FILE_FIELDS:
            getattr(data, field).name = src.get(field) or None
        data.save()

        ProblemTestCase.objects.filter(dataset=problem).delete()
        ProblemTestCase.objects.bulk_create(
            [
                ProblemTestCase(
                    dataset=problem,
                    order=case["order"],
                    type=case["type"],
                    input_file=case["input_file"],
                    output_file=case["output_file"],
                    generator_args=case["generator_args"],
                    points=case["points"],
                    is_pretest=case["is_pretest"],
                    output_limit=case["output_limit"],
                    output_prefix=case["output_prefix"],
                )
                for case in rec["testcases"]
            ]
        )

    def _group(self, name):
        if not name:
            return None
        group, _created = ProblemGroup.objects.get_or_create(
            name=name, defaults={"full_name": name}
        )
        return group

    def _type(self, name):
        ptype, _created = ProblemType.objects.get_or_create(
            name=name, defaults={"full_name": name}
        )
        return ptype

    # ------------------------------------------------------------------ contests

    def _import_contest(self, rec, author, report):
        key = rec["key"]
        try:
            with transaction.atomic():
                existed = Contest.objects.filter(key=key).exists()
                contest = Contest.objects.filter(key=key).first() or Contest(key=key)

                for field in CONTEST_SCALARS:
                    setattr(contest, field, rec[field])
                contest.start_time = _dt(rec["start_time"])
                contest.end_time = _dt(rec["end_time"])
                # Source stores DurationField as microseconds.
                contest.time_limit = (
                    timedelta(microseconds=rec["time_limit"])
                    if rec["time_limit"] is not None
                    else None
                )
                if rec["format_config"] is not None:
                    contest.format_config = rec["format_config"]
                contest.save()

                contest.authors.set([author])

                ContestProblem.objects.filter(contest=contest).delete()
                links = []
                for entry in rec["problems"]:
                    problem = Problem.objects.filter(code=entry["code"]).first()
                    if problem is None:
                        report["contests"]["failed"].append(
                            {
                                "key": key,
                                "error": "contest problem %r not imported" % entry["code"],
                            }
                        )
                        continue
                    links.append(
                        ContestProblem(
                            contest=contest,
                            problem=problem,
                            points=entry["points"],
                            partial=entry["partial"],
                            is_pretested=entry["is_pretested"],
                            max_submissions=entry["max_submissions"],
                            order=entry["order"],
                        )
                    )
                ContestProblem.objects.bulk_create(links)

            report["contests"]["updated" if existed else "created"].append(key)
        except Exception as exc:  # noqa: BLE001
            report["contests"]["failed"].append({"key": key, "error": repr(exc)})
            self.stderr.write(self.style.ERROR("contest %s failed: %r" % (key, exc)))

    # ------------------------------------------------------------------ output

    def _summarise(self, report):
        for kind in ("problems", "contests"):
            section = report[kind]
            self.stdout.write(
                "%-9s created=%d updated=%d failed=%d"
                % (
                    kind,
                    len(section["created"]),
                    len(section["updated"]),
                    len(section["failed"]),
                )
            )
            for failure in section["failed"][:10]:
                self.stdout.write(self.style.ERROR("  %s" % (failure,)))
        if report["counts"]:
            self.stdout.write("db totals: %s" % (report["counts"],))


class _Rollback(Exception):
    """Internal signal used to unwind the outer transaction on --dry-run."""
