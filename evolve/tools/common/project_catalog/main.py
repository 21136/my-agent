"""project_catalog — list workspace projects, session ids, and peek paths (T-1117)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_SUMMARY_MAX = 800


def _agent_root() -> Path:
    current = Path(__file__).resolve().parent
    for directory in (current, *current.parents):
        evolve_marker = directory / "evolve"
        if (evolve_marker / "_index.core.toml").is_file() or (evolve_marker / "_index.toml").is_file():
            return directory
    raise RuntimeError("could not locate agent root")


def _agent_core_dir() -> Path:
    return _agent_root() / "agent-core"


def _truncate(text: str, limit: int = _SUMMARY_MAX) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "\n…(truncated)"


def run_project_catalog(payload: dict[str, Any]) -> dict[str, Any]:
    core = _agent_core_dir()
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))

    from paths import AgentPaths
    from project_mode import list_projects, project_dir, read_project_artifacts, read_task_stats
    from project_switch import read_project_sessions
    from shell_switch import read_shell_sessions

    paths = AgentPaths.discover(start=_agent_root())
    include_summaries = bool(payload.get("include_summaries", False))
    dry_run = bool(payload.get("dry_run", False))

    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "project_count": len(list_projects(paths)),
        }

    project_sessions = read_project_sessions(paths)
    shell_sessions = read_shell_sessions(paths)
    projects: list[dict[str, Any]] = []

    for pid in list_projects(paths):
        root = project_dir(paths, pid)
        stats = read_task_stats(root / "TASKS.md")
        session_id = project_sessions.get(pid)
        entry: dict[str, Any] = {
            "project_id": pid,
            "root": f"workspace/{pid}",
            "session_id": session_id,
            "messages_path": (
                f"data/sessions/{session_id}/messages.jsonl" if session_id else None
            ),
            "meta_path": f"data/sessions/{session_id}/meta.json" if session_id else None,
            "tasks_done": stats.done,
            "tasks_total": stats.total,
            "tasks_open": stats.open_count,
        }
        if include_summaries:
            artifacts = read_project_artifacts(paths, pid)
            summary = artifacts.get("PROJECT.md", "")
            if summary:
                entry["project_summary"] = _truncate(summary)
        projects.append(entry)

    return {
        "ok": True,
        "state_path": "data/state.json",
        "shell_sessions": shell_sessions,
        "project_sessions": project_sessions,
        "projects": projects,
        "peek_hint": (
            "跨壳查对话：read_file data/sessions/<session_id>/messages.jsonl（非当前会话须 confirm）；"
            "查代码/进度：read_file workspace/<id>/…"
        ),
    }


def main() -> None:
    core = _agent_core_dir()
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))
    from evolve_tool_io import run_tool_main

    run_tool_main(run_project_catalog)


def _demo() -> None:
    core = _agent_core_dir()
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))

    from tools.builtin.run_evolved import run
    from tools.registry import ToolRegistry

    registry = ToolRegistry.load()
    tool = registry.get_evolved("project_catalog")
    assert tool is not None and tool.status == "active"
    print("[PASS] registry loads project_catalog")

    dry = run(
        {"tool_name": "project_catalog", "arguments": {"dry_run": True}},
        registry=registry,
    )
    assert dry.ok and dry.data.get("dry_run") is True
    print("[PASS] dry_run")

    live = run(
        {"tool_name": "project_catalog", "arguments": {"include_summaries": False}},
        registry=registry,
    )
    assert live.ok and isinstance(live.data.get("projects"), list)
    assert "peek_hint" in live.data
    print("[PASS] live catalog")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "demo":
        _demo()
    else:
        main()
