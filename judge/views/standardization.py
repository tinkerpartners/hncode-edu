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

from django.contrib.auth.views import redirect_to_login
from django.core.paginator import Paginator
from django.db.models import Count
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from judge.models import Problem, ProblemType
from judge.models.standardization import StandardizationProposal
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
    ProposalStatus,
    VerificationLevel,
)
from judge.standardization.snapshot import capture_group
from judge.standardization.verification import latest_level

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
)


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
    qs = StandardizationProposal.objects.select_related("problem", "batch").order_by(
        "-created_at"
    )
    if status == "open":
        qs = qs.filter(status__in=OPEN_STATUSES)
    elif status:
        qs = qs.filter(status=status)
    if group:
        qs = qs.filter(field_group=group)
    if batch.isdigit():
        qs = qs.filter(batch_id=int(batch))
    counts = dict(
        StandardizationProposal.objects.values_list("status").annotate(c=Count("id"))
    )
    page = Paginator(qs, 50).get_page(request.GET.get("page"))
    return _no_store(
        render(
            request,
            "internal/standardization/queue.html",
            {
                "title": "Chuẩn hóa bài — hàng chờ duyệt",
                "page": page,
                "status": status,
                "group": group,
                "batch": batch,
                "counts": counts,
                "statuses": ProposalStatus.choices,
                "groups": FieldGroup.choices,
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
    latest = {}
    for proposal in (
        StandardizationProposal.objects.filter(problem=problem)
        .select_related("snapshot", "batch")
        .order_by("-created_at")
    ):
        latest.setdefault(proposal.field_group, proposal)
    groups = [
        _group_view(problem, latest[g], can_see_source)
        for g in GROUP_ORDER
        if g in latest
    ]
    applied = {g for g, p in latest.items() if p.status == ProposalStatus.APPLIED}
    solution_ok = FieldGroup.SOLUTION in latest and latest_level(
        latest[FieldGroup.SOLUTION]
    ) in (
        VerificationLevel.CURRENT_TESTS_PASSED,
        VerificationLevel.ADDITIONAL_CHECKS_PASSED,
    )
    complete = applied == set(GROUP_ORDER) and solution_ok
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
                "groups": groups,
                "history": list(history),
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
def standardization_action(request, proposal_id, action):
    if not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path())
    proposal = get_object_or_404(
        StandardizationProposal.objects.select_related("problem"), pk=proposal_id
    )
    user = request.user
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
        return JsonResponse({"ok": True, "status": proposal.status})
    return redirect(
        reverse("internal_standardization_problem", args=(proposal.problem_id,))
    )
