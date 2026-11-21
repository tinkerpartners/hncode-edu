"""Conservative, independently recomputable evidence for statement preservation."""

import re

from bs4 import BeautifulSoup

from judge.standardization.contracts import NAMED_FILES


def normalized(text):
    return re.sub(r"\r+\n", "\n", text or "").replace("\r", "\n")


def source_examples(source):
    """Extract paired sample fences (including unnamed legacy fences) or HTML pre.

    Never interpret a C++ reference program as sample input. An unpaired fence
    yields no proof, rather than silently dropping an example.
    """
    matches = re.finditer(
        r"(?m)^(?P<indent>[ \t]*)```(?:sample|text|plaintext)?[ \t]*\n(?P<body>[\s\S]*?)^(?P=indent)```[ \t]*$",
        normalized(source),
    )
    blocks = []
    for match in matches:
        indent = match["indent"]
        body = match["body"].removesuffix("\n")
        # Remove only the Markdown container indentation, never dedent data.
        lines = body.split("\n")
        if indent and any(line and not line.startswith(indent) for line in lines):
            return []  # malformed container: no byte-preservation proof
        blocks.append(
            "\n".join(
                line[len(indent) :] if line.startswith(indent) else line
                for line in lines
            )
        )
    if not blocks and re.search(r"<pre\b", source, re.I):
        blocks = [
            pre.get_text()
            for pre in BeautifulSoup(source, "html.parser").find_all("pre")
        ]
    if not blocks or len(blocks) % 2:
        return []
    return [
        {
            "input": normalized(blocks[i]),
            "output": normalized(blocks[i + 1]),
            "note": None,
        }
        for i in range(0, len(blocks), 2)
    ]


def sample_pairs(examples):
    return [(normalized(x["input"]), normalized(x["output"])) for x in examples]


def preserve_source_examples(source, payload):
    """Copy readable sample bytes; flag substantive changes rather than trust AI.

    Whitespace-only model edits are undone, even for byte-sensitive checkers.
    Token comparison is used only to diagnose the discarded model response,
    never to accept changed sample bytes or change the judge's checker.
    """
    original = source_examples(source)
    if not original:
        return payload, [], False
    proposed = payload.get("examples") or []
    original_pairs, proposed_pairs = sample_pairs(original), sample_pairs(proposed)
    if original_pairs == proposed_pairs:
        return payload, [], False
    same_tokens = len(original) == len(proposed) and all(
        before[0].split() == after[0].split() and before[1].split() == after[1].split()
        for before, after in zip(original_pairs, proposed_pairs)
    )
    examples = [
        {**example, "note": proposed[i].get("note") if i < len(proposed) else None}
        for i, example in enumerate(original)
    ]
    return (
        {**payload, "examples": examples},
        [] if same_tokens else ["model_changed_source_examples"],
        True,
    )


def body_tokens(source):
    text = normalized(source)
    text = re.sub(r"(?ms)^[ \t]*```[^\n]*\n.*?^[ \t]*```[ \t]*$", "", text)
    text = re.sub(r"<pre\b[^>]*>.*?</pre>", "", text, flags=re.I | re.S)
    text = re.sub(r"</?h[1-6]\b[^>]*>", "\n", text, flags=re.I)
    text = BeautifulSoup(text, "html.parser").get_text("\n")
    kept = []
    for line in text.splitlines():
        line = line.strip()
        if re.match(r"^(?:!!!|\?\?\?)", line):
            title = re.search(r"""["'](.*)["']\s*$""", line)
            if title and re.fullmatch(
                r"(?:input|output|note|test\s+\d+)", title[1], re.I
            ):
                continue
            # A nonstandard admonition title can itself contain a constraint.
            line = title[1] if title else line
        if re.fullmatch(r"[#*\s]*(?:input|output|example|scoring)[:*#\s]*", line, re.I):
            continue
        kept.append(line)
    # Preserve words, numbers, operators and TeX commands. This deliberately
    # rejects many harmless rewrites; a model's confidence cannot certify them.
    return re.findall(r"\\[A-Za-z]+|\w+|[^\s\w$`*_#]", "\n".join(kept))


def statement_fidelity(source, markdown, payload):
    original = source_examples(source)
    proposed = payload.get("examples") or []
    displayed = source_examples(markdown)
    files = set(NAMED_FILES.findall(source or ""))
    mentioned = set(NAMED_FILES.findall(markdown or ""))

    def assets(text):
        images = re.findall(r"!\[[^\]]*\]\(([^\s)]+)", text or "")
        images += [
            tag.get("src", "")
            for tag in BeautifulSoup(text or "", "html.parser").find_all("img")
        ]
        return sorted(images)

    return {
        "samples": (
            "matched"
            if original
            and sample_pairs(original)
            == sample_pairs(proposed)
            == sample_pairs(displayed)
            else "changed" if original else "unconfirmed"
        ),
        "files": "matched" if files == mentioned else "changed",
        "body": (
            "matched" if body_tokens(source) == body_tokens(markdown) else "changed"
        ),
        "assets": "matched" if assets(source) == assets(markdown) else "changed",
    }


def content_issues(payload):
    issues = []
    for key in ("statement", "input", "output", "scoring"):
        text = payload.get(key) or ""
        if re.search(r"(?m)^\s*(?:```|!!!|\?\?\?|#{1,6}\s)", text):
            issues.append(
                f"{key}: sections/examples must use their dedicated schema fields"
            )
    if (
        not payload.get("statement", "").strip()
        or not payload.get("input", "").strip()
        or not payload.get("output", "").strip()
    ):
        issues.append("empty_required_body")
    for example in payload.get("examples") or []:
        if "```" in example["input"] or "```" in example["output"]:
            issues.append("sample_contains_fence")
        if re.search(
            r"[\x00-\x08\x0b\x0c\x0e-\x1f]", example["input"] + example["output"]
        ):
            issues.append("sample_contains_control_character")
    return issues


def can_auto_rewrite(source, markdown, structured):
    proof = statement_fidelity(source, markdown, structured)
    return (
        bool(body_tokens(source))
        and not re.search(
            r"(?im)<table\b|^\s*\|.+\|\s*$|^\s*```(?!sample\s*$|text\s*$|plaintext\s*$)\w+",
            source,
        )
        and not content_issues(structured)
        and structured.get("source_readable") is True
        and not structured.get("warnings")
        and not structured.get("unverified_spans")
        and all(value == "matched" for value in proof.values())
    )
