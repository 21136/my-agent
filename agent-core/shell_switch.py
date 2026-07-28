"""Per-shell session routing (DESKTOP shell switch · T-1116)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Literal

_AGENT_CORE = Path(__file__).resolve().parent
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from paths import AgentPaths, read_agent_state_payload, write_agent_state_payload
from project_mode import ProjectModeError, normalize_project_id, project_dir
from project_switch import (
    lookup_project_session,
    record_project_session,
    session_exists,
    switch_to_project,
)
from session import Session, ShellId, create_new, write_last_conversation_id

SHELL_SESSIONS_KEY = "shell_sessions"
LAST_PROJECT_ID_KEY = "last_project_id"
_CHAT_SHELLS = frozenset({"grow", "daily"})


class ShellSwitchError(Exception):
    """Invalid shell switch."""


def _read_state_payload(paths: AgentPaths) -> dict[str, Any]:
    return read_agent_state_payload(paths)


def _write_state_payload(paths: AgentPaths, payload: dict[str, Any]) -> None:
    write_agent_state_payload(paths, payload)


def read_shell_sessions(paths: AgentPaths) -> dict[str, str]:
    raw = _read_state_payload(paths).get(SHELL_SESSIONS_KEY, {})
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for key, value in raw.items():
        if isinstance(key, str) and isinstance(value, str) and key.strip() and value.strip():
            if key.strip() in _CHAT_SHELLS:
                out[key.strip()] = value.strip()
    return out


def record_shell_session(paths: AgentPaths, shell: str, conversation_id: str) -> None:
    if shell not in _CHAT_SHELLS:
        return
    cid = conversation_id.strip()
    if not cid:
        return
    payload = _read_state_payload(paths)
    mapping = read_shell_sessions(paths)
    mapping[shell] = cid
    payload[SHELL_SESSIONS_KEY] = mapping
    _write_state_payload(paths, payload)


def lookup_shell_session(paths: AgentPaths, shell: str) -> str | None:
    if shell not in _CHAT_SHELLS:
        return None
    return read_shell_sessions(paths).get(shell)


def read_last_project_id(paths: AgentPaths) -> str | None:
    raw = _read_state_payload(paths).get(LAST_PROJECT_ID_KEY)
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        return normalize_project_id(raw)
    except ProjectModeError:
        return None


def write_last_project_id(paths: AgentPaths, project_id: str) -> None:
    pid = normalize_project_id(project_id)
    payload = _read_state_payload(paths)
    payload[LAST_PROJECT_ID_KEY] = pid
    _write_state_payload(paths, payload)


def _clear_project_binding(meta: object) -> None:
    meta.project_root = ""
    meta.project_id = ""
    meta.project_plan_status = ""
    meta.project_plan_confirmed_at = ""
    meta.project_phase_fingerprint = ""
    meta.project_doc_fingerprint = ""


def lookup_shell_owner(paths: AgentPaths, conversation_id: str) -> Literal["grow", "daily"] | None:
    """Return the chat shell that already owns *conversation_id* in shell_sessions, if any."""
    cid = (conversation_id or "").strip()
    if not cid:
        return None
    mapping = read_shell_sessions(paths)
    owners = [shell for shell in ("grow", "daily") if mapping.get(shell) == cid]
    if not owners:
        return None
    if len(owners) == 1:
        return owners[0]  # type: ignore[return-value]
    # Polluted map (same cid under both): prefer grow (workbench home).
    return "grow"


def park_session(paths: AgentPaths, session: Session) -> None:
    """Persist current session pointer for its shell line before switching away.

    BUG-020 / STD-001: park by *owned* shell_sessions line (reverse lookup), not by
    a possibly activity-router-flipped ``meta.active_shell``.
    """
    session.save()
    shell = session.meta.active_shell
    if shell == "project" and session.meta.project_id:
        record_project_session(paths, session.meta.project_id, session.conversation_id)
        write_last_project_id(paths, session.meta.project_id)
        return

    owner = lookup_shell_owner(paths, session.conversation_id)
    park_shell: str | None = owner
    if park_shell is None and shell in _CHAT_SHELLS:
        park_shell = shell
    if park_shell in _CHAT_SHELLS:
        record_shell_session(paths, park_shell, session.conversation_id)
        if session.meta.active_shell != park_shell:
            session.meta.active_shell = park_shell  # type: ignore[assignment]
            session.save()


def _load_or_create_chat_shell_session(paths: AgentPaths, shell: Literal["grow", "daily"]) -> Session:
    mapped = lookup_shell_session(paths, shell)
    if mapped and session_exists(paths, mapped):
        loaded = Session.load(paths, mapped)
    else:
        loaded = create_new(paths)
    loaded.meta.active_shell = shell
    _clear_project_binding(loaded.meta)
    loaded.save()
    record_shell_session(paths, shell, loaded.conversation_id)
    return loaded


def switch_shell(
    paths: AgentPaths,
    session: Session,
    to_shell: ShellId,
    *,
    project_id: str | None = None,
) -> tuple[Session, bool]:
    """Switch active shell line; return (session, session_replaced)."""
    from_shell = session.meta.active_shell
    # BUG-020: meta.active_shell may have been flipped by activity_router; prefer owned line.
    owner = lookup_shell_owner(paths, session.conversation_id)
    effective_from: ShellId = owner if owner is not None else from_shell

    if to_shell == effective_from and to_shell != "project":
        if session.meta.active_shell != to_shell and to_shell in _CHAT_SHELLS:
            session.meta.active_shell = to_shell
            session.save()
        return session, False

    if to_shell == "govern":
        session.meta.active_shell = "govern"
        session.save()
        return session, False

    park_session(paths, session)

    if to_shell == "project":
        pid = (project_id or "").strip() or session.meta.project_id or read_last_project_id(paths) or ""
        if not pid:
            raise ShellSwitchError("请先选择项目（侧栏项目列表或「项目 切换 <id>」）")
        if not (project_dir(paths, pid) / "TASKS.md").is_file():
            raise ShellSwitchError(f"项目不存在或缺少 TASKS.md：workspace/{pid}")
        loaded, _message = switch_to_project(paths, session, pid)
        loaded.meta.active_shell = "project"
        loaded.save()
        write_last_project_id(paths, pid)
        write_last_conversation_id(paths, loaded.conversation_id)
        return loaded, loaded.conversation_id != session.conversation_id

    if to_shell not in _CHAT_SHELLS:
        raise ShellSwitchError(f"unsupported shell: {to_shell}")

    loaded = _load_or_create_chat_shell_session(paths, to_shell)
    write_last_conversation_id(paths, loaded.conversation_id)
    return loaded, loaded.conversation_id != session.conversation_id


def cross_session_read_target(paths: AgentPaths, current_conversation_id: str, path_arg: str) -> str | None:
    """If *path_arg* reads another session directory, return that session id."""
    from tools.builtin.read_file import resolve_read_path

    if not isinstance(path_arg, str) or not path_arg.strip():
        return None
    try:
        resolved = resolve_read_path(paths, path_arg.strip())
    except (FileNotFoundError, TypeError, ValueError):
        return None
    try:
        rel = paths.to_agent_relative(resolved).replace("\\", "/")
    except Exception:
        return None
    if not rel.startswith("data/sessions/"):
        return None
    parts = rel.split("/")
    if len(parts) < 3:
        return None
    target_id = parts[2].strip()
    current = current_conversation_id.strip()
    if not target_id or target_id == current:
        return None
    return target_id


def _demo() -> None:
    import shutil

    paths = AgentPaths.discover()
    grow_a = create_new(paths, conversation_id="_shell_demo_grow_a")
    grow_a.meta.active_shell = "grow"
    grow_a.save()
    record_shell_session(paths, "grow", grow_a.conversation_id)

    daily_a = create_new(paths, conversation_id="_shell_demo_daily_a")
    daily_a.meta.active_shell = "daily"
    daily_a.save()
    record_shell_session(paths, "daily", daily_a.conversation_id)

    from project_mode import create_project

    pid = "shell-demo-proj"
    dest = project_dir(paths, pid)
    if dest.is_dir():
        shutil.rmtree(dest)
    create_project(paths, pid)

    # simulate project-bound session — use mapped or create
    mapped = lookup_project_session(paths, pid)
    if mapped and session_exists(paths, mapped):
        proj = Session.load(paths, mapped)
    else:
        proj = create_new(paths, conversation_id="_shell_demo_proj_sess")
        from project_cli import bind_project_session

        bind_project_session(proj, pid, plan_status="draft")
        proj.save()
        record_project_session(paths, pid, proj.conversation_id)

    proj.meta.active_shell = "project"
    proj.save()

    switched, replaced = switch_shell(paths, proj, "grow")
    assert replaced and switched.conversation_id == grow_a.conversation_id
    assert switched.meta.active_shell == "grow"
    assert not switched.meta.project_id
    print("[PASS] project → grow loads grow session")

    back, replaced2 = switch_shell(paths, switched, "project", project_id=pid)
    assert replaced2 and back.conversation_id == proj.conversation_id
    assert back.meta.project_id == pid
    print("[PASS] grow → project resumes project session")

    daily_loaded, _ = switch_shell(paths, back, "daily")
    assert daily_loaded.conversation_id == daily_a.conversation_id
    print("[PASS] project → daily loads daily session")

    target = cross_session_read_target(
        paths,
        grow_a.conversation_id,
        f"data/sessions/{proj.conversation_id}/messages.jsonl",
    )
    assert target == proj.conversation_id
    assert cross_session_read_target(paths, grow_a.conversation_id, f"data/sessions/{grow_a.conversation_id}/messages.jsonl") is None
    print("[PASS] cross_session_read_target")

    for cid in ("_shell_demo_grow_a", "_shell_demo_daily_a", proj.conversation_id):
        shutil.rmtree(paths.data / "sessions" / cid, ignore_errors=True)
    shutil.rmtree(dest, ignore_errors=True)
    payload = _read_state_payload(paths)
    payload.pop(SHELL_SESSIONS_KEY, None)
    _write_state_payload(paths, payload)
    print("[PASS] T-1116: shell_switch demo")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "demo":
        _demo()
