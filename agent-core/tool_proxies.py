"""Flat LLM proxy tools that route to run_evolved (AGENT-HARNESS P1 · Phase 41)."""

from __future__ import annotations

from typing import Any

# Cap per AGENT-HARNESS.md §3.1 — do not grow without DOC-04.
PROXY_EVOLVED_TOOL_NAMES: tuple[str, ...] = (
    "run_command",
    "write_text",
    "patch_file",
    "git_diff",
    "git_snapshot",
)

_PROXY_DESCRIPTIONS: dict[str, str] = {
    "run_command": (
        "Run a one-shot shell command under agent root; captures stdout/stderr/exit. "
        "Call this function directly. Do not wrap it in run_evolved."
    ),
    "write_text": (
        "Create or overwrite a text file under agent root (deny-list excepted). "
        "Call this function directly so the user sees the path and the new text. "
        "Do not wrap it in run_evolved."
    ),
    "patch_file": (
        "Patch a text file by line range or unique anchor substring. "
        "Call this function directly so the user sees the path and the edit. "
        "Do not wrap it in run_evolved."
    ),
    "git_diff": (
        "Read-only git diff (no confirm). Supports paths, name_status, stat_only, staged, base_ref. "
        "Call this function directly without wrapping in run_evolved."
    ),
    "git_snapshot": (
        "Read-only git status + diff stat (no confirm). Optional include_diff, paths, base_ref. "
        "Call this function directly without wrapping in run_evolved."
    ),
}

_PROXY_PARAMETERS: dict[str, dict[str, Any]] = {
    "run_command": {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "Full shell command (PowerShell on Windows)",
            },
            "working_dir": {
                "type": "string",
                "description": "Working directory relative to agent root",
            },
            "timeout_sec": {
                "type": "integer",
                "description": "Timeout seconds (default 120; long installs auto-tier)",
            },
            "dry_run": {
                "type": "boolean",
                "description": "Preview cwd/command without executing",
                "default": False,
            },
            "background": {
                "type": "boolean",
                "description": "Escalate to run_service instead of foreground wait",
                "default": False,
            },
        },
        "required": ["command"],
        "additionalProperties": False,
    },
    "write_text": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path relative to agent root",
            },
            "content": {
                "type": "string",
                "description": "Full file body (UTF-8 text)",
            },
            "on_conflict": {
                "type": "string",
                "enum": ["skip", "rename", "overwrite"],
                "description": "When target exists (default skip)",
            },
            "dry_run": {
                "type": "boolean",
                "default": False,
            },
        },
        "required": ["path", "content"],
        "additionalProperties": False,
    },
    "patch_file": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Text file under agent root",
            },
            "replacement": {
                "type": "string",
                "description": "Replacement text",
            },
            "start_line": {
                "type": "integer",
                "minimum": 1,
                "description": "1-based start line (with end_line)",
            },
            "end_line": {
                "type": "integer",
                "minimum": 1,
                "description": "1-based end line inclusive",
            },
            "find": {
                "type": "string",
                "description": "Unique anchor substring when line numbers omitted",
            },
            "dry_run": {
                "type": "boolean",
                "default": False,
            },
        },
        "required": ["path", "replacement"],
        "additionalProperties": False,
    },
    "git_diff": {
        "type": "object",
        "properties": {
            "paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional pathspecs relative to repo root",
            },
            "staged": {
                "type": "boolean",
                "description": "Diff staged changes (--cached)",
                "default": False,
            },
            "name_status": {
                "type": "boolean",
                "description": "Show only names and status of changed files (--name-status)",
                "default": False,
            },
            "stat_only": {
                "type": "boolean",
                "description": "Show diffstat summary only (--stat)",
                "default": False,
            },
            "base_ref": {
                "type": "string",
                "description": "Optional base commit/branch ref",
            },
            "working_dir": {
                "type": "string",
                "description": "Optional working directory relative to agent root",
            },
            "max_chars": {
                "type": "integer",
                "minimum": 1000,
                "maximum": 500000,
                "description": "Max diff characters (default 120000)",
            },
        },
        "additionalProperties": False,
    },
    "git_snapshot": {
        "type": "object",
        "properties": {
            "paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional pathspecs to filter status/diff",
            },
            "include_staged": {
                "type": "boolean",
                "default": True,
            },
            "include_diff": {
                "type": "boolean",
                "description": "Include full unified diff in addition to --stat",
                "default": False,
            },
            "base_ref": {
                "type": "string",
                "description": "Optional base git ref",
            },
        },
        "additionalProperties": False,
    },
}


def is_proxy_evolved_tool(name: str) -> bool:
    return name in PROXY_EVOLVED_TOOL_NAMES


def rewrite_proxy_tool_call(
    tool_name: str,
    arguments: dict[str, Any] | None,
) -> tuple[str, dict[str, Any]]:
    """Map flat proxy call to run_evolved envelope."""
    if not is_proxy_evolved_tool(tool_name):
        return tool_name, dict(arguments or {})
    inner = dict(arguments or {})
    dry_run = inner.pop("dry_run", None)
    payload: dict[str, Any] = {"tool_name": tool_name, "arguments": inner}
    if dry_run is not None:
        payload["dry_run"] = dry_run
    return "run_evolved", payload


def build_proxy_tool_definition(name: str) -> dict[str, Any]:
    if name not in PROXY_EVOLVED_TOOL_NAMES:
        raise KeyError(f"unknown proxy tool: {name!r}")
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": _PROXY_DESCRIPTIONS[name],
            "parameters": _PROXY_PARAMETERS[name],
        },
    }


def build_proxy_tool_definitions() -> list[dict[str, Any]]:
    return [build_proxy_tool_definition(name) for name in PROXY_EVOLVED_TOOL_NAMES]
