"""run_python — run a .py script under workspace/ or agent root; capture stdout/stderr/exit_code."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

_DEFAULT_TIMEOUT = 120
_MAX_OUTPUT_CHARS = 32000


def _agent_root() -> Path:
    current = Path(__file__).resolve().parent
    for directory in (current, *current.parents):
        evolve_marker = directory / "evolve"
        if (evolve_marker / "_index.core.toml").is_file() or (evolve_marker / "_index.toml").is_file():
            return directory
    raise RuntimeError("could not locate agent root")


def _load_paths():
    core = _agent_root() / "agent-core"
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))
    from paths import AgentPaths, PathOutOfBoundsError

    return AgentPaths, PathOutOfBoundsError


def _truncate(text: str, limit: int = _MAX_OUTPUT_CHARS) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return text[:limit] + "\n…(truncated)", True


def _resolve_script(paths, path_arg: str) -> Path:
    text = path_arg.strip().replace("\\", "/").lstrip("/")
    if not text:
        raise ValueError("path is required")

    try:
        candidate = paths.resolve_under_agent(text, must_exist=True)
    except (FileNotFoundError, TypeError, ValueError):
        raise ValueError(f"file not found: {path_arg}")

    if candidate.suffix.lower() != ".py":
        raise ValueError("only .py scripts are allowed")
    return candidate


def run_python(payload: dict[str, Any]) -> dict[str, Any]:
    AgentPaths, _ = _load_paths()
    paths = AgentPaths.discover(start=_agent_root())

    path_arg = payload.get("path")
    if not isinstance(path_arg, str) or not path_arg.strip():
        return {"ok": False, "error": "path is required"}

    extra_args = payload.get("extra_args", [])
    if extra_args is None:
        extra_args = []
    if not isinstance(extra_args, list) or not all(isinstance(arg, str) for arg in extra_args):
        return {"ok": False, "error": "extra_args must be an array of strings"}

    timeout_sec = int(payload.get("timeout_sec", _DEFAULT_TIMEOUT))
    if timeout_sec < 1:
        timeout_sec = 1
    dry_run = bool(payload.get("dry_run", False))

    try:
        script = _resolve_script(paths, path_arg)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    command = [sys.executable, str(script), *extra_args]
    rel = paths.to_agent_relative(script)

    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "command": command,
            "path": rel,
        }

    try:
        completed = subprocess.run(
            command,
            cwd=str(script.parent),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_sec,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"script timed out after {timeout_sec}s", "command": command}
    except OSError as exc:
        return {"ok": False, "error": str(exc), "command": command}

    stdout, stdout_trunc = _truncate(completed.stdout or "")
    stderr, stderr_trunc = _truncate(completed.stderr or "")
    result: dict[str, Any] = {
        "ok": True,
        "path": rel,
        "command": command,
        "exit_code": completed.returncode,
        "stdout": stdout,
        "stderr": stderr,
    }
    if stdout_trunc or stderr_trunc:
        result["truncated"] = True
    return result


def main() -> None:
    core = _agent_root() / "agent-core"
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))
    from evolve_tool_io import run_tool_main
    run_tool_main(run_python)


def _demo() -> None:
    core = _agent_root() / "agent-core"
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))

    from paths import AgentPaths
    from tools.builtin.run_evolved import run
    from tools.registry import ToolRegistry

    paths = AgentPaths.discover()
    registry = ToolRegistry.load(paths)
    tool = registry.get_evolved("run_python")
    assert tool is not None and tool.status in {"active", "draft"}
    print("[PASS] registry loads run_python")

    rel = "workspace/_run_python_demo.py"
    target = paths.agent_root / rel
    target.write_text("print('hello world')", encoding="utf-8")

    live = run(
        {
            "tool_name": "run_python",
            "arguments": {"path": rel},
            "dry_run": False,
        },
        registry=registry,
    )
    assert live.ok and live.data.get("stdout", "").strip() == "hello world"
    print("[PASS] live run")

    dry = run(
        {
            "tool_name": "run_python",
            "arguments": {"path": rel, "dry_run": True},
        },
        registry=registry,
    )
    assert dry.ok and dry.data.get("dry_run") is True
    print("[PASS] dry_run")

    bad = run(
        {
            "tool_name": "run_python",
            "arguments": {"path": "_nonexistent.py"},
        },
        registry=registry,
    )
    assert not bad.ok and "not found" in (bad.error.message or "").lower()
    print("[PASS] file not found surfaces error message")

    target.unlink(missing_ok=True)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "demo":
        _demo()
    else:
        main()
