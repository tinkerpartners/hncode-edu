"""Capture the per-field-group state of a problem.

``capture_group`` is the single definition of "what does this field group
look like right now". It is used to freeze the before-state in a snapshot,
to detect conflicts before apply (someone edited the group since we read it),
to record after-state for audit, and to verify a restore target.
"""

from judge.standardization.enums import FieldGroup


def _solution(problem):
    try:
        return problem.solution
    except Exception:  # RelatedObjectDoesNotExist
        return None


def capture_group(problem, group) -> dict:
    if group == FieldGroup.CONTENT:
        return {"description": problem.description or ""}
    if group == FieldGroup.SOLUTION:
        sol = _solution(problem)
        if sol is None:
            return {"exists": False}
        return {
            "exists": True,
            "content": sol.content or "",
            "is_public": bool(sol.is_public),
        }
    if group == FieldGroup.TAGS:
        return {"type_ids": sorted(problem.types.values_list("id", flat=True))}
    if group == FieldGroup.POINTS:
        return {"points": float(problem.points)}
    if group == FieldGroup.CONFIG:
        sol = _solution(problem)
        return {
            "partial": bool(problem.partial),
            "memory_limit": int(problem.memory_limit),
            "language_ids": sorted(
                problem.allowed_languages.values_list("id", flat=True)
            ),
            "author_ids": sorted(problem.authors.values_list("id", flat=True)),
            "language_limits": sorted(
                (
                    {
                        "id": ll.id,
                        "language_id": ll.language_id,
                        "time_limit": float(ll.time_limit),
                        "memory_limit": int(ll.memory_limit),
                    }
                    for ll in problem.language_limits.all()
                ),
                key=lambda row: row["language_id"],
            ),
            "solution_is_public": bool(sol.is_public) if sol is not None else None,
        }
    raise ValueError(f"unknown field group {group!r}")


def capture_all(problem) -> dict:
    return {group.value: capture_group(problem, group) for group in FieldGroup}
