"""run_tests — run bundled acceptance demos (agent-core + evolve tools)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

_DEFAULT_TIMEOUT = 180
_MAX_OUTPUT_CHARS = 8000
_VALID_SUITES = frozenset({"quick", "core", "governance", "evolve", "all"})


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


def _truncate(text: str, limit: int = _MAX_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n…(truncated)"


def _job(path: str, *extra_args: str, cwd: str = "agent-core") -> dict[str, Any]:
    return {"path": path, "extra_args": list(extra_args), "cwd": cwd}


def _suite_jobs(suite: str) -> list[dict[str, Any]]:
    quick = [
        _job("agent-core/paths.py"),
        _job("agent-core/tools/schema.py"),
        _job("agent-core/tests/test_write_evolve_pipeline.py"),
        _job("agent-core/main.py", "--demo"),
        _job("agent-core/agent.py"),
    ]
    core = quick + [
        _job("agent-core/loader.py"),
        _job("agent-core/router.py"),
        _job("agent-core/session.py"),
        _job("agent-core/context.py"),
        _job("agent-core/boundaries.py"),
        _job("agent-core/evolve.py"),
        _job("agent-core/cli_tools.py"),
        _job("agent-core/llm_client.py"),
        _job("agent-core/tools/registry.py"),
        _job("agent-core/tools/executor.py"),
        _job("agent-core/tools/logging.py"),
        _job("agent-core/tools/builtin/run_evolved.py"),
    ]
    governance = [
        _job("agent-core/governance/review.py"),
        _job("agent-core/governance/suspect.py"),
        _job("agent-core/governance/feedback.py"),
        _job("agent-core/governance/audit.py"),
        _job("agent-core/governance/git_hints.py"),
        _job("agent-core/governance/entity_usage.py"),
    ]
    evolve: list[dict[str, Any]] = []
    tools_root = _agent_root() / "evolve" / "tools"
    if tools_root.is_dir():
        for main_py in sorted(tools_root.glob("**/main.py")):
            rel = main_py.relative_to(_agent_root()).as_posix()
            evolve.append(_job(rel, "demo", cwd="."))

    if suite == "quick":
        return quick
    if suite == "core":
        return core
    if suite == "governance":
        return governance
    if suite == "evolve":
        return evolve
    if suite == "all":
        seen: set[tuple[str, tuple[str, ...], str]] = set()
        merged: list[dict[str, Any]] = []
        for item in core + governance + evolve:
            key = (item["path"], tuple(item.get("extra_args", [])), item.get("cwd", "agent-core"))
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)
        return merged
    raise ValueError(f"unknown suite: {suite}")


def _resolve_job(paths, job: dict[str, Any]) -> tuple[Path, list[str]]:
    path_arg = job.get("path")
    if not isinstance(path_arg, str) or not path_arg.strip():
        raise ValueError("job.path is required")

    extra_args = job.get("extra_args", [])
    if extra_args is None:
        extra_args = []
    if not isinstance(extra_args, list) or not all(isinstance(arg, str) for arg in extra_args):
        raise ValueError("job.extra_args must be an array of strings")

    cwd_arg = job.get("cwd", "agent-core")
    if not isinstance(cwd_arg, str) or not cwd_arg.strip():
        raise ValueError("job.cwd must be a non-empty string")

    AgentPaths, PathOutOfBoundsError = _load_paths()
    try:
        script = paths.resolve_under_agent(path_arg, must_exist=True)
    except PathOutOfBoundsError as exc:
        raise ValueError(str(exc)) from exc
    except (TypeError, ValueError, FileNotFoundError) as exc:
        raise ValueError(str(exc)) from exc

    if script.suffix.lower() != ".py":
        raise ValueError(f"only .py scripts allowed: {path_arg}")

    if cwd_arg == ".":
        cwd = paths.agent_root
    else:
        cwd = paths.resolve_under_agent(cwd_arg, must_exist=True)
        if not cwd.is_dir():
            raise ValueError(f"job.cwd is not a directory: {cwd_arg}")

    command = [sys.executable, str(script), *extra_args]
    return cwd, command


def run_tests(payload: dict[str, Any]) -> dict[str, Any]:
    AgentPaths, _ = _load_paths()
    paths = AgentPaths.discover(start=_agent_root())

    suite = payload.get("suite", "quick")
    if not isinstance(suite, str):
        return {"ok": False, "error": "suite must be a string"}
    suite = suite.strip().lower()
    if suite not in _VALID_SUITES:
        return {"ok": False, "error": f"suite must be one of {sorted(_VALID_SUITES)}"}

    jobs = payload.get("jobs")
    if jobs is None:
        try:
            jobs = _suite_jobs(suite)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
    elif not isinstance(jobs, list):
        return {"ok": False, "error": "jobs must be an array"}
    elif len(jobs) == 0:
        return {"ok": False, "error": "jobs must not be empty"}

    stop_on_fail = bool(payload.get("stop_on_fail", False))
    timeout_sec = int(payload.get("timeout_sec", _DEFAULT_TIMEOUT))
    if timeout_sec < 1:
        timeout_sec = 1
    dry_run = bool(payload.get("dry_run", False))

    planned: list[dict[str, Any]] = []
    for index, job in enumerate(jobs):
        if not isinstance(job, dict):
            return {"ok": False, "error": f"jobs[{index}] must be an object"}
        try:
            cwd, command = _resolve_job(paths, job)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        planned.append(
            {
                "path": job.get("path"),
                "cwd": paths.to_agent_relative(cwd),
                "command": command,
            }
        )

    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "suite": suite,
            "total": len(planned),
            "planned": planned,
        }

    results: list[dict[str, Any]] = []
    passed = 0
    failed = 0

    for item in planned:
        cwd = paths.agent_root / item["cwd"] if item["cwd"] != "." else paths.agent_root
        command = item["command"]
        entry: dict[str, Any] = {
            "path": item["path"],
            "command": command,
            "exit_code": None,
            "ok": False,
        }
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
            )
            entry["exit_code"] = completed.returncode
            entry["stdout"] = _truncate(completed.stdout or "")
            entry["stderr"] = _truncate(completed.stderr or "")
            entry["ok"] = completed.returncode == 0
        except subprocess.TimeoutExpired:
            entry["error"] = f"timed out after {timeout_sec}s"
        except OSError as exc:
            entry["error"] = str(exc)

        if entry.get("ok"):
            passed += 1
        else:
            failed += 1
        results.append(entry)

        if stop_on_fail and not entry.get("ok"):
            break

    return {
        "ok": failed == 0,
        "suite": suite,
        "total": len(results),
        "passed": passed,
        "failed": failed,
        "results": results,
    }


def main() -> None:
    core = _agent_core_dir()
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))
    from evolve_tool_io import run_tool_main
    run_tool_main(run_tests)


def _demo() -> None:
    core = _agent_core_dir()
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))

    from tools.builtin.run_evolved import run
    from tools.registry import ToolRegistry

    registry = ToolRegistry.load()
    tool = registry.get_evolved("run_tests")
    assert tool is not None and tool.scope == "coding"
    print("[PASS] registry loads run_tests (coding, active)")

    dry = run(
        {
            "tool_name": "run_tests",
            "arguments": {"suite": "quick", "dry_run": True},
            "dry_run": True,
        },
        registry=registry,
    )
    assert dry.ok and dry.data.get("dry_run") is True
    assert dry.data.get("total", 0) >= 3
    print("[PASS] dry_run lists quick suite jobs")

    live = run(
        {
            "tool_name": "run_tests",
            "arguments": {"suite": "quick", "stop_on_fail": True},
            "dry_run": False,
        },
        registry=registry,
    )
    assert live.ok, live.error
    assert live.data.get("failed", 1) == 0
    print("[PASS] live quick suite all passed")

    single = run(
        {
            "tool_name": "run_tests",
            "arguments": {
                "jobs": [{"path": "agent-core/paths.py", "extra_args": [], "cwd": "agent-core"}],
            },
            "dry_run": False,
        },
        registry=registry,
    )
    assert single.ok and single.data.get("passed") == 1
    print("[PASS] custom jobs override suite")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "demo":
        _demo()
    else:
        main()
