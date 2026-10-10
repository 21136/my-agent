"""Hide agent bookkeeping from user-facing listings.

Internal code still reads these paths directly. A listing, glob, or search
includes them only when the path or pattern names the directory or file.
"""

from __future__ import annotations

INTERNAL_DIR_NAMES = frozenset({".plan-agent", ".agent"})
INTERNAL_FILE_NAMES = frozenset({"RUNAWAY-PROGRESS.md"})


def _parts(rel: str) -> list[str]:
    return [part for part in rel.replace("\\", "/").split("/") if part not in {"", "."}]


def path_is_internal_bookkeeping(rel: str) -> bool:
    """True when *rel* is inside agent bookkeeping or is a bookkeeping file."""
    parts = _parts(rel)
    if any(part in INTERNAL_DIR_NAMES for part in parts):
        return True
    return bool(parts) and parts[-1] in INTERNAL_FILE_NAMES


def request_names_internal(text: str) -> bool:
    """True when the user-facing path or pattern asks for bookkeeping by name."""
    raw = (text or "").replace("\\", "/")
    if any(name in raw for name in INTERNAL_DIR_NAMES):
        return True
    return any(name in raw for name in INTERNAL_FILE_NAMES)


def hide_from_listing(rel: str, *, explicit: str = "") -> bool:
    """Drop bookkeeping from a default listing. Keep it when *explicit* names it."""
    if not path_is_internal_bookkeeping(rel):
        return False
    return not request_names_internal(explicit)
