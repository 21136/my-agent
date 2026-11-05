"""Archive a live dialogue line into the day journal (fail-open helper)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from day_journal import DayJournal
from day_journal_events import record_dialogue_segment
from paths import AgentPaths
from session import Session


def archive_dialogue_line(
    session: Session,
    paths: AgentPaths,
    *,
    reason: str,
    event_id: str | None = None,
) -> str | None:
    """Record the current session as one day segment; returns user notice on failure."""
    prefix = (event_id or "").strip() or f"archive:{uuid.uuid4()}"
    try:
        journal = DayJournal(paths.data / "day-journal")
        record_dialogue_segment(
            session,
            occurred_at=datetime.now(timezone.utc),
            event_id=prefix,
            journal=journal,
            paths=paths,
            reason=reason,
        )
    except Exception as exc:
        return f"按天日记未能写入：{exc}"
    return None
