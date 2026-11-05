"""Project session store — per project, per day session segment index (DAY-JOURNAL-MEMORY §4)."""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from day_journal import DayJournal, JournalConflict
from paths import AgentPaths

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MAX_LIST = 31


def store_root(paths: AgentPaths) -> Path:
    return Path(paths.data) / "project-session-store"


def list_store_projects(root: Path) -> list[str]:
    if not root.is_dir():
        return []
    projects: list[str] = []
    for path in root.iterdir():
        if path.is_dir() and not path.name.startswith("."):
            try:
                DayJournal._validate_id(path.name, "project_id")
            except ValueError:
                continue
            projects.append(path.name)
    return sorted(projects)


def list_store_days(root: Path, project_id: str, *, limit: int = 14) -> list[str]:
    if type(limit) is not int or isinstance(limit, bool) or not 1 <= limit <= _MAX_LIST:
        raise ValueError(f"limit must be between 1 and {_MAX_LIST}")
    pid = DayJournal._validate_id(project_id, "project_id")
    base = root / pid
    if not base.is_dir():
        return []
    days: list[str] = []
    for path in base.iterdir():
        if not path.is_file() or path.suffix != ".json":
            continue
        key = path.stem
        if _DATE_RE.fullmatch(key):
            days.append(key)
    return sorted(set(days), reverse=True)[:limit]


def read_store_day(root: Path, project_id: str, day_key: str) -> dict[str, Any]:
    pid = DayJournal._validate_id(project_id, "project_id")
    if not _DATE_RE.fullmatch(day_key):
        raise ValueError("day must be an ISO calendar date")
    path = _day_path(root, pid, day_key)
    if not path.is_file():
        return {"status": "missing", "project_id": pid, "date": day_key, "segments": []}
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        raise ValueError("project session store record is unreadable") from exc
    if (
        not isinstance(record, dict)
        or record.get("project_id") != pid
        or record.get("date") != day_key
        or not isinstance(record.get("segments"), list)
    ):
        raise ValueError("project session store record schema mismatch")
    segments: list[dict[str, Any]] = []
    for item in record["segments"]:
        if not isinstance(item, dict):
            continue
        seg_id = item.get("segment_id")
        sess_id = item.get("session_id")
        occurred = item.get("occurred_at")
        if (
            isinstance(seg_id, str)
            and seg_id
            and isinstance(sess_id, str)
            and sess_id
            and isinstance(occurred, str)
            and occurred.strip()
        ):
            segments.append(
                {
                    "segment_id": seg_id,
                    "session_id": sess_id,
                    "occurred_at": occurred,
                }
            )
    return {
        "status": "ok",
        "project_id": pid,
        "date": day_key,
        "revision": record.get("revision"),
        "segments": segments,
    }


def build_project_store_payload(
    paths: AgentPaths,
    project_id: str,
    *,
    limit: int = 14,
) -> dict[str, Any]:
    """Agent-facing index: project → recent days → stable session pointers (§4.2)."""
    root = store_root(paths)
    pid = DayJournal._validate_id(project_id, "project_id")
    days = list_store_days(root, pid, limit=limit)
    entries: list[dict[str, Any]] = []
    for day in days:
        row = read_store_day(root, pid, day)
        if row.get("status") != "ok":
            continue
        entries.append(
            {
                "date": day,
                "segment_count": len(row.get("segments") or []),
                "segments": row.get("segments") or [],
            }
        )
    return {
        "type": "day_journal.project_store",
        "project_id": pid,
        "days": entries,
    }


def attach_project_store_pointers(paths: AgentPaths, result: dict[str, Any]) -> dict[str, Any]:
    """Follow day-journal project ids into the per-project store. Do not enumerate or open sessions."""
    if not isinstance(result, dict) or result.get("status") == "unauthorized":
        return result
    records = result.get("records")
    if not isinstance(records, list) or not records:
        return result
    issues = result.get("issues")
    if not isinstance(issues, list):
        issues = []
        result["issues"] = issues
    root = store_root(paths)
    store_corrupt = False
    for record in records:
        if not isinstance(record, dict):
            continue
        day_key = record.get("date")
        projects = record.get("projects")
        if not isinstance(day_key, str) or not _DATE_RE.fullmatch(day_key) or not isinstance(projects, list):
            record["project_store"] = []
            continue
        entries: list[dict[str, Any]] = []
        for raw_pid in projects:
            if not isinstance(raw_pid, str) or not raw_pid.strip():
                continue
            try:
                row = read_store_day(root, raw_pid, day_key)
            except ValueError:
                issues.append({"date": day_key, "status": "corrupt"})
                store_corrupt = True
                continue
            if row.get("status") != "ok":
                continue
            segments = row.get("segments") or []
            if not segments:
                continue
            entries.append(
                {
                    "project_id": row["project_id"],
                    "date": day_key,
                    "segments": segments,
                }
            )
        record["project_store"] = entries
    if store_corrupt and result.get("status") in {"ok", "summary_missing", "summary_empty"}:
        result["status"] = "partial"
        result["message"] = "部分日期的日记证据不可用；以下结果仅包含可读取的明确记录。"
    return result


def _day_path(root: Path, project_id: str, day_key: str) -> Path:
    DayJournal._validate_id(project_id, "project_id")
    if not day_key or len(day_key) != 10 or day_key[4] != "-" or day_key[7] != "-":
        raise ValueError("day must be an ISO calendar date")
    return root / project_id / f"{day_key}.json"


def append_segment_pointer(
    paths: AgentPaths,
    *,
    project_id: str,
    day_key: str,
    segment_id: str,
    session_id: str,
    occurred_at: str,
) -> dict[str, Any]:
    """Append one stable session pointer under project → day (fail-closed on corrupt data)."""
    pid = DayJournal._validate_id(project_id, "project_id")
    sid = DayJournal._validate_id(segment_id, "segment_id")
    DayJournal._validate_id(session_id, "session_id")
    if not isinstance(occurred_at, str) or not occurred_at.strip():
        raise ValueError("occurred_at must be a non-empty ISO timestamp")

    root = Path(paths.data) / "project-session-store"
    root.mkdir(parents=True, exist_ok=True)
    path = _day_path(root, pid, day_key)
    path.parent.mkdir(parents=True, exist_ok=True)

    proposed = {
        "segment_id": sid,
        "session_id": session_id,
        "occurred_at": occurred_at,
    }

    if not path.exists():
        record = {
            "project_id": pid,
            "date": day_key,
            "segments": [proposed],
            "revision": 1,
        }
    else:
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError) as exc:
            raise ValueError("project session store record is unreadable") from exc
        if (
            not isinstance(current, dict)
            or current.get("project_id") != pid
            or current.get("date") != day_key
            or not isinstance(current.get("segments"), list)
            or type(current.get("revision")) is not int
        ):
            raise ValueError("project session store record schema mismatch")
        for item in current["segments"]:
            if isinstance(item, dict) and item.get("segment_id") == sid:
                if item == proposed:
                    return current
                raise JournalConflict("segment_id already exists with different content")
        record = {
            **current,
            "segments": [*current["segments"], proposed],
            "revision": current["revision"] + 1,
        }

    fd, temp_name = tempfile.mkstemp(prefix=f".{day_key}.", suffix=".tmp", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(record, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass
    return record
