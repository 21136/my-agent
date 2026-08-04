"""Builtin plan_partner (PLAN-SUBAGENT Phase 39 · T-3901).

Execution (subagent spawn + sidebar refresh) is owned by ToolExecutor.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

_AGENT_CORE = Path(__file__).resolve().parents[2]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from tools.schema import ToolErrorCode, ToolResult, tool_fail, tool_ok

TOOL_NAME = "plan_partner"


def run(arguments: dict[str, Any], **_kwargs: Any) -> ToolResult:
    """Should not be called directly — executor runs the subagent path."""
    started = time.perf_counter()
    task = str(arguments.get("task") or "").strip()
    if not task:
        return tool_fail(
            TOOL_NAME,
            ToolErrorCode.VALIDATION_ERROR,
            "task is required",
            duration_ms=_ms(started),
        )
    return tool_ok(
        TOOL_NAME,
        {"note": "use ToolExecutor path", "task": task[:200]},
        duration_ms=_ms(started),
    )


def _ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)
