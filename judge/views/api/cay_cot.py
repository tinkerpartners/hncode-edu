"""Read-only Cầy Cốt integration. Bearer credentials are scoped to these routes."""

import hashlib
import hmac
import re
import time
from datetime import timezone as utc
from functools import wraps
from urllib.parse import urljoin

from django.conf import settings
from django.core.cache import cache
from django.db.models import Q
from django.http import JsonResponse
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from judge.models import ContestParticipation, Profile, Submission

MAX_PAGE_SIZE = 100
REQUESTS_PER_MINUTE = 120
MAX_ID = 2**63 - 1


def response(data, status=200):
    result = JsonResponse(
        data, status=status, json_dumps_params={"ensure_ascii": False}
    )
    result["Cache-Control"] = "private, no-store"
    return result


def error(code, status):
    return response({"error": code}, status)


def authorized(view):
    @wraps(view)
    def handle(request, *args, **kwargs):
        if request.method != "GET":
            result = error("method_not_allowed", 405)
            result["Allow"] = "GET"
            return result
        auth = request.headers.get("Authorization", "")
        scheme, _, key = auth.partition(" ")
        if scheme.lower() != "bearer" or not re.fullmatch(
            r"[A-Za-z0-9_-]{32,128}", key
        ):
            digest = ""
        else:
            digest = hashlib.sha256(key.encode("ascii")).hexdigest()
        scope = next(
            (
                scope
                for known, scope in getattr(settings, "CAY_COT_SCOPES", {}).items()
                if digest and hmac.compare_digest(known, digest)
            ),
            None,
        )
        if scope is None:
            result = error("invalid_token", 401)
            result["WWW-Authenticate"] = "Bearer"
            return result
        minute = int(time.time()) // 60
        key = f"cay_cot:quota:{digest}:{minute}"
        try:
            if cache.add(key, 1, timeout=120):
                count = 1
            else:
                count = cache.incr(key)
        except (ValueError, OSError):
            # Eviction between add and incr or an unavailable cache.
            result = error("quota_unavailable", 503)
            result["Retry-After"] = "1"
            return result
        if count > REQUESTS_PER_MINUTE:
            result = error("rate_limited", 429)
            result["Retry-After"] = str(60 - int(time.time()) % 60)
            return result
        return view(request, scope, *args, **kwargs)

    # Every mutation is rejected by handle before authentication. Return the
    # same JSON 405 to service clients regardless of their browser cookies.
    return csrf_exempt(handle)


def integer(value, minimum=1, maximum=MAX_ID):
    if not re.fullmatch(r"[0-9]{1,19}", value):
        raise ValueError
    number = int(value)
    if not minimum <= number <= maximum:
        raise ValueError
    return number


def allowed_profiles(scope):
    return Profile.objects.filter(pk__in=scope.get("profile_ids", ())).select_related(
        "user"
    )


def user_json(profile):
    return {
        "id": str(profile.pk),
        "username": profile.user.username,
        "is_active": profile.user.is_active,
    }


@authorized
def find_user(request, scope):
    username = request.GET.get("username", "")
    if not username or len(username) > 150 or username != username.strip():
        return error("invalid_username", 400)
    # MariaDB's username collation is normally case insensitive.
    profile = allowed_profiles(scope).filter(user__username=username).first()
    if not profile or profile.user.username != username:
        return error("not_found", 404)
    return response(user_json(profile))


@authorized
def get_user(request, scope, user_id):
    try:
        user_id = integer(user_id)
    except ValueError:
        return error("invalid_user_id", 400)
    profile = allowed_profiles(scope).filter(pk=user_id).first()
    return response(user_json(profile)) if profile else error("not_found", 404)


def allowed_submissions(scope):
    public = Q(problem__is_public=True, problem__is_organization_private=False)
    problems = Q(problem_id__in=scope.get("problem_ids", ()))
    if scope.get("include_public_problems", False):
        problems |= public
    # A public practice problem can also be used in a private/live contest.
    # Check that context independently. Contest rows are eligible after the
    # contest ends and only for explicitly approved contest IDs.
    contests = Q(contest__isnull=True, contest_object__isnull=True) | Q(
        contest__participation__contest_id__in=scope.get("contest_ids", ()),
        contest__participation__contest__end_time__lte=timezone.now(),
        contest__participation__contest__is_visible=True,
        contest__participation__virtual=ContestParticipation.LIVE,
    )
    return (
        Submission.objects.filter(user_id__in=scope.get("profile_ids", ()))
        .filter(problems, contests)
        .select_related("problem", "contest__problem")
        .prefetch_related("problem__types")
    )


def submission_json(request, sub):
    contest_sub = sub.contest_or_none
    hidden = bool(
        contest_sub
        and (contest_sub.is_result_hidden or contest_sub.problem.is_result_hidden)
    )
    pretest = sub.is_pretested or bool(contest_sub and contest_sub.is_pretest)
    scored = not hidden and sub.status == "D" and sub.case_total > 0
    base = getattr(settings, "CAY_COT_SITE_URL", None) or request.build_absolute_uri(
        "/"
    )
    problem = sub.problem
    return {
        "id": str(sub.pk),
        "user_id": str(sub.user_id),
        "submitted_at": sub.date.astimezone(utc.utc).isoformat().replace("+00:00", "Z"),
        "status": None if hidden else sub.status,
        "verdict": sub.result if sub.is_graded and not hidden else None,
        "is_final": sub.is_graded if not hidden else None,
        "is_pretest": pretest if not hidden else None,
        "is_full_accept": (
            None
            if hidden
            else (
                sub.status == "D"
                and sub.result == "AC"
                and not pretest
                and sub.case_total > 0
                and sub.case_points == sub.case_total
            )
        ),
        "result_hidden": hidden,
        "is_team": False,
        "score": sub.case_points if scored else None,
        "max_score": sub.case_total if scored else None,
        "url": urljoin(base, sub.get_absolute_url()),
        "problem": {
            "id": str(problem.pk),
            "code": problem.code,
            "title": problem.name,
            "type": "PROGRAMMING",
            "difficulty_rating": getattr(settings, "CAY_COT_PROBLEM_RATINGS", {}).get(
                problem.pk
            ),
            "tags": sorted(tag.name for tag in problem.types.all()),
            "url": urljoin(base, reverse("problem_detail", args=(problem.code,))),
        },
    }


@authorized
def list_submissions(request, scope):
    try:
        user_id = integer(request.GET.get("user_id", ""))
        offset = integer(request.GET.get("offset", "0"), minimum=0, maximum=2**31 - 1)
        limit = integer(request.GET.get("limit", "100"), maximum=MAX_PAGE_SIZE)
    except ValueError:
        return error("invalid_parameters", 400)
    if (
        user_id not in scope.get("profile_ids", ())
        or not Profile.objects.filter(pk=user_id).exists()
    ):
        return error("not_found", 404)
    rows = list(
        allowed_submissions(scope)
        .filter(user_id=user_id)
        .order_by("-pk")[offset : offset + limit + 1]
    )
    return response(
        {
            "items": [submission_json(request, row) for row in rows[:limit]],
            "pagination": {
                "offset": offset,
                "limit": limit,
                "has_more": len(rows) > limit,
            },
        }
    )


@authorized
def get_submission(request, scope, submission_id):
    try:
        submission_id = integer(submission_id)
    except ValueError:
        return error("invalid_submission_id", 400)
    sub = allowed_submissions(scope).filter(pk=submission_id).first()
    return response(submission_json(request, sub)) if sub else error("not_found", 404)
