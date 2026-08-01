"""npm_exec — run an npm command in a workspace directory; capture stdout/stderr/exit_code."""

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


def _find_npm() -> str:
    npm = shutil.which("npm")
    if npm:
        return npm
    for candidate in (
        r"C:\Program Files\nodejs\npm.cmd",
        r"C:\Program Files (x86)\nodejs\npm.cmd",
    ):
        if Path(candidate).is_file():
            return candidate
    return "npm"


def _resolve_pkg_bin(cwd: Path) -> tuple[str, str]:
    """Prefer ENV.md tools + prefer.package_manager near cwd."""
    try:
        core = _agent_root() / "agent-core"
        if str(core) not in sys.path:
            sys.path.insert(0, str(core))
        from project_env import load_env_near, resolve_package_manager_bin

        env = load_env_near(cwd)
        return resolve_package_manager_bin(env)
    except Exception:
        return "npm", _find_npm()


def _resolve_working_dir(paths, path_arg: str | None) -> Path:
    if not path_arg:
        return paths.agent_root
    text = path_arg.strip().replace("\\", "/").lstrip("/")
    try:
        return paths.resolve_under_agent(text, must_exist=True)
    except Exception:
        pass
    # Bare project-relative: try under workspace/
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


def npm_exec(payload: dict[str, Any]) -> dict[str, Any]:
    AgentPaths = _load_paths()
    paths = AgentPaths.discover(start=_agent_root())

    args = payload.get("args", [])
    if args is None:
        args = []
    if not isinstance(args, list) or not all(isinstance(item, str) for item in args):
        return {"ok": False, "error": "args must be an array of strings"}

    if not args:
        return {"ok": False, "error": "args is required (e.g. ['install'], ['run', 'dev'])"}

    working_dir_arg = _coalesce_working_dir(payload)
    timeout_sec = int(payload.get("timeout_sec", _DEFAULT_TIMEOUT))
    if timeout_sec < 1:
        timeout_sec = 1
    dry_run = bool(payload.get("dry_run", False))
    force_install = bool(payload.get("force_install", False))

    try:
        cwd = _resolve_working_dir(paths, working_dir_arg)
    except Exception as exc:
        return {"ok": False, "error": f"invalid working_dir: {exc}"}

    # E9: skip redundant install when node_modules already present
    if (
        not force_install
        and args
        and args[0] in {"install", "i", "ci", "add"}
        and (cwd / "node_modules").is_dir()
    ):
        return {
            "ok": False,
            "error": (
                f"{cwd} 已有 node_modules；测前端/验证请直接 "
                "npm_exec args=['run','build'] 或 ['run','test']，"
                "不要先 install。确需重装时传 force_install=true"
            ),
            "cwd": str(cwd),
            "hint": "working_dir 示例: workspace/<id>/frontend",
        }

    pm_label, pm_bin = _resolve_pkg_bin(cwd)
    command = [pm_bin, *args]

    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "command": command,
            "cwd": str(cwd),
            "package_manager": pm_label,
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
        return {
            "ok": False,
            "error": f"{pm_label} command timed out after {timeout_sec}s",
            "command": command,
            "package_manager": pm_label,
        }
    except OSError as exc:
        return {
            "ok": False,
            "error": f"{pm_label} not available: {exc}",
            "command": command,
            "package_manager": pm_label,
        }

    stdout, stdout_trunc = _truncate(completed.stdout or "")
    stderr, stderr_trunc = _truncate(completed.stderr or "")
    result: dict[str, Any] = {
        "ok": True,
        "cwd": str(cwd),
        "command": command,
        "package_manager": pm_label,
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

    run_tool_main(npm_exec)


def _demo() -> None:
    core = _agent_root() / "agent-core"
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))

    from paths import AgentPaths
    from tools.builtin.run_evolved import run
    from tools.registry import ToolRegistry

    paths = AgentPaths.discover()
    registry = ToolRegistry.load(paths)
    tool = registry.get_evolved("npm_exec")
    assert tool is not None and tool.status in {"active", "draft"}
    print("[PASS] registry loads npm_exec")

    dry = run(
        {
            "tool_name": "npm_exec",
            "arguments": {"args": ["--version"], "dry_run": True},
            "dry_run": True,
        },
        registry=registry,
    )
    assert dry.ok and dry.data.get("dry_run") is True
    print("[PASS] dry_run")

    if shutil.which("npm"):
        live = run(
            {
                "tool_name": "npm_exec",
                "arguments": {"args": ["--version"]},
                "dry_run": False,
            },
            registry=registry,
        )
        assert live.ok and live.data.get("exit_code") == 0
        print(f"[PASS] npm version: {live.data.get('stdout', '').strip()}")
    else:
        print("[SKIP] npm not found; live test skipped")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "demo":
        _demo()
    else:
        main()
