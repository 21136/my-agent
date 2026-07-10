"""Builtin run_evolved — execute registry evolved tools (TOOLS.md §7.6, TASKS T-107)."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

_AGENT_CORE = Path(__file__).resolve().parents[2]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from paths import AgentPaths
from tools.registry import EvolvedTool, ToolRegistry, parse_tool_manifest
from tools.schema import ToolErrorCode, ToolResult, tool_fail, tool_ok, to_json

TOOL_NAME = "run_evolved"


def run(
    arguments: dict[str, Any],
    *,
    registry: ToolRegistry | None = None,
    paths: AgentPaths | None = None,
    allowed_tools: set[str] | None = None,
) -> ToolResult:
    """Invoke an evolved tool script with JSON stdin; wrap stdout in ToolResult."""
    _ = paths
    started = time.perf_counter()
    registry = registry or ToolRegistry.load()

    tool_name = arguments.get("tool_name")
    if not isinstance(tool_name, str) or not tool_name.strip():
        return _fail("tool_name is required", ToolErrorCode.VALIDATION_ERROR, started)

    tool_args = arguments.get("arguments")
    if tool_args is None:
        tool_args = {}
    if not isinstance(tool_args, dict):
        return _fail("arguments must be an object", ToolErrorCode.VALIDATION_ERROR, started, tool_name=tool_name)

    dry_run = arguments.get("dry_run", False)
    if not isinstance(dry_run, bool):
        return _fail("dry_run must be a boolean", ToolErrorCode.VALIDATION_ERROR, started, tool_name=tool_name)

    tool = registry.get_evolved(tool_name.strip())
    if tool is None:
        return _fail(
            f"unknown evolved tool: {tool_name}",
            ToolErrorCode.TOOL_NOT_FOUND,
            started,
            tool_name=tool_name,
        )

    if allowed_tools is not None and tool.name not in allowed_tools:
        return _fail(
            f"tool not allowed in this session: {tool.name}",
            ToolErrorCode.TOOL_NOT_FOUND,
            started,
            tool_name=tool.name,
        )

    if dry_run and not tool.policy.dry_run_supported:
        return _fail(
            f"tool {tool.name} does not support dry_run",
            ToolErrorCode.VALIDATION_ERROR,
            started,
            tool_name=tool.name,
        )

    try:
        inner = execute_evolved_tool(tool, tool_args, dry_run=dry_run)
    except subprocess.TimeoutExpired:
        return _fail(
            f"tool {tool.name} timed out after {tool.policy.timeout_sec}s",
            ToolErrorCode.TIMEOUT,
            started,
            tool_name=tool.name,
        )
    except _EvolvedExecutionError as exc:
        return _fail(str(exc), exc.code, started, tool_name=tool.name, details=exc.details)

    if inner.get("ok") is False:
        message = str(inner.get("error") or inner.get("message") or "evolved tool failed")
        return _fail(message, ToolErrorCode.VALIDATION_ERROR, started, tool_name=tool.name, details=inner)

    data = {"tool_name": tool.name}
    for key, value in inner.items():
        if key == "ok":
            continue
        data[key] = value

    return tool_ok(TOOL_NAME, data, duration_ms=_elapsed_ms(started))


def execute_evolved_tool(
    tool: EvolvedTool,
    arguments: dict[str, Any],
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Run ``tool`` entry script; return parsed JSON from stdout."""
    payload = dict(arguments)
    payload["dry_run"] = dry_run
    stdin = json.dumps(payload, ensure_ascii=False)

    proc = subprocess.run(
        [sys.executable, str(tool.entry.script_path)],
        input=stdin,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(tool.directory),
        timeout=tool.policy.timeout_sec,
    )

    if proc.returncode != 0:
        message = proc.stderr.strip() or f"process exited with code {proc.returncode}"
        raise _EvolvedExecutionError(
            message,
            code=ToolErrorCode.VALIDATION_ERROR,
            details={"exit_code": proc.returncode, "stderr": proc.stderr.strip()},
        )

    try:
        inner = _parse_stdout_json(proc.stdout)
    except json.JSONDecodeError as exc:
        raise _EvolvedExecutionError(
            f"invalid JSON on stdout: {exc}",
            code=ToolErrorCode.VALIDATION_ERROR,
            details={"stdout": proc.stdout.strip()},
        ) from exc

    if not isinstance(inner, dict):
        raise _EvolvedExecutionError(
            "stdout JSON must be an object",
            code=ToolErrorCode.VALIDATION_ERROR,
            details={"stdout": proc.stdout.strip()},
        )
    return inner


class _EvolvedExecutionError(RuntimeError):
    def __init__(self, message: str, *, code: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = details


def _parse_stdout_json(stdout: str) -> dict[str, Any]:
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    if not lines:
        raise json.JSONDecodeError("empty stdout", stdout, 0)
    parsed = json.loads(lines[-1])
    if not isinstance(parsed, dict):
        raise json.JSONDecodeError("expected JSON object", lines[-1], 0)
    return parsed


def _fail(
    message: str,
    code: str,
    started: float,
    *,
    tool_name: str | None = None,
    details: dict[str, Any] | None = None,
) -> ToolResult:
    merged = dict(details or {})
    if tool_name is not None:
        merged.setdefault("tool_name", tool_name)
    return tool_fail(
        TOOL_NAME,
        code,
        message,
        duration_ms=_elapsed_ms(started),
        details=merged or None,
    )


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _demo() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        evolve = Path(tmp)
        tool_dir = evolve / "tools" / "common" / "echo_json"
        tool_dir.mkdir(parents=True)
        (tool_dir / "main.py").write_text(
            """import json
import sys

payload = json.load(sys.stdin)
if payload.get("dry_run"):
    print(json.dumps({"ok": True, "dry_run": True, "received": payload}))
else:
    print(json.dumps({"ok": True, "echo": payload.get("message", "")}))
""",
            encoding="utf-8",
        )
        manifest = tool_dir / "tool.toml"
        manifest.write_text(
            """[tool]
name = "echo_json"
description = "Echo stdin payload"
version = "1.0.0"
status = "active"
topics = ["common"]

[entry]
type = "python"
path = "main.py"

[schema.input]
type = "object"
required = ["message"]
[schema.input.properties.message]
type = "string"

[schema.output]
type = "object"

[policy]
confirm = true
dry_run_supported = true
workspace_only = true
timeout_sec = 30
""",
            encoding="utf-8",
        )

        paths = AgentPaths.discover()
        tool = parse_tool_manifest(manifest, evolve_dir=evolve)
        registry = ToolRegistry(agent_paths=paths, evolved=[tool])

        live = run(
            {"tool_name": "echo_json", "arguments": {"message": "hello"}, "dry_run": False},
            registry=registry,
        )
        assert live.ok and live.data["echo"] == "hello"
        print(f"[PASS] execute: {live.data}")

        dry = run(
            {"tool_name": "echo_json", "arguments": {"message": "hello"}, "dry_run": True},
            registry=registry,
        )
        assert dry.ok and dry.data.get("dry_run") is True
        print("[PASS] dry_run")

        missing = run({"tool_name": "missing", "arguments": {}}, registry=registry)
        assert not missing.ok and missing.error.code == ToolErrorCode.TOOL_NOT_FOUND
        print("[PASS] tool_not_found")

        blocked = run(
            {"tool_name": "echo_json", "arguments": {"message": "x"}},
            registry=registry,
            allowed_tools=set(),
        )
        assert not blocked.ok and blocked.error.code == ToolErrorCode.TOOL_NOT_FOUND
        print("[PASS] session allowlist")

        print(to_json(live, indent=2))


if __name__ == "__main__":
    _demo()
