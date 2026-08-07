"""Builtin codebase_search — semantic/BM25 code discovery (Pack 5 · T-5501)."""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

_AGENT_CORE = Path(__file__).resolve().parents[2]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from codebase_index import HARD_TOP_K, search_codebase
from paths import AgentPaths, PathOutOfBoundsError
from tools.schema import ToolErrorCode, ToolResult, tool_fail, tool_ok

TOOL_NAME = "codebase_search"


def run(
    arguments: dict[str, Any],
    *,
    paths: AgentPaths | None = None,
    project_root: str = "",
    project_id: str = "",
) -> ToolResult:
    started = time.perf_counter()
    paths = paths or AgentPaths.discover()

    query = arguments.get("query")
    if not isinstance(query, str) or not query.strip():
        return _fail("query is required", ToolErrorCode.VALIDATION_ERROR, started)

    top_k = arguments.get("top_k", 5)
    if not isinstance(top_k, int) or top_k < 1:
        return _fail("top_k must be a positive integer", ToolErrorCode.VALIDATION_ERROR, started)
    top_k = min(top_k, HARD_TOP_K)

    path_prefix = arguments.get("path_prefix", "")
    if path_prefix is not None and not isinstance(path_prefix, str):
        return _fail("path_prefix must be a string", ToolErrorCode.VALIDATION_ERROR, started)

    force_refresh = arguments.get("force_refresh", False)
    if not isinstance(force_refresh, bool):
        return _fail("force_refresh must be a boolean", ToolErrorCode.VALIDATION_ERROR, started)

    try:
        data = search_codebase(
            paths,
            query=query.strip(),
            project_root=project_root,
            project_id=project_id,
            top_k=top_k,
            path_prefix=str(path_prefix or ""),
            force_refresh=force_refresh,
        )
    except PathOutOfBoundsError as exc:
        return _fail(str(exc), exc.code, started)
    except OSError as exc:
        return _fail(str(exc), ToolErrorCode.PERMISSION_DENIED, started)

    return tool_ok(TOOL_NAME, data, duration_ms=_elapsed_ms(started))


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _fail(message: str, code: str, started: float) -> ToolResult:
    return tool_fail(TOOL_NAME, code, message, duration_ms=_elapsed_ms(started))
