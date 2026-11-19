"""Solution verification against the live judge (spec F05, AC10/AC11).

``verify_solution_proposal`` submits the proposal's reference C++ program as
the standardization bot and records a ``StandardizationVerification``:

* ``CE`` verdict                      -> FAILED (compile)
* ``AC`` on the current test set      -> CURRENT_TESTS_PASSED
* any other verdict                   -> FAILED
* judge timeout / no judge            -> UNKNOWN (never PASS)

``test_config_hash`` pins the test/config version the verdict refers to; a
later auto-accept must compare it with the live hash. The Submission itself is
deleted afterwards unless ``STANDARDIZATION_KEEP_VERIFICATION_SUBMISSIONS``
is true (ids and metrics stay in ``details_json``).
"""

import logging
import re
from hashlib import sha256
from math import isclose

from django.conf import settings
from django.contrib.auth.models import User
from django.db import transaction

from judge.models import Language, Profile, Submission
from judge.models.standardization import StandardizationVerification
from judge.review.submission_runner import (
    JudgeTimeout,
    judge_and_wait,
    _poll_submission,
)
from judge.standardization.enums import FieldGroup, VerificationLevel
from judge.standardization.contracts import read_contract, missing_files
from judge.standardization.hashing import compute_judge_hash, compute_statement_hash

logger = logging.getLogger(__name__)

BOT_USERNAME_DEFAULT = "standardization_bot"
NAMED_FILES = re.compile(r"(?i)\b([a-z0-9_-]+\.(?:inp|out))\b")


class VerificationError(Exception):
    pass


def get_bot_profile():
    username = getattr(settings, "STANDARDIZATION_BOT_USERNAME", BOT_USERNAME_DEFAULT)
    user, created = User.objects.get_or_create(
        username=username,
        defaults={
            "first_name": "Standardization",
            "last_name": "Bot",
            "is_active": False,
        },
    )
    if not created and user.is_active:
        raise VerificationError(
            "The standardization judge needs a dedicated inactive account."
        )
    if user.has_usable_password():
        user.set_unusable_password()
        user.save(update_fields=["password"])
    defaults = {}
    language = Language.objects.filter(key="CPP17").first() or Language.objects.first()
    if language is not None:
        defaults["language"] = language
    profile, _ = Profile.objects.get_or_create(user=user, defaults=defaults)
    return profile


def _language_for(proposal):
    key = (proposal.payload_json or {}).get("language_key") or getattr(
        settings, "STANDARDIZATION_VERIFY_LANGUAGE", "CPP17"
    )
    language = Language.objects.filter(key=key).first()
    if language is None:
        raise VerificationError(f"language {key} is not installed")
    return language


def _record(
    proposal,
    level,
    *,
    submission=None,
    metrics=None,
    verdict="",
    details=None,
    judge_name="",
    test_config_hash="",
):
    metrics = metrics or {}
    return StandardizationVerification.objects.create(
        proposal=proposal,
        level=level,
        submission=submission,
        verdict=verdict[:8],
        points=metrics.get("points"),
        time=metrics.get("time"),
        memory=metrics.get("memory"),
        test_config_hash=test_config_hash,
        judge_name=judge_name[:64],
        details_json=details or {},
    )


def has_full_score(submission):
    return (
        submission.status == "D"
        and submission.result == "AC"
        and not submission.is_pretested
        and (submission.case_total or 0) > 0
        and isclose(
            submission.case_points or 0, submission.case_total, rel_tol=0, abs_tol=1e-7
        )
    )


def missing_named_files(statement, code):
    files = set(NAMED_FILES.findall(statement or ""))
    if len(files) != 2 or {name[-3:].lower() for name in files} != {"inp", "out"}:
        return []
    return sorted(
        name
        for name in files
        if not re.search(rf"""["'](?:\./)?{re.escape(name)}["']""", code)
    )


def check_candidate(
    problem,
    language,
    source,
    *,
    timeout_seconds=None,
    keep_submission=False,
    reuse_submission_id=None,
    preflight_file_io=True,
):
    """Return judge evidence bound to the configuration used for this run."""
    problem.refresh_from_db()
    version = compute_judge_hash(problem, language)
    identity = {
        "source_hash": sha256(source.encode()).hexdigest(),
        "judge_hash": version,
    }
    contract = read_contract(problem, problem.description or "")
    if contract.issues:
        return (
            VerificationLevel.UNKNOWN,
            {
                **identity,
                "reason": "grading_contract_conflict",
                "issues": contract.issues,
            },
            {"test_config_hash": version},
        )
    if contract.kind in ("function", "interactive", "output_only"):
        return (
            VerificationLevel.UNKNOWN,
            {**identity, "reason": "special_judge_requires_review"},
            {"test_config_hash": version},
        )
    if preflight_file_io and language.key in ("CPP17", "CPP20"):
        missing = missing_files(contract, source)
        if missing:
            return (
                VerificationLevel.FAILED,
                {
                    **identity,
                    "reason": "io_mismatch",
                    "missing_files": missing,
                    "grading_contract": contract.as_json(),
                },
                {"test_config_hash": version},
            )
    profile = get_bot_profile()
    judge_id = getattr(settings, "STANDARDIZATION_JUDGE_ID", None)
    timeout_seconds = timeout_seconds or getattr(
        settings, "STANDARDIZATION_JUDGE_TIMEOUT", 90
    )
    try:
        if reuse_submission_id:
            previous = Submission.objects.filter(
                pk=reuse_submission_id,
                problem=problem,
                language=language,
                user=profile,
                source__source=source,
            ).first()
            if not previous:
                return (
                    VerificationLevel.UNKNOWN,
                    {**identity, "reason": "pending_submission_missing"},
                    {"test_config_hash": version},
                )
            submission = (
                _poll_submission(previous.pk, timeout_seconds)
                if previous.status in Submission.IN_PROGRESS_GRADING_STATUS
                else previous
            )
        else:
            submission = judge_and_wait(
                problem,
                language,
                source,
                profile,
                timeout_seconds=timeout_seconds,
                judge_id=judge_id,
            )
    except JudgeTimeout as exc:
        return (
            VerificationLevel.UNKNOWN,
            {
                "reason": "timeout",
                "submission_id": exc.submission_id,
                "kept_submission": bool(exc.submission_id),
                "judge_hash": version,
                "source_hash": sha256(source.encode()).hexdigest(),
            },
            {"test_config_hash": version},
        )
    except Exception:
        logger.exception("Standardization judge failed for problem %s", problem.pk)
        return (
            VerificationLevel.UNKNOWN,
            {**identity, "reason": "judge_error"},
            {"test_config_hash": version},
        )

    try:
        problem.refresh_from_db()
        unchanged = version == compute_judge_hash(problem, language)
        verdict = submission.result or ""
        details = {
            "submission_id": submission.pk,
            "kept_submission": bool(keep_submission),
            "verdict": verdict,
            "case_points": submission.case_points,
            "case_total": submission.case_total,
            "language": language.key,
            "judge_hash": version,
            "source_hash": sha256(source.encode()).hexdigest(),
            "reason": "",
        }
        if not unchanged:
            level, details["reason"] = VerificationLevel.UNKNOWN, "tests_changed"
        elif has_full_score(submission):
            level, details["reason"] = (
                VerificationLevel.CURRENT_TESTS_PASSED,
                "full_score",
            )
        elif verdict in ("", "IE", "AB") or (
            verdict == "AC" and not submission.case_total
        ):
            level, details["reason"] = VerificationLevel.UNKNOWN, "judge_unavailable"
        else:
            level, details["reason"] = VerificationLevel.FAILED, (
                "partial_score" if verdict == "AC" else verdict
            )
            if verdict == "CE":
                details["compile_error"] = (submission.error or "")[:1200]
            else:
                first_failure = (
                    submission.test_cases.exclude(status="AC")
                    .order_by("case")
                    .values("case", "status")
                    .first()
                )
                if first_failure:
                    details["first_failure"] = first_failure
        fields = {
            "submission": submission if keep_submission else None,
            "metrics": {
                "points": submission.points,
                "time": submission.time,
                "memory": submission.memory,
            },
            "verdict": verdict,
            "judge_name": submission.judged_on.name if submission.judged_on_id else "",
            "test_config_hash": version,
        }
        return level, details, fields
    finally:
        if not keep_submission:
            Submission.objects.filter(pk=submission.pk).delete()


def verify_solution_proposal(
    proposal, *, timeout_seconds=None, keep_submission=None, reuse_only=False
):
    """Judge the proposal's reference code; return the verification row."""
    if proposal.field_group != FieldGroup.SOLUTION:
        raise VerificationError("only SOLUTION proposals are verified by judging")
    source = (proposal.payload_json or {}).get("cpp_code") or ""
    if not source.strip():
        return record_candidate(
            proposal, VerificationLevel.UNKNOWN, {"reason": "missing_code"}, {}
        )
    evidence = proposal.evidence_json or {}
    if evidence.get("guided"):
        if evidence.get("statement_hash") and evidence[
            "statement_hash"
        ] != compute_statement_hash(proposal.problem):
            return record_candidate(
                proposal, VerificationLevel.UNKNOWN, {"reason": "source_changed"}, {}
            )
    if keep_submission is None:
        keep_submission = getattr(
            settings, "STANDARDIZATION_KEEP_VERIFICATION_SUBMISSIONS", False
        )
    language = _language_for(proposal)
    latest = proposal.verifications.order_by("-created_at", "-pk").first()
    pending = (
        latest.details_json
        if latest and latest.level == VerificationLevel.UNKNOWN
        else {}
    )
    reuse = None
    if (
        pending.get("reason") == "timeout"
        and pending.get("submission_id")
        and pending.get("judge_hash") == compute_judge_hash(proposal.problem, language)
        and pending.get("source_hash") == sha256(source.encode()).hexdigest()
    ):
        reuse = pending["submission_id"]
    if reuse_only and not reuse:
        return record_candidate(
            proposal,
            VerificationLevel.UNKNOWN,
            {"reason": "pending_evidence_changed"},
            {},
        )
    level, details, fields = check_candidate(
        proposal.problem,
        language,
        source,
        timeout_seconds=timeout_seconds,
        keep_submission=keep_submission,
        **({"preflight_file_io": False} if evidence.get("reference") else {}),
        **({"reuse_submission_id": reuse} if reuse else {}),
    )
    return record_candidate(proposal, level, details, fields)


def current_full_verification(proposal, problem=None):
    row = proposal.verifications.order_by("-created_at", "-pk").first()
    if not row or row.level not in (
        VerificationLevel.CURRENT_TESTS_PASSED,
        VerificationLevel.ADDITIONAL_CHECKS_PASSED,
    ):
        return False
    details = row.details_json or {}
    source = (proposal.payload_json or {}).get("cpp_code") or ""
    if not source or details.get("reason") != "full_score":
        return False
    if details.get("source_hash") != sha256(source.encode()).hexdigest():
        return False
    problem = problem or proposal.problem
    contract = read_contract(problem, problem.description or "")
    if contract.issues or contract.kind in ("function", "interactive", "output_only"):
        return False
    return row.test_config_hash == compute_judge_hash(problem, _language_for(proposal))


def record_candidate(proposal, level, details, fields):
    with transaction.atomic():
        proposal = type(proposal).objects.select_for_update().get(pk=proposal.pk)
        row = _record(proposal, level, details=details, **fields)
        evidence = dict(proposal.evidence_json or {})
        evidence["verification"] = level
        evidence["verification_id"] = row.pk
        proposal.evidence_json = evidence
        proposal.save(update_fields=["evidence_json"])
    from judge.standardization.reconciliation import reconcile_verification

    reconcile_verification(row)
    if level == VerificationLevel.FAILED and (proposal.evidence_json or {}).get(
        "guided"
    ):
        from judge.tasks.standardization import resume_guided_verification

        transaction.on_commit(lambda: resume_guided_verification.delay(row.pk))
    if details.get("reason") == "timeout" and (proposal.evidence_json or {}).get(
        "guided"
    ):
        from judge.tasks.standardization import poll_guided_verification

        transaction.on_commit(
            lambda: poll_guided_verification.apply_async(args=[row.pk], countdown=30)
        )
    return row


def latest_level(proposal):
    row = proposal.verifications.order_by("-created_at", "-pk").first()
    return row.level if row else VerificationLevel.UNVERIFIED
