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

from django.conf import settings
from django.contrib.auth.models import User
from django.db import transaction

from judge.models import Language, Profile, Submission
from judge.models.standardization import StandardizationVerification
from judge.review.hashing import compute_input_hash
from judge.review.submission_runner import JudgeTimeout, judge_and_wait
from judge.standardization.enums import FieldGroup, VerificationLevel

logger = logging.getLogger(__name__)

BOT_USERNAME_DEFAULT = "standardization_bot"


class VerificationError(Exception):
    pass


def get_bot_profile():
    username = getattr(settings, "STANDARDIZATION_BOT_USERNAME", BOT_USERNAME_DEFAULT)
    user, _ = User.objects.get_or_create(
        username=username,
        defaults={
            "first_name": "Standardization",
            "last_name": "Bot",
            "is_active": False,
        },
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
        test_config_hash=compute_input_hash(proposal.problem),
        judge_name=judge_name[:64],
        details_json=details or {},
    )


def verify_solution_proposal(proposal, *, timeout_seconds=None, keep_submission=None):
    """Judge the proposal's reference code; return the verification row."""
    if proposal.field_group != FieldGroup.SOLUTION:
        raise VerificationError("only SOLUTION proposals are verified by judging")
    source = (proposal.payload_json or {}).get("cpp_code") or ""
    if not source.strip():
        return _record(
            proposal, VerificationLevel.UNKNOWN, details={"reason": "no reference code"}
        )
    if keep_submission is None:
        keep_submission = getattr(
            settings, "STANDARDIZATION_KEEP_VERIFICATION_SUBMISSIONS", False
        )
    language = _language_for(proposal)
    profile = get_bot_profile()
    judge_id = getattr(settings, "STANDARDIZATION_JUDGE_ID", None)
    try:
        submission = judge_and_wait(
            proposal.problem,
            language,
            source,
            profile,
            timeout_seconds=timeout_seconds,
            judge_id=judge_id,
        )
    except JudgeTimeout as exc:
        logger.warning("verification timed out for proposal %s", proposal.pk)
        return _record(
            proposal,
            VerificationLevel.UNKNOWN,
            details={"reason": "timeout", "detail": str(exc)[:200]},
        )
    except Exception as exc:  # noqa: BLE001 - bridge down, no judge, etc.
        logger.exception("verification failed to run for proposal %s", proposal.pk)
        return _record(
            proposal,
            VerificationLevel.UNKNOWN,
            details={
                "reason": "judge_error",
                "detail": f"{type(exc).__name__}: {exc}"[:200],
            },
        )

    verdict = submission.result or ""
    judge_name = submission.judged_on.name if submission.judged_on_id else ""
    details = {
        "submission_id": submission.id,
        "verdict": verdict,
        "case_points": submission.case_points,
        "case_total": submission.case_total,
        "language": language.key,
        "kept_submission": bool(keep_submission),
    }
    if verdict == "CE":
        level = VerificationLevel.FAILED
        details["compile_error"] = (submission.error or "")[:2000]
    elif verdict == "AC":
        level = VerificationLevel.CURRENT_TESTS_PASSED
    elif verdict in ("", "IE", "AB"):
        level = VerificationLevel.UNKNOWN
    else:
        level = VerificationLevel.FAILED
    with transaction.atomic():
        row = _record(
            proposal,
            level,
            submission=submission if keep_submission else None,
            metrics={
                "points": submission.points,
                "time": submission.time,
                "memory": submission.memory,
            },
            verdict=verdict,
            details=details,
            judge_name=judge_name,
        )
        evidence = dict(proposal.evidence_json or {})
        evidence["verification"] = level
        evidence["verification_id"] = row.pk
        proposal.evidence_json = evidence
        proposal.save(update_fields=["evidence_json"])
    if not keep_submission:
        Submission.objects.filter(pk=submission.pk).delete()
    return row


def latest_level(proposal):
    row = proposal.verifications.order_by("-created_at").first()
    return row.level if row else VerificationLevel.UNVERIFIED
