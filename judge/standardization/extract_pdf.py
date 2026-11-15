"""Text extraction from PDF statements (spec F02: PDF_TEXT vs PDF_SCAN).

A PDF whose pages average fewer than ``STANDARDIZATION_PDF_SCAN_CHARS_PER_PAGE``
extractable characters is treated as a scan; OCR is deliberately out of scope
for now, so those problems end in NEEDS_SOURCE rather than a guessed draft.
"""

from dataclasses import dataclass

from django.conf import settings


@dataclass
class PdfText:
    pages: int
    chars: int
    text: str
    error: str = ""

    @property
    def is_scan(self):
        if self.pages == 0:
            return False
        threshold = getattr(settings, "STANDARDIZATION_PDF_SCAN_CHARS_PER_PAGE", 40)
        return self.chars / self.pages < threshold

    @property
    def readable(self):
        return not self.error and self.pages > 0 and not self.is_scan


def extract_text(fileobj, max_pages=None) -> PdfText:
    """Extract text from an open binary file. Never raises; failures are
    reported in ``error`` so the classifier can flag UNREADABLE."""
    max_pages = max_pages or getattr(settings, "STANDARDIZATION_PDF_MAX_PAGES", 30)
    try:
        from pypdf import PdfReader
    except ImportError:
        return PdfText(0, 0, "", error="pypdf not installed")
    try:
        reader = PdfReader(fileobj)
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception:  # noqa: BLE001
                return PdfText(len(reader.pages), 0, "", error="encrypted")
        chunks = []
        pages = reader.pages
        count = min(len(pages), max_pages)
        for index in range(count):
            chunks.append((pages[index].extract_text() or "").strip())
        text = "\n\n".join(chunk for chunk in chunks if chunk)
        return PdfText(count, len(text), text)
    except Exception as exc:  # noqa: BLE001 - corrupt/unsupported PDF
        return PdfText(0, 0, "", error=type(exc).__name__)
