"""Builtin propose_context_switch (CONTEXT-SWITCH Phase 19 M0).

Execution (confirm + apply) is owned by ToolExecutor; this module only
validates arguments for the catalog / dry docs.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

_AGENT_CORE = Path(__file__).resolve().parents[2]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from context_switch import ContextSwitchError, normalize_proposal
from tools.schema import ToolErrorCode, ToolResult, tool_fail, tool_ok

TOOL_NAME = "propose_context_switch"


def run(arguments: dict[str, Any], **_kwargs: Any) -> ToolResult:
    """Should not be called directly — executor runs the confirm/apply path."""
    started = time.perf_counter()
    try:
        proposal = normalize_proposal(
            action=str(arguments.get("action", "")),
            target=str(arguments.get("target", "")),
            reason=str(arguments.get("reason", "") or ""),
            template=(
                str(arguments["template"]) if isinstance(arguments.get("template"), str) else None
            ),
        )
    except (ContextSwitchError, Exception) as exc:
        from project_mode import ProjectModeError

        code = ToolErrorCode.VALIDATION_ERROR
        if isinstance(exc, ProjectModeError):
            return tool_fail(TOOL_NAME, code, str(exc), duration_ms=_ms(started))
        return tool_fail(TOOL_NAME, code, str(exc), duration_ms=_ms(started))
    return tool_ok(
        TOOL_NAME,
        {
            "note": "use ToolExecutor path",
            "action": proposal.action,
            "target": proposal.target,
        },
        duration_ms=_ms(started),
    )


def _ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)
