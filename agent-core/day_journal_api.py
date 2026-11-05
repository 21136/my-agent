"""WebSocket day journal directory/read API."""

from __future__ import annotations

from typing import Any

from day_journal_directory import build_directory_payload, read_public_day
from paths import AgentPaths


class DayJournalApiError(Exception):
    pass


def dispatch_day_journal_message(paths: AgentPaths, message: dict[str, Any]) -> dict[str, Any]:
    msg_type = message.get("type")
    root = paths.data / "day-journal"

    if msg_type == "day_journal.directory":
        limit = message.get("limit")
        lim = int(limit) if isinstance(limit, int) and not isinstance(limit, bool) else 14
        return build_directory_payload(root, limit=lim)

    if msg_type == "day_journal.read":
        day = message.get("day")
        if not isinstance(day, str) or not day.strip():
            raise DayJournalApiError("day_journal.read requires day")
        project_id = message.get("project_id")
        pid = project_id.strip() if isinstance(project_id, str) and project_id.strip() else None
        result = read_public_day(root, day.strip(), project_id=pid)
        return {"type": "day_journal.read.done", **result}

    if msg_type == "day_journal.project_store":
        from project_session_store import build_project_store_payload

        project_id = message.get("project_id")
        if not isinstance(project_id, str) or not project_id.strip():
            raise DayJournalApiError("day_journal.project_store requires project_id")
        limit = message.get("limit")
        lim = int(limit) if isinstance(limit, int) and not isinstance(limit, bool) else 14
        return build_project_store_payload(paths, project_id.strip(), limit=lim)

    raise DayJournalApiError(f"unknown day_journal message type: {msg_type}")
