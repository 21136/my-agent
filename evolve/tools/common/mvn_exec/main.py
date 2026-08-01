"""mvn_exec — run a Maven command in a workspace directory; capture stdout/stderr/exit_code."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

_DEFAULT_TIMEOUT = 300
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
    from paths import AgentPaths

    return AgentPaths


def _truncate(text: str, limit: int = _MAX_OUTPUT_CHARS) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return text[:limit] + "\n…(truncated)", True


def _find_mvn() -> str:
    mvn = shutil.which("mvn")
    if mvn:
        return mvn
    for candidate in (
        r"C:\Program Files\apache-maven\bin\mvn.cmd",
        r"C:\Program Files (x86)\apache-maven\bin\mvn.cmd",
    ):
        if Path(candidate).is_file():
            return candidate
    return "mvn"


def _resolve_mvn_bin(cwd: Path) -> str:
    try:
        core = _agent_root() / "agent-core"
        if str(core) not in sys.path:
            sys.path.insert(0, str(core))
        from project_env import load_env_near, resolve_mvn_bin

        return resolve_mvn_bin(load_env_near(cwd))
    except Exception:
        return _find_mvn()


def _resolve_working_dir(paths, path_arg: str | None) -> Path:
    if not path_arg:
        return paths.agent_root
    text = path_arg.strip().replace("\\", "/").lstrip("/")
    try:
        return paths.resolve_under_agent(text, must_exist=True)
    except Exception:
        pass
    if not text.startswith("workspace/"):
        try:
            return paths.resolve_under_agent(f"workspace/{text}", must_exist=True)
        except Exception:
            pass
    return paths.agent_root / text


def _coalesce_working_dir(payload: dict[str, Any]) -> str:
    """E7: accept cwd as alias for working_dir."""
    working = payload.get("working_dir", "")
    if isinstance(working, str) and working.strip():
        return working.strip()
    cwd = payload.get("cwd", "")
    if isinstance(cwd, str) and cwd.strip():
        return cwd.strip()
    return ""


def mvn_exec(payload: dict[str, Any]) -> dict[str, Any]:
    AgentPaths = _load_paths()
    paths = AgentPaths.discover(start=_agent_root())

    args = payload.get("args", [])
    if args is None:
        args = []
    if not isinstance(args, list) or not all(isinstance(item, str) for item in args):
        return {"ok": False, "error": "args must be an array of strings"}

    if not args:
        return {"ok": False, "error": "args is required (e.g. ['-q', 'test'], ['package', '-DskipTests'])"}

    working_dir_arg = _coalesce_working_dir(payload)
    timeout_sec = int(payload.get("timeout_sec", _DEFAULT_TIMEOUT))
    if timeout_sec < 1:
        timeout_sec = 1
    dry_run = bool(payload.get("dry_run", False))

    try:
        cwd = _resolve_working_dir(paths, working_dir_arg)
    except Exception as exc:
        return {"ok": False, "error": f"invalid working_dir: {exc}"}

    mvn = _resolve_mvn_bin(cwd)
    command = [mvn, *args]

    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "command": command,
            "cwd": str(cwd),
        }

    env = {**os.environ, "CI": "true"}
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_sec,
            check=False,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"mvn command timed out after {timeout_sec}s", "command": command}
    except OSError as exc:
        return {"ok": False, "error": f"mvn not available: {exc}", "command": command}

    stdout, stdout_trunc = _truncate(completed.stdout or "")
    stderr, stderr_trunc = _truncate(completed.stderr or "")
    result: dict[str, Any] = {
        "ok": True,
        "cwd": str(cwd),
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

    run_tool_main(mvn_exec)


def _demo() -> None:
    core = _agent_root() / "agent-core"
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))

    from paths import AgentPaths
    from tools.builtin.run_evolved import run
    from tools.registry import ToolRegistry

    paths = AgentPaths.discover()
    registry = ToolRegistry.load(paths)
    tool = registry.get_evolved("mvn_exec")
    assert tool is not None and tool.status in {"active", "draft"}
    print("[PASS] registry loads mvn_exec")

    dry = run(
        {
            "tool_name": "mvn_exec",
            "arguments": {"args": ["--version"], "dry_run": True},
            "dry_run": True,
        },
        registry=registry,
    )
    assert dry.ok and dry.data.get("dry_run") is True
    print("[PASS] dry_run")

    if shutil.which("mvn"):
        live = run(
            {
                "tool_name": "mvn_exec",
                "arguments": {"args": ["--version"]},
                "dry_run": False,
            },
            registry=registry,
        )
        assert live.ok and live.data.get("exit_code") == 0
        print(f"[PASS] mvn version: {live.data.get('stdout', '').strip()}")
    else:
        print("[SKIP] mvn not found; live test skipped")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "demo":
        _demo()
    else:
        main()
