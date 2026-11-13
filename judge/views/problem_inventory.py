from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponseForbidden
from django.shortcuts import render
from django.views.decorators.http import require_GET

from judge.utils.problem_inventory import (
    ISSUE_LABELS,
    STATE_LABELS,
    inventory_queryset,
    inventory_rows,
)


@require_GET
def problem_inventory(request):
    if not request.user.is_authenticated:
        from django.contrib.auth.views import redirect_to_login

        return redirect_to_login(request.get_full_path())
    if not request.user.is_superuser:
        return HttpResponseForbidden()
    query = request.GET.get("q", "").strip()
    queryset = inventory_queryset()
    if query:
        queryset = queryset.filter(Q(code__icontains=query) | Q(name__icontains=query))
    page = Paginator(queryset, 30).get_page(request.GET.get("page"))
    response = render(
        request,
        "internal/problem_inventory.html",
        {
            "title": "Kiểm kê ngân hàng bài",
            "rows": list(inventory_rows(page.object_list)),
            "page": page,
            "query": query,
            "states": STATE_LABELS,
            "issues": ISSUE_LABELS,
        },
    )
    response["Cache-Control"] = "private, no-store"
    return response
