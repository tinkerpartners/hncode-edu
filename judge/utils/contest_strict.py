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
    "is_strict_locked",
    "lock_participation",
    "unlock_participation",
    "strict_ide_redirect",
    "strict_locked_response",
    "strict_problem_lock",
    "strict_quiz_gate",
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


def is_strict_locked(participation):
    """Locked out of a strict contest until an administrator unlocks them (A18)."""
    return bool(
        is_strict_live(participation) and participation.strict_locked_at is not None
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
        "locked": participation.strict_locked_at is not None,
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

    if (
        participation.is_disqualified
        or participation.strict_locked_at is not None
        or not contest.strict_autoban
    ):
        return strict_state(participation)

    over_limit = counted and participation.strict_violations >= (
        contest.strict_violation_limit
    )
    if not (over_limit or force_ban):
        return strict_state(participation)

    # A18: lock, do not disqualify. The contestant keeps their scores and their
    # rating is not touched; an administrator decides whether to let them back
    # in. (Disqualifying is still available, by hand, from the rankings page.)
    lock_participation(
        participation,
        detail=_("Triggered by: %(action)s") % {"action": action},
        ip=ip,
    )
    return strict_state(participation)


def lock_participation(participation, detail="", ip=None):
    """Lock a strict participant out, log it and tell everyone concerned."""
    if participation.strict_locked_at is not None:
        return
    participation.strict_locked_at = timezone.now()
    participation.save(update_fields=["strict_locked_at"])
    ContestViolationLog.log_action(
        participation,
        ContestViolationLog.AUTO_LOCK,
        detail=detail,
        violation_number=participation.strict_violations,
        ip=ip,
    )
    notify_locked(participation)
    notify_proctors(participation, detail)


def unlock_participation(participation, moderator, reason=""):
    """Let a locked participant back in, with a clean session (A18).

    reset_strict_session() clears the lock, the counter and the armed flag, so
    the contestant re-arms fullscreen like at the start and does not come back
    already at the limit. Their submissions and quiz answers are untouched.
    """
    participation.reset_strict_session()
    ContestViolationLog.log_action(
        participation,
        ContestViolationLog.ADMIN_UNLOCK,
        detail=reason,
        is_automated=False,
        moderator=moderator,
    )
    from django.utils.html import format_html

    from judge.models.notification import Notification, NotificationCategory

    Notification.objects.create_notification(
        owner=participation.user,
        category=NotificationCategory.CONTEST_UNLOCKED,
        html_link=format_html(
            "{}",
            _('You can continue "%(contest)s". Re-enter fullscreen to resume.')
            % {"contest": participation.contest.name},
        ),
        author=moderator,
        deduplicate=False,
    )


def notify_locked(participation):
    """Tell the contestant, in-app, that they are locked out and why."""
    from django.utils.html import format_html

    from judge.models.notification import Notification, NotificationCategory

    contest = participation.contest
    Notification.objects.create_notification(
        owner=participation.user,
        category=NotificationCategory.CONTEST_LOCKED,
        html_link=format_html(
            "{}<br>{}",
            _('You were locked out of "%(contest)s" for leaving the proctored session.')
            % {"contest": contest.name},
            _(
                "Your work so far is kept. Ask the contest administrator to "
                "unlock you to continue."
            ),
        ),
        author=None,
        deduplicate=False,
    )


def notify_proctors(participation, detail=""):
    """Alert the contest's authors and curators that someone was locked out."""
    from django.utils.html import format_html

    from judge.models.notification import Notification, NotificationCategory

    contest = participation.contest
    proctors = set(contest.authors.values_list("id", flat=True)) | set(
        contest.curators.values_list("id", flat=True)
    )
    proctors.discard(participation.user_id)
    if not proctors:
        return
    from judge.models import Profile

    link = reverse("contest_strict_violations", args=(contest.key,))
    for proctor in Profile.objects.filter(id__in=proctors):
        Notification.objects.create_notification(
            owner=proctor,
            category=NotificationCategory.CONTEST_PROCTOR_ALERT,
            html_link=format_html(
                '<a href="{}">{}</a>',
                link,
                _('%(user)s was locked out of "%(contest)s" (%(detail)s).')
                % {
                    "user": participation.user.username,
                    "contest": contest.name,
                    "detail": detail,
                },
            ),
            author=participation.user,
            deduplicate=False,
        )


def notify_disqualified(participation):
    """Tell the contestant, in-app, why they were removed.

    Being ejected mid-contest with only a transient overlay to explain it is not
    good enough -- the overlay is gone as soon as they navigate, and they are
    left guessing. This survives the page.
    """
    from django.utils.html import format_html

    from judge.models.notification import Notification, NotificationCategory

    contest = participation.contest
    Notification.objects.create_notification(
        owner=participation.user,
        category=NotificationCategory.CONTEST_DISQUALIFIED,
        html_link=format_html(
            "{}<br>{}",
            _(
                'You were disqualified from "%(contest)s" for leaving the proctored session.'
            )
            % {"contest": contest.name},
            _(
                "You cannot rejoin this contest. Contact the contest "
                "administrator if you believe this was a mistake."
            ),
        ),
        author=None,
        deduplicate=False,
    )


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
    if participation.strict_locked_at is not None:
        return False, _(
            "You are locked out of this contest for leaving the proctored "
            "session. Your work so far is kept; ask the contest administrator "
            "to unlock you."
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
    if participation.strict_locked_at is not None:
        return strict_locked_response(request, participation)
    return HttpResponseRedirect(
        reverse("contest_ide", args=(contest.key, problem.code))
    )


def strict_problem_lock(request, problem):
    """The locked page if a locked strict contestant asks for a statement of
    their contest (PDF, raw); otherwise None."""
    profile = getattr(request, "profile", None)
    participation = getattr(profile, "current_contest", None) if profile else None
    if not is_strict_locked(participation):
        return None
    if not participation.contest.contest_problems.filter(
        problem_id=problem.id
    ).exists():
        return None
    return strict_locked_response(request, participation)


def strict_locked_response(request, participation):
    """The page a locked-out contestant gets instead of a problem or a quiz."""
    from judge.utils.views import generic_message

    return generic_message(
        request,
        _("Locked out of the contest"),
        _(
            "You left the proctored session, so you are locked out of "
            '"%(contest)s". Your work so far is kept. Ask the contest '
            "administrator to unlock you, then reopen the contest."
        )
        % {"contest": participation.contest.name},
        status=403,
    )


def strict_quiz_gate(attempt, need_live_session=True):
    """May this quiz attempt be worked on right now? Returns ``(ok, reason)``.

    Quizzes inside a strict contest get the same server-side gate as problem
    submissions (A18): a locked or disqualified participant is refused, and
    saving or submitting needs an armed, still-beating fullscreen session.
    Attempts outside a strict contest are never affected.
    """
    participation = getattr(attempt, "contest_participation", None)
    if not is_strict_live(participation):
        return True, ""
    if not need_live_session:
        if participation.is_disqualified or participation.strict_locked_at:
            return strict_session_ok(participation)
        return True, ""
    return strict_session_ok(participation)
