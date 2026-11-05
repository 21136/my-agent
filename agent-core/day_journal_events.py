from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from day_journal import DayJournal, SegmentScope
from day_journal_compact import project_boundary_segment_hint, seal_elapsed_day_summaries
from paths import AgentPaths
from project_session_store import append_segment_pointer
from session import Session, _read_meta

_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _is_link(path: Path) -> bool:
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


def _validate_session_core(session: Session, event_id: str) -> tuple[str, Path]:
    session_id = session.conversation_id
    if not session_id:
        raise ValueError("session_id is required")
    DayJournal._validate_id(event_id, "event_id")
    if not isinstance(session_id, str) or not _SESSION_ID_RE.fullmatch(session_id) or session_id in {".", ".."}:
        raise ValueError("session_id must be a safe stable path component")

    if type(session.paths) is not AgentPaths:
        raise ValueError("session paths must be a verified AgentPaths instance")
    data_root = Path(session.paths.data)
    expected_data = Path(session.paths.agent_root) / "data"
    if data_root.resolve() != expected_data.resolve():
        raise ValueError("session data root does not match AgentPaths.agent_root")
    sessions_root = data_root / "sessions"
    if _is_link(sessions_root):
        raise ValueError("sessions root must not be a symlink or junction")
    try:
        safe_root = sessions_root.resolve(strict=True)
    except OSError as exc:
        raise ValueError("sessions root is unavailable") from exc
    if not safe_root.is_dir():
        raise ValueError("sessions root is not a directory")

    candidate = sessions_root / session_id
    if _is_link(candidate):
        raise ValueError("session directory must not be a symlink or junction")
    try:
        safe_session_dir = candidate.resolve(strict=True)
    except OSError as exc:
        raise ValueError("session directory is unavailable") from exc
    if safe_session_dir.parent != safe_root or not safe_session_dir.is_dir():
        raise ValueError("session directory escapes the sessions root")

    supplied_dir = Path(session.session_dir)
    try:
        supplied_resolved = supplied_dir.resolve(strict=True)
    except OSError as exc:
        raise ValueError("session object directory is unavailable") from exc
    if supplied_resolved != safe_session_dir:
        raise ValueError("session directory does not match the current AgentPaths identity")

    meta_path = safe_session_dir / "meta.json"
    if _is_link(meta_path):
        raise ValueError("session metadata must not be a symlink or junction")
    try:
        resolved_meta = meta_path.resolve(strict=True)
    except OSError as exc:
        raise ValueError("session metadata is unavailable") from exc
    if resolved_meta.parent != safe_session_dir or not resolved_meta.is_file():
        raise ValueError("session metadata escapes the session directory")
    return session_id, resolved_meta


def _validate_session_identity(session: Session, event_id: str) -> tuple[str, str, Path]:
    session_id, meta_path = _validate_session_core(session, event_id)
    project_id = (session.meta.project_id or "").strip()
    if not project_id:
        raise ValueError("a bound project Session is required")
    DayJournal._validate_id(project_id, "project_id")
    return session_id, project_id, meta_path


def record_dialogue_segment(
    session: Session,
    *,
    occurred_at: datetime,
    event_id: str,
    journal: DayJournal,
    paths: AgentPaths | None = None,
    reason: str | None = None,
) -> dict:
    """Append one day segment for ordinary or project-scoped dialogue (DAY-JOURNAL-MEMORY §2–§4)."""
    if type(session) is not Session:
        raise ValueError("session must be a live session.Session instance")
    if not isinstance(occurred_at, datetime) or occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
        raise ValueError("occurred_at must be an explicit timezone-aware datetime")

    session_id, meta_path = _validate_session_core(session, event_id)
    corruption: list[str] = []
    persisted_meta = _read_meta(meta_path, corruption_kinds=corruption)
    if corruption:
        raise ValueError("session metadata is corrupt")

    project_id = (persisted_meta.project_id or "").strip()
    scope: SegmentScope = "project" if project_id else "ordinary"
    if scope == "project":
        DayJournal._validate_id(project_id, "project_id")
        if (session.meta.project_id or "").strip() != project_id:
            raise ValueError("session project identity does not match persisted meta.json")

    summary = None
    if reason and reason.strip():
        summary = reason.strip()[:2000]

    row = journal.record_segment(
        occurred_at,
        summary,
        segment_id=event_id,
        source={"kind": "session", "session_id": session_id},
        scope=scope,
        project_id=project_id or None,
        summary_authorized=summary is not None,
    )

    if scope == "project" and paths is not None:
        day_key = row["date"]
        segment = next(item for item in row["segments"] if item["segment_id"] == event_id)
        append_segment_pointer(
            paths,
            project_id=project_id,
            day_key=day_key,
            segment_id=event_id,
            session_id=session_id,
            occurred_at=segment["occurred_at"],
        )
    try:
        seal_elapsed_day_summaries(journal, now=occurred_at)
    except Exception:
        pass
    return row


def record_project_boundary(
    session: Session,
    *,
    occurred_at: datetime,
    event_id: str,
    journal: DayJournal,
    paths: AgentPaths | None = None,
) -> dict:
    """Persist one explicit project-boundary event for a verified live Session."""
    if type(session) is not Session:
        raise ValueError("session must be a live session.Session instance")
    if not isinstance(occurred_at, datetime) or occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
        raise ValueError("occurred_at must be an explicit timezone-aware datetime")

    session_id, project_id, meta_path = _validate_session_identity(session, event_id)
    corruption: list[str] = []
    persisted_meta = _read_meta(meta_path, corruption_kinds=corruption)
    if corruption or persisted_meta.project_id != project_id:
        raise ValueError("session project identity does not match persisted meta.json")

    summary = project_boundary_segment_hint(session)
    row = journal.record_segment(
        occurred_at,
        summary,
        segment_id=event_id,
        source={"kind": "session", "session_id": session_id},
        scope="project",
        project_id=project_id,
        summary_authorized=True,
    )
    if paths is not None:
        segment = next(item for item in row["segments"] if item["segment_id"] == event_id)
        append_segment_pointer(
            paths,
            project_id=project_id,
            day_key=row["date"],
            segment_id=event_id,
            session_id=session_id,
            occurred_at=segment["occurred_at"],
        )
    try:
        seal_elapsed_day_summaries(journal, now=occurred_at)
    except Exception:
        pass
    return row
