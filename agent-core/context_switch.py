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
    plan_project_switch,
    record_project_session,
)
from session import Session, create_new, write_last_conversation_id

ContextAction = Literal["project.create", "project.switch", "shell.switch", "session.new"]
SUPPORTED_ACTIONS = frozenset(
    {"project.create", "project.switch", "shell.switch", "session.new"}
)
SHELL_TARGETS = frozenset({"grow", "daily", "project", "govern"})
SESSION_NEW_TARGETS = SHELL_TARGETS | {"current"}


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

    if act == "shell.switch":
        shell = target.strip().casefold()
        if shell not in SHELL_TARGETS:
            raise ContextSwitchError(
                f"shell.switch target must be one of: {', '.join(sorted(SHELL_TARGETS))}"
            )
        pid: str | None = None
        if project_id and str(project_id).strip():
            pid = normalize_project_id(str(project_id))
        return ContextSwitchProposal(
            action=act,
            target=shell,
            reason=reason_text,
            request_id=rid,
            project_id=pid,
        )

    if act == "session.new":
        shell = target.strip().casefold() or "current"
        if shell not in SESSION_NEW_TARGETS:
            raise ContextSwitchError(
                "session.new target must be current or grow|daily|project|govern"
            )
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
        shell = _resolve_session_new_shell(session, proposal.target)
        display_target = shell
        side_effects.append(f"在「{shell}」壳新建空白会话")
        side_effects.append("聊天区将清空为新会话（旧会话仍保留在磁盘）")
        if shell == "project":
            pid = proposal.project_id or current_pid or "(需已绑定项目)"
            side_effects.append(f"仍绑定项目：{pid}")
        title = f"新会话 · {shell}"
    else:
        side_effects.append(f"切换到外壳 · {proposal.target}")
        side_effects.append("将加载该壳专用会话（聊天区可能替换）")
        if proposal.target == "project":
            pid = proposal.project_id or current_pid or "(需指定项目)"
            side_effects.append(f"项目：{pid}")
        title = f"切换外壳 · {proposal.target}"

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
    if proposal.action == "shell.switch":
        return _apply_shell_switch(paths, session, proposal)
    if proposal.action == "session.new":
        return _apply_session_new(paths, session, proposal)
    raise ContextSwitchError(f"unsupported action: {proposal.action}")


def _resolve_session_new_shell(session: Session, target: str) -> str:
    from shell_switch import lookup_shell_owner

    if target == "current":
        owner = lookup_shell_owner(session.paths, session.conversation_id)
        return owner or session.meta.active_shell or "daily"
    return target


def _current_shell_line(session: Session) -> str:
    from shell_switch import lookup_shell_owner

    owner = lookup_shell_owner(session.paths, session.conversation_id)
    if owner:
        return owner
    if session.meta.active_shell == "project" and (session.meta.project_id or "").strip():
        return "project"
    return session.meta.active_shell or "daily"


def _apply_session_new(
    paths: AgentPaths,
    session: Session,
    proposal: ContextSwitchProposal,
) -> tuple[Session, str]:
    from shell_switch import (
        _clear_project_binding,
        record_shell_session,
    )

    shell = _resolve_session_new_shell(session, proposal.target)
    current = _current_shell_line(session)
    if shell != current:
        raise ContextSwitchError(
            f"session.new 仅限当前壳（当前 {current}）；跨壳请先 shell.switch 到 {shell}"
        )

    fresh = create_new(paths)
    if shell == "project":
        pid = (proposal.project_id or session.meta.project_id or "").strip()
        if not pid:
            raise ContextSwitchError("project 壳新会话需要当前已绑定项目或提供 project_id")
        from project_cli import bind_project_session

        plan_status = session.meta.project_plan_status or "draft"
        bind_project_session(fresh, pid, plan_status=plan_status)
        if plan_status == "confirmed" and session.meta.project_plan_confirmed_at:
            fresh.meta.project_plan_confirmed_at = session.meta.project_plan_confirmed_at
        fresh.meta.project_phase_fingerprint = session.meta.project_phase_fingerprint
        fresh.meta.project_doc_fingerprint = session.meta.project_doc_fingerprint
        fresh.save()
        record_project_session(paths, pid, fresh.conversation_id)
        write_last_conversation_id(paths, fresh.conversation_id)
        return fresh, f"已在项目 {pid} 新建会话（旧对话仍保留）"

    fresh.meta.active_shell = shell  # type: ignore[assignment]
    _clear_project_binding(fresh.meta)
    fresh.save()
    if shell in {"grow", "daily"}:
        record_shell_session(paths, shell, fresh.conversation_id)
    write_last_conversation_id(paths, fresh.conversation_id)
    return fresh, f"已在外壳 · {shell} 新建会话（旧对话仍保留）"


def _apply_shell_switch(
    paths: AgentPaths,
    session: Session,
    proposal: ContextSwitchProposal,
) -> tuple[Session, str]:
    from shell_switch import ShellSwitchError, switch_shell

    shell = proposal.target
    if shell not in SHELL_TARGETS:
        raise ContextSwitchError(f"invalid shell: {shell}")
    try:
        updated, replaced = switch_shell(
            paths,
            session,
            shell,  # type: ignore[arg-type]
            project_id=proposal.project_id,
        )
    except ShellSwitchError as exc:
        raise ContextSwitchError(str(exc)) from exc
    note = "（会话已替换）" if replaced else "（同会话）"
    return updated, f"已切换到外壳 · {shell}{note}"


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
        verb = "已打开" if not created else "已创建"
        return session, f"{verb}项目 workspace/{pid}（当前会话）"

    # Unbound session may bind in place; bound to another project → new session.
    if not current_pid:
        from project_cli import bind_project_session

        bind_project_session(session, pid, plan_status="draft")
        session.save()
        record_project_session(paths, pid, session.conversation_id)
        write_last_conversation_id(paths, session.conversation_id)
        verb = "已创建并打开" if created else "已打开"
        return session, f"{verb}项目 workspace/{pid}（计划待确认）"

    fresh = create_new(paths)
    from project_cli import bind_project_session

    bind_project_session(fresh, pid, plan_status="draft")
    fresh.save()
    record_project_session(paths, pid, fresh.conversation_id)
    write_last_conversation_id(paths, fresh.conversation_id)
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
            "shell": updated.meta.active_shell if choice == "y" else previous.meta.active_shell,
        }
    ]
    if choice != "y":
        return events

    if proposal.action == "shell.switch":
        events.append(
            {
                "type": "shell.switch.done",
                "shell": updated.meta.active_shell,
                "session_id": updated.conversation_id,
                "session_replaced": session_replaced,
            }
        )

    if updated.meta.active_shell == "project" or proposal.action.startswith("project."):
        events.append(project_state_payload(updated, updated.paths))
    events.append(session_banner_event(updated))
    if session_replaced:
        events.append(session_memory_event(updated))
        events.append(session_history_event(updated))
        events.extend(corruption_notice_events(updated))
    events.append({"type": "notice", "text": message})
    return events
