"""Fail-closed tool policy for sessions bound to an evolution supervisor."""
from __future__ import annotations

from typing import Any

_READ_ONLY_BUILTINS = frozenset({
    "read_file", "list_dir", "grep", "glob_file_search", "codebase_search", "show_images",
})
_EVOLUTION_ACTIONS = frozenset({
    "inspect", "patch", "snapshot", "build", "launch", "stop", "run_case",
})


def evolution_tool_decision(evolution_id: str, tool_name: str, arguments: dict[str, Any]) -> dict[str, str]:
    """Gate executor dispatch after proxy rewriting; an empty binding preserves legacy behavior."""
    if not isinstance(evolution_id, str) or not evolution_id.strip():
        return {"decision": "allow", "reason": "ordinary session has no evolution binding", "code": ""}
    if tool_name == "evolution":
        action = arguments.get("action") if isinstance(arguments, dict) else None
        if isinstance(action, str) and action in _EVOLUTION_ACTIONS:
            return {"decision": "allow", "reason": "action is handled by the bound evolution client", "code": ""}
        return {"decision": "deny", "reason": "unsupported evolution action", "code": "unsupported_capability"}
    if tool_name in {"read_file", "list_dir", "grep", "glob_file_search"}:
        for key in ("path", "root", "directory"):
            value = arguments.get(key) if isinstance(arguments, dict) else None
            if isinstance(value, str) and value.strip().lower().startswith("host:"):
                return {"decision": "deny", "reason": "host-scoped reads are unavailable in an evolution session; use evolution.inspect", "code": "permission_denied"}
    if tool_name in _READ_ONLY_BUILTINS:
        return {"decision": "allow", "reason": "read-only builtin is allowed in an evolution session", "code": ""}
    return {
        "decision": "deny",
        "reason": f"tool {tool_name!r} is not allowed while an evolution binding is active",
        "code": "permission_denied",
    }
