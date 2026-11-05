"""Read-only day journal directory listing (DAY-JOURNAL-MEMORY §2)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from day_journal import DayJournal
from day_journal_recall import query_day_journal

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MAX_LIST = 31


def list_recent_days(root: str | Path, *, limit: int = 14) -> list[str]:
    if type(limit) is not int or isinstance(limit, bool) or not 1 <= limit <= _MAX_LIST:
        raise ValueError(f"limit must be between 1 and {_MAX_LIST}")
    base = Path(root).expanduser().resolve()
    if not base.is_dir():
        return []
    days: list[str] = []
    for path in base.iterdir():
        if not path.is_file() or path.suffix != ".json":
            continue
        key = path.stem
        if not _DATE_RE.fullmatch(key):
            continue
        days.append(key)
    return sorted(set(days), reverse=True)[:limit]


def read_public_day(
    root: str | Path,
    day: str,
    *,
    project_id: str | None = None,
) -> dict[str, Any]:
    return query_day_journal(
        root,
        day=day,
        authorized=True,
        project_id=project_id,
    )


def build_directory_payload(root: str | Path, *, limit: int = 14) -> dict[str, Any]:
    """Compact directory rows for UI: date, summary, projects, segment count."""
    days = list_recent_days(root, limit=limit)
    entries: list[dict[str, Any]] = []
    for day in days:
        result = read_public_day(root, day)
        if result.get("status") not in {"ok", "summary_missing", "summary_empty"}:
            continue
        for record in result.get("records") or []:
            entries.append(
                {
                    "date": record["date"],
                    "summary": record.get("summary"),
                    "projects": record.get("projects") or [],
                    "segment_count": len(record.get("segments") or []),
                    "segments": record.get("segments") or [],
                }
            )
            break
        else:
            entries.append({"date": day, "summary": None, "projects": [], "segment_count": 0, "segments": []})
    return {"type": "day_journal.directory", "days": entries}
