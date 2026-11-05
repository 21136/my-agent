"""Post-compact day journal refresh (DAY-JOURNAL-MEMORY §2.2, §3.2–§3.3)."""

from __future__ import annotations

import re
from datetime import date, datetime, time, timezone
from typing import Any
from pathlib import Path

from context import CompactResult
from day_journal import DayJournal
from day_journal_directory import list_recent_days
from paths import AgentPaths
from session import Session

_SEAL_SUMMARY = (
    "按天收尾：当日未生成压缩摘要；进项目时段见短提示与会话指针，普通对话见链接会话原文。"
)

_DIGEST_SECTION_RE = re.compile(r"^##\s+压缩\s+(\d+)", re.MULTILINE)


def _digest_excerpt(session: Session, *, max_chars: int = 480) -> str | None:
    path = session.digest_path
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    matches = list(_DIGEST_SECTION_RE.finditer(text))
    if not matches:
        tail = text.strip()
        return tail[:max_chars] if tail else None
    start = matches[-1].start()
    section = text[start:].strip()
    return section[:max_chars] if section else None


def mechanical_day_summary(session: Session, compact: CompactResult) -> str | None:
    if not compact.compacted:
        return None
    excerpt = _digest_excerpt(session)
    if excerpt:
        return f"上下文已压缩（{compact.digested_message_count} 条）。摘要片段：{excerpt}"
    goal = (session.goal or "").strip()
    if goal:
        return f"上下文已压缩（{compact.digested_message_count} 条）。当前目标：{goal[:400]}"
    return f"上下文已压缩（{compact.digested_message_count} 条）。"


def format_project_segment_template(*, did: str, next_step: str) -> str:
    return (
        f"做了什么：{did.strip()}\n"
        f"未完成/下一步：{next_step.strip()}\n"
        "完整片段：见链接会话记录。"
    )[:2000]


def project_boundary_segment_hint(session: Session) -> str:
    goal = (session.goal or "").strip()
    did = goal[:300] if goal else "进入项目上下文"
    return format_project_segment_template(did=did, next_step="继续当前项目对话。")


def project_segment_hint(session: Session, compact: CompactResult) -> str | None:
    pid = (session.meta.project_id or "").strip()
    if not pid:
        return None
    goal = (session.goal or "").strip()
    did = goal[:300] if goal else "项目对话进行中"
    if compact.compacted:
        next_step = f"已压缩 {compact.digested_message_count} 条旧消息，近期上下文在 digest。"
    else:
        next_step = "继续当前项目对话。"
    return format_project_segment_template(did=did, next_step=next_step)


def _segment_for_session(row: dict, session_id: str) -> dict | None:
    for segment in row.get("segments") or []:
        source = segment.get("source") or {}
        if source.get("session_id") == session_id:
            return segment
    return None


def seal_elapsed_day_summaries(journal: DayJournal, *, now: datetime | None = None) -> int:
    """Fill empty summaries on past local days without inventing what happened."""
    at = now if now is not None else datetime.now(timezone.utc)
    today, _ = journal._instant(at)
    sealed = 0
    for day_key in list_recent_days(journal.root, limit=31):
        if day_key >= today:
            continue
        current = journal.read_day(day_key)
        if current is None or current.get("summary"):
            continue
        noon = datetime.combine(date.fromisoformat(day_key), time(12, 0), tzinfo=journal._zone)
        journal.save_day(
            noon,
            _SEAL_SUMMARY,
            current.get("projects") or [],
            expected_revision=current["revision"],
            summary_authorized=True,
        )
        sealed += 1
    return sealed


def refresh_day_journal_after_compact(
    session: Session,
    paths: AgentPaths,
    compact: CompactResult,
) -> None:
    """Update directory summary and project segment hints after a successful compact."""
    journal = DayJournal(paths.data / "day-journal")
    at = datetime.now(timezone.utc)
    try:
        day_key, _ = journal._instant(at)
        project_id = (session.meta.project_id or "").strip()
        current = journal.read_day(day_key)
        projects = sorted(set((current or {}).get("projects") or []) | ({project_id} if project_id else set()))

        summary = mechanical_day_summary(session, compact)
        if current is None:
            if summary or projects:
                journal.save_day(at, summary, projects, summary_authorized=summary is not None)
                current = journal.read_day(day_key)
        elif summary and current.get("summary") is None:
            journal.save_day(
                at,
                summary,
                projects,
                expected_revision=current["revision"],
                summary_authorized=True,
            )
            current = journal.read_day(day_key)
        elif project_id and project_id not in (current.get("projects") or []):
            journal.save_day(
                at,
                current.get("summary"),
                projects,
                expected_revision=current["revision"],
            )
            current = journal.read_day(day_key)

        if not current:
            return

        hint = project_segment_hint(session, compact)
        if not hint:
            return
        segment = _segment_for_session(current, session.conversation_id)
        if segment is None:
            return
        if segment.get("summary") == hint:
            return
        journal.patch_segment_summary(
            day_key,
            segment["segment_id"],
            hint,
            summary_authorized=True,
            expected_revision=current["revision"],
        )
    finally:
        try:
            seal_elapsed_day_summaries(journal, now=at)
        except Exception:
            pass


def refresh_mechanical_day_summary(session: Session, paths: AgentPaths) -> dict[str, Any]:
    """Fill today's directory summary from digest excerpt when still empty (no LLM)."""
    journal = DayJournal(paths.data / "day-journal")
    at = datetime.now(timezone.utc)
    day_key, _ = journal._instant(at)
    excerpt = _digest_excerpt(session)
    if not excerpt:
        return {"updated": False, "day": day_key, "message": "没有可用的 digest 片段。"}
    summary = f"摘要片段：{excerpt}"[:2000]
    project_id = (session.meta.project_id or "").strip()
    current = journal.read_day(day_key)
    projects = sorted(set((current or {}).get("projects") or []) | ({project_id} if project_id else set()))
    if current is None:
        journal.save_day(at, summary, projects, summary_authorized=True)
        return {"updated": True, "day": day_key, "message": "已写入今日日摘要。"}
    if current.get("summary"):
        return {"updated": False, "day": day_key, "message": "今日已有日摘要，未覆盖。"}
    journal.save_day(
        at,
        summary,
        projects,
        expected_revision=current["revision"],
        summary_authorized=True,
    )
    return {"updated": True, "day": day_key, "message": "已写入今日日摘要。"}


def touch_day_projects_after_compact(session: Session, paths: AgentPaths) -> None:
    """Ensure today's directory lists project ids touched in this session; no unauthorized summary."""
    project_id = (session.meta.project_id or "").strip()
    if not project_id:
        return
    journal = DayJournal(paths.data / "day-journal")
    at = datetime.now(timezone.utc)
    day_key, _ = journal._instant(at)
    current = journal.read_day(day_key)
    if current is None:
        journal.save_day(at, None, [project_id])
        return
    if project_id in current["projects"]:
        return
    journal.save_day(
        at,
        current["summary"],
        sorted(set(current["projects"]) | {project_id}),
        expected_revision=current["revision"],
    )
