from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from datetime import timezone as datetime_timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from day_journal import DayJournal

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_REQUIRED_FIELDS = {"date", "summary", "projects", "segments", "revision"}
_SEGMENT_FIELDS = {"project_id", "date", "segment_id", "summary", "source", "occurred_at"}
_DEFAULT_MAX_RECORD_BYTES = 64 * 1024
_MAX_RANGE_DAYS = 31


def query_day_journal(
    root: str | Path,
    *,
    authorized: bool,
    day: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    project_id: str | None = None,
    max_record_bytes: int = _DEFAULT_MAX_RECORD_BYTES,
    timezone: str = "Asia/Shanghai",
) -> dict[str, Any]:
    """Read explicitly requested local journal dates; never enumerate or dereference sources.

    Supply either ``day`` or both range endpoints. A query is deliberately gated
    by ``authorized=True`` and returns only recorded dates, project IDs, summaries,
    segment summaries, and stable source coordinates. Source coordinates are not
    checked for existence or presented as clickable links.
    """
    if authorized is not True:
        return {"status": "unauthorized", "records": [], "issues": [], "message": "本次日记查询未获授权。"}
    dates = _requested_dates(day, start_date, end_date)
    if project_id is not None and (not isinstance(project_id, str) or not _ID_RE.fullmatch(project_id)):
        raise ValueError("project_id must be a stable project identifier")
    if type(max_record_bytes) is not int or not 1 <= max_record_bytes <= _DEFAULT_MAX_RECORD_BYTES:
        raise ValueError(f"max_record_bytes must be between 1 and {_DEFAULT_MAX_RECORD_BYTES}")
    zone = _load_timezone(timezone)

    base = Path(root).expanduser()
    if _is_link_or_junction(base):
        return {"status": "partial", "records": [], "issues": [{"date": None, "status": "unsafe_root"}],
                "message": "日记存储目录不可安全读取。"}
    if not base.exists():
        return {"status": "no_record", "records": [], "issues": [], "message": "日记存储目录或请求记录不存在；这不表示当日没有发生相关事件。"}
    if not base.is_dir():
        return {"status": "partial", "records": [], "issues": [{"date": None, "status": "unsafe_root"}],
                "message": "日记存储目录不可安全读取。"}
    base = base.absolute()
    records: list[dict[str, Any]] = []
    issues: list[dict[str, str]] = []
    summary_empty = False
    summary_missing = False
    for day_key in dates:
        path = base / f"{day_key}.json"
        if not path.exists() and not path.is_symlink():
            continue
        try:
            if _is_link_or_junction(path) or path.is_dir():
                issues.append({"date": day_key, "status": "unsafe_path"})
                continue
            size = path.stat().st_size
            if size > max_record_bytes:
                issues.append({"date": day_key, "status": "oversized"})
                continue
            payload = json.loads(path.read_text(encoding="utf-8"))
            record, empty = _validated_public_record(payload, day_key, project_id, zone)
            if record is not None:
                records.append(record)
                summary_empty = summary_empty or empty
                summary_missing = summary_missing or record["summary"] is None
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError, KeyError):
            issues.append({"date": day_key, "status": "corrupt"})

    if issues:
        status = "partial"
        message = "部分日期的日记证据不可用；以下结果仅包含可读取的明确记录。"
    elif not records:
        status = "no_record"
        message = "请求日期范围内没有可读取的日记记录；这不表示当日没有发生相关事件。"
    elif summary_missing:
        status = "summary_missing"
        message = "记录存在，但至少一条摘要缺失；不据此补全或推断内容。"
    elif summary_empty:
        status = "summary_empty"
        message = "记录存在，但至少一条摘要为空；不据此补全或推断内容。"
    else:
        status = "ok"
        message = "仅返回本地日记中明确记载的信息。"
    return {"status": status, "records": records, "issues": issues, "message": message}


def _requested_dates(day: str | None, start_date: str | None, end_date: str | None) -> list[str]:
    if day is not None:
        if start_date is not None or end_date is not None:
            raise ValueError("use an exact day or a date range, not both")
        return [_parse_date(day)]
    if start_date is None or end_date is None:
        raise ValueError("supply an exact day or both explicit range endpoints")
    start = date.fromisoformat(_parse_date(start_date))
    end = date.fromisoformat(_parse_date(end_date))
    if end < start:
        raise ValueError("end_date must not precede start_date")
    span = (end - start).days + 1
    if span > _MAX_RANGE_DAYS:
        raise ValueError(f"date range is limited to {_MAX_RANGE_DAYS} days")
    return [(start + timedelta(days=offset)).isoformat() for offset in range(span)]


def _parse_date(value: str) -> str:
    if not isinstance(value, str) or not _DATE_RE.fullmatch(value):
        raise ValueError("dates must use explicit YYYY-MM-DD form")
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise ValueError("invalid calendar date") from exc


def _validated_public_record(payload: Any, requested_day: str, project_filter: str | None, zone) -> tuple[dict[str, Any] | None, bool]:
    if not isinstance(payload, dict) or set(payload) != _REQUIRED_FIELDS or payload.get("date") != requested_day:
        raise ValueError("record schema or date mismatch")
    summary = payload["summary"]
    _validate_summary(summary)
    if not isinstance(payload["projects"], list) or not isinstance(payload["segments"], list):
        raise ValueError("invalid record lists")
    if type(payload["revision"]) is not int or payload["revision"] < 1:
        raise ValueError("invalid revision")
    projects = payload["projects"]
    if any(not isinstance(value, str) or not _ID_RE.fullmatch(value) for value in projects):
        raise ValueError("invalid project identity")
    if projects != sorted(set(projects)):
        raise ValueError("noncanonical project identities")
    public_segments = []
    seen_segment_ids: set[str] = set()
    for segment in payload["segments"]:
        if (not isinstance(segment, dict)
                or set(segment) not in (_SEGMENT_FIELDS, _SEGMENT_FIELDS | {"summary_metadata"})):
            raise ValueError("invalid segment")
        pid, sid = segment["project_id"], segment["segment_id"]
        if (not isinstance(pid, str) or not _ID_RE.fullmatch(pid) or pid not in projects
                or not isinstance(sid, str) or not _ID_RE.fullmatch(sid)
                or segment["date"] != requested_day):
            raise ValueError("invalid segment identity")
        if sid in seen_segment_ids:
            raise ValueError("duplicate segment_id")
        seen_segment_ids.add(sid)
        seg_summary = segment["summary"]
        _validate_summary(seg_summary)
        public_metadata = None
        if "summary_metadata" in segment:
            metadata = DayJournal._validate_summary_metadata(segment["summary_metadata"])
            public_metadata = {"kind": metadata["kind"], "verified": False,
                               "source_ids": metadata["source_ids"]}
        source = segment["source"]
        if (not isinstance(source, dict) or set(source) != {"kind", "session_id"}
                or source.get("kind") != "session" or not isinstance(source.get("session_id"), str)
                or not _ID_RE.fullmatch(source["session_id"])):
            raise ValueError("invalid stable source coordinate")
        if not isinstance(segment["occurred_at"], str):
            raise ValueError("invalid timestamp")
        occurred_at = datetime.fromisoformat(segment["occurred_at"])
        if occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
            raise ValueError("timestamp must retain timezone")
        if occurred_at.astimezone(zone).date().isoformat() != requested_day:
            raise ValueError("timestamp local date does not match day key")
        if project_filter is None or pid == project_filter:
            public_segment = {"project_id": pid, "date": requested_day, "segment_id": sid,
                              "summary": seg_summary, "source": {"kind": "session", "session_id": source["session_id"]}}
            if public_metadata is not None:
                public_segment["summary_metadata"] = public_metadata
            public_segments.append(public_segment)
    if project_filter is not None and project_filter not in projects:
        return None, False
    visible_projects = [project_filter] if project_filter is not None else projects[:]
    return ({"date": requested_day, "summary": summary, "projects": visible_projects, "segments": public_segments},
            isinstance(summary, str) and not summary.strip())



def _validate_summary(value: Any) -> None:
    if value is not None and (not isinstance(value, str) or not value.strip() or len(value) > 2000):
        raise ValueError("summary must be absent or non-empty text up to 2000 characters")


def _load_timezone(timezone_name: str):
    if not isinstance(timezone_name, str) or not timezone_name:
        raise ValueError("timezone must be a non-empty timezone name")
    # UTC has no transition data; use the stdlib fixed-offset singleton so
    # Windows environments do not need an IANA tzdata package for this case.
    if timezone_name == "UTC":
        return datetime_timezone.utc
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        if timezone_name == "Asia/Shanghai":
            # Match DayJournal's Windows fallback when IANA data is unavailable.
            from datetime import timedelta
            return datetime_timezone(timedelta(hours=8), "Asia/Shanghai")
        raise ValueError(f"unknown timezone: {timezone_name!r}") from exc


def _is_link_or_junction(path: Path) -> bool:
    is_junction = getattr(path, "is_junction", None)
    return path.is_symlink() or (callable(is_junction) and is_junction())
