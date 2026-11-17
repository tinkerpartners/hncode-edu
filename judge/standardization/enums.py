from django.db import models
from django.utils.translation import gettext_lazy as _


class FieldGroup(models.TextChoices):
    """The independently reviewable parts of a problem (spec §3, F10)."""

    CONTENT = "content", _("Statement")
    SOLUTION = "solution", _("Solution")
    TAGS = "tags", _("Tags")
    POINTS = "points", _("Points")
    CONFIG = "config", _("Configuration")


class BatchKind(models.TextChoices):
    MECHANICAL_CONFIG = "mechanical_config", _("Mechanical configuration")
    HEADING_FIX = "heading_fix", _("Heading fix")
    AI_PIPELINE = "ai_pipeline", _("AI pipeline")
    POINTS_WAVE = "points_wave", _("Points wave")


class BatchStatus(models.TextChoices):
    QUEUED = "queued", _("Queued")
    RUNNING = "running", _("Running")
    PAUSING = "pausing", _("Pausing")
    PAUSED = "paused", _("Paused")
    CANCEL_REQUESTED = "cancel_requested", _("Cancel requested")
    CANCELLED = "cancelled", _("Cancelled")
    FINISHED = "finished", _("Finished")
    FAILED = "failed", _("Failed")


class ProposalStatus(models.TextChoices):
    DRAFT = "draft", _("Draft")
    NEEDS_SOURCE = "needs_source", _("Needs source")
    NEEDS_CLARIFICATION = "needs_clarification", _("Needs clarification")
    PENDING_REVIEW = "pending_review", _("Pending review")
    APPROVED = "approved", _("Approved")
    AUTO_ACCEPTED = "auto_accepted", _("Auto accepted")
    APPLIED = "applied", _("Applied")
    RESTORED = "restored", _("Restored")
    REJECTED = "rejected", _("Rejected")
    REWORK = "rework", _("Rework")
    ERROR = "error", _("Error")
    STALE = "stale", _("Stale")
    BLOCKED_CONTEST = "blocked_contest", _("Blocked by contest")
    UNKNOWN = "unknown", _("Unknown")


# Allowed status transitions. Anything not listed is refused by
# ``judge.standardization.apply.transition``.
PROPOSAL_TRANSITIONS = {
    ProposalStatus.DRAFT: {
        ProposalStatus.NEEDS_SOURCE,
        ProposalStatus.NEEDS_CLARIFICATION,
        ProposalStatus.PENDING_REVIEW,
        ProposalStatus.AUTO_ACCEPTED,
        ProposalStatus.ERROR,
        ProposalStatus.STALE,
        ProposalStatus.UNKNOWN,
    },
    ProposalStatus.NEEDS_SOURCE: {ProposalStatus.PENDING_REVIEW, ProposalStatus.STALE},
    ProposalStatus.NEEDS_CLARIFICATION: {
        ProposalStatus.PENDING_REVIEW,
        ProposalStatus.REJECTED,
        ProposalStatus.STALE,
    },
    ProposalStatus.PENDING_REVIEW: {
        ProposalStatus.APPROVED,
        ProposalStatus.AUTO_ACCEPTED,
        ProposalStatus.REJECTED,
        ProposalStatus.REWORK,
        ProposalStatus.STALE,
    },
    ProposalStatus.APPROVED: {
        ProposalStatus.APPLIED,
        ProposalStatus.STALE,
        ProposalStatus.BLOCKED_CONTEST,
        ProposalStatus.REJECTED,
        ProposalStatus.ERROR,
    },
    ProposalStatus.AUTO_ACCEPTED: {
        ProposalStatus.APPLIED,
        ProposalStatus.STALE,
        ProposalStatus.BLOCKED_CONTEST,
        ProposalStatus.ERROR,
    },
    ProposalStatus.BLOCKED_CONTEST: {
        ProposalStatus.APPROVED,
        ProposalStatus.AUTO_ACCEPTED,
        ProposalStatus.STALE,
    },
    ProposalStatus.APPLIED: {ProposalStatus.RESTORED},
    ProposalStatus.REWORK: {ProposalStatus.PENDING_REVIEW, ProposalStatus.REJECTED},
    ProposalStatus.RESTORED: set(),
    ProposalStatus.REJECTED: set(),
    ProposalStatus.ERROR: {ProposalStatus.PENDING_REVIEW},
    ProposalStatus.STALE: set(),
    ProposalStatus.UNKNOWN: {ProposalStatus.ERROR, ProposalStatus.PENDING_REVIEW},
}

APPLICABLE_STATUSES = (ProposalStatus.APPROVED, ProposalStatus.AUTO_ACCEPTED)


class VerificationLevel(models.TextChoices):
    UNVERIFIED = "unverified", _("Unverified")
    COMPILED = "compiled", _("Compiled")
    SAMPLE_PASSED = "sample_passed", _("Samples passed")
    CURRENT_TESTS_PASSED = "current_tests_passed", _("Current tests passed")
    ADDITIONAL_CHECKS_PASSED = "additional_checks_passed", _("Additional checks passed")
    FAILED = "failed", _("Failed")
    UNKNOWN = "unknown", _("Unknown")


class DecisionAction(models.TextChoices):
    APPROVE = "approve", _("Approve")
    REJECT = "reject", _("Reject")
    REWORK = "rework", _("Request rework")
    APPLY = "apply", _("Apply")
    RESTORE = "restore", _("Restore")
    AUTO_ACCEPT = "auto_accept", _("Auto accept")
    CONFLICT = "conflict", _("Conflict")
    BLOCKED = "blocked", _("Blocked")


class ActorKind(models.TextChoices):
    HUMAN = "human", _("Human")
    SERVICE = "service", _("Service")


class AuditVerdict(models.TextChoices):
    PENDING = "pending", _("Pending")
    OK = "ok", _("Correct")
    WRONG = "wrong", _("Wrong")


class RescoreStatus(models.TextChoices):
    PENDING = "pending", _("Pending")
    RUNNING = "running", _("Running")
    DONE = "done", _("Done")
    FAILED = "failed", _("Failed")


class SourceKind(models.TextChoices):
    """Statement source labels (spec F02)."""

    PLAIN_TEXT = "PLAIN_TEXT", "PLAIN_TEXT"
    MARKDOWN = "MARKDOWN", "MARKDOWN"
    LEGACY_HTML = "LEGACY_HTML", "LEGACY_HTML"
    IMAGE = "IMAGE", "IMAGE"
    PDF_TEXT = "PDF_TEXT", "PDF_TEXT"
    PDF_SCAN = "PDF_SCAN", "PDF_SCAN"
    PDF_UNINSPECTED = "PDF_UNINSPECTED", "PDF_UNINSPECTED"
    EXTERNAL_LINK = "EXTERNAL_LINK", "EXTERNAL_LINK"
    TRANSLATION = "TRANSLATION", "TRANSLATION"


class QualityFlag(models.TextChoices):
    STATEMENT_MISSING = "statement_missing", _("Statement missing")
    PLACEHOLDER = "placeholder", _("Placeholder or title only")
    MISSING_REQUIRED_SECTION = "missing_required_section", _("Missing required section")
    MISSING_CONSTRAINTS = "missing_constraints", _("Missing constraints")
    MISSING_EXAMPLE = "missing_example", _("Missing example")
    BROKEN_ASSET = "broken_asset", _("Broken image or file")
    UNREADABLE = "unreadable", _("Could not read source")
    OCR_UNCERTAIN = "ocr_uncertain", _("OCR uncertain")
    CONTRADICTION = "contradiction", _("Sources contradict")
    NEEDS_EXPERT = "needs_expert", _("Needs expert")
