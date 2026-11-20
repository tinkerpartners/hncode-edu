"""Durable per-field outcomes; parked work is not a mandatory review queue."""

from judge.models import Problem
from judge.models.standardization import (
    StandardizationDisposition as Route,
    StandardizationProposal,
)
from judge.standardization.enums import FieldGroup, ProposalStatus
from judge.standardization.readiness import fingerprint
from judge.standardization import format_v1

INFRA_REASONS = {
    "timeout",
    "judge_timeout",
    "judge_error",
    "judge_unavailable",
    "workflow_error",
    "provider:config",
    "provider:credentials",
    "provider:rate_limit",
}

ISSUE_LABELS = {
    "source": "Đề bài, PDF/ảnh hoặc quy tắc chưa rõ",
    "tests": "Bộ test, checker hoặc hợp đồng chấm",
    "content": "Bố cục đề cần kiểm tra",
    "solution": "Hướng dẫn thuật toán cần kiểm tra",
    "tags": "Tag cần kiểm tra",
    "points": "Độ khó cần kiểm tra",
    "config": "Cấu hình chưa áp dụng được",
    "infrastructure": "Máy chấm, dịch vụ hoặc ngân sách",
    "policy": "Chờ policy hoặc đến lượt xử lý",
}


def issue_category(reason, group=""):
    if reason in INFRA_REASONS or reason.startswith(
        ("provider:", "budget:", "review:")
    ):
        return "infrastructure"
    if reason in (
        "grading_contract_conflict",
        "special_judge_requires_review",
    ) or reason.startswith(
        ("test_", "full_test", "checker_", "generator_", "grading_", "invalid_test")
    ):
        return "tests"
    if reason in (
        "statement_missing",
        "source_clarification",
        "broken_asset",
        "unreadable",
        "needs_expert",
        "ocr_uncertain",
    ):
        return "source"
    return group or "source"


def _field_reason(proposal):
    evidence = proposal.evidence_json or {}
    if evidence.get("error"):
        return evidence["error"]
    ev = evidence.get("auto_apply") or {}
    conditions = ev.get("conditions") or {}
    # Stable grouping is based on the actual failed condition, not model text.
    if isinstance(conditions, dict):
        failed = {
            name
            for name, value in conditions.items()
            if not (value.get("ok") if isinstance(value, dict) else value[0])
        }
        if failed == {"policy"}:
            return "policy_blocked"
    return {
        "content": "content_preservation_unproven",
        "solution": "editorial_verification_required",
        "tags": "tag_evidence_required",
        "points": "rating_evidence_required",
        "config": "config_unresolved",
    }.get(proposal.field_group, "conditions_not_met")


def refresh_routes(problem, batch=None):
    """Materialize current exceptions, including fields never eligible to sweep.

    Do not overwrite model drafts or turn errors into compulsory manual review.
    The problem lock serializes route summaries with ordinary apply operations.
    """
    from django.db import transaction
    from judge.standardization.readiness import source_readiness, save_readiness
    from judge.standardization.verification import current_full_verification

    with transaction.atomic():
        problem = Problem.objects.select_for_update().get(pk=problem.pk)
        report = source_readiness(problem)
        whole = save_readiness(problem, report, batch)
        if not report.ready:
            whole.details_json = {
                **whole.details_json,
                "category": issue_category(report.reason),
                "action": "Bổ sung hoặc sửa dữ liệu nguồn rồi chạy lại kiểm tra.",
            }
            whole.save(update_fields=["details_json", "updated_at"])
            return whole
        latest = {}
        for p in StandardizationProposal.objects.filter(problem=problem).order_by(
            "-created_at", "-pk"
        ):
            latest.setdefault(p.field_group, p)
        run_info = (batch.counters_json or {}) if batch else {}
        run_reason = run_info.get("reason", "")
        pending_technical = (
            run_info.get("phase") == "needs_review"
            and issue_category(run_reason) == "infrastructure"
        )
        completed = []
        deferred, infra, policy_waiting = [], [], []
        for group, p in latest.items():
            if group not in ("content", "solution", "tags", "points", "config"):
                continue
            applied = p.status == ProposalStatus.APPLIED
            reason = _field_reason(p)
            state = Route.State.DEFERRED_VERIFICATION
            if pending_technical and group in ("solution", "tags", "points"):
                reason = run_reason
            solution_id = (
                p.pk
                if group == "solution"
                else p.evidence_json.get("solution_proposal_id")
            )
            from judge.models.standardization import StandardizationVerification

            review = (
                StandardizationVerification.objects.filter(
                    proposal_id=solution_id,
                    details_json__kind="independent_reference_review",
                )
                .order_by("-pk")
                .first()
                if solution_id
                else None
            )
            review_issues = [
                item["reason"]
                for item in (
                    review.details_json.get("result", {}).get("issues", [])
                    if review
                    else []
                )
                if item.get("group") == group
            ]
            if solution_id:
                from judge.standardization.independent_review import context

                owner = latest.get("solution")
                review_error = (
                    (owner.evidence_json or {}).get("independent_review_error")
                    if owner and owner.pk == solution_id
                    else None
                )
                if review_error and review_error.get("identity") == context(owner):
                    reason = review_error["code"]
            if applied:
                from judge.standardization.snapshot import capture_group

                applied = capture_group(problem, group) == (p.after_json or {})
            if applied and (
                group != "solution" or current_full_verification(p, problem)
            ):
                state, reason = Route.State.AUTO_DONE, "applied"
                if (
                    group == "content"
                    and format_v1.validate(problem.description or "").label
                    != "STRICT_OK"
                ):
                    state, reason = (
                        Route.State.AUTO_PARTIAL,
                        "headings_preserved_example_layout_deferred",
                    )
                else:
                    completed.append(group)
            elif applied and group == "solution":
                reason = "tests_changed"
            if reason == "policy_blocked":
                category = "policy"
                policy_waiting.append(group)
            else:
                category = issue_category(reason, group)
            if category == "infrastructure":
                state = Route.State.RETRY_INFRA
                infra.append(group)
            elif state not in (Route.State.AUTO_DONE,) and category != "policy":
                deferred.append(group)
            Route.objects.update_or_create(
                problem=problem,
                field_group=group,
                defaults={
                    "state": state,
                    "reason_code": reason,
                    "fingerprint": report.fingerprint,
                    "batch_id": p.batch_id,
                    "details_json": {
                        "category": category,
                        "proposal_id": p.pk,
                        "conditions": (p.evidence_json or {}).get("auto_apply", {}),
                        "status": p.status,
                        "review_issues": review_issues,
                        "action": "Xem bản so sánh và bằng chứng; chỉ duyệt phần này nếu đã xác nhận.",
                    },
                },
            )
        applied_groups = list(completed)
        unchanged_groups = []
        # A configuration already satisfying the profile needs no artificial
        # write/apply proposal just to make a completion counter reach five.
        if "config" not in completed:
            from judge.standardization import config_profile

            try:
                config = config_profile.plan_config(
                    problem, config_profile.resolve_context()
                )
            except config_profile.ProfileResolutionError:
                config = None
            if config is not None and config.is_noop():
                completed.append("config")
                unchanged_groups.append("config")
                deferred = [g for g in deferred if g != "config"]
                policy_waiting = [g for g in policy_waiting if g != "config"]
                Route.objects.update_or_create(
                    problem=problem,
                    field_group="config",
                    defaults={
                        "state": Route.State.AUTO_DONE,
                        "reason_code": "already_standard",
                        "fingerprint": report.fingerprint,
                        "batch": batch,
                        "details_json": {
                            "category": "config",
                            "evidence_kind": "deterministic_profile_noop",
                            "profile": config_profile.CONFIG_PROFILE_VERSION,
                        },
                    },
                )
        required = {"content", "solution", "tags", "points", "config"}
        whole.state = (
            Route.State.AUTO_DONE
            if set(completed) >= required
            else (
                Route.State.AUTO_PARTIAL
                if completed
                else (
                    Route.State.DEFERRED_VERIFICATION
                    if deferred
                    else Route.State.RETRY_INFRA if infra else Route.State.READY
                )
            )
        )
        whole.reason_code = ""
        from judge.standardization.hashing import compute_source_hash

        whole.details_json = {
            "applied_groups": applied_groups,
            "validated_unchanged_groups": unchanged_groups,
            "completed_groups": completed,
            "completed_source_hash": compute_source_hash(problem),
            "review_groups": deferred,
            "infrastructure_groups": infra,
            "policy_groups": policy_waiting,
        }
        whole.save(update_fields=["state", "reason_code", "details_json", "updated_at"])
        return whole


def record_run_outcome(batch):
    info = batch.counters_json or {}
    reason, phase = info.get("reason", ""), info.get("phase", "")
    if phase not in ("needs_review", "needs_source"):
        return
    problem = Problem.objects.get(pk=batch.id_from)
    version = fingerprint(problem)
    # A semantic ambiguity in the task is as blocking as an unreadable PDF.
    # Do not retry it with a different model while the task data is unchanged.
    if phase == "needs_source" or reason in {
        "source_clarification",
        "grading_contract_conflict",
        "special_judge_requires_review",
    }:
        Route.objects.update_or_create(
            problem=problem,
            field_group="",
            defaults={
                "state": Route.State.DEFERRED_SOURCE,
                "reason_code": reason,
                "fingerprint": version,
                "details_json": {
                    **info.get("source_readiness", {}),
                    "phase": phase,
                    "batch_id": batch.pk,
                },
                "batch": batch,
            },
        )
    else:
        Route.objects.update_or_create(
            problem=problem,
            field_group="",
            defaults={
                "state": (
                    Route.State.RETRY_INFRA
                    if reason in INFRA_REASONS or reason.startswith("provider:")
                    else Route.State.DEFERRED_VERIFICATION
                ),
                "reason_code": reason,
                "fingerprint": version,
                "details_json": {"phase": phase},
                "batch": batch,
            },
        )


def save_field_outcome(proposal, evaluation, *, applied=False):
    problem = Problem.objects.get(pk=proposal.problem_id)
    state = Route.State.AUTO_DONE if applied else Route.State.DEFERRED_VERIFICATION
    reason = "applied" if applied else _field_reason(proposal)
    if (
        applied
        and proposal.field_group == FieldGroup.CONTENT
        and format_v1.validate(problem.description or "").label != "STRICT_OK"
    ):
        state, reason = (
            Route.State.AUTO_PARTIAL,
            "headings_preserved_example_layout_deferred",
        )
    Route.objects.update_or_create(
        problem=problem,
        field_group=proposal.field_group,
        defaults={
            "state": state,
            "reason_code": reason,
            "fingerprint": fingerprint(problem),
            "details_json": {
                "proposal_id": proposal.pk,
                "conditions": evaluation.as_json(),
                "category": issue_category(reason, proposal.field_group),
            },
            "batch_id": proposal.batch_id,
        },
    )
    current = Route.objects.filter(problem=problem, field_group="").first()
    if (
        current
        and current.state == Route.State.DEFERRED_SOURCE
        and current.fingerprint == fingerprint(problem)
    ):
        return
    latest = {}
    for row in StandardizationProposal.objects.filter(problem=problem).order_by(
        "-created_at", "-pk"
    ):
        latest.setdefault(row.field_group, row)
    completed = {g for g, p in latest.items() if p.status == ProposalStatus.APPLIED}
    from judge.standardization.verification import current_full_verification

    required = {
        str(g)
        for g in (
            FieldGroup.CONTENT,
            FieldGroup.SOLUTION,
            FieldGroup.TAGS,
            FieldGroup.POINTS,
            FieldGroup.CONFIG,
        )
    }
    fully_verified = (
        completed >= required
        and format_v1.validate(problem.description or "").label == "STRICT_OK"
        and current_full_verification(latest[FieldGroup.SOLUTION], problem)
    )
    if completed or not applied:
        # AC alone is deliberately insufficient. AUTO_DONE requires all five
        # groups applied; unchanged groups need a separate certificate first.
        state = (
            Route.State.AUTO_DONE
            if fully_verified
            else (
                Route.State.AUTO_PARTIAL
                if completed
                else Route.State.DEFERRED_VERIFICATION
            )
        )
        Route.objects.update_or_create(
            problem=problem,
            field_group="",
            defaults={
                "state": state,
                "reason_code": "",
                "fingerprint": fingerprint(problem),
                "details_json": {"applied_groups": sorted(completed)},
                "batch_id": proposal.batch_id,
            },
        )
