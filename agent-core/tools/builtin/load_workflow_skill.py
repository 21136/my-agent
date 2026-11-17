"""Load one evolve/skills workflow body into the current task context (prompt only)."""

from __future__ import annotations

from typing import Any

from paths import AgentPaths
from skill_runtime import agent_tool_load_workflow_skill
from tools.schema import ToolResult


def run(
    arguments: dict[str, Any],
    *,
    paths: AgentPaths | None = None,
    agent_session: Any | None = None,
) -> ToolResult:
    if paths is None:
        return {"ok": False, "tool": "load_workflow_skill", "error": "paths_unavailable"}
    name = arguments.get("skill_id") or arguments.get("name")
    return agent_tool_load_workflow_skill(paths, agent_session, name)