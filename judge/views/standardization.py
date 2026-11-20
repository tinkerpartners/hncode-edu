"""Minimal review UI for standardization proposals (spec F08, AC15, AC18).

* ``/internal/standardization/``                      queue of proposals
* ``/internal/standardization/<problem_id>/``         source beside the latest
  proposal per field group, diff, evidence, verifications, decision log
* ``POST .../proposal/<id>/<action>/``                approve | reject | rework |
  apply | restore | verify

Everything is ``private, no-store``; private statement/solution text is only
shown to users who may see the problem's source.
"""

import difflib
import re
from decimal import Decimal

from django.conf import settings
from django.contrib import messages

from django.contrib.auth.views import redirect_to_login
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Exists, Max, OuterRef
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from judge.models import Problem, ProblemType
from judge.models.standardization import (
    StandardizationBatch,
    StandardizationProposal,
    StandardizationDisposition,
)
from judge.standardization import permissions
from judge.standardization.apply import (
    StandardizationError,
    apply_proposal,
    check_conflict,
    restore_proposal,
    review_proposal,
)
from judge.standardization.enums import (
    DecisionAction,
    FieldGroup,
    PROPOSAL_TRANSITIONS,
    ProposalStatus,
)
from judge.standardization.snapshot import capture_group
from judge.standardization.verification import current_full_verification, latest_level
from judge.standardization.guided import ACTIVE, VERSION, cancel_run, queue_run

GROUP_ORDER = [
    FieldGroup.CONTENT,
    FieldGroup.SOLUTION,
    FieldGroup.TAGS,
    FieldGroup.POINTS,
    FieldGroup.CONFIG,
]
REVIEW_ACTIONS = {"approve", "reject", "rework"}
OPEN_STATUSES = (
    ProposalStatus.PENDING_REVIEW,
    ProposalStatus.NEEDS_CLARIFICATION,
    ProposalStatus.NEEDS_SOURCE,
    ProposalStatus.APPROVED,
    ProposalStatus.BLOCKED_CONTEST,
    ProposalStatus.ERROR,
    ProposalStatus.UNKNOWN,
    ProposalStatus.REWORK,
)
PHASES = {
    "content": "Đang chuẩn hóa đề",
    "reference": "Đang kiểm tra code mẫu",
    "guided_reference_v1": "Luna đọc code đã kiểm chứng",
    "guided_luna_v1": "Luna đang viết lời giải",
    "guided_luna_repair_v1": "Luna đang sửa lần đầu",
    "guided_sol_v1": "Sol đang xử lý lời giải",
    "ready": "Code đạt bộ test · xét điều kiện từng phần",
    "partial_ready": "Code đạt bộ test · có phần đang để riêng",
    "existing_solution": "Đã có Solution và tag · giữ nội dung hiện có",
    "needs_source": "Để riêng · thiếu căn cứ hoặc dữ liệu",
    "needs_review": "Để riêng · chưa đủ kiểm chứng",
}
REASONS = {
    "already_standard": "Đã đúng cấu hình chuẩn, giữ nguyên và xác minh bằng quy tắc.",
    "content_preservation_unproven": "Bản đề chưa chứng minh được bảo toàn nội dung.",
    "editorial_verification_required": "Hướng dẫn chưa đạt kiểm chứng độc lập.",
    "tag_evidence_required": "Tag chưa đủ bằng chứng hoặc chưa ánh xạ được.",
    "rating_evidence_required": "Độ khó còn chênh lệch hoặc thiếu căn cứ.",
    "config_unresolved": "Chưa giải được cấu hình theo profile.",
    "policy_blocked": "Chờ policy, hạn mức áp dụng hoặc xử lý bản mới nhất.",
    "test_manifest_missing": "Chưa có init.yml để kiểm tra bộ test đầy đủ.",
    "full_tests_missing": "Chưa có full test; pretest không đủ để xác minh.",
    "test_files_missing": "Thiếu tệp input/output được khai báo trong bộ test.",
    "test_archive_corrupted": "Tệp ZIP bộ test không vượt kiểm tra CRC.",
    "test_data_unreadable": "Không đọc được bộ test hoặc tệp ZIP.",
    "checker_missing_or_invalid": "Thiếu checker hoặc cấu hình checker không hợp lệ.",
    "generator_missing": "Thiếu tệp sinh dữ liệu được khai báo.",
    "needs_expert": "Chưa có luồng kiểm chứng tự động phù hợp.",
    "conditions_not_met": "Chưa đủ điều kiện tự áp dụng; xem bằng chứng của từng phần.",
    "solution_quality_incomplete": "Hướng dẫn chưa đủ nội dung; giữ nguyên nguồn để xử lý sau.",
    "headings_preserved_example_layout_deferred": "Đã chuẩn heading, khung ví dụ còn giữ nguyên.",
    "statement_missing": "Thiếu nội dung đề.",
    "content_needs_review": "Code đã đạt đủ test nhưng bản đề còn vấn đề; chưa hoàn tất chuẩn hóa.",
    "metadata_needs_review": "Code đã đạt đủ test nhưng tags hoặc độ khó chưa đủ bằng chứng.",
    "grading_contract_conflict": "Đề, cấu hình dữ liệu hoặc init.yml không thống nhất. Chưa gọi AI giải bài.",
    "special_judge_requires_review": "Bài tương tác, hàm hoặc output-only cần luồng kiểm chứng chuyên biệt.",
    "source_clarification": "Đề hoặc Solution có chỗ cần làm rõ.",
    "editorial_and_tags_covered": "Solution hiện tại có thuật toán, tính đúng, độ phức tạp và bài đã có tag.",
    "judge_timeout": "Máy chấm chưa trả kết quả đúng hạn.",
    "timeout": "Máy chấm chưa trả kết quả đúng hạn.",
    "judge_error": "Không kết nối được máy chấm.",
    "tests_changed": "Bộ test hoặc cấu hình chấm đã đổi.",
    "source_changed": "Đề hoặc nguồn tham chiếu đã đổi trong lúc chạy.",
    "resume_evidence_changed": "Dữ liệu hoặc quyết định duyệt đã thay đổi; đã dừng lượt sửa tiếp.",
    "pending_evidence_changed": "Kết quả chờ không còn khớp mã nguồn hoặc cấu hình hiện tại.",
    "pending_submission_missing": "Không còn bài nộp đang chờ; cần kiểm tra lại máy chấm.",
    "local_verification_required": "Bản sao QA: cần chấm lại trên máy chấm local.",
    "judge_unavailable": "Chưa có kết quả chấm đủ để xác nhận.",
    "partial_score": "Code AC nhưng chưa đạt đủ điểm.",
    "full_score": "Lời giải đã đạt đủ điểm trên bộ test vừa kiểm tra.",
    "provider:config": "Chưa cấu hình OpenRouter trên worker.",
    "provider:credentials": "OpenRouter từ chối thông tin xác thực.",
    "provider:truncated": "Model chưa trả đủ nội dung.",
    "missing_code": "Model chưa trả mã nguồn.",
    "io_mismatch": "Code chưa dùng đúng tên tệp vào hoặc ra.",
    "workflow_error": "Tác vụ bị lỗi. Xem nhật ký worker hoặc chạy lại.",
    "CE": "Code không biên dịch được.",
    "WA": "Code sai ít nhất một test.",
    "TLE": "Code vượt thời gian chạy.",
    "MLE": "Code vượt bộ nhớ.",
}


def _run_view(batch, problem=None):
    info = batch.counters_json or {}
    reason = info.get("reason", "")
    notes = [info[k] for k in ("config_note", "points_note") if info.get(k)]
    reference_note = {
        "verified": "Code tham chiếu đã được chấm đủ điểm.",
        "not_verified": "Đã tìm code tham chiếu nhưng chưa có code đạt kiểm chứng.",
        "not_found": "Chưa tìm được code tham chiếu được phép dùng.",
    }.get(info.get("reference_status"))
    if reference_note:
        notes.append(reference_note)
    if info.get("has_existing_editorial"):
        notes.append("Có Solution hiện tại để AI đối chiếu.")
    return {
        "batch": batch,
        "problem": problem,
        "phase": PHASES.get(info.get("phase"), ""),
        "reason": REASONS.get(reason, reason),
        "model": info.get("model", ""),
        "active": batch.status in ACTIVE,
        "notes": notes,
    }


def _no_store(response):
    response["Cache-Control"] = "private, no-store"
    return response


def _gate(request, check):
    if not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path())
    if not check(request.user):
        return HttpResponseForbidden()
    return None


def _html_diff(before, after):
    return difflib.HtmlDiff(wrapcolumn=80).make_table(
        (before or "").splitlines(),
        (after or "").splitlines(),
        "Hiện tại",
        "Đề xuất",
        context=True,
        numlines=3,
    )


def _type_names(ids):
    names = dict(ProblemType.objects.filter(id__in=ids).values_list("id", "full_name"))
    return [names.get(i, f"#{i}") for i in ids]


def _is_stale(problem, proposal):
    """Per-group staleness, matching what apply/restore will refuse: an open
    proposal whose group changed since its snapshot, or an applied one whose
    group was edited afterwards (restore would conflict)."""
    group = FieldGroup(proposal.field_group)
    if proposal.status == ProposalStatus.APPLIED:
        return capture_group(problem, group) != (proposal.after_json or {})
    if proposal.status in (ProposalStatus.RESTORED, ProposalStatus.REJECTED):
        return False
    ok, _ = check_conflict(problem, proposal)
    return not ok


def _group_view(problem, proposal, can_see_source):
    """Everything the template needs for one field group."""
    group = proposal.field_group
    payload = proposal.payload_json or {}
    before = proposal.before_json
    view = {
        "group": group,
        "proposal": proposal,
        "verification": (
            latest_level(proposal) if group == FieldGroup.SOLUTION else None
        ),
        "verifications": list(proposal.verifications.order_by("-created_at")[:5]),
        "decisions": list(
            proposal.decisions.select_related("actor__user").order_by("-created_at")[
                :10
            ]
        ),
        "stale": _is_stale(problem, proposal),
        "transitions": PROPOSAL_TRANSITIONS[ProposalStatus(proposal.status)],
    }
    if group == FieldGroup.CONTENT:
        view["kind"] = "text"
        if can_see_source:
            view["before_text"] = problem.description or ""
            view["after_text"] = payload.get("description", "")
            view["diff_html"] = _html_diff(view["before_text"], view["after_text"])
    elif group == FieldGroup.SOLUTION:
        view["kind"] = "text"
        if can_see_source:
            try:
                view["before_text"] = problem.solution.content
            except Exception:  # noqa: BLE001
                view["before_text"] = ""
            view["after_text"] = payload.get("content", "")
            view["diff_html"] = _html_diff(view["before_text"], view["after_text"])
            view["code"] = payload.get("cpp_code", "")
        view["reference"] = (proposal.evidence_json or {}).get("reference")
        view["ready"] = not (proposal.evidence_json or {}).get(
            "guided"
        ) or current_full_verification(proposal, problem)
        if view["verifications"]:
            details = view["verifications"][0].details_json or {}
            view["judge_reason"] = REASONS.get(details.get("reason"), "")
            if can_see_source:
                view["judge_details"] = details
    elif group == FieldGroup.TAGS:
        view["kind"] = "table"
        view["rows"] = [
            ("Hiện tại", ", ".join(_type_names(before.get("type_ids", []))) or "—"),
            (
                "Đề xuất",
                ", ".join(
                    payload.get("names") or _type_names(payload.get("type_ids", []))
                )
                or "—",
            ),
            ("Tag chính", payload.get("primary_tag", "")),
            ("Không khớp danh mục", ", ".join(payload.get("unresolved", [])) or "—"),
        ]
    elif group == FieldGroup.POINTS:
        view["kind"] = "table"
        rng = payload.get("range") or ["", ""]
        view["rows"] = [
            ("Points hiện tại", before.get("points", problem.points)),
            ("Points đề xuất", payload.get("points", "")),
            ("Khoảng hợp lý", f"{rng[0]} – {rng[1]}"),
            ("Độ tin cậy", payload.get("confidence", "")),
            ("Nhãn", payload.get("label", "")),
        ]
        if (proposal.evidence_json or {}).get("official_rating"):
            view["official_source"] = proposal.evidence_json.get("cf_source_url")
    else:  # CONFIG
        view["kind"] = "json"
    return view


@require_GET
def standardization_queue(request):
    denied = _gate(request, permissions.can_view_standardization)
    if denied:
        return denied
    status = request.GET.get("status", "open")
    group = request.GET.get("group", "")
    batch = request.GET.get("batch", "")
    search = request.GET.get("q", "").strip()[:100]
    from judge.standardization.routing import ISSUE_LABELS

    issue = request.GET.get("issue", "")
    from django.db.models import Q

    issue_routes = StandardizationDisposition.objects.exclude(
        state__in=("ready", "auto_done")
    )
    source_blocked = StandardizationDisposition.objects.filter(
        field_group="", state="deferred_source"
    ).values("problem_id")
    issue_routes = issue_routes.exclude(
        ~Q(field_group="") & Q(problem_id__in=source_blocked)
    )
    if search:
        from django.db.models import Q

        issue_routes = issue_routes.filter(
            Q(problem__code__icontains=search) | Q(problem__name__icontains=search)
        )
    if batch.isdigit():
        issue_routes = issue_routes.filter(batch_id=int(batch))
    issue_counts = dict(
        issue_routes.values_list("details_json__category").annotate(
            n=Count("problem_id", distinct=True)
        )
    )
    issue_page = None
    if issue in ISSUE_LABELS:
        issue_page = Paginator(
            issue_routes.filter(details_json__category=issue)
            .select_related("problem")
            .order_by("problem_id", "field_group"),
            35,
        ).get_page(request.GET.get("issue_page"))
    route_filter = request.GET.get("route", "active")
    routes = StandardizationDisposition.objects.filter(field_group="")
    parked = ("deferred_source", "deferred_verification", "retry_infra")
    base = StandardizationProposal.objects.all()
    if batch.isdigit():
        base = base.filter(batch_id=int(batch))
    else:
        from judge.standardization.policy import newer_proposals

        base = base.annotate(
            _has_newer=Exists(
                newer_proposals(
                    OuterRef("problem_id"),
                    OuterRef("field_group"),
                    OuterRef("created_at"),
                    OuterRef("pk"),
                )
            )
        ).filter(_has_newer=False)
    if search:
        from django.db.models import Q

        base = base.filter(
            Q(problem__code__icontains=search) | Q(problem__name__icontains=search)
        )
    qs = base
    if issue in ISSUE_LABELS:
        qs = qs.filter(
            problem_id__in=issue_routes.filter(details_json__category=issue).values(
                "problem_id"
            )
        )
    if route_filter == "active":
        qs = qs.exclude(
            problem_id__in=routes.filter(state__in=parked).values("problem_id")
        )
    elif route_filter in dict(StandardizationDisposition.State.choices):
        qs = qs.filter(
            problem_id__in=routes.filter(state=route_filter).values("problem_id")
        )
    if status == "open":
        qs = qs.filter(status__in=OPEN_STATUSES)
    elif status:
        qs = qs.filter(status=status)
    if group:
        qs = qs.filter(field_group=group)
    counts = dict(base.order_by().values_list("status").annotate(c=Count("id")))
    page = Paginator(
        qs.values("problem_id")
        .annotate(recent=Max("created_at"))
        .order_by("-recent", "problem_id"),
        35,
    ).get_page(request.GET.get("page"))
    ids = [item["problem_id"] for item in page.object_list]
    summary = {pk: {} for pk in ids}
    for proposal in base.filter(problem_id__in=ids).order_by("-created_at", "-pk"):
        summary[proposal.problem_id].setdefault(proposal.field_group, proposal)
    problems = Problem.objects.in_bulk(ids)
    kept_groups = {}
    for problem_id, kept_group in StandardizationDisposition.objects.filter(
        problem_id__in=ids, state="auto_done", reason_code="already_standard"
    ).values_list("problem_id", "field_group"):
        kept_groups.setdefault(problem_id, []).append(kept_group)
    recent_runs = list(
        StandardizationBatch.objects.filter(config_version=VERSION).order_by("-pk")[:12]
    )
    active_problems = Problem.objects.in_bulk({r.id_from for r in recent_runs})
    return _no_store(
        render(
            request,
            "internal/standardization/queue.html",
            {
                "title": "Chuẩn hóa bài — theo dõi tự động",
                "issue": issue,
                "issue_choices": ISSUE_LABELS,
                "issue_counts": issue_counts,
                "issue_page": issue_page,
                "kept_groups": kept_groups,
                "route_filter": route_filter,
                "route_choices": StandardizationDisposition.State.choices,
                "reason_labels": REASONS,
                "route_counts": dict(
                    routes.order_by().values_list("state").annotate(n=Count("pk"))
                ),
                "parked_items": (
                    list(
                        routes.filter(state__in=parked)
                        .select_related("problem")
                        .order_by("-updated_at")[:20]
                    )
                    if route_filter != "active"
                    else []
                ),
                "page": page,
                "items": [
                    (problems[pk], [summary[pk].get(field) for field in GROUP_ORDER])
                    for pk in ids
                    if pk in problems
                ],
                "runs": [
                    _run_view(r, active_problems.get(r.id_from)) for r in recent_runs
                ],
                "status": status,
                "group": group,
                "batch": batch,
                "search": search,
                "counts": counts,
                "statuses": ProposalStatus.choices,
                "groups": FieldGroup.choices,
                "group_order": GROUP_ORDER,
                "can_review": permissions.can_review_standardization(request.user),
                "can_see_cost": permissions.can_view_standardization_cost(request.user),
            },
        )
    )


@require_GET
def standardization_problem(request, problem_id):
    denied = _gate(request, permissions.can_view_standardization)
    if denied:
        return denied
    problem = get_object_or_404(Problem, pk=problem_id)
    can_see_source = permissions.can_view_problem_source(request.user, problem)
    run = (
        StandardizationBatch.objects.filter(config_version=VERSION, id_from=problem.pk)
        .order_by("-pk")
        .first()
    )
    latest = {}
    batch_filter = request.GET.get("batch", "")
    proposals = StandardizationProposal.objects.filter(problem=problem)
    if batch_filter.isdigit():
        proposals = proposals.filter(batch_id=int(batch_filter))
    for proposal in proposals.select_related("snapshot", "batch").order_by(
        "-created_at"
    ):
        latest.setdefault(proposal.field_group, proposal)
    groups = [
        _group_view(problem, latest[g], can_see_source)
        for g in GROUP_ORDER
        if g in latest
    ]
    applied = {g for g, p in latest.items() if p.status == ProposalStatus.APPLIED}
    solution_ok = FieldGroup.SOLUTION in latest and current_full_verification(
        latest[FieldGroup.SOLUTION], problem
    )
    from judge.standardization import format_v1

    complete = (
        applied == set(GROUP_ORDER)
        and solution_ok
        and format_v1.validate(problem.description or "").label == "STRICT_OK"
    )
    route_summary = StandardizationDisposition.objects.filter(
        problem=problem, field_group=""
    ).first()
    if not batch_filter and route_summary and route_summary.state == "auto_done":
        from judge.standardization.readiness import fingerprint
        from judge.standardization.hashing import compute_source_hash

        complete = (
            route_summary.fingerprint == fingerprint(problem)
            and solution_ok
            and route_summary.details_json.get("completed_source_hash")
            == compute_source_hash(problem)
        )
    history = (
        StandardizationProposal.objects.filter(problem=problem)
        .order_by("-created_at")
        .values(
            "id", "field_group", "step", "status", "created_at", "cost_usd", "model"
        )[:40]
    )
    return _no_store(
        render(
            request,
            "internal/standardization/problem.html",
            {
                "title": f"Chuẩn hóa: {problem.code}",
                "problem": problem,
                "can_open_problem": problem.is_accessible_by(request.user),
                "can_edit_problem": problem.is_editable_by(request.user),
                "groups": groups,
                "routes": list(
                    StandardizationDisposition.objects.filter(problem=problem).order_by(
                        "field_group"
                    )
                ),
                "reason_labels": REASONS,
                "history": list(history),
                "run": _run_view(run) if run else None,
                "allow_submission": getattr(
                    settings, "STANDARDIZATION_ALLOW_SUBMISSION_REFERENCES", False
                ),
                "complete": complete,
                "can_see_source": can_see_source,
                "can_review": permissions.can_review_standardization(request.user),
                "can_apply": permissions.can_apply_standardization(request.user),
                "can_see_cost": permissions.can_view_standardization_cost(request.user),
                "statement_html_allowed": can_see_source,
            },
        )
    )


@require_POST
def standardization_run(request, problem_id=None, run_id=None, action="start"):
    denied = _gate(request, permissions.can_review_standardization)
    if denied:
        return denied
    from judge.tasks.standardization import run_guided

    if action == "cancel":
        run = get_object_or_404(StandardizationBatch, pk=run_id, config_version=VERSION)
        problem = get_object_or_404(Problem, pk=run.id_from)
        if not permissions.can_view_problem_source(request.user, problem):
            return HttpResponseForbidden()
        cancel_run(run)
        messages.success(request, "Đã ghi nhận yêu cầu dừng.")
        return redirect("internal_standardization_problem", problem.pk)

    destination = "internal_standardization_queue"
    args = ()
    if problem_id is None:
        codes = list(
            dict.fromkeys(
                re.split(r"[,\s]+", request.POST.get("codes", "")[:3000].strip())
            )
        )
        if (
            not codes
            or len(codes) > 25
            or any(not re.fullmatch(r"[a-z0-9_]{1,50}", code) for code in codes)
        ):
            messages.error(request, "Nhập từ 1 đến 25 mã bài hợp lệ mỗi lượt.")
            return redirect(destination)
        by_code = Problem.objects.in_bulk(codes, field_name="code")
        if len(by_code) != len(codes):
            messages.error(
                request,
                "Mã bài không tìm thấy: "
                + ", ".join(code for code in codes if code not in by_code),
            )
            return redirect(destination)
        problems = [by_code[code] for code in codes]
    else:
        problems = [get_object_or_404(Problem, pk=problem_id)]
        destination, args = "internal_standardization_problem", (problem_id,)
    if any(
        not permissions.can_view_problem_source(request.user, problem)
        for problem in problems
    ):
        return HttpResponseForbidden()
    submission = (
        request.POST.get("submission_id", "").strip() if problem_id is not None else ""
    )
    if submission and (not submission.isdecimal() or len(submission) > 10):
        messages.error(request, "ID bài nộp phải là số.")
        return redirect(destination, *args)
    limit = request.POST.get("budget_cap", "").strip()
    if limit and (
        not re.fullmatch(r"\d{1,2}(?:\.\d{1,4})?", limit)
        or not Decimal("0.001") <= Decimal(limit) <= Decimal("10")
    ):
        messages.error(request, "Nhập ngân sách từ 0,001 đến 10 USD cho mỗi bài.")
        return redirect(destination, *args)
    created = 0
    failed = 0
    for problem in problems:
        try:
            run, is_new = queue_run(
                problem,
                request.user.profile,
                include_content="content" in request.POST,
                include_config="config" in request.POST,
                allow_sol="sol" in request.POST,
                submission_id=int(submission) if submission else None,
                cf_source=(
                    request.POST.get("cf_source", "").strip()[:200]
                    if problem_id is not None
                    else ""
                ),
                budget_cap=Decimal(limit) if limit else None,
            )
        except ValueError as exc:
            messages.error(request, str(exc))
            return redirect(destination, *args)
        if is_new:
            try:
                transaction.on_commit(lambda run_id=run.pk: run_guided.delay(run_id))
            except Exception:
                StandardizationBatch.objects.filter(pk=run.pk).update(status="failed")
                failed += 1
            else:
                created += 1
    if failed:
        messages.error(
            request, f"Worker chưa nhận {failed} lượt chuẩn hóa. Có thể thử lại."
        )
    if created:
        messages.success(request, f"Đã tạo {created} lượt chuẩn hóa bản nháp.")
    elif not failed:
        messages.info(request, "Các bài đã có lượt chuẩn hóa đang hoạt động.")
    return redirect(destination, *args)


@require_POST
def standardization_action(request, proposal_id, action):
    if not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path())
    proposal = get_object_or_404(
        StandardizationProposal.objects.select_related("problem"), pk=proposal_id
    )
    user = request.user
    if not permissions.can_view_problem_source(user, proposal.problem):
        return HttpResponseForbidden()
    note = (request.POST.get("note") or "")[:2000]
    try:
        if action in REVIEW_ACTIONS:
            if not permissions.can_review_standardization(user):
                return HttpResponseForbidden()
            review_proposal(proposal, user.profile, DecisionAction(action), note=note)
        elif action == "apply":
            if not permissions.can_apply_standardization(user):
                return HttpResponseForbidden()
            apply_proposal(
                proposal, actor=user.profile, policy_version="manual", note=note
            )
        elif action == "restore":
            if not permissions.can_apply_standardization(user):
                return HttpResponseForbidden()
            restore_proposal(proposal, actor=user.profile, note=note)
        elif action == "verify":
            if not permissions.can_review_standardization(user):
                return HttpResponseForbidden()
            if proposal.field_group != FieldGroup.SOLUTION:
                return HttpResponseForbidden()
            from judge.tasks.standardization import verify_solution

            verify_solution.apply_async(args=[proposal.pk])
        elif action == "certify":
            if not permissions.can_review_standardization(user):
                return HttpResponseForbidden()
            from judge.standardization.verification import record_editorial_review

            record_editorial_review(proposal, user.profile, note=note)
        else:
            return HttpResponseForbidden()
    except StandardizationError as exc:
        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({"ok": False, "error": str(exc)}, status=409)
        return _no_store(
            render(
                request,
                "internal/standardization/error.html",
                {
                    "title": "Không thực hiện được",
                    "error": str(exc),
                    "problem": proposal.problem,
                },
                status=409,
            )
        )
    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        proposal.refresh_from_db()
        from judge.standardization.routing import refresh_routes

        refresh_routes(proposal.problem, proposal.batch)
        return JsonResponse({"ok": True, "status": proposal.status})
    from judge.standardization.routing import refresh_routes

    refresh_routes(proposal.problem, proposal.batch)
    return redirect(
        reverse("internal_standardization_problem", args=(proposal.problem_id,))
    )
