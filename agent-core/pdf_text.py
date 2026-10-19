"""Extract plain text from PDF attachments (resume / docs drag-drop)."""

from __future__ import annotations

from pathlib import Path

# Align with image_input staging cap; extraction output is still line-sliced in read_file.
MAX_PDF_FILE_BYTES = 8 * 1024 * 1024


def is_pdf_path(path: Path) -> bool:
    return path.suffix.casefold() == ".pdf"


def pdf_file_readable(size: int) -> bool:
    return 0 < size <= MAX_PDF_FILE_BYTES


def extract_pdf_text(path: Path) -> str:
    """Return UTF-8 text from all pages; raises on missing dependency or corrupt file."""
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError("pypdf is not installed") from exc

    reader = PdfReader(str(path))
    chunks: list[str] = []
    for page in reader.pages:
        text = page.extract_text() or ""
        if text.strip():
            chunks.append(text)
    return "\n\n".join(chunks).strip()
