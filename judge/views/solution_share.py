"""Secret, expiring links to a private editorial."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from judge.models import ContestProblem, Problem, Solution, SolutionShareLink
from judge.models.solution_share import MAX_ACTIVE_LINKS, SHARE_DURATIONS, share_url

# Session key for the link made a moment ago, shown once on the editorial.
NEW_LINK_SESSION_KEY = "editorial_share_new"


def editorial_is_private(solution):
    """Whether the editorial page is closed to ordinary visitors."""
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


def problem_in_live_contest(problem, profile=None):
    """Whether the problem is part of a contest that is running now, or of
    the viewer's own attempt that has not ended (virtual and mock attempts
    included)."""
    now = timezone.now()
    if ContestProblem.objects.filter(
        problem=problem, contest__start_time__lte=now, contest__end_time__gt=now
    ).exists():
        return True
    participation = getattr(profile, "current_contest", None) if profile else None
    return bool(
        participation is not None
        and not participation.ended
        and participation.contest.contest_problems.filter(
            problem_id=problem.id
        ).exists()
    )


def take_new_link(request, solution):
    """The full link made a moment ago for this editorial, once."""
    new = request.session.get(NEW_LINK_SESSION_KEY)
    if not new or new.get("solution") != solution.pk:
        return None
    del request.session[NEW_LINK_SESSION_KEY]
    return new.get("url")


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
    SolutionShareLink.purge_expired()
    if SolutionShareLink.active().filter(solution=solution).count() >= (
        MAX_ACTIVE_LINKS
    ):
        messages.error(
            request,
            _("This editorial already has %(count)d live links. Revoke some first.")
            % {"count": MAX_ACTIVE_LINKS},
        )
        return _back(problem)
    _link, token = SolutionShareLink.create_for(solution, request.profile, duration)
    request.session[NEW_LINK_SESSION_KEY] = {
        "solution": solution.pk,
        "url": request.build_absolute_uri(share_url(token)),
    }
    messages.success(request, _("Share link created."))
    return _back(problem)


@login_required
@require_POST
def revoke_share_link(request, problem, link):
    problem, solution = _solution_for_editor(request, problem)
    get_object_or_404(SolutionShareLink, pk=link, solution=solution).delete()
    messages.success(request, _("Share link revoked."))
    return _back(problem)


@login_required
@require_POST
def revoke_all_share_links(request, problem):
    problem, solution = _solution_for_editor(request, problem)
    SolutionShareLink.objects.filter(solution=solution).delete()
    request.session.pop(NEW_LINK_SESSION_KEY, None)
    messages.success(request, _("All share links revoked."))
    return _back(problem)


def _private_response(response):
    response["X-Robots-Tag"] = "noindex, nofollow"
    response["Referrer-Policy"] = "no-referrer"
    response["Cache-Control"] = "private, no-store"
    return response


def shared_editorial(request, token):
    link = (
        SolutionShareLink.by_token(token)
        .select_related("solution__problem", "created_by__user")
        .first()
    )
    # A link only lasts while the person who made it may still share it.
    if (
        link is None
        or link.solution.problem is None
        or link.created_by is None
        or not can_share(link.created_by.user, link.solution)
    ):
        return _private_response(
            render(
                request,
                "generic-message.html",
                {
                    "title": _("Link expired"),
                    "message": _(
                        "This editorial link has expired or been revoked. Ask "
                        "the person who shared it for a new one."
                    ),
                },
                status=404,
            )
        )
    problem = link.solution.problem
    if problem_in_live_contest(problem, getattr(request, "profile", None)):
        return _private_response(
            render(
                request,
                "generic-message.html",
                {
                    "title": _("Editorial unavailable"),
                    "message": _(
                        "This problem is in a contest that is running now, so "
                        "its editorial cannot be opened. Try again after the "
                        "contest ends."
                    ),
                },
                status=403,
            )
        )
    return _private_response(
        render(
            request,
            "problem/editorial-raw.html",
            {
                "solution": link.solution,
                "problem": problem,
                "share_link": link,
                "back_url": "",
                "back_label": "",
            },
        )
    )
