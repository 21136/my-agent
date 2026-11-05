"""Project day-journal LLM summary readiness (DAY-JOURNAL-MEMORY §3.2)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from day_journal import DayJournal
from session import Session
from work_records import WorkRecordStore


def shanghai_timezone():
    try:
        return ZoneInfo("Asia/Shanghai")
    except ZoneInfoNotFoundError:
        return timezone(timedelta(hours=8), "Asia/Shanghai")


def _has_session_boundary_segment(
    day_record: dict,
    *,
    day_key: str,
    project_id: str,
    session_id: str,
) -> bool:
    segments = day_record.get("segments")
    if not isinstance(segments, list):
        return False
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        if segment.get("date") != day_key or segment.get("project_id") != project_id:
            continue
        if segment.get("summary") is not None or "summary_metadata" in segment:
            continue
        source = segment.get("source")
        if source != {"kind": "session", "session_id": session_id}:
            continue
        segment_id = segment.get("segment_id")
        if isinstance(segment_id, str) and segment_id:
            return True
    return False


def discover_same_day_requirement_keys(
    store: WorkRecordStore,
    *,
    project_id: str,
    day_key: str,
    zone,
    items: list[dict[str, Any]] | None = None,
) -> list[tuple[str, str]] | None:
    if items is None:
        items = store.list_items()
    if not isinstance(items, list) or len(items) > 200:
        return None
    found: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in items:
        if not isinstance(item, dict):
            return None
        if item.get("scope") != {"kind": "project", "project_id": project_id}:
            continue
        history = item.get("history")
        if not isinstance(history, list) or len(history) > 200:
            return None
        for event in history:
            if not isinstance(event, dict):
                return None
            if event.get("kind") != "requirement_added":
                continue
            event_at = event.get("at")
            if isinstance(event_at, bool) or not isinstance(event_at, (int, float)):
                return None
            try:
                event_day = datetime.fromtimestamp(event_at, timezone.utc).astimezone(zone).date().isoformat()
            except (OverflowError, OSError, ValueError):
                return None
            if event_day != day_key:
                continue
            item_id, event_id = item.get("item_id"), event.get("event_id")
            if not isinstance(item_id, str) or not isinstance(event_id, str):
                return None
            pair = (item_id, event_id)
            if pair in seen:
                return None
            seen.add(pair)
            found.append(pair)
            if len(found) > 20:
                return None
    return sorted(found)


def evaluate_summary_readiness(session: Session, journal: DayJournal) -> dict[str, Any]:
    """Return whether the current session can start the two-step LLM summary flow."""
    session_id = session.conversation_id
    project_id = getattr(session.meta, "project_id", "")
    if not isinstance(project_id, str) or not project_id or not isinstance(session_id, str) or not session_id:
        return {
            "status": "blocked",
            "ready": False,
            "message": "当前对话没有绑定的项目；无法生成项目日记摘要。",
        }
    zone = shanghai_timezone()
    day_key = datetime.now(zone).date().isoformat()
    day_record = journal.read_day(day_key)
    if (not isinstance(day_record, dict) or day_record.get("date") != day_key
            or project_id not in day_record.get("projects", [])):
        return {
            "status": "blocked",
            "ready": False,
            "day": day_key,
            "project_id": project_id,
            "message": "今天还没有当前项目的日记条目。",
        }
    if not _has_session_boundary_segment(
        day_record, day_key=day_key, project_id=project_id, session_id=session_id,
    ):
        return {
            "status": "blocked",
            "ready": False,
            "day": day_key,
            "project_id": project_id,
            "message": "今天缺少当前会话的项目边界段落（切换项目或今日重来后会写入）。",
        }
    store = WorkRecordStore(session)
    candidate_keys = discover_same_day_requirement_keys(
        store, project_id=project_id, day_key=day_key, zone=zone,
    )
    if candidate_keys is None or not candidate_keys:
        return {
            "status": "blocked",
            "ready": False,
            "day": day_key,
            "project_id": project_id,
            "message": "今天没有可核验的项目需求来源，无法外发摘要。",
        }
    return {
        "status": "ready",
        "ready": True,
        "day": day_key,
        "project_id": project_id,
        "source_count": len(candidate_keys),
        "message": "可以发起外发确认与保存确认；摘要将标记为未经核实。",
    }
