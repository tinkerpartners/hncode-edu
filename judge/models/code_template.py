from django.db import models
from django.utils.translation import gettext_lazy as _

from judge.models.profile import Profile

# Personal starting code, one per language family. Each family covers every
# version of the language (C++11, C++17, C++20 share the C++ template).
CODE_TEMPLATE_KINDS = (
    ("cpp", "C++"),
    ("python", "Python"),
)
CODE_TEMPLATE_MAX_LENGTH = 65536


def code_template_kind(language):
    """The personal template family a judge language belongs to, or None."""
    name = (language.common_name or "").strip().lower()
    key = (language.key or "").upper()
    if name == "c++" or key.startswith("CPP"):
        return "cpp"
    if name in ("python", "pypy") or key.startswith(("PY", "PYPY")):
        return "python"
    return None


class ProfileCodeTemplate(models.Model):
    profile = models.ForeignKey(
        Profile,
        on_delete=models.CASCADE,
        related_name="code_templates",
        verbose_name=_("user"),
    )
    kind = models.CharField(
        max_length=10, choices=CODE_TEMPLATE_KINDS, verbose_name=_("language")
    )
    source = models.TextField(
        max_length=CODE_TEMPLATE_MAX_LENGTH, verbose_name=_("code template")
    )

    class Meta:
        verbose_name = _("personal code template")
        verbose_name_plural = _("personal code templates")
        constraints = [
            models.UniqueConstraint(
                fields=["profile", "kind"], name="unique_profile_code_template"
            )
        ]

    def __str__(self):
        return f"{self.profile} ({self.kind})"

    @classmethod
    def for_language(cls, profile, language):
        kind = code_template_kind(language)
        if kind is None:
            return None
        return (
            cls.objects.filter(profile=profile, kind=kind)
            .values_list("source", flat=True)
            .first()
        )

    @classmethod
    def save_for(cls, profile, kind, source):
        """Store one template; an empty one is removed."""
        if source and source.strip():
            cls.objects.update_or_create(
                profile=profile, kind=kind, defaults={"source": source}
            )
        else:
            cls.objects.filter(profile=profile, kind=kind).delete()
