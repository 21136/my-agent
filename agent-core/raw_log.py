"""Append-only raw message log, next to messages.jsonl (RUNTIME.md §2.4).

messages.jsonl is the working copy: saves rewrite it whole, a reply can be
patched after the fact, and load-time repair can drop records. This log is only
ever appended to, so every record that reached messages.jsonl stays recoverable.

One JSON object per line:
  seed     a record that was already in messages.jsonl when logging began
  append   a record added to the end
  rewrite  records ``before`` at ``index`` were replaced by ``after``

Callers hold the session message lock and call in *before* touching
messages.jsonl, so nothing is overwritten that was not logged first.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

RAW_MESSAGES_FILENAME = "messages.raw.jsonl"


def raw_log_path(messages_path: Path) -> Path:
    return messages_path.with_name(RAW_MESSAGES_FILENAME)


def record_append(messages_path: Path, message: dict[str, Any], *, index: int) -> None:
    """Log one record about to be appended at position ``index``."""
    entries = _seed_entries(messages_path)
    entries.append({"ts": _now(), "op": "append", "index": index, "message": message})
    _write_entries(messages_path, entries, durable=False)


def record_snapshot(messages_path: Path, new_content: str) -> None:
    """Log what a whole-file rewrite of messages.jsonl to ``new_content`` changes."""
    old_content = _read_text(messages_path)
    if old_content == new_content:
        return
    old_lines = _record_lines(old_content)
    new_lines = _record_lines(new_content)
    span = _changed_span(old_lines, new_lines)
    if span is None:
        return
    start, old_end, new_end = span
    entries = _seed_entries(messages_path)
    entries.append(
        {
            "ts": _now(),
            "op": "rewrite",
            "index": start,
            "before": [_parse(line) for line in old_lines[start:old_end]],
            "after": [_parse(line) for line in new_lines[start:new_end]],
        }
    )
    _write_entries(messages_path, entries, durable=True)


def _seed_entries(messages_path: Path) -> list[dict[str, Any]]:
    """Existing records, the first time a session written before this log is touched."""
    if raw_log_path(messages_path).exists():
        return []
    now = _now()
    return [
        {"ts": now, "op": "seed", "index": index, "message": _parse(line)}
        for index, line in enumerate(_record_lines(_read_text(messages_path)))
    ]


def _write_entries(messages_path: Path, entries: list[dict[str, Any]], *, durable: bool) -> None:
    path = raw_log_path(messages_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = "".join(json.dumps(entry, ensure_ascii=False, default=str) + "\n" for entry in entries)
    with path.open("ab") as handle:
        if _ends_mid_line(path):
            # A crash can leave a partial last line; keep it from swallowing this entry.
            handle.write(b"\n")
        handle.write(payload.encode("utf-8"))
        if durable:
            handle.flush()
            os.fsync(handle.fileno())


def _ends_mid_line(path: Path) -> bool:
    with path.open("rb") as existing:
        if not existing.seek(0, os.SEEK_END):
            return False
        existing.seek(-1, os.SEEK_END)
        return existing.read(1) != b"\n"


def _read_text(messages_path: Path) -> str:
    try:
        return messages_path.read_bytes().decode("utf-8", errors="replace")
    except FileNotFoundError:
        return ""


def _record_lines(content: str) -> list[str]:
    # Not str.splitlines(): it also splits on U+2028 and friends, which
    # ensure_ascii=False leaves unescaped inside JSON strings.
    return [line for line in (raw.rstrip("\r") for raw in content.split("\n")) if line.strip()]


def _parse(line: str) -> Any:
    try:
        return json.loads(line)
    except ValueError:
        return {"unparsed": line}


def _same_record(old_line: str, new_line: str) -> bool:
    if old_line == new_line:
        return True
    try:
        return json.loads(old_line) == json.loads(new_line)
    except ValueError:
        return False


def _changed_span(old_lines: list[str], new_lines: list[str]) -> tuple[int, int, int] | None:
    """(start, old_end, new_end) of the differing middle, or None when nothing changed."""
    limit = min(len(old_lines), len(new_lines))
    start = 0
    while start < limit and _same_record(old_lines[start], new_lines[start]):
        start += 1
    old_end, new_end = len(old_lines), len(new_lines)
    while old_end > start and new_end > start and _same_record(old_lines[old_end - 1], new_lines[new_end - 1]):
        old_end -= 1
        new_end -= 1
    if start == old_end and start == new_end:
        return None
    return start, old_end, new_end


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
