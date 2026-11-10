"""digest.md section format (RUNTIME.md §8). Dependency-free: shared by context and loader."""

from __future__ import annotations

import re

# A cumulative section already folds in every section before it, so only the
# text from the newest cumulative header onward is live.
DIGEST_CUMULATIVE_MARK = "（累计）"
DIGEST_SECTION_HEADER = re.compile(
    rf"^#\s*压缩\s+(\d+)(?:\s*{DIGEST_CUMULATIVE_MARK})?\s*$",
    re.MULTILINE,
)


def digest_section_header(section_number: int, *, cumulative: bool) -> str:
    mark = DIGEST_CUMULATIVE_MARK if cumulative else ""
    return f"# 压缩 {section_number}{mark}"


def active_digest_text(text: str) -> str:
    """Part of digest.md that still needs to be in context.

    Starts at the newest cumulative section. Digests written before cumulative
    sections existed have none, so they are returned whole.
    """
    start = 0
    for match in DIGEST_SECTION_HEADER.finditer(text):
        if DIGEST_CUMULATIVE_MARK in match.group(0):
            start = match.start()
    return text[start:].strip()
