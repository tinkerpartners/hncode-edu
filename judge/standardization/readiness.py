"""One conservative source gate for guided drafting and unattended application.

This validates availability/consistency, not the mathematical quality of tests.
It reads metadata and archive directories only; hidden case contents never enter
an AI prompt. All failures are closed and explainable.
"""

import re
from math import isfinite
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from zipfile import ZipFile


from judge.models import Language
from judge.models.problem_data import CHECKERS, problem_data_storage
from judge.models.standardization import (
    StandardizationDisposition as Route,
    StandardizationProposal,
)
from judge.standardization.classify import classify_problem, visible_text
from judge.standardization.contracts import read_contract, grading_manifest
from judge.standardization.hashing import compute_judge_hash, compute_statement_hash

SOURCE_FLAGS = {
    "unreadable",
    "ocr_uncertain",
    "contradiction",
    "broken_asset",
    "needs_expert",
}
TITLE_ONLY = re.compile(r"^(?:bài\s*\d+\s*[:.]\s*|đề bài\s*[:.]\s*)", re.I)


@dataclass
class Readiness:
    fingerprint: str
    classification: object
    contract: object
    issues: list = field(default_factory=list)
    reused_parking: bool = False

    @property
    def ready(self):
        return not self.issues

    @property
    def reason(self):
        return self.issues[0] if self.issues else ""

    def as_json(self):
        return {
            "ready": self.ready,
            "fingerprint": self.fingerprint,
            "issues": self.issues,
            "contract": self.contract.as_json(),
            "reused_parking": self.reused_parking,
        }


def fingerprint(problem):
    # Tags, rating, authors and editorial edits must not retry a missing-source
    # task. Judge identity covers the statement, limits and actual data files.
    language = Language.objects.filter(key="CPP17").first() or Language.objects.first()
    if language is None:
        raise ValueError("No verification language installed")
    return compute_judge_hash(problem, language)


def _safe_path(name):
    return (
        isinstance(name, str)
        and bool(name)
        and "\\" not in name
        and not PurePosixPath(name).is_absolute()
        and all(p not in ("..", ".") for p in name.split("/"))
    )


def test_data_issues(problem):
    """Check judge configuration and archive membership. Execution checks payloads."""
    try:
        init, issue = grading_manifest(problem)
        if issue:
            return [issue]
        if init is None:
            return ["test_manifest_missing"]
        cases = init.get("test_cases")
        if not isinstance(cases, list) or not cases:
            return ["full_tests_missing"]
        members = None
        archive = init.get("archive")
        if archive:
            if not _safe_path(archive):
                return ["unsafe_test_path"]
            with problem_data_storage.open(
                f"{problem.code}/{archive}", "rb"
            ) as stream, ZipFile(stream) as zipped:
                members = {x.filename for x in zipped.infolist() if not x.is_dir()}

        def exists(path, archived=False):
            if not _safe_path(path):
                return False
            if archived and members is not None:
                return path in members
            return problem_data_storage.exists(f"{problem.code}/{path}")

        def files(value):
            if isinstance(value, str):
                return exists(value)
            return (
                isinstance(value, list)
                and bool(value)
                and all(exists(p) for p in value)
            )

        generator = init.get("generator")
        if generator and not files(generator):
            return ["generator_missing"]

        def checker_ok(checker):
            if checker is None:
                return True
            if isinstance(checker, str):
                return (
                    exists(checker)
                    if checker.endswith(".py")
                    else checker in {key for key, _ in CHECKERS}
                )
            if not isinstance(checker, dict):
                return False
            args = checker.get("args") or {}
            if not isinstance(args, dict):
                return False
            if "files" in args and not files(args["files"]):
                return False
            if checker.get("name") in (
                "custom",
                "customcpp",
                "testlib",
                "testlibcms",
                "bridged",
            ):
                return bool(args.get("files"))
            return checker.get("name") in {key for key, _ in CHECKERS}

        if not checker_ok(init.get("checker")):
            return ["checker_missing_or_invalid"]
        count, score = 0, 0

        def visit(items, depth=0):
            nonlocal count, score
            if depth > 8 or not isinstance(items, list) or not items:
                raise ValueError("invalid_test_cases")
            for case in items:
                if not isinstance(case, dict):
                    raise ValueError("invalid_test_cases")
                if not checker_ok(case.get("checker")):
                    raise ValueError("checker_missing_or_invalid")
                points = case.get("points", 1)
                if (
                    type(points) not in (int, float)
                    or not isfinite(points)
                    or points < 0
                ):
                    raise ValueError("invalid_test_score")
                if "batched" in case:
                    before = score
                    visit(case["batched"], depth + 1)
                    score = before + points
                else:
                    generated = case.get("generator") or generator
                    if case.get("generator") and not files(case["generator"]):
                        raise ValueError("generator_missing")
                    if not generated and not all(
                        exists(case.get(key), True) for key in ("in", "out")
                    ):
                        raise ValueError("test_files_missing")
                    count += 1
                    score += points

        visit(cases)
        return [] if count and score > 0 else ["full_test_score_missing"]
    except ValueError as exc:
        return [
            (
                str(exc)
                if str(exc)
                in {
                    "invalid_test_cases",
                    "checker_missing_or_invalid",
                    "invalid_test_score",
                    "generator_missing",
                    "test_files_missing",
                }
                else "test_data_unreadable"
            )
        ]
    except Exception:  # noqa: BLE001 - unreadable storage must fail closed
        # Storage backends and bad ZIPs have different exception types. Do not
        # leak paths or treat a failed read as proof of valid tests.
        return ["test_data_unreadable"]


def source_readiness(
    problem, *, classification=None, require_tests=True, use_parking=True
):
    classification = classification or classify_problem(problem)
    contract = read_contract(problem, classification.extracted_text)
    report = Readiness(fingerprint(problem), classification, contract)
    plain = visible_text(classification.extracted_text).strip("# -_\n")
    if (
        classification.needs_source
        or not TITLE_ONLY.sub("", plain).strip()
        or (TITLE_ONLY.match(plain) and len(plain.split()) <= 10)
        or plain.casefold() == visible_text(problem.name).strip().casefold()
    ):
        report.issues.append("statement_missing")
    report.issues.extend(sorted(set(classification.quality_flags) & SOURCE_FLAGS))
    if contract.issues:
        report.issues.append("grading_contract_conflict")
    if contract.kind in ("function", "interactive", "output_only"):
        report.issues.append("special_judge_requires_review")
    if require_tests and not report.issues:
        report.issues.extend(test_data_issues(problem))
    if use_parking and not report.issues:
        latest = (
            StandardizationProposal.objects.filter(
                problem=problem, field_group="solution"
            )
            .order_by("-created_at", "-pk")
            .first()
        )
        evidence = latest.evidence_json if latest else {}
        if evidence.get("needs_clarification") is True and evidence.get(
            "statement_hash"
        ) == compute_statement_hash(problem):
            report.issues.append("source_clarification")
        if latest:
            from judge.standardization.independent_review import context

            review = (
                latest.verifications.filter(
                    details_json__kind="independent_reference_review"
                )
                .order_by("-pk")
                .first()
            )
            if (
                review
                and review.details_json.get("identity") == context(latest)
                and any(
                    item.get("group") == "source"
                    for item in review.details_json.get("result", {}).get("issues", [])
                )
            ):
                report.issues.append("source_clarification")
    if use_parking and not report.issues:
        parked = Route.objects.filter(
            problem=problem,
            field_group="",
            fingerprint=report.fingerprint,
            state=Route.State.DEFERRED_SOURCE,
        ).first()
        if parked:
            report.issues = [parked.reason_code]
            report.reused_parking = True
    return report


def save_readiness(problem, report, batch=None):
    from judge.standardization.routing import issue_category

    previous = Route.objects.filter(
        problem=problem,
        field_group="",
        fingerprint=report.fingerprint,
        state__in=(Route.State.AUTO_DONE, Route.State.AUTO_PARTIAL),
    ).first()
    state = (
        previous.state
        if report.ready and previous
        else Route.State.READY if report.ready else Route.State.DEFERRED_SOURCE
    )
    return Route.objects.update_or_create(
        problem=problem,
        field_group="",
        defaults={
            "state": state,
            "reason_code": report.reason,
            "fingerprint": report.fingerprint,
            "details_json": {
                **report.as_json(),
                "category": issue_category(report.reason) if report.reason else "",
            },
            "batch": batch,
        },
    )[0]
