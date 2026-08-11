"""Server-side logic for strict ("proctored") contests.

The browser half of this feature is trivially bypassable -- a contestant can open
devtools and delete the script. The two things that are *not* bypassable live
here: the arm gate (you cannot submit into a strict contest without having
started a proctored session) and the liveness gate (you cannot submit if your
session stopped heartbeating). A contestant who kills the script therefore stops
being able to submit within ~90 seconds; they cannot silently opt out of
monitoring while still competing.
"""

from datetime import timedelta

from django.db import transaction
from django.db.models import F
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from judge.models.contest_strict import ContestViolationLog

__all__ = [
    "HEARTBEAT_INTERVAL",
    "HEARTBEAT_GRACE",
    "MONITORING_GAP",
    "record_violation",
    "strict_session_ok",
    "strict_state",
    "is_strict_live",
    "strict_ide_redirect",
]

# How often the browser is asked to check in.
HEARTBEAT_INTERVAL = 15
# How long a session may go silent before submissions are refused. Six missed
# beats -- generous enough for a slow network, short enough that killing the
# script costs you the contest.
HEARTBEAT_GRACE = 90
# A gap longer than this gets a log row: the script was gone and came back,
# which is something the client would never volunteer.
MONITORING_GAP = 90


def is_strict_live(participation):
    """True when this participation is one strict mode actually applies to.

    Editors and testers get a SPECTATE participation, and virtual runs happen
    after the contest is over -- neither is being proctored.
    """
    return bool(
        participation is not None
        and participation.live
        and participation.contest.is_strict
    )


def strict_state(participation, banned=None):
    """The state blob the client polls on; also the endpoints' response body."""
    contest = participation.contest
    return {
        "violations": participation.strict_violations,
        "limit": contest.strict_violation_limit,
        "grace_seconds": contest.strict_grace_seconds,
        "autoban": contest.strict_autoban,
        "armed": participation.strict_armed_at is not None,
        "banned": participation.is_disqualified if banned is None else banned,
    }


@transaction.atomic
def record_violation(
    participation, action, detail="", ip=None, force_ban=False, moderator=None
):
    """Write one violation row, advance the counter, and ban if warranted.

    Returns the state dict for the client. ``force_ban`` is for events that end
    a session outright regardless of the count (the grace period running out).
    """
    contest = participation.contest
    counted = action in ContestViolationLog.COUNTED_ACTIONS

    if counted:
        # F() so two tabs racing cannot both read the same pre-increment value.
        type(participation).objects.filter(pk=participation.pk).update(
            strict_violations=F("strict_violations") + 1
        )
        participation.refresh_from_db(fields=["strict_violations"])

    ContestViolationLog.log_action(
        participation,
        action,
        detail=detail,
        violation_number=participation.strict_violations if counted else 0,
        is_automated=moderator is None,
        moderator=moderator,
        ip=ip,
    )

    if participation.is_disqualified or not contest.strict_autoban:
        return strict_state(participation)

    over_limit = counted and participation.strict_violations >= (
        contest.strict_violation_limit
    )
    if not (over_limit or force_ban):
        return strict_state(participation)

    participation.set_disqualified(True)
    ContestViolationLog.log_action(
        participation,
        ContestViolationLog.AUTO_BAN,
        detail=_("Triggered by: %(action)s") % {"action": action},
        violation_number=participation.strict_violations,
        ip=ip,
    )
    return strict_state(participation, banned=True)


def strict_session_ok(participation):
    """The submission gate. Returns ``(ok, reason)``.

    This is the one check in the feature that does not depend on the client
    cooperating, so it is what actually makes strict mode mean something.
    """
    if participation.is_disqualified:
        return False, _(
            "You have been disqualified from this contest for repeatedly leaving "
            "the proctored session. Contact the contest administrator."
        )
    if participation.strict_armed_at is None:
        return False, _(
            "This contest requires a proctored fullscreen session. Return to the "
            "contest page and click "
            "“Start proctored session” before submitting."
        )
    last_seen = participation.strict_last_seen
    if last_seen is None or timezone.now() - last_seen > timedelta(
        seconds=HEARTBEAT_GRACE
    ):
        return False, _(
            "Your proctored session stopped responding, so this submission was "
            "not accepted. Return to the contest page and re-enter fullscreen."
        )
    return True, ""


def strict_ide_redirect(request, problem):
    """Send a strict-contest participant to the locked IDE for this problem.

    Returns a redirect response, or None when the normal page should render.
    """
    from django.http import HttpResponseRedirect

    profile = getattr(request, "profile", None)
    if profile is None:
        return None
    participation = profile.current_contest
    if not is_strict_live(participation):
        return None
    contest = participation.contest
    if not contest.contest_problems.filter(problem_id=problem.id).exists():
        return None
    return HttpResponseRedirect(
        reverse("contest_ide", args=(contest.key, problem.code))
    )
