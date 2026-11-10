"""Builtin search_past_dialogue — keyword search over other sessions' chat text (MEMORY.md §6.4).

Every call is confirmed by the user (registry ``confirm=True``): hits are other
sessions' words, and returning them hands them to the model.
"""

from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

_AGENT_CORE = Path(__file__).resolve().parents[2]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from dialogue_index import DEFAULT_LIMIT, MAX_LIMIT, search_dialogue
from paths import AgentPaths
from tools.schema import ToolErrorCode, ToolResult, tool_fail, tool_ok

TOOL_NAME = "search_past_dialogue"
MAX_QUERY_CHARS = 200


def run(
    arguments: dict[str, Any],
    *,
    paths: AgentPaths | None = None,
    current_session_id: str | None = None,
) -> ToolResult:
    """Return snippets from other sessions that contain every term of *query*."""
    started = time.perf_counter()
    paths = paths or AgentPaths.discover()

    query = arguments.get("query")
    if not isinstance(query, str) or not query.strip():
        return _fail("query is required", ToolErrorCode.VALIDATION_ERROR, started)
    if len(query) > MAX_QUERY_CHARS:
        return _fail(
            f"query must be at most {MAX_QUERY_CHARS} characters",
            ToolErrorCode.VALIDATION_ERROR,
            started,
        )
    limit = arguments.get("limit", DEFAULT_LIMIT)
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        return _fail("limit must be a positive integer", ToolErrorCode.VALIDATION_ERROR, started)

    try:
        hits = search_dialogue(
            paths,
            query,
            limit=min(limit, MAX_LIMIT),
            exclude_session_id=current_session_id,
        )
    except (OSError, sqlite3.Error) as exc:
        return _fail(f"dialogue index unavailable: {exc}", ToolErrorCode.PERMISSION_DENIED, started)

    return tool_ok(
        TOOL_NAME,
        {
            "query": query.strip(),
            "hits": [
                {
                    "session_id": hit.session_id,
                    "message_index": hit.message_index,
                    "role": hit.role,
                    "snippet": hit.snippet,
                }
                for hit in hits
            ],
        },
        duration_ms=_elapsed_ms(started),
    )


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _fail(message: str, code: str, started: float) -> ToolResult:
    return tool_fail(TOOL_NAME, code, message, duration_ms=_elapsed_ms(started))
