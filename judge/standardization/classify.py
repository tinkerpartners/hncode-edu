"""Rule-based source classification (spec F02 / AC02, AC03, AC05, AC09).

No model call here. ``classify_problem`` decides where the statement text
can come from (Markdown, legacy HTML, PDF text, a translation, images, an
external link), which quality flags apply, and which text an AI step should
read. An empty ``description`` is *not* "missing" when a PDF or a
translation carries the statement.
"""

import re
from dataclasses import dataclass, field

from judge.standardization.enums import QualityFlag, SourceKind
from judge.standardization.extract_pdf import extract_text
from judge.utils.problem_inventory import IMAGE, LINK, content_inventory

PLACEHOLDER_RE = re.compile(
    r"^[\s#*_\-]*(?:todo|tbd|test|chưa có đề|đang cập nhật|coming soon|updating|đề bài)?[\s.!#*_\-]*$",
    re.I,
)
MIN_STATEMENT_CHARS = 40
INTERACTIVE_RE = re.compile(r"\binteractive\b|tương tác", re.I)


@dataclass
class Classification:
    source_kinds: list = field(default_factory=list)
    quality_flags: list = field(default_factory=list)
    primary_source: str = ""  # which source_kind an AI step should read
    extracted_text: str = ""  # text taken from the primary source
    pdf: dict = field(default_factory=dict)
    inventory: dict = field(default_factory=dict)

    @property
    def needs_source(self):
        """True when no readable statement exists anywhere (spec: NEEDS_SOURCE)."""
        return not self.primary_source

    def as_json(self):
        return {
            "source_kinds": self.source_kinds,
            "quality_flags": self.quality_flags,
            "primary_source": self.primary_source,
            "pdf": self.pdf,
        }


def _looks_like_html(text):
    return bool(
        re.search(r"<(p|br|div|table|ul|ol|li|b|i|strong|em|h[1-6])\b", text, re.I)
    )


def _plain_len(text):
    return len(re.sub(r"\s+", " ", text or "").strip())


def visible_text(markdown):
    """Statement text as a reader sees it: no images, link targets or tags."""
    from bs4 import BeautifulSoup

    text = IMAGE.sub("", markdown or "")
    text = LINK.sub("", text)
    return BeautifulSoup(text, "html.parser").get_text(" ", strip=True)


def _is_placeholder(text):
    stripped = (text or "").strip()
    title_only = len(stripped.split()) <= 10 and not re.search(
        r"[\d$=<>]|\b(?:cho|tính|đếm|in|nhập|given|compute|find|print|return)\b",
        stripped,
        re.I,
    )
    return title_only or (
        _plain_len(stripped) < MIN_STATEMENT_CHARS
        and (not stripped or bool(PLACEHOLDER_RE.match(stripped)))
    )


def classify_problem(problem, read_pdf=True) -> Classification:
    description = problem.description or ""
    attachments = (
        [a.filename for a in problem.attachments.all()]
        if hasattr(problem, "attachments")
        else []
    )
    pdf_name = str(problem.pdf_description) if problem.pdf_description else ""
    inventory = content_inventory(description, pdf_name, attachments)
    result = Classification(inventory=inventory)
    kinds, flags = result.source_kinds, result.quality_flags

    has_text = not _is_placeholder(visible_text(description))
    if has_text:
        kinds.append(
            SourceKind.LEGACY_HTML
            if _looks_like_html(description)
            else SourceKind.MARKDOWN
        )
    elif description.strip():
        flags.append(QualityFlag.PLACEHOLDER)
    if inventory["image_count"]:
        kinds.append(SourceKind.IMAGE)
    if "external_link" in inventory["sources"]:
        kinds.append(SourceKind.EXTERNAL_LINK)

    translations = (
        list(problem.translations.all()) if hasattr(problem, "translations") else []
    )
    translation_text = next(
        (
            t.description
            for t in translations
            if not _is_placeholder(visible_text(t.description))
        ),
        "",
    )
    if translation_text:
        kinds.append(SourceKind.TRANSLATION)

    pdf_text = ""
    if pdf_name:
        if not read_pdf:
            kinds.append(SourceKind.PDF_UNINSPECTED)
        else:
            try:
                with problem.pdf_description.open("rb") as fh:
                    extracted = extract_text(fh)
            except Exception as exc:  # noqa: BLE001 - missing file on storage
                extracted = None
                flags.append(QualityFlag.BROKEN_ASSET)
                result.pdf = {"error": type(exc).__name__}
            if extracted is not None:
                result.pdf = {
                    "pages": extracted.pages,
                    "chars": extracted.chars,
                    "error": extracted.error,
                }
                if extracted.error:
                    kinds.append(SourceKind.PDF_UNINSPECTED)
                    flags.append(QualityFlag.UNREADABLE)
                elif extracted.is_scan:
                    kinds.append(SourceKind.PDF_SCAN)
                    flags.append(QualityFlag.OCR_UNCERTAIN)
                else:
                    kinds.append(SourceKind.PDF_TEXT)
                    pdf_text = extracted.text

    # Primary source: the problem's own text, then PDF text, then a translation.
    if has_text:
        result.primary_source = kinds[0]
        result.extracted_text = description
    elif pdf_text:
        result.primary_source = SourceKind.PDF_TEXT
        result.extracted_text = pdf_text
    elif translation_text:
        result.primary_source = SourceKind.TRANSLATION
        result.extracted_text = translation_text
    elif SourceKind.IMAGE in kinds and not has_text:
        flags.append(QualityFlag.NEEDS_EXPERT)  # statement lives in an image
    if not kinds and not description.strip():
        flags.append(QualityFlag.STATEMENT_MISSING)

    if result.primary_source:
        headings = inventory["headings"]
        if not (headings["input"] and headings["output"]):
            flags.append(QualityFlag.MISSING_REQUIRED_SECTION)
        if not headings["example"]:
            flags.append(QualityFlag.MISSING_EXAMPLE)

    data = getattr(problem, "data_files", None)
    try:
        if data is not None and (
            bool(data.interactive_judge) or bool(data.use_ioi_signature)
        ):
            flags.append(QualityFlag.NEEDS_EXPERT)
    except Exception:  # noqa: BLE001 - no ProblemData row
        pass
    if INTERACTIVE_RE.search(description) and QualityFlag.NEEDS_EXPERT not in flags:
        flags.append(QualityFlag.NEEDS_EXPERT)

    result.source_kinds = list(dict.fromkeys(str(k) for k in kinds))
    result.quality_flags = list(dict.fromkeys(str(f) for f in flags))
    return result


__all__ = ("Classification", "classify_problem")
