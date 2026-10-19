"""Render PDF pages to JPEG data URIs for multimodal LLM input (scanned docs)."""

from __future__ import annotations

import base64
from pathlib import Path

from pdf_text import MAX_PDF_FILE_BYTES, is_pdf_path, pdf_file_readable

MAX_PDF_VISION_PAGES = 20
PDF_RENDER_DPI = 150
JPEG_QUALITY = 85
# Per-page cap aligned with image drag-drop (FILES-DROP §8.1).
MAX_PAGE_BYTES = 8 * 1024 * 1024
_MAX_EDGE_PX = 4096


def is_pdf_mime(mime: str) -> bool:
    return mime.strip().casefold() == "application/pdf"


def pdf_vision_eligible(path: Path, *, size: int | None = None) -> bool:
    if not is_pdf_path(path):
        return False
    file_size = size if size is not None else path.stat().st_size
    return pdf_file_readable(file_size)


def pdf_vision_eligible_mime(mime: str, size: int) -> bool:
    return is_pdf_mime(mime) and 0 < size <= MAX_PDF_FILE_BYTES


def render_pdf_page_data_uris(path: Path) -> list[str]:
    """Rasterize up to MAX_PDF_VISION_PAGES; returns data:image/jpeg;base64,... URIs."""
    try:
        import fitz  # PyMuPDF
    except ImportError as exc:
        raise RuntimeError("pymupdf is not installed") from exc

    if not path.is_file():
        return []
    if path.stat().st_size > MAX_PDF_FILE_BYTES:
        return []

    doc = fitz.open(path)
    try:
        page_count = min(len(doc), MAX_PDF_VISION_PAGES)
        uris: list[str] = []
        scale = PDF_RENDER_DPI / 72.0
        for index in range(page_count):
            page = doc[index]
            uri = _render_page_uri(page, scale=scale, fitz=fitz)
            if uri:
                uris.append(uri)
        return uris
    finally:
        doc.close()


def _render_page_uri(page: object, *, scale: float, fitz: object) -> str | None:
    attempts = 0
    current_scale = scale
    data = b""
    while attempts < 8:
        matrix = fitz.Matrix(current_scale, current_scale)
        pix = page.get_pixmap(matrix=matrix, alpha=False)
        if pix.width > _MAX_EDGE_PX or pix.height > _MAX_EDGE_PX:
            edge_ratio = min(_MAX_EDGE_PX / pix.width, _MAX_EDGE_PX / pix.height)
            current_scale *= edge_ratio
            attempts += 1
            continue
        data = pix.tobytes("jpeg", jpg_quality=JPEG_QUALITY)
        if len(data) <= MAX_PAGE_BYTES:
            break
        current_scale *= 0.75
        attempts += 1
    if not data or len(data) > MAX_PAGE_BYTES:
        return None
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"
