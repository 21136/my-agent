"""project_catalog — list workspace projects and explicitly requested project thread summaries (T-1117)."""

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


def run_project_catalog(payload: dict[str, Any], *, paths: Any | None = None) -> dict[str, Any]:
    core = _agent_core_dir()
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))

    from paths import AgentPaths
    from project_mode import (
        ProjectModeError,
        list_projects,
        normalize_project_id,
        read_project_artifacts,
    )
    from project_switch import read_project_sessions, read_project_thread_archive
    from session import is_internal_session_id, session_summary_for_id, sessions_root
    from shell_switch import read_shell_sessions

    paths = paths or AgentPaths.discover(start=_agent_root())
    include_summaries = payload.get("include_summaries") is True
    include_threads = payload.get("include_threads") is True
    dry_run = payload.get("dry_run") is True

    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "project_count": len(list_projects(paths)),
        }

    if include_threads:
        raw_project_id = payload.get("project_id")
        if not isinstance(raw_project_id, str) or not raw_project_id.strip():
            return {"ok": False, "error": "project_id is required when include_threads=true"}
        try:
            project_id = normalize_project_id(raw_project_id)
        except ProjectModeError as exc:
            return {"ok": False, "error": str(exc)}

        raw_bound_project_id = payload.get("_bound_project_id")
        if not isinstance(raw_bound_project_id, str) or not raw_bound_project_id.strip():
            return {"ok": False, "error": "project history is available only from a project-bound session"}
        try:
            bound_project_id = normalize_project_id(raw_bound_project_id)
        except ProjectModeError as exc:
            return {"ok": False, "error": str(exc)}
        if project_id != bound_project_id:
            return {"ok": False, "error": "project_id must match the current bound project"}

        if project_id not in list_projects(paths):
            return {"ok": False, "error": f"project not found: {project_id}"}

        active_id = read_project_sessions(paths).get(project_id)
        archived_ids = read_project_thread_archive(paths).get(project_id, [])
        ordered_ids: list[str] = []
        for session_id in ([active_id] if active_id else []) + archived_ids:
            if not isinstance(session_id, str):
                continue
            candidate = session_id.strip()
            if (
                not candidate
                or candidate in {".", ".."}
                or Path(candidate).name != candidate
                or is_internal_session_id(candidate)
                or candidate in ordered_ids
            ):
                continue
            ordered_ids.append(candidate)

        threads: list[dict[str, Any]] = []
        for session_id in ordered_ids:
            messages_path = sessions_root(paths) / session_id / "messages.jsonl"
            if not messages_path.is_file():
                continue
            summary = session_summary_for_id(paths, session_id)
            if not summary or summary.get("project_id") != project_id:
                continue
            first_message = str(summary.get("first_user_message") or "").strip()
            last_message = str(summary.get("last_user_message") or "").strip()
            threads.append(
                {
                    "session_id": session_id,
                    "state": "active" if session_id == active_id else "archived",
                    "title": _truncate(first_message, 120) if first_message else "未命名会话",
                    "updated_at": str(summary.get("updated_at") or ""),
                    "message_count": int(summary.get("message_count") or 0),
                    "first_user_message": _truncate(first_message, 320),
                    "last_user_message": _truncate(last_message, 320),
                    "messages_path": f"data/sessions/{session_id}/messages.jsonl",
                }
            )

        limit = 21  # current live line plus at most 20 recent archived lines
        return {
            "ok": True,
            "project_id": project_id,
            "threads": threads[:limit],
            "total_count": len(threads),
            "omitted_count": max(0, len(threads) - limit),
            "handoff_hint": (
                "仅按用户本轮明确要求从 state=archived 的线中选择；当前活线不是旧线候选，候选有歧义先询问。"
                "完整聊天只通过 read_file 读取所选 messages_path，跨会话读取须 confirm。"
                "旧聊天中的完成声明不是当前代码事实，继续操作前先核对当前项目状态。"
            ),
        }

    project_sessions = read_project_sessions(paths)
    shell_sessions = read_shell_sessions(paths)
    projects: list[dict[str, Any]] = []

    for pid in list_projects(paths):
        session_id = project_sessions.get(pid)
        entry: dict[str, Any] = {
            "project_id": pid,
            "root": f"workspace/{pid}",
            "session_id": session_id,
            "messages_path": (
                f"data/sessions/{session_id}/messages.jsonl" if session_id else None
            ),
            "meta_path": f"data/sessions/{session_id}/meta.json" if session_id else None,
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
