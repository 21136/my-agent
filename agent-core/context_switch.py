"""Context switch gate — propose / apply (CONTEXT-SWITCH.md Phase 19 M0/M1)."""

from __future__ import annotations

import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

_AGENT_CORE = Path(__file__).resolve().parent
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from paths import AgentPaths
from project_mode import (
    ProjectModeError,
    create_project,
    normalize_project_id,
    project_dir,
)
from project_switch import (
    execute_project_switch,
    lookup_project_session,
    plan_project_switch,
    record_project_session,
    session_exists,
)
from session import Session, build_seed_message, create_new, write_last_conversation_id

ContextAction = Literal["project.create", "project.switch", "session.new"]
SUPPORTED_ACTIONS = frozenset(
    {"project.create", "project.switch", "session.new"}
)


@dataclass(frozen=True, slots=True)
class ContextSwitchProposal:
    action: ContextAction
    target: str
    reason: str = ""
    request_id: str = ""
    project_id: str | None = None


class ContextSwitchError(Exception):
    """Invalid context-switch proposal or apply failure."""


def _normalize_action(action: str) -> ContextAction:
    text = action.strip()
    if text not in SUPPORTED_ACTIONS:
        raise ContextSwitchError(
            f"unsupported action {action!r}; allowed: {', '.join(sorted(SUPPORTED_ACTIONS))}"
        )
    return text  # type: ignore[return-value]


def normalize_proposal(
    *,
    action: str,
    target: str,
    reason: str = "",
    request_id: str | None = None,
    project_id: str | None = None,
) -> ContextSwitchProposal:
    act = _normalize_action(action)
    rid = (request_id or "").strip() or str(uuid.uuid4())
    reason_text = (reason or "").strip()

    if act == "session.new":
        shell = target.strip().casefold() or "current"
        pid = None
        if project_id and str(project_id).strip():
            pid = normalize_project_id(str(project_id))
        return ContextSwitchProposal(
            action=act,
            target=shell,
            reason=reason_text,
            request_id=rid,
            project_id=pid,
        )

    pid_target = normalize_project_id(target)
    return ContextSwitchProposal(
        action=act,
        target=pid_target,
        reason=reason_text,
        request_id=rid,
        project_id=None,
    )


def build_context_switch_request(
    session: Session,
    proposal: ContextSwitchProposal,
) -> dict[str, Any]:
    current_pid = (session.meta.project_id or "").strip()
    side_effects: list[str] = []
    display_target = proposal.target
    if proposal.action == "project.create":
        side_effects.append(f"创建 workspace/{proposal.target}/ 三件套（若不存在）")
        side_effects.append("新建专用会话并切换到 project 壳")
        side_effects.append("聊天区将替换为新会话历史")
        title = f"新建项目 · {proposal.target}"
    elif proposal.action == "project.switch":
        side_effects.append(f"切换到项目 workspace/{proposal.target}")
        side_effects.append("可能加载该项目专用会话（聊天区替换）")
        title = f"切换项目 · {proposal.target}"
    elif proposal.action == "session.new":
        display_target = "当前"
        side_effects.append("新建空白会话")
        side_effects.append("聊天区将清空为新会话（旧会话仍保留在磁盘）")
        title = "新会话"

    return {
        "type": "context.switch.request",
        "request_id": proposal.request_id,
        "action": proposal.action,
        "target": display_target if proposal.action == "session.new" else proposal.target,
        "project_id": proposal.project_id,
        "reason": proposal.reason,
        "title": title,
        "side_effects": side_effects,
        "current": {
            "shell": session.meta.active_shell,
            "project_id": current_pid or None,
            "session_id": session.conversation_id,
        },
    }


def apply_context_switch(
    paths: AgentPaths,
    session: Session,
    proposal: ContextSwitchProposal,
) -> tuple[Session, str]:
    """Apply confirmed proposal. Returns (possibly new) session + message."""
    if proposal.action == "project.create":
        return _apply_project_create(paths, session, proposal.target)
    if proposal.action == "project.switch":
        return _apply_project_switch(paths, session, proposal.target)
    if proposal.action == "session.new":
        return _apply_session_new(paths, session, proposal)
    raise ContextSwitchError(f"unsupported action: {proposal.action}")


def _apply_session_new(
    paths: AgentPaths,
    session: Session,
    proposal: ContextSwitchProposal,
) -> tuple[Session, str]:
    """Blank chat session. Never opens a second session for an existing project (D6).

    If the previous session was project-bound, the project mapping is left alone (parked);
    the new session is unbound ordinary chat.
    """
    fresh = create_new(paths)
    _inject_seed(fresh, session, proposal.reason)
    write_last_conversation_id(paths, fresh.conversation_id)
    prev_pid = (session.meta.project_id or "").strip()
    if prev_pid:
        return (
            fresh,
            f"已新建普通对话（项目 {prev_pid} 会话已挂起，可从侧栏/列表续接）",
        )
    return fresh, "已新建会话（已衔接上文上下文）"


def _inject_seed(fresh: Session, previous: Session, reason: str) -> None:
    """Write a seed message so the new session's agent remembers the previous context."""
    goal = previous.goal.strip()
    hint = ""
    if reason and "工具" in reason:
        hint = "新会话已重新加载 evolved 工具清单，之前创建的工具现已可用。"
    seed = build_seed_message(
        previous_session_id=previous.conversation_id,
        previous_goal=goal,
        reason=reason,
        hint=hint,
    )
    fresh.append_message(seed, persist=False)
    fresh.save()


def _apply_project_create(
    paths: AgentPaths,
    session: Session,
    project_id: str,
) -> tuple[Session, str]:
    pid = normalize_project_id(project_id)
    dest = project_dir(paths, pid)
    created = False
    try:
        create_project(paths, pid)
        created = True
    except ProjectModeError as exc:
        if "already exists" not in str(exc):
            raise ContextSwitchError(str(exc)) from exc
        if not (dest / "TASKS.md").is_file():
            raise ContextSwitchError(
                f"workspace/{pid} 已存在但缺少 TASKS.md；请手工补齐或换 id"
            ) from exc

    current_pid = (session.meta.project_id or "").strip()
    # Already on this project in project shell — noop bind.
    if current_pid == pid and session.meta.active_shell == "project":
        record_project_session(paths, pid, session.conversation_id)
        write_last_conversation_id(paths, session.conversation_id)
        try:
            from project_env import ensure_project_env

            ensure_project_env(paths, pid)
        except Exception:
            pass
        verb = "已打开" if not created else "已创建"
        return session, f"{verb}项目 workspace/{pid}（当前会话）"

    # D6: project already has a dedicated session → resume it (never open a second).
    mapped = lookup_project_session(paths, pid)
    if mapped and session_exists(paths, mapped) and mapped != session.conversation_id:
        loaded = Session.load(paths, mapped)
        if (loaded.meta.project_id or "").strip() != pid:
            from project_cli import bind_project_session

            bind_project_session(loaded, pid, plan_status="draft")
            loaded.save()
        record_project_session(paths, pid, loaded.conversation_id)
        write_last_conversation_id(paths, loaded.conversation_id)
        try:
            from project_env import ensure_project_env

            ensure_project_env(paths, pid)
        except Exception:
            pass
        verb = "已创建并续接" if created else "已续接"
        return loaded, f"{verb}项目 workspace/{pid}（会话 {mapped}）"

    # Unbound session may bind in place; bound to another project → new session for NEW project only.
    if not current_pid:
        from project_cli import bind_project_session

        bind_project_session(session, pid, plan_status="draft")
        session.save()
        record_project_session(paths, pid, session.conversation_id)
        write_last_conversation_id(paths, session.conversation_id)
        try:
            from project_env import ensure_project_env

            ensure_project_env(paths, pid)
        except Exception:
            pass
        verb = "已创建并打开" if created else "已打开"
        return session, f"{verb}项目 workspace/{pid}（计划待确认）"

    fresh = create_new(paths)
    _inject_seed(fresh, session, reason=f"切换到项目 {pid}")
    from project_cli import bind_project_session

    bind_project_session(fresh, pid, plan_status="draft")
    fresh.save()
    record_project_session(paths, pid, fresh.conversation_id)
    write_last_conversation_id(paths, fresh.conversation_id)
    try:
        from project_env import ensure_project_env

        ensure_project_env(paths, pid)
    except Exception:
        pass
    verb = "已创建并切换到" if created else "已切换到"
    return fresh, f"{verb}项目 workspace/{pid}（新会话 · 计划待确认）"


def _apply_project_switch(
    paths: AgentPaths,
    session: Session,
    project_id: str,
) -> tuple[Session, str]:
    plan = plan_project_switch(paths, session, project_id)
    return execute_project_switch(paths, session, plan)


def create_project_with_session_isolation(
    paths: AgentPaths,
    session: Session,
    project_id: str,
) -> tuple[Session, str]:
    """Meta-command 「项目 新建」: never rebind a session already tied to another project."""
    return _apply_project_create(paths, session, project_id)


def foreign_workspace_project_write(
    path: str,
    *,
    current_project_root: str,
) -> str | None:
    """If path targets another workspace project folder, return that project id.

    ``write_text`` paths are usually relative to ``workspace/``
    (e.g. ``java-doudizhu/PROJECT.md``) or agent-root style
    (``workspace/java-doudizhu/PROJECT.md``).
    """
    from project_mode import normalize_meta_path

    normalized = path.strip().replace("\\", "/").lstrip("/")
    if not normalized:
        return None

    root = normalize_meta_path(current_project_root)
    current_id = ""
    if root.startswith("workspace/"):
        current_id = root[len("workspace/") :].split("/", 1)[0]

    if root and (normalized == root or normalized.startswith(f"{root}/")):
        return None
    if current_id and (
        normalized == current_id or normalized.startswith(f"{current_id}/")
    ):
        return None

    if normalized.startswith("workspace/"):
        rest = normalized[len("workspace/") :]
        candidate = rest.split("/", 1)[0] if rest else ""
    else:
        candidate = normalized.split("/", 1)[0] if "/" in normalized else ""

    if not candidate or candidate.startswith("_"):
        return None
    if candidate in {"_template", "workspace"}:
        return None
    try:
        normalize_project_id(candidate)
    except ProjectModeError:
        return None
    if current_id and candidate == current_id:
        return None
    return candidate


def done_events(
    *,
    request_id: str,
    proposal: ContextSwitchProposal,
    updated: Session,
    previous: Session,
    message: str,
    choice: str = "y",
) -> list[dict[str, Any]]:
    from project_api import project_state_payload
    from session import session_banner_event, session_history_event, corruption_notice_events
    from context import session_memory_event

    session_replaced = updated.conversation_id != previous.conversation_id
    events: list[dict[str, Any]] = [
        {
            "type": "context.switch.done",
            "request_id": request_id,
            "choice": choice,
            "applied": choice == "y",
            "action": proposal.action,
            "target": proposal.target,
            "project_id": proposal.project_id,
            "session_id": updated.conversation_id,
            "session_replaced": session_replaced,
            "message": message,
            "shell": "",
        }
    ]
    if choice != "y":
        return events

    if proposal.action.startswith("project."):
        events.append(project_state_payload(updated, updated.paths))
    events.append(session_banner_event(updated))
    if session_replaced:
        events.append(session_memory_event(updated))
        events.append(session_history_event(updated))
        events.extend(corruption_notice_events(updated))
    events.append({"type": "notice", "text": message})
    return events
