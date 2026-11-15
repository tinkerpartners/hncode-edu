"""Persistent records for the problem-bank standardization flow (issue #124).

One ``StandardizationBatch`` groups a run over an id range. For every problem
touched, a ``ProblemSnapshot`` freezes the state the run read; each
reviewable change is a ``StandardizationProposal`` for one field group, with
its evidence, verifications and an append-only ``StandardizationDecision``
audit trail. ``StandardizationRescoreJob`` queues the derived-score sync that
a points/partial change needs, de-duplicated per target state.
"""

from django.db import models
from django.utils.translation import gettext_lazy as _

from judge.standardization.enums import (
    ActorKind,
    BatchKind,
    BatchStatus,
    DecisionAction,
    FieldGroup,
    ProposalStatus,
    RescoreStatus,
    VerificationLevel,
)

__all__ = [
    "StandardizationBatch",
    "ProblemSnapshot",
    "StandardizationProposal",
    "StandardizationVerification",
    "StandardizationDecision",
    "StandardizationRescoreJob",
    "LLMUsageLedger",
    "LLMBatchJob",
]


class StandardizationBatch(models.Model):
    name = models.CharField(max_length=120, verbose_name=_("name"))
    kind = models.CharField(
        max_length=32, choices=BatchKind.choices, verbose_name=_("kind")
    )
    id_from = models.PositiveIntegerField(default=0, verbose_name=_("first problem id"))
    id_through = models.PositiveIntegerField(
        verbose_name=_("last problem id"),
        help_text=_("Frozen upper bound when the batch started (spec F01)."),
    )
    checkpoint_id = models.PositiveIntegerField(
        default=0, verbose_name=_("checkpoint problem id")
    )
    status = models.CharField(
        max_length=20,
        choices=BatchStatus.choices,
        default=BatchStatus.QUEUED,
        db_index=True,
        verbose_name=_("status"),
    )
    steps_json = models.JSONField(default=list, blank=True, verbose_name=_("steps"))
    config_version = models.CharField(
        max_length=64, blank=True, verbose_name=_("configuration version")
    )
    budget_usd_cap = models.DecimalField(
        max_digits=10, decimal_places=4, null=True, blank=True,
        verbose_name=_("budget cap (USD)"),
    )
    spent_usd = models.DecimalField(
        max_digits=10, decimal_places=4, default=0, verbose_name=_("spent (USD)")
    )
    counters_json = models.JSONField(default=dict, blank=True, verbose_name=_("counters"))
    created_by = models.ForeignKey(
        "judge.Profile", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+", verbose_name=_("created by"),
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("created at"))
    finished_at = models.DateTimeField(null=True, blank=True, verbose_name=_("finished at"))

    class Meta:
        verbose_name = _("standardization batch")
        verbose_name_plural = _("standardization batches")
        ordering = ["-created_at"]

    def __str__(self):
        return f"Batch #{self.id} {self.kind} [{self.status}]"

    def is_active(self):
        return self.status in (BatchStatus.QUEUED, BatchStatus.RUNNING)


class ProblemSnapshot(models.Model):
    problem = models.ForeignKey(
        "judge.Problem", on_delete=models.CASCADE,
        related_name="standardization_snapshots", verbose_name=_("problem"),
    )
    batch = models.ForeignKey(
        StandardizationBatch, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="snapshots", verbose_name=_("batch"),
    )
    source_hash = models.CharField(
        max_length=64, db_index=True, verbose_name=_("source hash"),
        help_text=_("sha256 of everything the standardization flow reads."),
    )
    before_json = models.JSONField(
        default=dict, verbose_name=_("state before"),
        help_text=_("Per field group state captured when the snapshot was taken."),
    )
    source_kinds_json = models.JSONField(default=list, blank=True, verbose_name=_("source kinds"))
    quality_flags_json = models.JSONField(default=list, blank=True, verbose_name=_("quality flags"))
    extracted_text = models.TextField(blank=True, verbose_name=_("extracted text"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("created at"))

    class Meta:
        verbose_name = _("problem snapshot")
        verbose_name_plural = _("problem snapshots")
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["problem", "source_hash"], name="std_snapshot_problem_hash_idx"),
        ]

    def __str__(self):
        return f"Snapshot #{self.id} of {self.problem_id} ({self.source_hash[:12]})"


class StandardizationProposal(models.Model):
    snapshot = models.ForeignKey(
        ProblemSnapshot, on_delete=models.CASCADE,
        related_name="proposals", verbose_name=_("snapshot"),
    )
    batch = models.ForeignKey(
        StandardizationBatch, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="proposals", verbose_name=_("batch"),
    )
    problem = models.ForeignKey(
        "judge.Problem", on_delete=models.CASCADE,
        related_name="standardization_proposals", verbose_name=_("problem"),
    )
    field_group = models.CharField(
        max_length=16, choices=FieldGroup.choices, db_index=True, verbose_name=_("field group")
    )
    step = models.CharField(max_length=64, verbose_name=_("step"))
    prompt_version = models.CharField(max_length=64, blank=True, verbose_name=_("prompt version"))
    model = models.CharField(max_length=120, blank=True, verbose_name=_("model"))
    provider = models.CharField(max_length=40, blank=True, verbose_name=_("provider"))
    dedupe_key = models.CharField(
        max_length=64, unique=True, verbose_name=_("dedupe key"),
        help_text=_("sha256(problem|source_hash|field_group|step|prompt_version)"),
    )
    status = models.CharField(
        max_length=24, choices=ProposalStatus.choices,
        default=ProposalStatus.DRAFT, db_index=True, verbose_name=_("status"),
    )
    payload_json = models.JSONField(default=dict, blank=True, verbose_name=_("proposed values"))
    diff_json = models.JSONField(default=dict, blank=True, verbose_name=_("diff"))
    evidence_json = models.JSONField(default=dict, blank=True, verbose_name=_("evidence"))
    cost_usd = models.DecimalField(
        max_digits=10, decimal_places=6, null=True, blank=True, verbose_name=_("cost (USD)")
    )
    provider_request_id = models.CharField(
        max_length=120, blank=True, verbose_name=_("provider request id")
    )
    created_by = models.ForeignKey(
        "judge.Profile", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+", verbose_name=_("created by"),
        help_text=_("Empty when a service created the proposal."),
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("created at"))
    reviewed_by = models.ForeignKey(
        "judge.Profile", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+", verbose_name=_("reviewed by"),
    )
    reviewed_at = models.DateTimeField(null=True, blank=True, verbose_name=_("reviewed at"))
    review_note = models.TextField(blank=True, verbose_name=_("review note"))
    applied_by = models.ForeignKey(
        "judge.Profile", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+", verbose_name=_("applied by"),
    )
    applied_at = models.DateTimeField(null=True, blank=True, verbose_name=_("applied at"))
    after_json = models.JSONField(default=dict, blank=True, verbose_name=_("state after apply"))
    restored_from = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="restorations", verbose_name=_("restored from"),
    )

    class Meta:
        verbose_name = _("standardization proposal")
        verbose_name_plural = _("standardization proposals")
        ordering = ["-created_at"]
        permissions = (
            ("review_standardization", "Can approve or reject standardization proposals"),
            ("apply_standardization", "Can apply or restore standardization proposals"),
            ("view_standardization_cost", "Can see standardization AI costs"),
        )
        indexes = [
            models.Index(
                fields=["problem", "field_group", "-created_at"],
                name="std_proposal_latest_idx",
            ),
        ]

    def __str__(self):
        return f"Proposal #{self.id} {self.field_group}/{self.step} [{self.status}]"

    @property
    def before_json(self):
        return (self.snapshot.before_json or {}).get(self.field_group, {})


class StandardizationVerification(models.Model):
    proposal = models.ForeignKey(
        StandardizationProposal, on_delete=models.CASCADE,
        related_name="verifications", verbose_name=_("proposal"),
    )
    level = models.CharField(
        max_length=32, choices=VerificationLevel.choices,
        default=VerificationLevel.UNVERIFIED, verbose_name=_("level"),
    )
    code_run = models.ForeignKey(
        "judge.CodeRun", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+", verbose_name=_("code run"),
    )
    submission = models.ForeignKey(
        "judge.Submission", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+", verbose_name=_("submission"),
    )
    verdict = models.CharField(max_length=8, blank=True, verbose_name=_("verdict"))
    points = models.FloatField(null=True, blank=True, verbose_name=_("points"))
    time = models.FloatField(null=True, blank=True, verbose_name=_("time (s)"))
    memory = models.FloatField(null=True, blank=True, verbose_name=_("memory (KB)"))
    test_config_hash = models.CharField(
        max_length=64, blank=True, verbose_name=_("test/config hash"),
        help_text=_("judge.review.hashing.compute_input_hash at verification time."),
    )
    judge_name = models.CharField(max_length=64, blank=True, verbose_name=_("judge"))
    details_json = models.JSONField(default=dict, blank=True, verbose_name=_("details"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("created at"))

    class Meta:
        verbose_name = _("standardization verification")
        verbose_name_plural = _("standardization verifications")
        ordering = ["-created_at"]

    def __str__(self):
        return f"Verification #{self.id} {self.level} for proposal {self.proposal_id}"


class StandardizationDecision(models.Model):
    proposal = models.ForeignKey(
        StandardizationProposal, on_delete=models.CASCADE,
        related_name="decisions", verbose_name=_("proposal"),
    )
    action = models.CharField(max_length=16, choices=DecisionAction.choices, verbose_name=_("action"))
    actor = models.ForeignKey(
        "judge.Profile", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+", verbose_name=_("actor"),
    )
    actor_kind = models.CharField(
        max_length=8, choices=ActorKind.choices, default=ActorKind.HUMAN, verbose_name=_("actor kind")
    )
    policy_version = models.CharField(max_length=64, blank=True, verbose_name=_("policy version"))
    conditions_json = models.JSONField(default=dict, blank=True, verbose_name=_("conditions met"))
    before_json = models.JSONField(default=dict, blank=True, verbose_name=_("state before"))
    after_json = models.JSONField(default=dict, blank=True, verbose_name=_("state after"))
    conflict_json = models.JSONField(default=dict, blank=True, verbose_name=_("conflict"))
    note = models.TextField(blank=True, verbose_name=_("note"))
    revision_id = models.PositiveIntegerField(null=True, blank=True, verbose_name=_("reversion id"))
    rescore_job = models.ForeignKey(
        "judge.StandardizationRescoreJob", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="decisions", verbose_name=_("rescore job"),
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("created at"))

    class Meta:
        verbose_name = _("standardization decision")
        verbose_name_plural = _("standardization decisions")
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.action} on proposal {self.proposal_id} by {self.actor_kind}"


class StandardizationRescoreJob(models.Model):
    """One pending derived-score sync for a problem whose points/partial changed.

    Unique per (problem, target_source_hash): re-applying the same result does
    not enqueue a second job (spec F07.5, AC28). Drained by a throttled Celery
    beat task, never by direct ``rescore_problem.delay`` from the apply path.
    """

    problem = models.ForeignKey(
        "judge.Problem", on_delete=models.CASCADE,
        related_name="standardization_rescore_jobs", verbose_name=_("problem"),
    )
    target_source_hash = models.CharField(max_length=64, verbose_name=_("target source hash"))
    reason = models.CharField(max_length=64, blank=True, verbose_name=_("reason"))
    status = models.CharField(
        max_length=10, choices=RescoreStatus.choices,
        default=RescoreStatus.PENDING, db_index=True, verbose_name=_("status"),
    )
    attempts = models.PositiveSmallIntegerField(default=0, verbose_name=_("attempts"))
    task_id = models.CharField(max_length=64, blank=True, verbose_name=_("task id"))
    error = models.TextField(blank=True, verbose_name=_("error"))
    enqueued_at = models.DateTimeField(auto_now_add=True, verbose_name=_("enqueued at"))
    started_at = models.DateTimeField(null=True, blank=True, verbose_name=_("started at"))
    finished_at = models.DateTimeField(null=True, blank=True, verbose_name=_("finished at"))

    class Meta:
        verbose_name = _("standardization rescore job")
        verbose_name_plural = _("standardization rescore jobs")
        unique_together = ("problem", "target_source_hash")
        ordering = ["enqueued_at"]

    def __str__(self):
        return f"Rescore {self.problem_id} [{self.status}]"


class LLMUsageLedger(models.Model):
    provider = models.CharField(max_length=40, verbose_name=_("provider"))
    model = models.CharField(max_length=120, verbose_name=_("model"))
    step = models.CharField(max_length=64, verbose_name=_("step"))
    problem = models.ForeignKey(
        "judge.Problem", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+", verbose_name=_("problem"),
    )
    batch = models.ForeignKey(
        StandardizationBatch, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="usage", verbose_name=_("batch"),
    )
    proposal = models.ForeignKey(
        StandardizationProposal, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="usage", verbose_name=_("proposal"),
    )
    request_id = models.CharField(max_length=120, blank=True, verbose_name=_("request id"))
    provider_batch_id = models.CharField(max_length=120, blank=True, verbose_name=_("provider batch id"))
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    cache_read_tokens = models.PositiveIntegerField(default=0)
    cache_write_tokens = models.PositiveIntegerField(default=0)
    cost_usd = models.DecimalField(
        max_digits=10, decimal_places=6, null=True, blank=True, verbose_name=_("cost (USD)"),
        help_text=_("Empty when the provider reports no usage (e.g. Poe)."),
    )
    status = models.CharField(max_length=10, default="ok", verbose_name=_("status"))
    created_at = models.DateTimeField(auto_now_add=True, db_index=True, verbose_name=_("created at"))

    class Meta:
        verbose_name = _("LLM usage entry")
        verbose_name_plural = _("LLM usage ledger")
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.provider}/{self.model} {self.step} {self.cost_usd}"


class LLMBatchJob(models.Model):
    provider = models.CharField(max_length=40, verbose_name=_("provider"))
    provider_batch_id = models.CharField(max_length=120, unique=True, verbose_name=_("provider batch id"))
    step = models.CharField(max_length=64, verbose_name=_("step"))
    batch = models.ForeignKey(
        StandardizationBatch, on_delete=models.CASCADE,
        related_name="llm_batches", verbose_name=_("batch"),
    )
    request_count = models.PositiveIntegerField(default=0, verbose_name=_("request count"))
    status = models.CharField(max_length=20, default="submitted", verbose_name=_("status"))
    submitted_at = models.DateTimeField(auto_now_add=True, verbose_name=_("submitted at"))
    ended_at = models.DateTimeField(null=True, blank=True, verbose_name=_("ended at"))
    results_ingested = models.BooleanField(default=False, verbose_name=_("results ingested"))

    class Meta:
        verbose_name = _("LLM batch job")
        verbose_name_plural = _("LLM batch jobs")
        ordering = ["-submitted_at"]

    def __str__(self):
        return f"LLM batch {self.provider_batch_id} ({self.status})"
