"""Batch soft or hard trash workspace projects without per-session project binding."""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

_AGENT_CORE = Path(__file__).resolve().parents[2]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from project_mode import ProjectModeError
from project_workspace_trash import trash_workspace_projects
from tools.schema import ToolErrorCode, ToolResult, tool_fail, tool_ok

TOOL_NAME = "trash_workspace_projects"


def _ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def run(
    arguments: dict[str, Any],
    *,
    paths: Any | None = None,
    session: Any | None = None,
) -> ToolResult:
    started = time.perf_counter()
    if paths is None:
        from paths import AgentPaths

        paths = AgentPaths.discover()

    raw_ids = arguments.get("project_ids")
    project_ids: list[str] | None = None
    if raw_ids is not None:
        if not isinstance(raw_ids, list) or not all(isinstance(item, str) for item in raw_ids):
            return tool_fail(
                TOOL_NAME,
                ToolErrorCode.VALIDATION_ERROR,
                "project_ids must be a list of strings when provided",
                duration_ms=_ms(started),
            )
        project_ids = [item.strip() for item in raw_ids if item.strip()]

    glob_pattern = arguments.get("glob_pattern")
    if glob_pattern is not None and not isinstance(glob_pattern, str):
        return tool_fail(
            TOOL_NAME,
            ToolErrorCode.VALIDATION_ERROR,
            "glob_pattern must be a string when provided",
            duration_ms=_ms(started),
        )
    glob_pattern = glob_pattern.strip() if isinstance(glob_pattern, str) else None

    if not project_ids and not glob_pattern:
        return tool_fail(
            TOOL_NAME,
            ToolErrorCode.VALIDATION_ERROR,
            "provide project_ids and/or glob_pattern",
            duration_ms=_ms(started),
        )

    confirm_permanent = arguments.get("confirm_permanent", False)
    if confirm_permanent is not None and not isinstance(confirm_permanent, bool):
        return tool_fail(
            TOOL_NAME,
            ToolErrorCode.VALIDATION_ERROR,
            "confirm_permanent must be a boolean",
            duration_ms=_ms(started),
        )

    dry_run = arguments.get("dry_run", False)
    if dry_run is not None and not isinstance(dry_run, bool):
        return tool_fail(
            TOOL_NAME,
            ToolErrorCode.VALIDATION_ERROR,
            "dry_run must be a boolean",
            duration_ms=_ms(started),
        )

    request_id = arguments.get("request_id")
    if request_id is not None and not isinstance(request_id, str):
        return tool_fail(
            TOOL_NAME,
            ToolErrorCode.VALIDATION_ERROR,
            "request_id must be a string when provided",
            duration_ms=_ms(started),
        )

    bound_session = session
    if bound_session is None and hasattr(arguments.get("session"), "conversation_id"):
        bound_session = arguments.get("session")

    try:
        payload = trash_workspace_projects(
            paths,
            project_ids=project_ids,
            glob_pattern=glob_pattern,
            confirm_permanent=bool(confirm_permanent),
            dry_run=bool(dry_run),
            request_id=request_id if isinstance(request_id, str) else None,
            bound_session=bound_session,
        )
    except ProjectModeError as exc:
        return tool_fail(
            TOOL_NAME,
            ToolErrorCode.VALIDATION_ERROR,
            str(exc),
            duration_ms=_ms(started),
        )

    if not payload.get("ok"):
        return tool_fail(
            TOOL_NAME,
            ToolErrorCode.VALIDATION_ERROR,
            str(payload.get("error") or "trash failed"),
            duration_ms=_ms(started),
            details=payload,
        )
    return tool_ok(TOOL_NAME, payload, duration_ms=_ms(started))
