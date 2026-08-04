"""Project session index and switch flow (PROJECT-MODE §4.4 P7)."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

_AGENT_CORE = Path(__file__).resolve().parent
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from paths import AgentPaths, read_agent_state_payload, write_agent_state_payload
from project_mode import ProjectModeError, normalize_project_id, project_dir
from session import Session, build_seed_message, create_new, sessions_root, write_last_conversation_id

PROJECT_SESSIONS_KEY = "project_sessions"
PROJECT_THREAD_ARCHIVE_KEY = "project_thread_archive"
SwitchAction = Literal["noop", "bind_current", "load_session", "new_session"]


def _inject_project_seed(fresh: Session, previous: Session, project_id: str) -> None:
    """Write a seed message when project switch creates a new session."""
    seed = build_seed_message(
        previous_session_id=previous.conversation_id,
        previous_goal=previous.goal.strip(),
        reason=f"切换到项目 {project_id}",
        hint=f"当前项目 workspace/{project_id}，TASKS.md 包含任务列表。",
    )
    fresh.append_message(seed, persist=False)
    fresh.save()


@dataclass(frozen=True, slots=True)
class ProjectSwitchPlan:
    action: SwitchAction
    project_id: str
    session_id: str | None = None
    message: str = ""


def _read_state_payload(paths: AgentPaths) -> dict[str, Any]:
    return read_agent_state_payload(paths)


def _write_state_payload(paths: AgentPaths, payload: dict[str, Any]) -> None:
    write_agent_state_payload(paths, payload)


def read_project_sessions(paths: AgentPaths) -> dict[str, str]:
    raw = _read_state_payload(paths).get(PROJECT_SESSIONS_KEY, {})
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for key, value in raw.items():
        if isinstance(key, str) and isinstance(value, str) and key.strip() and value.strip():
            out[key.strip()] = value.strip()
    return out


def read_project_thread_archive(paths: AgentPaths) -> dict[str, list[str]]:
    raw = _read_state_payload(paths).get(PROJECT_THREAD_ARCHIVE_KEY, {})
    if not isinstance(raw, dict):
        return {}
    out: dict[str, list[str]] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not key.strip():
            continue
        if not isinstance(value, list):
            continue
        ids = [item.strip() for item in value if isinstance(item, str) and item.strip()]
        if ids:
            out[key.strip()] = ids
    return out


def archive_project_thread(
    paths: AgentPaths,
    project_id: str,
    conversation_id: str,
) -> None:
    pid = normalize_project_id(project_id)
    cid = conversation_id.strip()
    if not cid:
        return
    payload = _read_state_payload(paths)
    archive = read_project_thread_archive(paths)
    entries = list(archive.get(pid, []))
    if cid in entries:
        entries.remove(cid)
    entries.insert(0, cid)
    archive[pid] = entries
    payload[PROJECT_THREAD_ARCHIVE_KEY] = archive
    _write_state_payload(paths, payload)


def record_project_session(paths: AgentPaths, project_id: str, conversation_id: str) -> None:
    pid = normalize_project_id(project_id)
    cid = conversation_id.strip()
    if not cid:
        return
    payload = _read_state_payload(paths)
    mapping = read_project_sessions(paths)
    mapping[pid] = cid
    payload[PROJECT_SESSIONS_KEY] = mapping
    _write_state_payload(paths, payload)


def lookup_project_session(paths: AgentPaths, project_id: str) -> str | None:
    pid = normalize_project_id(project_id)
    return read_project_sessions(paths).get(pid)


def session_exists(paths: AgentPaths, conversation_id: str) -> bool:
    cid = conversation_id.strip()
    if not cid:
        return False
    return (sessions_root(paths) / cid).is_dir()


def _resolve_open_plan_status(session: Session, project_id: str) -> str:
    if session.meta.project_id == project_id and session.meta.project_plan_status:
        return session.meta.project_plan_status
    return "draft"


def plan_project_open(paths: AgentPaths, session: Session, project_id: str) -> ProjectSwitchPlan:
    pid = normalize_project_id(project_id)
    dest = project_dir(paths, pid)
    if not (dest / "TASKS.md").is_file():
        raise ProjectModeError(f"project missing TASKS.md: workspace/{pid}")

    current_pid = (session.meta.project_id or "").strip()
    if current_pid == pid:
        return ProjectSwitchPlan(
            action="noop",
            project_id=pid,
            message=f"已是当前项目 workspace/{pid}",
        )
    if current_pid:
        raise ProjectModeError(
            f"当前会话已绑定项目 {current_pid}；请使用「项目 切换 {pid}」"
        )

    mapped = lookup_project_session(paths, pid)
    if (
        mapped
        and mapped != session.conversation_id
        and session_exists(paths, mapped)
    ):
        raise ProjectModeError(
            f"项目 {pid} 已有专用会话 {mapped}；请用「项目 切换 {pid}」续接"
        )

    return ProjectSwitchPlan(
        action="bind_current",
        project_id=pid,
        message=f"已在本会话打开 workspace/{pid}",
    )


def _bind(session: Session, project_id: str, *, plan_status: str) -> None:
    from project_cli import bind_project_session

    bind_project_session(session, project_id, plan_status=plan_status)


def plan_project_switch(paths: AgentPaths, session: Session, project_id: str) -> ProjectSwitchPlan:
    pid = normalize_project_id(project_id)
    dest = project_dir(paths, pid)
    if not (dest / "TASKS.md").is_file():
        raise ProjectModeError(f"project missing TASKS.md: workspace/{pid}")

    current_pid = (session.meta.project_id or "").strip()
    if current_pid == pid and session.meta.active_shell == "project":
        return ProjectSwitchPlan(
            action="noop",
            project_id=pid,
            message=f"已是当前项目 workspace/{pid}",
        )

    mapped = lookup_project_session(paths, pid)
    if mapped and session_exists(paths, mapped):
        if mapped == session.conversation_id:
            _bind(session, pid, plan_status=_resolve_open_plan_status(session, pid))
            return ProjectSwitchPlan(
                action="bind_current",
                project_id=pid,
                session_id=mapped,
                message=f"已绑定项目 workspace/{pid}",
            )
        return ProjectSwitchPlan(
            action="load_session",
            project_id=pid,
            session_id=mapped,
            message=f"续接项目 {pid} 的专用会话 {mapped}",
        )

    return ProjectSwitchPlan(
        action="new_session",
        project_id=pid,
        message=f"为项目 {pid} 新建专用会话",
    )


def execute_project_switch(
    paths: AgentPaths,
    session: Session,
    plan: ProjectSwitchPlan,
) -> tuple[Session, str]:
    if plan.action == "noop":
        try:
            from project_env import ensure_project_env

            ensure_project_env(paths, plan.project_id)
        except Exception:
            pass
        return session, plan.message

    if plan.action == "bind_current":
        _bind(session, plan.project_id, plan_status=_resolve_open_plan_status(session, plan.project_id))
        session.save()
        record_project_session(paths, plan.project_id, session.conversation_id)
        write_last_conversation_id(paths, session.conversation_id)
        try:
            from project_env import ensure_project_env

            ensure_project_env(paths, plan.project_id)
        except Exception:
            pass
        return session, plan.message

    if plan.action == "load_session":
        assert plan.session_id
        loaded = Session.load(paths, plan.session_id)
        if (loaded.meta.project_id or "").strip() != plan.project_id:
            _bind(loaded, plan.project_id, plan_status=_resolve_open_plan_status(loaded, plan.project_id))
        elif loaded.meta.active_shell != "project":
            loaded.meta.active_shell = "project"
        loaded.save()
        record_project_session(paths, plan.project_id, loaded.conversation_id)
        write_last_conversation_id(paths, loaded.conversation_id)
        try:
            from project_env import ensure_project_env

            ensure_project_env(paths, plan.project_id)
        except Exception:
            pass
        return loaded, plan.message

    if plan.action == "new_session":
        fresh = create_new(paths)
        _inject_project_seed(fresh, session, plan.project_id)
        _bind(fresh, plan.project_id, plan_status="draft")
        fresh.save()
        record_project_session(paths, plan.project_id, fresh.conversation_id)
        write_last_conversation_id(paths, fresh.conversation_id)
        try:
            from project_env import ensure_project_env

            ensure_project_env(paths, plan.project_id)
        except Exception:
            pass
        return fresh, plan.message

    raise ProjectModeError(f"unknown switch action: {plan.action}")


def _inherit_project_meta(fresh: Session, previous: Session, project_id: str) -> None:
    from project_cli import bind_project_session

    plan_status = previous.meta.project_plan_status or "draft"
    bind_project_session(fresh, project_id, plan_status=plan_status)
    if previous.meta.project_plan_confirmed_at:
        fresh.meta.project_plan_confirmed_at = previous.meta.project_plan_confirmed_at
    fresh.meta.project_phase_fingerprint = previous.meta.project_phase_fingerprint
    fresh.meta.project_doc_fingerprint = previous.meta.project_doc_fingerprint


def start_new_project_thread(
    paths: AgentPaths,
    session: Session,
    *,
    project_id: str | None = None,
) -> tuple[Session, str]:
    """Same project: archive current live line and start a blank bound session."""
    pid = normalize_project_id(project_id or session.meta.project_id or "")
    if not pid:
        raise ProjectModeError("当前未绑定项目；请先用「项目 打开」或「项目 切换」")

    current_pid = (session.meta.project_id or "").strip()
    if current_pid and current_pid != pid:
        raise ProjectModeError(
            f"当前会话绑定项目 {current_pid}；请先切换到 {pid} 再新开线"
        )

    dest = project_dir(paths, pid)
    if not (dest / "TASKS.md").is_file():
        raise ProjectModeError(f"project missing TASKS.md: workspace/{pid}")

    mapped = lookup_project_session(paths, pid)
    old_cid = session.conversation_id
    if mapped and mapped != old_cid:
        raise ProjectModeError(
            f"当前不是项目 {pid} 的活线（活线为 {mapped}）；请先切回活线再新开线"
        )

    fresh = create_new(paths)
    _inherit_project_meta(fresh, session, pid)
    fresh.save()

    if old_cid:
        archive_project_thread(paths, pid, old_cid)

    record_project_session(paths, pid, fresh.conversation_id)
    write_last_conversation_id(paths, fresh.conversation_id)

    try:
        from shell_switch import record_shell_session

        if session.meta.active_shell == "project":
            record_shell_session(paths, "project", fresh.conversation_id)
    except Exception:
        pass

    try:
        from project_env import ensure_project_env

        ensure_project_env(paths, pid)
    except Exception:
        pass

    return (
        fresh,
        f"已为项目 {pid} 新开线（{fresh.conversation_id}）；"
        f"上一线 {old_cid} 已归档，可回看",
    )


def build_project_threads_payload(
    paths: AgentPaths,
    session: Session,
    *,
    project_id: str | None = None,
) -> dict[str, Any]:
    from session import list_session_summaries

    pid = normalize_project_id(project_id or session.meta.project_id or "")
    archive = read_project_thread_archive(paths).get(pid, [])
    active = lookup_project_session(paths, pid)
    summaries = {
        item["session_id"]: item
        for item in list_session_summaries(paths, limit=200, include_internal=True)
        if isinstance(item.get("session_id"), str)
    }

    def _thread_item(sid: str, *, archived: bool) -> dict[str, Any]:
        summary = summaries.get(sid, {})
        return {
            "session_id": sid,
            "title": summary.get("title") or sid,
            "preview": summary.get("preview") or "",
            "updated_at": summary.get("updated_at") or "",
            "archived": archived,
        }

    threads: list[dict[str, Any]] = []
    if active:
        threads.append(_thread_item(active, archived=False))
    for sid in archive:
        if sid != active:
            threads.append(_thread_item(sid, archived=True))

    return {
        "type": "project.threads",
        "project_id": pid or None,
        "active_session_id": active,
        "archived_session_ids": list(archive),
        "threads": threads,
        "is_active": session.conversation_id == active if active else False,
    }


def switch_to_project(
    paths: AgentPaths,
    session: Session,
    project_id: str,
) -> tuple[Session, str]:
    plan = plan_project_switch(paths, session, project_id)
    return execute_project_switch(paths, session, plan)


def open_project_on_session(
    paths: AgentPaths,
    session: Session,
    project_id: str,
) -> tuple[Session, str]:
    plan = plan_project_open(paths, session, project_id)
    return execute_project_switch(paths, session, plan)


def build_switch_request_payload(
    paths: AgentPaths,
    session: Session,
    project_id: str,
    *,
    request_id: str,
) -> dict[str, Any]:
    plan = plan_project_switch(paths, session, project_id)
    current_pid = (session.meta.project_id or "").strip() or None
    needs_confirm = bool(
        current_pid
        and current_pid != plan.project_id
        and plan.action in {"load_session", "new_session"}
    )
    return {
        "type": "project.switch.request",
        "request_id": request_id,
        "project_id": plan.project_id,
        "current_project_id": current_pid,
        "action": plan.action,
        "target_session_id": plan.session_id,
        "message": plan.message,
        "needs_confirm": needs_confirm,
    }


def _demo() -> None:
    import shutil

    from session import create_new

    paths = AgentPaths.discover()
    demo_a = "switch-demo-a"
    demo_b = "switch-demo-b"
    from project_mode import create_project

    for pid in (demo_a, demo_b):
        dest = project_dir(paths, pid)
        if dest.is_dir():
            shutil.rmtree(dest)
        create_project(paths, pid)

    payload = _read_state_payload(paths)
    mapping = read_project_sessions(paths)
    for pid in (demo_a, demo_b):
        mapping.pop(pid, None)
    payload[PROJECT_SESSIONS_KEY] = mapping
    _write_state_payload(paths, payload)

    session = create_new(paths, conversation_id="project-switch-demo")
    opened, msg = open_project_on_session(paths, session, demo_a)
    assert opened.meta.project_id == demo_a
    print(f"[PASS] open bind_current: {msg}")

    try:
        open_project_on_session(paths, opened, demo_b)
        raise AssertionError("expected cross-open block")
    except ProjectModeError as exc:
        assert "切换" in str(exc)
    print("[PASS] open blocks cross-project on same session")

    switched, switch_msg = switch_to_project(paths, opened, demo_b)
    assert switched.conversation_id != opened.conversation_id
    assert switched.meta.project_id == demo_b
    print(f"[PASS] switch new_session: {switch_msg}")

    mapped = lookup_project_session(paths, demo_b)
    assert mapped == switched.conversation_id
    print("[PASS] project_sessions index")

    again, again_msg = switch_to_project(paths, session, demo_b)
    assert again.conversation_id == switched.conversation_id
    print(f"[PASS] switch resume: {again_msg}")

    shutil.rmtree(project_dir(paths, demo_a), ignore_errors=True)
    shutil.rmtree(project_dir(paths, demo_b), ignore_errors=True)
    shutil.rmtree(paths.data / "sessions" / "project-switch-demo", ignore_errors=True)
    shutil.rmtree(paths.data / "sessions" / switched.conversation_id, ignore_errors=True)
    mapping = read_project_sessions(paths)
    for pid in (demo_a, demo_b):
        mapping.pop(pid, None)
    payload[PROJECT_SESSIONS_KEY] = mapping
    _write_state_payload(paths, payload)
    print("[PASS] T-1113: project_switch demo")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "demo":
        _demo()
