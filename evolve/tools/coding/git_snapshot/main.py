"""git_snapshot — read-only git status and diff --stat (P3 coding)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

_MAX_STAT_CHARS = 32000


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
    from paths import AgentPaths

    return AgentPaths


def _run_git(args: list[str], *, cwd: Path) -> tuple[int, str, str]:
    completed = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return completed.returncode, completed.stdout or "", completed.stderr or ""


def _truncate(text: str) -> tuple[str, bool]:
    if len(text) <= _MAX_STAT_CHARS:
        return text, False
    return text[:_MAX_STAT_CHARS] + "\n…(truncated)", True


def run_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    paths = _load_paths().discover(start=_agent_root())
    root = paths.agent_root

    if not (root / ".git").exists():
        return {"ok": False, "error": "not a git repository"}

    path_specs = payload.get("paths", [])
    if path_specs is None:
        path_specs = []
    if not isinstance(path_specs, list) or not all(isinstance(p, str) for p in path_specs):
        return {"ok": False, "error": "paths must be an array of strings"}

    include_staged = bool(payload.get("include_staged", True))
    dry_run = bool(payload.get("dry_run", False))

    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "repo_root": paths.to_agent_relative(root),
            "paths": path_specs,
            "include_staged": include_staged,
        }

    branch_code, branch_out, branch_err = _run_git(
        ["rev-parse", "--abbrev-ref", "HEAD"], cwd=root
    )
    if branch_code != 0:
        return {"ok": False, "error": branch_err.strip() or "git rev-parse failed"}

    status_code, status_out, status_err = _run_git(
        ["status", "--porcelain=v1"], cwd=root
    )
    if status_code != 0:
        return {"ok": False, "error": status_err.strip() or "git status failed"}

    diff_args = ["diff", "--stat"]
    if path_specs:
        diff_args.append("--")
        diff_args.extend(path_specs)
    diff_code, diff_out, diff_err = _run_git(diff_args, cwd=root)
    if diff_code != 0:
        return {"ok": False, "error": diff_err.strip() or "git diff failed"}

    staged_out = ""
    staged_trunc = False
    if include_staged:
        staged_args = ["diff", "--cached", "--stat"]
        if path_specs:
            staged_args.append("--")
            staged_args.extend(path_specs)
        staged_code, staged_out, staged_err = _run_git(staged_args, cwd=root)
        if staged_code != 0:
            return {"ok": False, "error": staged_err.strip() or "git diff --cached failed"}
        staged_out, staged_trunc = _truncate(staged_out.strip())

    diff_out, diff_trunc = _truncate(diff_out.strip())
    status_lines = [line for line in status_out.splitlines() if line.strip()]

    result: dict[str, Any] = {
        "ok": True,
        "branch": branch_out.strip(),
        "status_lines": status_lines,
        "diff_stat": diff_out,
        "staged_diff_stat": staged_out,
    }
    if diff_trunc or staged_trunc:
        result["truncated"] = True
    return result


def main() -> None:
    core = _agent_core_dir()
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))
    from evolve_tool_io import run_tool_main
    run_tool_main(run_snapshot)


def _demo() -> None:
    core = _agent_core_dir()
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))

    from tools.builtin.run_evolved import run
    from tools.registry import ToolRegistry

    registry = ToolRegistry.load()
    tool = registry.get_evolved("git_snapshot")
    assert tool is not None and tool.scope == "coding"
    print("[PASS] registry loads git_snapshot (coding, active)")

    dry = run(
        {"tool_name": "git_snapshot", "arguments": {}, "dry_run": True},
        registry=registry,
    )
    assert dry.ok and dry.data.get("dry_run") is True
    print("[PASS] dry_run reports intent")

    live = run(
        {"tool_name": "git_snapshot", "arguments": {"paths": ["docs/MAP.md"]}},
        registry=registry,
    )
    assert live.ok and isinstance(live.data.get("branch"), str)
    assert isinstance(live.data.get("status_lines"), list)
    print("[PASS] live git snapshot")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "demo":
        _demo()
    else:
        main()
