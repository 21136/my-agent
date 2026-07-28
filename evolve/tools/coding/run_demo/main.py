"""run_demo — run python acceptance scripts under agent-core (P3 coding)."""

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


def _agent_core_dir() -> Path:
    return _agent_root() / "agent-core"


def _load_paths():
    core = _agent_core_dir()
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))
    from paths import AgentPaths, PathOutOfBoundsError

    return AgentPaths, PathOutOfBoundsError


def _resolve_script(paths, path_arg: str) -> Path:
    text = path_arg.strip()
    if text.startswith("agent-core/"):
        text = text.removeprefix("agent-core/")

    try:
        candidate = text if ("/" in text or "\\" in text) else f"agent-core/{text}"
        resolved = paths.resolve_under_agent(candidate, must_exist=True)
    except Exception as exc:
        raise ValueError(str(exc)) from exc

    agent_core = paths.agent_root / "agent-core"
    try:
        resolved.resolve().relative_to(agent_core.resolve())
    except ValueError as exc:
        raise ValueError("path must be under agent-core/") from exc
    if resolved.suffix.lower() != ".py":
        raise ValueError("only .py scripts are allowed")
    return resolved


def _truncate(text: str, limit: int = _MAX_OUTPUT_CHARS) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return text[:limit] + "\n…(truncated)", True


def run_demo(payload: dict[str, Any]) -> dict[str, Any]:
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
            cwd=str(paths.agent_root / "agent-core"),
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
    core = _agent_core_dir()
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))
    from evolve_tool_io import run_tool_main
    run_tool_main(run_demo)


def _demo() -> None:
    core = _agent_core_dir()
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))

    from tools.builtin.run_evolved import run
    from tools.registry import ToolRegistry

    registry = ToolRegistry.load()
    tool = registry.get_evolved("run_demo")
    assert tool is not None and tool.scope == "coding"
    print("[PASS] registry loads run_demo (coding, active)")

    dry = run(
        {
            "tool_name": "run_demo",
            "arguments": {"path": "paths.py", "extra_args": []},
            "dry_run": True,
        },
        registry=registry,
    )
    assert dry.ok and dry.data.get("dry_run") is True
    print("[PASS] dry_run reports command")

    live = run(
        {
            "tool_name": "run_demo",
            "arguments": {"path": "paths.py", "extra_args": []},
            "dry_run": False,
        },
        registry=registry,
    )
    assert live.ok and live.data.get("exit_code") == 0
    assert "[PASS]" in (live.data.get("stdout") or "")
    print("[PASS] live run paths.py")

    bad = run(
        {
            "tool_name": "run_demo",
            "arguments": {"path": "../outside.py"},
            "dry_run": False,
        },
        registry=registry,
    )
    assert not bad.ok
    print("[PASS] path outside agent-core rejected")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "demo":
        _demo()
    else:
        main()
