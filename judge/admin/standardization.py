from django.contrib import admin
from django.db.models import Sum
from django.utils.translation import gettext_lazy as _

from django.utils import timezone

from judge.standardization.enums import AuditVerdict, BatchStatus


class StandardizationBatchAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "name",
        "kind",
        "status",
        "id_from",
        "id_through",
        "checkpoint_id",
        "spent_usd",
        "created_at",
        "finished_at",
    )
    list_filter = ("kind", "status")
    search_fields = ("name",)
    readonly_fields = (
        "checkpoint_id",
        "spent_usd",
        "counters_json",
        "created_at",
        "finished_at",
    )
    actions = ("pause_batches", "resume_batches", "cancel_batches")

    @admin.action(description=_("Pause selected batches"))
    def pause_batches(self, request, queryset):
        queryset.filter(status=BatchStatus.RUNNING).update(status=BatchStatus.PAUSING)

    @admin.action(description=_("Resume selected batches"))
    def resume_batches(self, request, queryset):
        queryset.filter(status=BatchStatus.PAUSED).update(status=BatchStatus.QUEUED)

    @admin.action(description=_("Cancel selected batches"))
    def cancel_batches(self, request, queryset):
        queryset.exclude(
            status__in=(BatchStatus.FINISHED, BatchStatus.CANCELLED, BatchStatus.FAILED)
        ).update(status=BatchStatus.CANCEL_REQUESTED)


class ProblemSnapshotAdmin(admin.ModelAdmin):
    list_display = ("id", "problem", "batch", "source_hash", "created_at")
    search_fields = ("problem__code", "source_hash")
    raw_id_fields = ("problem", "batch")
    readonly_fields = (
        "source_hash",
        "before_json",
        "source_kinds_json",
        "quality_flags_json",
        "created_at",
    )


class StandardizationProposalAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "problem",
        "field_group",
        "step",
        "status",
        "provider",
        "model",
        "created_at",
        "reviewed_by",
        "applied_at",
    )
    list_filter = ("field_group", "status", "step", "provider")
    search_fields = ("problem__code", "dedupe_key")
    raw_id_fields = (
        "problem",
        "snapshot",
        "batch",
        "created_by",
        "reviewed_by",
        "applied_by",
        "restored_from",
    )
    readonly_fields = (
        "dedupe_key",
        "payload_json",
        "diff_json",
        "evidence_json",
        "after_json",
        "cost_usd",
        "provider_request_id",
        "created_at",
        "reviewed_at",
        "applied_at",
    )


class StandardizationVerificationAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "proposal",
        "level",
        "verdict",
        "points",
        "time",
        "memory",
        "judge_name",
        "created_at",
    )
    list_filter = ("level",)
    raw_id_fields = ("proposal", "code_run", "submission")
    readonly_fields = ("details_json", "created_at")


class StandardizationDecisionAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "proposal",
        "action",
        "actor",
        "actor_kind",
        "policy_version",
        "created_at",
    )
    list_filter = ("action", "actor_kind")
    raw_id_fields = ("proposal", "actor", "rescore_job")
    readonly_fields = (
        "proposal",
        "action",
        "actor",
        "actor_kind",
        "policy_version",
        "conditions_json",
        "before_json",
        "after_json",
        "conflict_json",
        "note",
        "revision_id",
        "rescore_job",
        "created_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


class StandardizationRescoreJobAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "problem",
        "reason",
        "status",
        "attempts",
        "enqueued_at",
        "finished_at",
    )
    list_filter = ("status", "reason")
    search_fields = ("problem__code",)
    raw_id_fields = ("problem",)
    readonly_fields = (
        "target_source_hash",
        "task_id",
        "error",
        "enqueued_at",
        "started_at",
        "finished_at",
    )


class LLMUsageLedgerAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "provider",
        "model",
        "step",
        "problem",
        "batch",
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cost_usd",
        "status",
        "created_at",
    )
    list_filter = ("provider", "model", "step", "status")
    raw_id_fields = ("problem", "batch", "proposal")
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        qs = self.get_queryset(request)
        extra_context["ledger_totals"] = qs.aggregate(
            cost=Sum("cost_usd"), inp=Sum("input_tokens"), out=Sum("output_tokens")
        )
        return super().changelist_view(request, extra_context=extra_context)


class LLMBatchJobAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "provider",
        "provider_batch_id",
        "step",
        "batch",
        "request_count",
        "status",
        "submitted_at",
        "ended_at",
        "results_ingested",
    )
    list_filter = ("provider", "status", "results_ingested")
    raw_id_fields = ("batch",)


class StandardizationPolicyAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "version",
        "name",
        "is_enabled",
        "halted",
        "groups_json",
        "id_from",
        "id_through",
        "include_public",
        "daily_cap",
        "applied_count",
        "updated_at",
    )
    list_filter = ("is_enabled", "halted", "include_public")
    search_fields = ("name", "version")
    raw_id_fields = ("batch", "created_by")
    readonly_fields = (
        "halted",
        "halted_reason",
        "halted_at",
        "applied_count",
        "created_at",
        "updated_at",
    )
    actions = ("enable_policies", "disable_policies", "resume_policies")

    @admin.action(description=_("Enable selected policies"))
    def enable_policies(self, request, queryset):
        queryset.update(is_enabled=True)

    @admin.action(description=_("Disable selected policies (kill switch)"))
    def disable_policies(self, request, queryset):
        queryset.update(is_enabled=False)

    @admin.action(description=_("Resume halted policies"))
    def resume_policies(self, request, queryset):
        queryset.filter(halted=True).update(
            halted=False, halted_reason="", halted_at=None
        )


class StandardizationAuditSampleAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "policy",
        "proposal",
        "problem_code",
        "field_group",
        "verdict",
        "reviewed_by",
        "reviewed_at",
        "created_at",
    )
    list_filter = ("verdict", "policy")
    raw_id_fields = ("proposal", "reviewed_by")
    readonly_fields = ("policy", "proposal", "created_at")
    actions = ("mark_ok", "mark_wrong")

    @admin.display(description=_("problem"))
    def problem_code(self, obj):
        return obj.proposal.problem.code

    @admin.display(description=_("group"))
    def field_group(self, obj):
        return obj.proposal.field_group

    def _mark(self, request, queryset, verdict):
        from judge.standardization.policy import check_breaker

        queryset.update(
            verdict=verdict, reviewed_by=request.profile, reviewed_at=timezone.now()
        )
        for policy in {s.policy for s in queryset.select_related("policy")}:
            check_breaker(policy)

    @admin.action(description=_("Mark selected samples correct"))
    def mark_ok(self, request, queryset):
        self._mark(request, queryset, AuditVerdict.OK)

    @admin.action(description=_("Mark selected samples wrong"))
    def mark_wrong(self, request, queryset):
        self._mark(request, queryset, AuditVerdict.WRONG)
