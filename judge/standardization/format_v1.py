"""HNCode statement v1: validator and rule-based heading fixer.

The format is fixed by the owner (spec v3 §2, issue #124): exactly
``### Input``, ``### Output``, ``### Example`` at heading level 3, in that
order, with ``### Scoring`` only when the source has subtasks. Everything that
builds, checks or renders statements must agree on this one definition, so it
lives here and nowhere else.

``rule_fix_headings`` only rewrites the heading *lines* for the four English
section names (any level -> level 3, canonical capitalisation), outside code
fences. It never touches body text, Vietnamese headings or sample content, so
``can_auto_apply`` can prove the change was heading-only before the fix is
applied without human review.
"""

import re
from dataclasses import dataclass, field

FORMAT_VERSION = "hncode_statement_v1"
HEADING_FIX_STEP = "heading_fix_v1"

SECTION_NAMES = ("Input", "Output", "Example", "Scoring")
REQUIRED = ("Input", "Output", "Example")
_CANON = {name.lower(): name for name in SECTION_NAMES}

# A heading line for one of the four section names, any level 1..6. Trailing
# closing hashes ("### Input ###") and surrounding spaces are tolerated.
HEADING_RE = re.compile(
    r"^(?P<indent>[ \t]{0,3})(?P<hashes>#{1,6})[ \t]*(?P<name>input|output|example|scoring)[ \t]*#*[ \t]*$",
    re.I,
)
FENCE_OPEN_RE = re.compile(r"^[ \t]*(`{3,}|~{3,})")

LEVEL_INVALID = 0
LEVEL_HEADINGS_OK = 1
LEVEL_STRICT_OK = 2


@dataclass
class Heading:
    name: str
    level: int
    line: int


@dataclass
class ValidationResult:
    level: int
    headings: list = field(default_factory=list)
    errors: list = field(default_factory=list)

    @property
    def ok(self):
        return self.level >= LEVEL_HEADINGS_OK

    @property
    def label(self):
        return {0: "INVALID", 1: "HEADINGS_OK", 2: "STRICT_OK"}[self.level]


def _iter_lines(markdown):
    """Yield (index, line, in_fence). Fences are ``` or ~~~ of >=3 chars; a
    fence closes on a line starting with the same character run.

    Lines are yielded without their line ending. About 14k statements are
    stored with CRLF (browser editors); treating ``\\r`` as content made every
    heading regex miss, so the fixer's output is LF-only by design."""
    fence = None
    for i, line in enumerate(
        markdown.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    ):
        if fence is None:
            m = FENCE_OPEN_RE.match(line)
            if m:
                fence = m.group(1)[0] * 3
                yield i, line, True
                continue
            yield i, line, False
        else:
            yield i, line, True
            if line.strip().startswith(fence):
                fence = None


def find_headings(markdown):
    out = []
    for i, line, in_fence in _iter_lines(markdown or ""):
        if in_fence:
            continue
        m = HEADING_RE.match(line)
        if m:
            out.append(
                Heading(_CANON[m.group("name").lower()], len(m.group("hashes")), i)
            )
    return out


def validate(markdown):
    headings = find_headings(markdown)
    errors = []
    by_name = {}
    for h in headings:
        by_name.setdefault(h.name, []).append(h)
    for name in REQUIRED:
        found = by_name.get(name, [])
        if not found:
            errors.append(f"missing ### {name}")
        elif len(found) > 1:
            errors.append(f"{name} appears {len(found)} times")
        elif found[0].level != 3:
            errors.append(f"{name} is level {found[0].level}, not 3")
    scoring = by_name.get("Scoring", [])
    if len(scoring) > 1:
        errors.append(f"Scoring appears {len(scoring)} times")
    elif scoring and scoring[0].level != 3:
        errors.append(f"Scoring is level {scoring[0].level}, not 3")
    if not errors:
        order = [by_name[n][0].line for n in REQUIRED]
        if order != sorted(order):
            errors.append("sections out of order (Input, Output, Example)")
    if errors:
        return ValidationResult(LEVEL_INVALID, headings, errors)

    level = LEVEL_HEADINGS_OK
    example_start = by_name["Example"][0].line
    tail = "\n".join((markdown or "").split("\n")[example_start:])
    if re.search(r"^!!! question", tail, re.M) and re.search(
        r"^\s+\?\?\?\+", tail, re.M
    ):
        level = LEVEL_STRICT_OK
    return ValidationResult(level, headings, [])


def rule_fix_headings(markdown):
    """Rewrite section heading lines to ``### Name``. Body text is untouched."""
    out = []
    for _, line, in_fence in _iter_lines(markdown or ""):
        if not in_fence:
            m = HEADING_RE.match(line)
            if m:
                out.append(f"### {_CANON[m.group('name').lower()]}")
                continue
        out.append(line)
    return "\n".join(out)


def strip_heading_lines(markdown):
    """The statement with section heading lines removed; equal before/after
    means a change was heading-only."""
    return "\n".join(
        line
        for _, line, in_fence in _iter_lines(markdown or "")
        if in_fence or not HEADING_RE.match(line)
    )


def can_auto_apply(before, after):
    """True when ``after`` is a heading-only rewrite of ``before`` that reaches
    at least HEADINGS_OK. Returns (ok, reason)."""
    if before == after:
        return False, "no change"
    if strip_heading_lines(before) != strip_heading_lines(after):
        return False, "non-heading content changed"
    result = validate(after)
    if not result.ok:
        return False, "; ".join(result.errors)
    return True, result.label


def _indent(text, spaces):
    pad = " " * spaces
    return "\n".join(pad + line for line in text.replace("\r\n", "\n").split("\n"))


def render(payload):
    """Render a CONTENT_SCHEMA payload into statement v1 Markdown.

    The renderer is the single place that knows the admonition layout, so
    the model never writes headings or fences itself and the output always
    validates at least HEADINGS_OK (STRICT_OK when examples exist)."""
    contract = payload.get("io_contract") or {}
    input_text, output_text = payload["input"].strip(), payload["output"].strip()
    if contract.get("kind") == "file":
        for key, name, text in (
            ("input", contract.get("input_file"), input_text),
            ("output", contract.get("output_file"), output_text),
        ):
            if name and name not in text:
                prefix = (
                    "Đọc dữ liệu từ tệp" if key == "input" else "Ghi kết quả vào tệp"
                )
                if payload.get("language") == "en":
                    prefix = (
                        "Read input from file"
                        if key == "input"
                        else "Write output to file"
                    )
                text = f"- {prefix} `{name}`.\n" + text
            if key == "input":
                input_text = text
            else:
                output_text = text
    out = [
        payload["statement"].strip(),
        "",
        "### Input",
        input_text,
        "",
        "### Output",
        output_text,
    ]
    scoring = (payload.get("scoring") or "").strip()
    if scoring:
        out += ["", "### Scoring", scoring]
    out += ["", "### Example"]
    examples = payload.get("examples") or []
    if not examples:
        out.append("- (Đề gốc không có ví dụ.)")
    for index, example in enumerate(examples, start=1):
        out.append(f'!!! question "Test {index}"')
        out.append('    ???+ "Input"')
        out.append("        ```sample")
        out.append(_indent(example["input"], 8))
        out.append("        ```")
        out.append('    ???+ success "Output"')
        out.append("        ```sample")
        out.append(_indent(example["output"], 8))
        out.append("        ```")
        note = (example.get("note") or "").strip()
        if note:
            out.append('    ??? warning "Note"')
            out.append(_indent(note, 8))
        out.append("")
    text = "\n".join(out).rstrip("\n") + "\n"
    return text.replace("\r", "")


# Explicit whole-line aliases only. Ordinary prose, formulas, images, tables,
# constraints and filenames are copied verbatim, not reconstructed by AI.
SECTION_ALIASES = {
    "input": "Input",
    "dữ liệu vào": "Input",
    "dữ liệu": "Input",
    "đầu vào": "Input",
    "output": "Output",
    "kết quả": "Output",
    "dữ liệu ra": "Output",
    "đầu ra": "Output",
    "example": "Example",
    "examples": "Example",
    "ví dụ": "Example",
    "scoring": "Scoring",
    "subtasks": "Scoring",
    "chấm điểm": "Scoring",
}


def preserve_format(markdown):
    """Normalize known headings/sample containers, or return no proof.

    Refuse mixed example markup rather than guess how notes/tables map to tests.
    No example is invented; a source without one remains HEADINGS_OK at most.
    """
    from judge.standardization.integrity import source_examples

    lines = []
    for _, line, in_fence in _iter_lines(markdown or ""):
        match = (
            None if in_fence else re.fullmatch(r"\s{0,3}#{1,6}\s+(.+?)\s*#*\s*", line)
        )
        alias = (
            SECTION_ALIASES.get(match[1].rstrip(":").strip().casefold())
            if match
            else None
        )
        lines.append(f"### {alias}" if alias else line)
    fixed = "\n".join(lines)
    if not validate(fixed).ok:
        return markdown, {}
    example = next(h for h in find_headings(fixed) if h.name == "Example")
    following = next(
        (
            i
            for i in range(example.line + 1, len(lines))
            if re.match(r"^#{1,3}\s", lines[i])
        ),
        len(lines),
    )
    region = "\n".join(lines[example.line + 1 : following])
    pairs = source_examples(region)
    # Only a region entirely consisting of unindented, paired sample fences
    # can be wrapped automatically. Notes and HTML remain exactly as written.
    remainder = re.sub(
        r"(?m)^```(?:sample|text|plaintext)?[ \t]*\n[\s\S]*?^```[ \t]*$", "", region
    )
    if pairs and not remainder.strip():
        wrapped = []
        for index, pair in enumerate(pairs, 1):
            wrapped.extend(
                [
                    f'!!! question "Test {index}"',
                    '    ???+ "Input"',
                    "        ```sample",
                    _indent(pair["input"], 8),
                    "        ```",
                    '    ???+ success "Output"',
                    "        ```sample",
                    _indent(pair["output"], 8),
                    "        ```",
                    "",
                ]
            )
        lines[example.line + 1 : following] = wrapped
        fixed = "\n".join(lines)
    if fixed == markdown:
        return markdown, {}
    if source_examples(markdown) != source_examples(fixed):
        return markdown, {}
    return fixed, {
        "kind": "deterministic_preservation_v1",
        "samples": "byte_preserved",
        "body": "copied",
    }


def preservation_proof(before, after):
    fixed, proof = preserve_format(before)
    return proof if proof and fixed == after else {}
