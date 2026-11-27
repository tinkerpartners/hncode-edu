import hashlib
import secrets
from datetime import timedelta

from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from judge.models.problem import Solution
from judge.models.profile import Profile

# How long a new link lasts: (key, days, label).
SHARE_DURATIONS = (
    ("7d", 7, _("7 days")),
    ("30d", 30, _("30 days")),
    ("1y", 365, _("1 year")),
)

# Live links one editorial may have at a time.
MAX_ACTIVE_LINKS = 20


def hash_share_token(token):
    return hashlib.sha256(token.encode()).hexdigest()


def share_url(token):
    return reverse("editorial_share", args=[token])


class SolutionShareLink(models.Model):
    """A secret link that shows one private editorial until it expires.

    Only the SHA-256 of the secret is stored: the full link is shown once,
    right after it is made, so a copy of the database cannot open it.
    """

    solution = models.ForeignKey(
        Solution,
        on_delete=models.CASCADE,
        related_name="share_links",
        verbose_name=_("editorial"),
    )
    token_hash = models.CharField(
        max_length=64, unique=True, editable=False, verbose_name=_("token hash")
    )
    token_hint = models.CharField(
        max_length=8, editable=False, verbose_name=_("link starts with")
    )
    created_by = models.ForeignKey(
        Profile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name=_("created by"),
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("created"))
    expires_at = models.DateTimeField(db_index=True, verbose_name=_("expires"))

    class Meta:
        ordering = ("-created_at",)
        verbose_name = _("editorial share link")
        verbose_name_plural = _("editorial share links")

    def __str__(self):
        return f"{self.solution} ({self.token_hint}…, {self.expires_at:%Y-%m-%d})"

    @classmethod
    def create_for(cls, solution, profile, duration_key):
        """Make a link and return ``(link, secret)``. The secret is not stored."""
        days = {key: days for key, days, _label in SHARE_DURATIONS}[duration_key]
        token = secrets.token_urlsafe(24)
        link = cls.objects.create(
            solution=solution,
            token_hash=hash_share_token(token),
            token_hint=token[:6],
            created_by=profile,
            expires_at=timezone.now() + timedelta(days=days),
        )
        return link, token

    @classmethod
    def active(cls):
        return cls.objects.filter(expires_at__gt=timezone.now())

    @classmethod
    def by_token(cls, token):
        return cls.active().filter(token_hash=hash_share_token(token))

    @classmethod
    def purge_expired(cls):
        return cls.objects.filter(expires_at__lte=timezone.now()).delete()[0]

    @property
    def is_active(self):
        return self.expires_at > timezone.now()
