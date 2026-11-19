"""JSON schemas for the AI steps.

Every schema is strict (``additionalProperties: false``, all properties
required) so it can be passed as ``output_config.format`` to the Anthropic
API and validated locally with ``jsonschema`` for every provider. Nullable
fields use ``["string", "null"]`` rather than being optional.

The schema version travels with ``prompts.PROMPT_VERSION``: changing a schema
means bumping that version so dedupe keys change too.
"""


def _str_or_null():
    return {"type": ["string", "null"]}


def _str_list():
    return {"type": "array", "items": {"type": "string"}}


CONTENT_SCHEMA = {
    "type": "object",
    "properties": {
        "language": {"type": "string", "enum": ["vi", "en"]},
        "source_readable": {
            "type": "boolean",
            "description": "false when the source could not be read well enough to rewrite faithfully",
        },
        "statement": {
            "type": "string",
            "description": "Markdown body before ### Input: story, task, **Yêu cầu:** line. No headings.",
        },
        "input": {
            "type": "string",
            "description": "Markdown bullet list describing the input and constraints",
        },
        "output": {"type": "string", "description": "Markdown bullet list"},
        "scoring": {
            "type": ["string", "null"],
            "description": "Markdown bullets for subtasks; null when the source has none",
        },
        "examples": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "input": {"type": "string"},
                    "output": {"type": "string"},
                    "note": _str_or_null(),
                },
                "required": ["input", "output", "note"],
                "additionalProperties": False,
            },
        },
        "io_contract": {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": [
                        "stdin_stdout",
                        "file",
                        "function",
                        "interactive",
                        "output_only",
                    ],
                },
                "input_file": _str_or_null(),
                "output_file": _str_or_null(),
            },
            "required": ["kind", "input_file", "output_file"],
            "additionalProperties": False,
        },
        "kept_images": _str_list(),
        "warnings": _str_list(),
        "unverified_spans": _str_list(),
    },
    "required": [
        "language",
        "source_readable",
        "statement",
        "input",
        "output",
        "scoring",
        "examples",
        "io_contract",
        "kept_images",
        "warnings",
        "unverified_spans",
    ],
    "additionalProperties": False,
}

TAGS_POINTS_SCHEMA = {
    "type": "object",
    "properties": {
        "insufficient_evidence": {
            "type": "boolean",
            "description": "true when statement/constraints are too incomplete to rate",
        },
        "tags": {
            "type": "array",
            "items": {"type": "string"},
            "description": "1-4 canonical tag names from the catalogue, most decisive first",
        },
        "primary_tag": {"type": "string"},
        "rating": {
            "type": "integer",
            "description": "Codeforces-style estimate, 800-3500",
        },
        "rating_low": {"type": "integer"},
        "rating_high": {"type": "integer"},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        "reasoning": {"type": "string"},
        "cf_source_url": _str_or_null(),
    },
    "required": [
        "insufficient_evidence",
        "tags",
        "primary_tag",
        "rating",
        "rating_low",
        "rating_high",
        "confidence",
        "reasoning",
        "cf_source_url",
    ],
    "additionalProperties": False,
}

SOLUTION_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "description": "Tóm tắt / mô hình hóa bài toán"},
        "key_observations": _str_list(),
        "algorithm": {"type": "string"},
        "correctness": {"type": "string"},
        "complexity_time": {"type": "string"},
        "complexity_memory": {"type": "string"},
        "edge_cases": _str_list(),
        "subtasks": {
            **_str_list(),
            "description": (
                "Algorithm guides for actual source subtasks, easy to hard. Each string: "
                "first line 'Subtask i — actual limits/score', then newline and approach/complexity. "
                "Easy subtasks are brief; refer to full algorithm fields for the final subtask. "
                "Empty for easy tasks with the same solution throughout, or sources without subtasks. "
                "Never invent source subtasks or scores."
            ),
        },
        "language_key": {"type": "string", "enum": ["CPP17", "CPP20"]},
        "cpp_code": {"type": "string"},
        "notes_on_existing": {
            "type": ["string", "null"],
            "description": "How the existing editorial compares; null when there was none",
        },
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
    },
    "required": [
        "summary",
        "key_observations",
        "algorithm",
        "correctness",
        "complexity_time",
        "complexity_memory",
        "edge_cases",
        "subtasks",
        "language_key",
        "cpp_code",
        "notes_on_existing",
        "confidence",
    ],
    "additionalProperties": False,
}
