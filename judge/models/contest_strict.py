from django.db import models
from django.db.models import CASCADE
from django.utils.translation import gettext_lazy as _

from judge.models.contest import Contest, ContestParticipation
from judge.models.profile import Profile

__all__ = ["ContestViolationLog"]


class ContestViolationLog(models.Model):
    """Append-only ledger of strict-mode events for one contest participation.

    Rows are written both by the browser (via the strict event endpoint) and by
    the server. The two sets are disjoint on purpose: a client that posts a
    server-only action must be rejected, since those actions are the ones that
    justify a ban.
    """

    # Reportable by the browser.
    SESSION_START = "session_start"
    RETURNED = "returned"
    FULLSCREEN_EXIT = "fullscreen_exit"
    FOCUS_LOST = "focus_lost"
    PASTE = "paste"
    COPY = "copy"
    CUT = "cut"
    CONTEXT_MENU = "context_menu"
    BLOCKED_KEY = "blocked_key"
    NAVIGATE_AWAY = "navigate_away"
    GRACE_EXPIRED = "grace_expired"

    # Written by the server only.
    STRICT_GAP = "strict_gap"
    IP_CHANGE = "ip_change"
    AUTO_BAN = "auto_ban"
    ADMIN_UNBAN = "admin_unban"
    SESSION_END = "session_end"

    ACTIONS = (
        (SESSION_START, _("Session started")),
        (RETURNED, _("Returned to fullscreen")),
        (FULLSCREEN_EXIT, _("Left fullscreen")),
        (FOCUS_LOST, _("Switched tab or window")),
        (PASTE, _("Paste blocked")),
        (COPY, _("Copy blocked")),
        (CUT, _("Cut blocked")),
        (CONTEXT_MENU, _("Context menu blocked")),
        (BLOCKED_KEY, _("Blocked keyboard shortcut")),
        (NAVIGATE_AWAY, _("Tried to navigate away")),
        (GRACE_EXPIRED, _("Grace period expired")),
        (STRICT_GAP, _("Monitoring gap")),
        (IP_CHANGE, _("IP address changed")),
        (AUTO_BAN, _("Automatically disqualified")),
        (ADMIN_UNBAN, _("Unbanned by an administrator")),
        (SESSION_END, _("Session ended")),
    )

    CLIENT_ACTIONS = frozenset(
        (
            SESSION_START,
            RETURNED,
            FULLSCREEN_EXIT,
            FOCUS_LOST,
            PASTE,
            COPY,
            CUT,
            CONTEXT_MENU,
            BLOCKED_KEY,
            NAVIGATE_AWAY,
            GRACE_EXPIRED,
        )
    )

    SERVER_ACTIONS = frozenset(
        (STRICT_GAP, IP_CHANGE, AUTO_BAN, ADMIN_UNBAN, SESSION_END)
    )

    # Actions that move the violation counter. Deliberately narrow: context menu,
    # blocked keys, copy, monitoring gaps and IP changes are recorded for a human
    # to read but never ban anyone by themselves, because all of them fire for
    # innocent reasons (screen readers, mobile NAT handoff, flaky wifi).
    COUNTED_ACTIONS = frozenset(
        (FULLSCREEN_EXIT, FOCUS_LOST, PASTE, CUT, NAVIGATE_AWAY)
    )

    DETAIL_MAX_LENGTH = 255

    contest = models.ForeignKey(
        Contest,
        verbose_name=_("contest"),
        related_name="violation_logs",
        on_delete=CASCADE,
    )
    participation = models.ForeignKey(
        ContestParticipation,
        verbose_name=_("participation"),
        related_name="violations",
        on_delete=CASCADE,
    )
    action = models.CharField(verbose_name=_("action"), max_length=32, choices=ACTIONS)
    detail = models.CharField(
        verbose_name=_("detail"), max_length=DETAIL_MAX_LENGTH, blank=True
    )
    violation_number = models.PositiveIntegerField(
        verbose_name=_("violation number"),
        default=0,
        help_text=_("Counter value after this event, or 0 if it was not counted."),
    )
    is_automated = models.BooleanField(verbose_name=_("automated"), default=True)
    moderator = models.ForeignKey(
        Profile,
        verbose_name=_("moderator"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    ip = models.GenericIPAddressField(verbose_name=_("IP address"), null=True, blank=True)
    created = models.DateTimeField(verbose_name=_("event time"), auto_now_add=True)

    class Meta:
        ordering = ["-created"]
        indexes = [
            models.Index(fields=["contest", "-created"]),
            models.Index(fields=["participation", "-created"]),
        ]
        verbose_name = _("contest violation log")
        verbose_name_plural = _("contest violation logs")

    def __str__(self):
        return "%s: %s @ %s" % (
            self.participation_id,
            self.action,
            self.created,
        )

    @property
    def is_counted(self):
        return self.action in self.COUNTED_ACTIONS

    @classmethod
    def sanitize_detail(cls, detail):
        """Client-supplied free text: drop control characters, then truncate."""
        if not detail:
            return ""
        detail = "".join(c for c in str(detail) if c == " " or c.isprintable())
        return detail[: cls.DETAIL_MAX_LENGTH]

    @classmethod
    def log_action(
        cls,
        participation,
        action,
        detail="",
        violation_number=0,
        is_automated=True,
        moderator=None,
        ip=None,
    ):
        return cls.objects.create(
            contest_id=participation.contest_id,
            participation=participation,
            action=action,
            detail=cls.sanitize_detail(detail),
            violation_number=violation_number,
            is_automated=is_automated,
            moderator=moderator,
            ip=ip,
        )
