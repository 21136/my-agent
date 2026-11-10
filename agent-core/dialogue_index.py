"""Local full-text index over past sessions' user/assistant text (MEMORY.md §6.4).

The index is a derived cache under ``data/dialogue-index/``: messages.jsonl stays
the source of truth, and a session is re-indexed whenever its file changes.
Nothing here talks to a model; callers decide who may see the hits.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from paths import AgentPaths
from session import (
    ANCHOR_HEADER,
    MESSAGES_FILENAME,
    SEED_PREFIX,
    SESSIONS_DIRNAME,
    is_internal_session_id,
)

INDEX_DIRNAME = "dialogue-index"
INDEX_FILENAME = "index.sqlite"
DEFAULT_LIMIT = 8
MAX_LIMIT = 20
SNIPPET_RADIUS = 120
# The trigram tokenizer matches substrings of 3+ characters, which is what makes
# unsegmented Chinese searchable; shorter terms fall back to a LIKE scan.
_MIN_TRIGRAM_CHARS = 3
# Host-authored user rows (session anchor, kernel notes) are not conversation.
_HOST_USER_PREFIXES = (ANCHOR_HEADER, SEED_PREFIX, "[内核]", "[Harness]")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS indexed_sessions (
    session_id TEXT PRIMARY KEY,
    size INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS dialogue USING fts5(
    text,
    session_id UNINDEXED,
    message_index UNINDEXED,
    role UNINDEXED,
    tokenize = 'trigram'
);
"""


@dataclass(frozen=True, slots=True)
class DialogueHit:
    session_id: str
    message_index: int
    role: str
    snippet: str


def index_path(paths: AgentPaths) -> Path:
    return paths.data / INDEX_DIRNAME / INDEX_FILENAME


def search_dialogue(
    paths: AgentPaths,
    query: str,
    *,
    limit: int = DEFAULT_LIMIT,
    exclude_session_id: str | None = None,
) -> list[DialogueHit]:
    """Hits for all whitespace-separated terms of *query*, best match first.

    Brings the index up to date first, so results reflect messages.jsonl now.
    """
    terms = [term for term in query.split() if term]
    if not terms:
        return []
    limit = max(1, min(int(limit), MAX_LIMIT))
    connection = _connect(paths)
    try:
        refresh_index(connection, paths)
        rows = _query(connection, terms, limit=limit, exclude_session_id=exclude_session_id)
    finally:
        connection.close()
    return [
        DialogueHit(
            session_id=session_id,
            message_index=int(message_index),
            role=role,
            snippet=_snippet(text, terms),
        )
        for text, session_id, message_index, role in rows
    ]


def refresh_index(connection: sqlite3.Connection, paths: AgentPaths) -> int:
    """Re-index sessions whose messages.jsonl changed; return how many were re-indexed."""
    sessions_root = paths.data / SESSIONS_DIRNAME
    known = {
        session_id: (size, mtime_ns)
        for session_id, size, mtime_ns in connection.execute(
            "SELECT session_id, size, mtime_ns FROM indexed_sessions"
        )
    }
    seen: set[str] = set()
    reindexed = 0
    if sessions_root.is_dir():
        for session_dir in sessions_root.iterdir():
            session_id = session_dir.name
            messages_path = session_dir / MESSAGES_FILENAME
            if is_internal_session_id(session_id) or not messages_path.is_file():
                continue
            seen.add(session_id)
            stat = messages_path.stat()
            fingerprint = (stat.st_size, stat.st_mtime_ns)
            if known.get(session_id) == fingerprint:
                continue
            _reindex_session(connection, session_id, messages_path, fingerprint)
            reindexed += 1
    for session_id in known.keys() - seen:
        connection.execute("DELETE FROM dialogue WHERE session_id = ?", (session_id,))
        connection.execute("DELETE FROM indexed_sessions WHERE session_id = ?", (session_id,))
    connection.commit()
    return reindexed


def _connect(paths: AgentPaths) -> sqlite3.Connection:
    path = index_path(paths)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=5.0)
    connection.executescript(_SCHEMA)
    return connection


def _reindex_session(
    connection: sqlite3.Connection,
    session_id: str,
    messages_path: Path,
    fingerprint: tuple[int, int],
) -> None:
    connection.execute("DELETE FROM dialogue WHERE session_id = ?", (session_id,))
    connection.executemany(
        "INSERT INTO dialogue (text, session_id, message_index, role) VALUES (?, ?, ?, ?)",
        (
            (text, session_id, message_index, role)
            for message_index, role, text in _conversation_rows(messages_path)
        ),
    )
    connection.execute(
        "INSERT OR REPLACE INTO indexed_sessions (session_id, size, mtime_ns) VALUES (?, ?, ?)",
        (session_id, *fingerprint),
    )


def _conversation_rows(messages_path: Path) -> list[tuple[int, str, str]]:
    """(record index, role, text) for what the user and the assistant actually said."""
    rows: list[tuple[int, str, str]] = []
    content = messages_path.read_bytes().decode("utf-8", errors="replace")
    # Split on "\n" only: JSON strings may hold U+2028, which splitlines() breaks on.
    for message_index, line in enumerate(raw for raw in content.split("\n") if raw.strip()):
        try:
            message: Any = json.loads(line)
        except ValueError:
            continue
        if not isinstance(message, dict):
            continue
        role, text = message.get("role"), message.get("content")
        if role not in ("user", "assistant") or not isinstance(text, str) or not text.strip():
            continue
        if role == "user" and text.lstrip().startswith(_HOST_USER_PREFIXES):
            continue
        rows.append((message_index, role, text))
    return rows


def _query(
    connection: sqlite3.Connection,
    terms: list[str],
    *,
    limit: int,
    exclude_session_id: str | None,
) -> list[tuple[str, str, int, str]]:
    long_terms = [term for term in terms if len(term) >= _MIN_TRIGRAM_CHARS]
    short_terms = [term for term in terms if len(term) < _MIN_TRIGRAM_CHARS]
    clauses: list[str] = []
    params: list[Any] = []
    if long_terms:
        clauses.append("dialogue MATCH ?")
        params.append(" AND ".join('"' + term.replace('"', '""') + '"' for term in long_terms))
    for term in short_terms:
        clauses.append("text LIKE ? ESCAPE '\\'")
        params.append("%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
    if exclude_session_id:
        clauses.append("session_id != ?")
        params.append(exclude_session_id)
    # Relevance when the FTS index is in play; otherwise newest session first.
    order = "rank" if long_terms else "session_id DESC, CAST(message_index AS INTEGER) DESC"
    sql = (
        "SELECT text, session_id, message_index, role FROM dialogue "
        f"WHERE {' AND '.join(clauses)} ORDER BY {order} LIMIT ?"
    )
    return list(connection.execute(sql, (*params, limit)))


def _snippet(text: str, terms: list[str]) -> str:
    folded = text.casefold()
    positions = [pos for pos in (folded.find(term.casefold()) for term in terms) if pos >= 0]
    center = min(positions) if positions else 0
    start = max(0, center - SNIPPET_RADIUS)
    end = min(len(text), center + SNIPPET_RADIUS)
    snippet = " ".join(text[start:end].split())
    return ("…" if start > 0 else "") + snippet + ("…" if end < len(text) else "")
