"""Builtin bind_project_dir — Codex-like open folder for the current session."""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

_AGENT_CORE = Path(__file__).resolve().parents[2]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from project_mode import ProjectModeError
from tools.schema import ToolErrorCode, ToolResult, tool_fail, tool_ok

TOOL_NAME = "bind_project_dir"


def run(
    arguments: dict[str, Any],
    *,
    paths: Any | None = None,
    session: Any | None = None,
) -> ToolResult:
    """Bind an absolute directory as the current session project.

    Requires *session* (ToolExecutor path). dry_run=true only scans.
    """
    started = time.perf_counter()
    if session is None:
        return tool_fail(
            TOOL_NAME,
            ToolErrorCode.VALIDATION_ERROR,
            "bind_project_dir requires an active session (use ToolExecutor)",
            duration_ms=_ms(started),
        )
    if paths is None:
        from paths import AgentPaths

        paths = AgentPaths.discover()

    path_arg = arguments.get("path")
    if not isinstance(path_arg, str) or not path_arg.strip():
        return tool_fail(
            TOOL_NAME,
            ToolErrorCode.VALIDATION_ERROR,
            "path is required (absolute directory)",
            duration_ms=_ms(started),
        )

    project_id = arguments.get("project_id")
    if project_id is not None and not isinstance(project_id, str):
        return tool_fail(
            TOOL_NAME,
            ToolErrorCode.VALIDATION_ERROR,
            "project_id must be a string when provided",
            duration_ms=_ms(started),
        )
    project_id = project_id.strip() if isinstance(project_id, str) and project_id.strip() else None

    dry_run = arguments.get("dry_run", False)
    if dry_run is not None and not isinstance(dry_run, bool):
        return tool_fail(
            TOOL_NAME,
            ToolErrorCode.VALIDATION_ERROR,
            "dry_run must be a boolean",
            duration_ms=_ms(started),
        )
    dry_run = bool(dry_run)

    from project_cli import bind_external_project_dir

    try:
        payload = bind_external_project_dir(
            session,
            paths,
            path_arg.strip(),
            project_id=project_id,
            dry_run=dry_run,
        )
    except ProjectModeError as exc:
        code = ToolErrorCode.VALIDATION_ERROR
        msg = str(exc)
        if "禁止接入受保护目录" in msg:
            code = ToolErrorCode.PATH_DENIED
        elif "不可访问" in msg or "不是目录" in msg or "不能为空" in msg:
            code = ToolErrorCode.NOT_FOUND if "不可访问" in msg or "不是目录" in msg else ToolErrorCode.VALIDATION_ERROR
        return tool_fail(TOOL_NAME, code, msg, duration_ms=_ms(started))
    except Exception as exc:
        return tool_fail(
            TOOL_NAME,
            ToolErrorCode.VALIDATION_ERROR,
            str(exc),
            duration_ms=_ms(started),
        )

    return tool_ok(TOOL_NAME, payload, duration_ms=_ms(started))


def _ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)
