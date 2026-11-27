"""Secret, expiring links to a private editorial."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from judge.models import Problem, Solution, SolutionShareLink
from judge.models.solution_share import SHARE_DURATIONS


def editorial_is_private(solution):
    """Whether the editorial page is closed to ordinary visitors."""
    from django.utils import timezone

    problem = solution.problem
    return not (
        solution.is_public
        and solution.publish_on <= timezone.now()
        and problem is not None
        and problem.is_public
        and not problem.is_organization_private
    )


def can_share(user, solution):
    return (
        user.is_authenticated
        and solution.problem is not None
        and solution.problem.is_editable_by(user)
        and solution.is_accessible_by(user)
    )


def _solution_for_editor(request, problem):
    problem = get_object_or_404(Problem, code=problem)
    solution = get_object_or_404(Solution, problem=problem)
    if not can_share(request.user, solution):
        raise Http404()
    return problem, solution


def _back(problem):
    return HttpResponseRedirect(
        reverse("problem_editorial", args=[problem.code]) + "#editorial-share"
    )


@login_required
@require_POST
def create_share_link(request, problem):
    problem, solution = _solution_for_editor(request, problem)
    duration = request.POST.get("duration")
    if duration not in {key for key, _days, _label in SHARE_DURATIONS}:
        raise Http404()
    SolutionShareLink.create_for(solution, request.profile, duration)
    messages.success(request, _("Share link created."))
    return _back(problem)


@login_required
@require_POST
def revoke_share_link(request, problem, link):
    problem, solution = _solution_for_editor(request, problem)
    get_object_or_404(SolutionShareLink, pk=link, solution=solution).delete()
    messages.success(request, _("Share link revoked."))
    return _back(problem)


def shared_editorial(request, token):
    link = (
        SolutionShareLink.active()
        .select_related("solution__problem")
        .filter(token=token)
        .first()
    )
    if link is None or link.solution.problem is None:
        response = render(
            request,
            "generic-message.html",
            {
                "title": _("Link expired"),
                "message": _(
                    "This editorial link has expired or been revoked. Ask the "
                    "person who shared it for a new one."
                ),
            },
            status=404,
        )
        response["X-Robots-Tag"] = "noindex, nofollow"
        return response
    response = render(
        request,
        "problem/editorial-raw.html",
        {
            "solution": link.solution,
            "problem": link.solution.problem,
            "share_link": link,
            "back_url": "",
            "back_label": "",
        },
    )
    response["X-Robots-Tag"] = "noindex, nofollow"
    response["Referrer-Policy"] = "no-referrer"
    response["Cache-Control"] = "private, no-store"
    return response
