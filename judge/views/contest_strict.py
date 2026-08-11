"""Endpoints and admin views for strict ("proctored") contests.

The two POST endpoints here are called by ``resources/contest-strict.js``. They
are the only writers of client-reported violations, and they treat everything
the browser sends as hostile: the action must be one of the client-reportable
ones, the reporter must actually hold a live participation in the contest they
name, and repeated reports of one physical event are collapsed so that a single
alt-tab costs one violation rather than three.
"""

import json
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.cache import cache
from django.db.models import Count, Max
from django.http import Http404, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST
from django.views.generic import ListView, View
from django.views.generic.detail import SingleObjectMixin

from judge.models import Contest, ContestParticipation, ContestViolationLog
from judge.utils.contest_strict import (
    HEARTBEAT_INTERVAL,
    MONITORING_GAP,
    is_strict_live,
    record_violation,
    strict_state,
)
from judge.utils.ratelimit import get_client_ip, ratelimit
from judge.utils.views import TitleMixin
from judge.views.contests import ContestMixin

__all__ = [
    "contest_strict_event",
    "contest_strict_heartbeat",
    "ContestViolationList",
    "ContestStrictUnban",
]

# Two events fired by one physical action land in the same bucket, and only the
# first one inside the window is counted. Alt-tabbing raises blur *and*
# visibilitychange *and* fullscreenchange; without this the first tab switch of
# any contest would immediately exhaust a limit of three.
COALESCE_BUCKETS = {
    ContestViolationLog.FULLSCREEN_EXIT: "focus",
    ContestViolationLog.FOCUS_LOST: "focus",
    ContestViolationLog.NAVIGATE_AWAY: "focus",
}
COALESCE_SECONDS = 3
NONCE_TTL = 60
# One UPDATE per this many seconds, however often the client beats.
HEARTBEAT_WRITE_THROTTLE = 10

INACTIVE = {"ok": False, "state": "inactive"}


def _resolve(request, key):
    """Return (contest, participation) or None when strict mode does not apply.

    A stale tab left open after the contest ended, a spectator, or a contest
    that had strict mode switched off mid-flight all land here, and all of them
    should make the client quietly disarm rather than error.
    """
    contest = get_object_or_404(Contest, key=key)
    participation = request.profile.current_contest
    if participation is None or participation.contest_id != contest.id:
        return None
    if not is_strict_live(participation) or participation.ended:
        return None
    return contest, participation


def _payload(request):
    if request.content_type == "application/json":
        try:
            return json.loads(request.body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
    return request.POST


@ratelimit(key="user", rate=settings.RL_STRICT_EVENT)
@login_required
@require_POST
def contest_strict_event(request, contest):
    resolved = _resolve(request, contest)
    if resolved is None:
        return JsonResponse(INACTIVE)
    _contest, participation = resolved

    data = _payload(request)
    if data is None:
        return JsonResponse({"error": _("Invalid request data")}, status=400)

    action = (data.get("action") or "").strip()
    detail = data.get("detail") or ""
    nonce = (data.get("nonce") or "").strip()[:64]

    # The privilege boundary: auto_ban / admin_unban / strict_gap justify a ban
    # or clear one, so a browser must never be able to write them.
    if action not in ContestViolationLog.CLIENT_ACTIONS:
        return JsonResponse({"error": _("Unknown action")}, status=400)

    if participation.is_disqualified:
        return JsonResponse(
            dict(
                strict_state(participation),
                ok=True,
                redirect=reverse("contest_view", args=(_contest.key,)),
            )
        )

    ip = get_client_ip(request)

    if action in (ContestViolationLog.SESSION_START, ContestViolationLog.RETURNED):
        now = timezone.now()
        fields = ["strict_last_seen"]
        participation.strict_last_seen = now
        if participation.strict_armed_at is None:
            participation.strict_armed_at = now
            fields.append("strict_armed_at")
        participation.save(update_fields=fields)
        ContestViolationLog.log_action(participation, action, detail=detail, ip=ip)
        return JsonResponse(dict(strict_state(participation), ok=True))

    # Retry of a request we already handled (the client retries on network
    # failure, and beforeunload reports can arrive twice).
    if nonce:
        nonce_key = "strict:nonce:%d:%s" % (participation.id, nonce)
        if not cache.add(nonce_key, 1, NONCE_TTL):
            return JsonResponse(dict(strict_state(participation), ok=True))

    bucket = COALESCE_BUCKETS.get(action)
    if bucket is not None:
        bucket_key = "strict:last:%d:%s" % (participation.id, bucket)
        if not cache.add(bucket_key, 1, COALESCE_SECONDS):
            return JsonResponse(dict(strict_state(participation), ok=True))

    force_ban = False
    if action == ContestViolationLog.GRACE_EXPIRED:
        # Only honour "my countdown ran out" if the server can independently see
        # the exit that started it. Otherwise a bug -- or a tampered client --
        # could disqualify someone who never left.
        deadline = timezone.now() - timedelta(
            seconds=max(_contest.strict_grace_seconds - 2, 1)
        )
        force_ban = participation.violations.filter(
            action__in=(
                ContestViolationLog.FULLSCREEN_EXIT,
                ContestViolationLog.FOCUS_LOST,
            ),
            created__lte=deadline,
        ).exists()

    state = record_violation(
        participation, action, detail=detail, ip=ip, force_ban=force_ban
    )
    if state["banned"]:
        state["redirect"] = reverse("contest_view", args=(_contest.key,))
    state["ok"] = True
    return JsonResponse(state)


@ratelimit(key="user", rate=settings.RL_STRICT_HEARTBEAT)
@login_required
@require_POST
def contest_strict_heartbeat(request, contest):
    resolved = _resolve(request, contest)
    if resolved is None:
        return JsonResponse(INACTIVE)
    _contest, participation = resolved

    if participation.is_disqualified:
        return JsonResponse(
            dict(
                strict_state(participation),
                ok=True,
                redirect=reverse("contest_view", args=(_contest.key,)),
            )
        )

    now = timezone.now()
    ip = get_client_ip(request)
    previous = participation.strict_last_seen

    # Two things the server can see that the client would never volunteer: the
    # script having been gone for a while, and the session moving to a different
    # address. Neither is counted -- flaky wifi and mobile NAT handoff both
    # produce them innocently -- but both belong in the ledger.
    if previous is not None and (now - previous) > timedelta(seconds=MONITORING_GAP):
        ContestViolationLog.log_action(
            participation,
            ContestViolationLog.STRICT_GAP,
            detail=_("No heartbeat for %(seconds)d seconds")
            % {"seconds": int((now - previous).total_seconds())},
            ip=ip,
        )

    if ip and ip != "unknown":
        last_ip = (
            participation.violations.exclude(ip=None)
            .order_by("-created")
            .values_list("ip", flat=True)
            .first()
        )
        if last_ip and last_ip != ip:
            ContestViolationLog.log_action(
                participation,
                ContestViolationLog.IP_CHANGE,
                detail=_("%(old)s to %(new)s") % {"old": last_ip, "new": ip},
                ip=ip,
            )

    throttle_key = "strict:beat:%d" % participation.id
    if cache.add(throttle_key, 1, HEARTBEAT_WRITE_THROTTLE):
        participation.strict_last_seen = now
        participation.save(update_fields=["strict_last_seen"])

    return JsonResponse(
        dict(strict_state(participation), ok=True, interval=HEARTBEAT_INTERVAL)
    )


class ContestStrictAdminMixin(LoginRequiredMixin, ContestMixin):
    """Same permission as the rankings-page disqualify button."""

    def get_object(self, queryset=None):
        # Resolve directly rather than through ContestMixin: on the list view
        # get_queryset() returns violation rows, so the inherited
        # SingleObjectMixin lookup would search the wrong model.
        contest = get_object_or_404(Contest, key=self.kwargs[self.slug_url_kwarg])
        if not contest.is_editable_by(self.request.user):
            raise Http404()
        return contest


class ContestViolationList(
    ContestStrictAdminMixin, TitleMixin, SingleObjectMixin, ListView
):
    template_name = "contest/violations.html"
    context_object_name = None
    paginate_by = 50

    def get(self, request, *args, **kwargs):
        self.object = self.get_object()
        return super().get(request, *args, **kwargs)

    def get_title(self):
        return _("Strict mode violations for %s") % self.object.name

    def get_queryset(self):
        queryset = self.object.violation_logs.select_related(
            "participation__user__user", "moderator__user"
        )
        user = self.request.GET.get("user")
        if user:
            queryset = queryset.filter(participation__user__user__username=user)
        action = self.request.GET.get("action")
        if action:
            queryset = queryset.filter(action=action)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        summary = (
            self.object.users.filter(strict_violations__gt=0)
            .select_related("user__user")
            .order_by("-strict_violations")
        )
        counts = dict(
            self.object.violation_logs.values_list("participation")
            .annotate(n=Count("id"))
            .values_list("participation", "n")
        )
        last_events = dict(
            self.object.violation_logs.values_list("participation")
            .annotate(last=Max("created"))
            .values_list("participation", "last")
        )
        context["summary"] = [
            {
                "participation": p,
                "events": counts.get(p.id, 0),
                "last_event": last_events.get(p.id),
            }
            for p in summary
        ]
        context["filter_user"] = self.request.GET.get("user", "")
        context["filter_action"] = self.request.GET.get("action", "")
        context["violation_actions"] = ContestViolationLog.ACTIONS
        context["page_type"] = "violations"

        # Not paginate_query_context(): it drops ?user=, which is one of our
        # filters, so paging a filtered log would silently widen it.
        query = self.request.GET.copy()
        query.setlist("page", [])
        query = query.urlencode()
        path = self.request.path
        if query:
            context["page_prefix"] = "%s?%s&page=" % (path, query)
            context["first_page_href"] = "%s?%s" % (path, query)
        else:
            context["page_prefix"] = "%s?page=" % path
            context["first_page_href"] = path
        return context


class ContestStrictUnban(ContestStrictAdminMixin, SingleObjectMixin, View):
    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        try:
            participation = self.object.users.get(pk=request.POST.get("participation"))
        except ContestParticipation.DoesNotExist:
            raise Http404()

        # set_disqualified(False) is what clears banned_users *and* resets the
        # strict counter, so the participant does not come back already at the
        # limit.
        if participation.is_disqualified:
            participation.set_disqualified(False)
        else:
            participation.reset_strict_session()

        ContestViolationLog.log_action(
            participation,
            ContestViolationLog.ADMIN_UNBAN,
            detail=request.POST.get("reason", ""),
            is_automated=False,
            moderator=request.profile,
        )
        return HttpResponseRedirect(
            reverse("contest_strict_violations", args=(self.object.key,))
        )
