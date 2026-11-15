"""Source hash for standardization: everything the flow reads or writes.

Extends ``judge.review.hashing.compute_input_hash`` (statement, limits,
points, partial, test data, checker, solution codes) with the fields the
standardization rules change: authors, allowed languages, tags, per-language
limits and the editorial. A change to any of them must change the hash; see
``judge/tests/test_standardization_hashing.py``.
"""

import hashlib
import json

from judge.review.hashing import compute_input_hash


def compute_source_hash(problem) -> str:
    payload = {
        "base": compute_input_hash(problem),
        "author_ids": sorted(problem.authors.values_list("id", flat=True)),
        "language_ids": sorted(problem.allowed_languages.values_list("id", flat=True)),
        "type_ids": sorted(problem.types.values_list("id", flat=True)),
        "language_limits": sorted(
            (ll.language_id, float(ll.time_limit), int(ll.memory_limit))
            for ll in problem.language_limits.all()
        ),
    }
    try:
        solution = problem.solution
    except Exception:  # RelatedObjectDoesNotExist
        solution = None
    payload["solution"] = (
        {"content": solution.content or "", "is_public": bool(solution.is_public)}
        if solution is not None
        else None
    )
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def dedupe_key(problem_id, source_hash, field_group, step, prompt_version="") -> str:
    raw = f"{problem_id}|{source_hash}|{field_group}|{step}|{prompt_version}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
