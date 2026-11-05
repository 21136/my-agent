"""Follow day-journal coordinates into bounded session excerpts (DAY-JOURNAL-MEMORY §3–§4).

Does not enumerate sessions. Only session_ids already present in an authorized
journal/store result are opened. Sources are not treated as clickable UI links.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from paths import AgentPaths
from session import MESSAGES_FILENAME, _read_messages_tail, sessions_root

_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_TOKEN_WRAP = r"[A-Za-z0-9._:-]"
_THIS_PROJECT_RE = re.compile(r"本项目|这个项目|当前项目")
_MAX_SESSIONS = 6
_MAX_MESSAGES = 8
_MAX_CHARS = 320


def resolve_recall_project_id(
    text: str,
    result: dict[str, Any],
    *,
    current_project_id: str | None = None,
) -> str | None:
    """Return a unique project id named in the query; never guess among several."""
    if not isinstance(text, str) or not text.strip() or not isinstance(result, dict):
        return None
    candidates: list[str] = []
    seen: set[str] = set()
    for record in result.get("records") or []:
        if not isinstance(record, dict):
            continue
        for raw in record.get("projects") or []:
            if isinstance(raw, str) and raw and raw not in seen:
                seen.add(raw)
                candidates.append(raw)
    hits: list[str] = []
    for pid in sorted(candidates, key=len, reverse=True):
        pattern = rf"(?<!{_TOKEN_WRAP}){re.escape(pid)}(?!{_TOKEN_WRAP})"
        if re.search(pattern, text):
            hits.append(pid)
    unique = list(dict.fromkeys(hits))
    if len(unique) == 1:
        return unique[0]
    if unique:
        return None
    current = (current_project_id or "").strip()
    if current and current in seen and _THIS_PROJECT_RE.search(text):
        return current
    return None


def attach_session_excerpts(paths: AgentPaths, result: dict[str, Any]) -> dict[str, Any]:
    """Attach trailing user/assistant lines for pointed sessions. Never scan the session tree."""
    if not isinstance(result, dict) or result.get("status") == "unauthorized":
        return result
    records = result.get("records")
    if not isinstance(records, list) or not records:
        return result
    excerpts: list[dict[str, Any]] = []
    seen: set[str] = set()
    for session_id in _session_ids_from_result(result):
        if session_id in seen:
            continue
        seen.add(session_id)
        if len(excerpts) >= _MAX_SESSIONS:
            break
        excerpt = _read_session_excerpt(paths, session_id)
        if excerpt is not None:
            excerpts.append(excerpt)
    result["session_excerpts"] = excerpts
    return result


def _session_ids_from_result(result: dict[str, Any]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()

    def _add(session_id: Any) -> None:
        if not isinstance(session_id, str) or not _SESSION_ID_RE.fullmatch(session_id) or session_id in {".", ".."}:
            return
        if session_id in seen:
            return
        seen.add(session_id)
        ordered.append(session_id)

    for record in result.get("records") or []:
        if not isinstance(record, dict):
            continue
        for segment in record.get("segments") or []:
            if not isinstance(segment, dict):
                continue
            source = segment.get("source")
            if isinstance(source, dict) and source.get("kind") == "session":
                _add(source.get("session_id"))
        for entry in record.get("project_store") or []:
            if not isinstance(entry, dict):
                continue
            for item in entry.get("segments") or []:
                if isinstance(item, dict):
                    _add(item.get("session_id"))
    return ordered


def _is_link(path: Path) -> bool:
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


def _read_session_excerpt(paths: AgentPaths, session_id: str) -> dict[str, Any] | None:
    root = sessions_root(paths)
    if _is_link(root) or not root.is_dir():
        return None
    session_dir = root / session_id
    if _is_link(session_dir) or not session_dir.is_dir():
        return None
    messages_path = session_dir / MESSAGES_FILENAME
    if _is_link(messages_path) or not messages_path.is_file():
        return None
    try:
        rows, _total = _read_messages_tail(messages_path, _MAX_MESSAGES)
    except (OSError, UnicodeError, ValueError, TypeError):
        return None
    lines: list[dict[str, str]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        role = row.get("role")
        if role not in {"user", "assistant"}:
            continue
        content = row.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        text = content.strip()
        if len(text) > _MAX_CHARS:
            text = text[:_MAX_CHARS] + "…"
        lines.append({"role": role, "text": text})
    if not lines:
        return None
    return {"session_id": session_id, "messages": lines}
