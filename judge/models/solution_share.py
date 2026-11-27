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


def new_share_token():
    return secrets.token_urlsafe(24)


class SolutionShareLink(models.Model):
    """A secret link that shows one private editorial until it expires."""

    solution = models.ForeignKey(
        Solution,
        on_delete=models.CASCADE,
        related_name="share_links",
        verbose_name=_("editorial"),
    )
    token = models.CharField(
        max_length=64, unique=True, default=new_share_token, verbose_name=_("token")
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
    expires_at = models.DateTimeField(verbose_name=_("expires"))

    class Meta:
        ordering = ("-created_at",)
        verbose_name = _("editorial share link")
        verbose_name_plural = _("editorial share links")

    def __str__(self):
        return f"{self.solution} ({self.expires_at:%Y-%m-%d})"

    @classmethod
    def create_for(cls, solution, profile, duration_key):
        days = {key: days for key, days, _label in SHARE_DURATIONS}[duration_key]
        return cls.objects.create(
            solution=solution,
            created_by=profile,
            expires_at=timezone.now() + timedelta(days=days),
        )

    @classmethod
    def active(cls):
        return cls.objects.filter(expires_at__gt=timezone.now())

    @property
    def is_active(self):
        return self.expires_at > timezone.now()

    def get_absolute_url(self):
        return reverse("editorial_share", args=[self.token])
