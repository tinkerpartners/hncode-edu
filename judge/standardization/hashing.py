"""Source hash for standardization: everything the flow reads or writes.

Extends ``judge.review.hashing.compute_input_hash`` (statement, limits,
points, partial, test data, checker, solution codes) with the fields the
standardization rules change: authors, allowed languages, tags, per-language
limits and the editorial. A change to any of them must change the hash; see
``judge/tests/test_standardization_hashing.py``.
"""

import hashlib
import json
from pathlib import Path

from django.core.cache import cache

from judge.models.problem_data import ProblemData, problem_data_storage
from judge.review.hashing import _file_field_content_hash, compute_input_hash
from judge.standardization.format_v1 import rule_fix_headings


def compute_statement_hash(problem):
    source = [
        rule_fix_headings(problem.description or ""),
        (
            _file_field_content_hash(problem.pdf_description)
            if problem.pdf_description
            else ""
        ),
        list(problem.translations.order_by("pk").values_list("description", flat=True)),
    ]
    return hashlib.sha256(json.dumps(source, ensure_ascii=False).encode()).hexdigest()


def compute_judge_hash(problem, language):
    """Changes to scoring metadata alone do not invalidate a judged solution."""
    data = ProblemData.objects.filter(problem=problem).first()
    limit = (
        problem.language_limits.filter(language=language)
        .values_list("time_limit", "memory_limit")
        .first()
    )
    digest = hashlib.sha256(
        json.dumps(
            [
                problem.code,
                language.key,
                float(problem.time_limit),
                int(problem.memory_limit),
                limit,
                compute_statement_hash(problem),
            ]
        ).encode()
    )
    if data:
        digest.update(
            json.dumps(
                [
                    data.checker,
                    data.checker_args,
                    data.use_ioi_signature,
                    list(
                        problem.cases.order_by("order", "pk").values(
                            "type",
                            "input_file",
                            "output_file",
                            "points",
                            "is_pretest",
                            "checker",
                            "checker_args",
                            "generator_args",
                            "batch_scoring",
                        )
                    ),
                ],
                sort_keys=True,
            ).encode()
        )
    directory = problem_data_storage.path(problem.code)
    for file in sorted(Path(directory).rglob("*")):
        if not file.is_file() or file.suffix.lower() == ".pdf":
            continue
        try:
            info = file.stat()
            key = (
                "standardization-file:"
                + hashlib.sha256(
                    f"{file}|{info.st_size}|{info.st_mtime_ns}|{info.st_ctime_ns}|{info.st_ino}".encode()
                ).hexdigest()
            )
            checksum = cache.get(key)
            if checksum is None:
                with file.open("rb") as stream:
                    checksum = hashlib.file_digest(stream, "sha256").hexdigest()
                cache.set(key, checksum, timeout=3600)
            digest.update(
                json.dumps([file.relative_to(directory).as_posix(), checksum]).encode()
            )
        except OSError:
            digest.update(f"unreadable:{file.name}".encode())
    return digest.hexdigest()


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
    if problem.pdf_description:
        payload["pdf"] = _file_field_content_hash(problem.pdf_description)
    translations = list(
        problem.translations.order_by("pk").values_list("description", flat=True)
    )
    if translations:
        payload["translations"] = translations
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
