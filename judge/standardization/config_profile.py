"""The five mechanical configuration rules (spec §2 / F08).

* ``partial = True``
* ``allowed_languages`` ⊇ {C++17, C++20, Python 3, PyPy 3}
* ``memory_limit`` = 1024 MB (stored in KB), and every ``LanguageLimit`` row
  raised to at least that value, since the bridge prefers the per-language row
* ``authors`` ∋ the standard author account (``MrTee``)
* ``Solution.is_public = False``

``plan_config`` is pure and only reads; ``apply_config_diff`` and
``revert_config_diff`` do the ORM writes through ``save()`` / ``.add()`` so the
cache-invalidation signals fire. Nothing here touches ``Problem.points``;
``_bypass_points_cap`` is set only so a save does not clamp an existing value.
"""

from dataclasses import asdict, dataclass, field

from django.conf import settings

from judge.models import Language, Profile
from judge.models.problem import LanguageLimit

REQUIRED_LANGUAGE_KEYS = ("CPP17", "CPP20", "PY3", "PYPY3")
TARGET_MEMORY_KB = 1024 * 1024  # 1024 MB; the edit form divides by 1024 for display
CONFIG_PROFILE_VERSION = "mechanical_config_v1"


class ProfileResolutionError(Exception):
    """The fixed rules cannot be resolved against this database."""


@dataclass(frozen=True)
class ProfileContext:
    author_profile_id: int
    author_username: str
    language_ids: dict  # key -> Language id
    target_memory_kb: int = TARGET_MEMORY_KB


@dataclass
class ConfigDiff:
    partial_from: bool
    partial_to: bool
    memory_from: int
    memory_to: int
    add_language_ids: list = field(default_factory=list)
    add_author_ids: list = field(default_factory=list)
    # [{"id", "language_id", "memory_from", "memory_to"}]
    language_limit_updates: list = field(default_factory=list)
    # None = no Solution row; False = already private; True = will be privatised
    solution_make_private: bool | None = None

    @property
    def partial_changed(self):
        return self.partial_from != self.partial_to

    @property
    def rules_changed(self):
        rules = []
        if self.partial_changed:
            rules.append("partial")
        if self.add_language_ids:
            rules.append("languages")
        if self.memory_from != self.memory_to or self.language_limit_updates:
            rules.append("memory")
        if self.add_author_ids:
            rules.append("authors")
        if self.solution_make_private:
            rules.append("solution_private")
        return rules

    def is_noop(self):
        return not self.rules_changed

    def to_json(self):
        data = asdict(self)
        data["rules_changed"] = self.rules_changed
        return data

    @classmethod
    def from_json(cls, data):
        data = {k: v for k, v in data.items() if k != "rules_changed"}
        return cls(**data)


def resolve_context(require_judge=True) -> ProfileContext:
    username = getattr(settings, "STANDARDIZATION_AUTHOR_USERNAME", "MrTee")
    profiles = list(
        Profile.objects.filter(user__username__iexact=username).values_list(
            "id", "user__username"
        )
    )
    if len(profiles) != 1:
        raise ProfileResolutionError(
            f"expected exactly one profile for username {username!r}, found {len(profiles)}"
        )
    author_id, author_username = profiles[0]

    language_ids = {}
    missing, unsupported = [], []
    for key in REQUIRED_LANGUAGE_KEYS:
        lang = Language.objects.filter(key=key).only("id").first()
        if lang is None:
            missing.append(key)
            continue
        if require_judge and not lang.judges.filter(is_blocked=False).exists():
            unsupported.append(key)
        language_ids[key] = lang.id
    if missing:
        raise ProfileResolutionError(f"runtime(s) missing from Language table: {missing}")
    if unsupported:
        raise ProfileResolutionError(f"no unblocked judge supports: {unsupported}")

    return ProfileContext(
        author_profile_id=author_id,
        author_username=author_username,
        language_ids=language_ids,
    )


def plan_config(problem, ctx: ProfileContext) -> ConfigDiff:
    current_langs = set(problem.allowed_languages.values_list("id", flat=True))
    current_authors = set(problem.authors.values_list("id", flat=True))
    diff = ConfigDiff(
        partial_from=bool(problem.partial),
        partial_to=True,
        memory_from=int(problem.memory_limit),
        memory_to=max(int(problem.memory_limit), ctx.target_memory_kb),
    )
    diff.add_language_ids = sorted(set(ctx.language_ids.values()) - current_langs)
    if ctx.author_profile_id not in current_authors:
        diff.add_author_ids = [ctx.author_profile_id]
    for ll in problem.language_limits.all().order_by("language_id"):
        if int(ll.memory_limit) < ctx.target_memory_kb:
            diff.language_limit_updates.append(
                {
                    "id": ll.id,
                    "language_id": ll.language_id,
                    "memory_from": int(ll.memory_limit),
                    "memory_to": ctx.target_memory_kb,
                }
            )
    try:
        solution = problem.solution
    except Exception:  # RelatedObjectDoesNotExist
        solution = None
    if solution is not None:
        diff.solution_make_private = bool(solution.is_public)
    return diff


def apply_config_diff(problem, diff: ConfigDiff):
    problem.partial = diff.partial_to
    problem.memory_limit = diff.memory_to
    problem._bypass_points_cap = True
    problem.save()
    if diff.add_language_ids:
        problem.allowed_languages.add(*diff.add_language_ids)
    if diff.add_author_ids:
        problem.authors.add(*diff.add_author_ids)
    for row in diff.language_limit_updates:
        ll = LanguageLimit.objects.filter(pk=row["id"], problem=problem).first()
        if ll is not None:
            ll.memory_limit = row["memory_to"]
            ll.save()
    if diff.solution_make_private:
        solution = problem.solution
        solution.is_public = False
        solution.save()


def revert_config_diff(problem, diff: ConfigDiff):
    """Undo a previously applied diff. Never re-publishes a Solution (spec F10)."""
    problem.partial = diff.partial_from
    problem.memory_limit = diff.memory_from
    problem._bypass_points_cap = True
    problem.save()
    if diff.add_language_ids:
        problem.allowed_languages.remove(*diff.add_language_ids)
    if diff.add_author_ids:
        problem.authors.remove(*diff.add_author_ids)
    for row in diff.language_limit_updates:
        ll = LanguageLimit.objects.filter(pk=row["id"], problem=problem).first()
        if ll is not None:
            ll.memory_limit = row["memory_from"]
            ll.save()
