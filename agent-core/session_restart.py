"""Unified live-dialogue restart (今日重来) with day-journal segment pointers."""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_AGENT_CORE = Path(__file__).resolve().parent
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from day_journal import DayJournal
from day_journal_events import record_dialogue_segment
from paths import AgentPaths
from project_mode import ProjectModeError
from project_switch import start_new_project_thread
from session import Session, build_seed_message, create_new, write_last_conversation_id


def day_journal_root(paths: AgentPaths) -> Path:
    return Path(paths.data) / "day-journal"


def _fail_open_record_segment(
    paths: AgentPaths,
    session: Session,
    *,
    event_id: str,
    reason: str,
) -> str | None:
    try:
        journal = DayJournal(day_journal_root(paths))
        record_dialogue_segment(
            session,
            occurred_at=datetime.now(timezone.utc),
            event_id=event_id,
            journal=journal,
            reason=reason,
            paths=paths,
        )
    except Exception as exc:
        return f"对话已重来，但按天日记未能写入：{exc}"
    return None


def perform_session_restart_today(
    paths: AgentPaths,
    session: Session,
    message: dict[str, Any] | None = None,
) -> tuple[Session, list[dict[str, Any]], str | None]:
    """Archive the live line as a day segment, then start a fresh session in the same UI lane."""
    from project_api import project_state_payload
    from session import corruption_notice_events, session_banner_event, session_history_event

    message = message or {}
    request_id = message.get("request_id")
    rid = request_id.strip() if isinstance(request_id, str) and request_id.strip() else str(uuid.uuid4())
    reason = message.get("reason")
    reason_text = reason.strip() if isinstance(reason, str) and reason.strip() else "今日重来"

    previous = session
    segment_id = f"restart:{rid}"
    journal_notice = _fail_open_record_segment(
        paths,
        previous,
        event_id=segment_id,
        reason=reason_text,
    )

    pid = (previous.meta.project_id or "").strip()
    try:
        if pid and previous.meta.active_shell == "project":
            updated, msg = start_new_project_thread(paths, previous, project_id=pid)
        else:
            fresh = create_new(paths)
            seed = build_seed_message(
                previous_session_id=previous.conversation_id,
                previous_goal=previous.goal.strip(),
                reason=reason_text,
                hint="旧对话仍保留在磁盘，可通过按天日记中的会话指针回看。",
            )
            fresh.append_message(seed, persist=False)
            fresh.save()
            write_last_conversation_id(paths, fresh.conversation_id)
            updated, msg = fresh, "已今日重来：聊天区已清空，上一段对话已记入按天日记。"
    except ProjectModeError as exc:
        raise SessionRestartError(str(exc)) from exc

    session_replaced = updated.conversation_id != previous.conversation_id
    events: list[dict[str, Any]] = [
        {
            "type": "session.restart_today.done",
            "request_id": rid,
            "session_id": updated.conversation_id,
            "previous_session_id": previous.conversation_id,
            "segment_id": segment_id,
            "message": msg,
            "session_replaced": session_replaced,
            "journal_notice": journal_notice,
        },
        session_banner_event(updated),
    ]
    if pid and updated.meta.project_id:
        events.insert(1, project_state_payload(updated, paths))
    if session_replaced:
        from context import session_memory_event

        events.append(session_memory_event(updated, quick=not updated._messages_fully_loaded))
        events.append(session_history_event(updated))
        events.extend(corruption_notice_events(updated))
    notice = msg if not journal_notice else f"{msg} {journal_notice}"
    events.append({"type": "notice", "text": notice})
    return updated, events, journal_notice


class SessionRestartError(Exception):
    """Invalid restart request or project thread guard failure."""
