from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from day_journal import DayJournal, JournalConflict
from paths import AgentPaths
from session import Session, _read_meta

_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _is_link(path: Path) -> bool:
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


def _validate_session_identity(session: Session, event_id: str) -> tuple[str, str, Path]:
    session_id = session.conversation_id
    project_id = session.meta.project_id
    # Validate every caller-controlled identity before using any value in a path
    # or consulting persisted session metadata.
    if not session_id or not project_id:
        raise ValueError("a bound project Session is required")
    DayJournal._validate_id(project_id, "project_id")
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
    return session_id, project_id, resolved_meta


def record_project_boundary(
    session: Session,
    *,
    occurred_at: datetime,
    event_id: str,
    journal: DayJournal,
) -> dict:
    """Persist one explicit project-boundary event for a verified live Session.

    This records a point event only; it does not infer a project interval,
    inspect messages, or create an unauthorized summary.
    """
    if type(session) is not Session:
        raise ValueError("session must be a live session.Session instance")
    if not isinstance(occurred_at, datetime) or occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
        raise ValueError("occurred_at must be an explicit timezone-aware datetime")

    session_id, project_id, meta_path = _validate_session_identity(session, event_id)
    corruption: list[str] = []
    persisted_meta = _read_meta(meta_path, corruption_kinds=corruption)
    if corruption or persisted_meta.project_id != project_id:
        raise ValueError("session project identity does not match persisted meta.json")

    return journal.record_segment(
        occurred_at,
        project_id,
        None,
        segment_id=event_id,
        source={"kind": "session", "session_id": session_id},
    )
