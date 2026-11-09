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
    open_project_on_session,
    plan_project_switch,
    record_project_session,
)
from companion_home_policy import (
    companion_home_rejects_session_bind,
    is_main_home_companion_session,
)
from session import Session, build_seed_message, create_new, write_last_conversation_id

ContextAction = Literal[
    "project.create", "project.open", "project.switch", "project.release", "session.new"
]
SUPPORTED_ACTIONS = frozenset(
    {"project.create", "project.open", "project.switch", "project.release", "session.new"}
)

_SESSION_NEW_PROJECT_MARKERS = (
    "项目",
    "workspace/",
    "project",
    "专用会话",
    "切换项目",
    "换线",
    "switch project",
    "project switch",
)


@dataclass(frozen=True, slots=True)
class ContextSwitchProposal:
    action: ContextAction
    target: str
    reason: str = ""
    request_id: str = ""
    project_id: str | None = None
    template: str | None = None


class ContextSwitchError(Exception):
    """Invalid context-switch proposal or apply failure."""


def context_switch_replaces_session(
    paths: AgentPaths,
    session: Session,
    proposal: ContextSwitchProposal,
) -> bool:
    """True when applying the proposal would swap conversation_id (chat replace)."""
    if proposal.action in ("project.create", "project.open"):
        return False
    if proposal.action == "project.switch":
        plan = plan_project_switch(paths, session, proposal.target)
        return plan.action in {"load_session", "new_session"}
    if proposal.action == "session.new":
        return True
    return False


def _session_new_blocked_for_project_work(
    session: Session,
    proposal: ContextSwitchProposal,
) -> str | None:
    if proposal.action != "session.new" or not is_main_home_companion_session(session):
        return None
    text = f"{proposal.reason} {proposal.target}".casefold()
    if any(marker.casefold() in text for marker in _SESSION_NEW_PROJECT_MARKERS):
        return (
            "主界面继续/开工项目请用 propose_context_switch · action=project.open "
            "（或 project.switch），不要 session.new；新话题清聊天仅当用户明确要求"
        )
    return None


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
    template: str | None = None,
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

    if act == "project.release":
        shell = target.strip().casefold() or "current"
        if shell not in {"current", ""}:
            raise ContextSwitchError("project.release target must be current")
        return ContextSwitchProposal(
            action=act,
            target="current",
            reason=reason_text,
            request_id=rid,
            project_id=None,
        )

    pid_target = normalize_project_id(target)
    template_id = (template or "").strip() or None
    return ContextSwitchProposal(
        action=act,
        target=pid_target,
        reason=reason_text,
        request_id=rid,
        project_id=None,
        template=template_id,
    )


def should_auto_apply_context_switch(
    proposal: ContextSwitchProposal,
    *,
    paths: AgentPaths,
    session: Session,
) -> bool:
    """In-place project actions on the companion UI never show the 换线 overlay."""
    if _session_new_blocked_for_project_work(session, proposal):
        return False
    if getattr(session, "_companion_multi_project_orchestration_active", False):
        current_pid = (session.meta.project_id or "").strip()
        if proposal.action == "project.open" and current_pid and proposal.target.strip() != current_pid:
            return False
        if proposal.action == "project.switch":
            return False
    if proposal.action in ("project.create", "project.open", "project.release"):
        return True
    if proposal.action == "project.switch":
        plan = plan_project_switch(paths, session, proposal.target)
        if companion_home_rejects_session_bind(session):
            return plan.action in {"noop", "register_only"}
        return plan.action in {"noop", "bind_current", "register_only"}
    return False


def build_context_switch_request(
    session: Session,
    proposal: ContextSwitchProposal,
    *,
    paths: AgentPaths | None = None,
) -> dict[str, Any]:
    current_pid = (session.meta.project_id or "").strip()
    side_effects: list[str] = []
    display_target = proposal.target
    if proposal.action == "project.create":
        side_effects.append(f"创建 workspace/{proposal.target}/ 三件套（若不存在）")
        if proposal.template:
            side_effects.append(f"运行配方 scaffold {proposal.template}（phase=init）")
        side_effects.append("在主界面登记该项目，不换线、不替换聊天记录")
        title = f"新建项目 · {proposal.target}"
    elif proposal.action == "project.open":
        side_effects.append(f"在主界面登记 workspace/{proposal.target}")
        side_effects.append("不绑定本会话、不换线、不替换聊天记录")
        title = f"打开项目 · {proposal.target}"
    elif proposal.action == "project.release":
        side_effects.append("解除本会话对单个项目的写入绑定")
        side_effects.append("不换线、不替换聊天记录")
        title = "解除项目绑定"
    elif proposal.action == "project.switch":
        replaces = (
            context_switch_replaces_session(paths, session, proposal)
            if paths is not None
            else True
        )
        if replaces:
            side_effects.append(f"切换到项目 workspace/{proposal.target}")
            side_effects.append("可能加载该项目专用会话（聊天区替换）")
            title = f"切换项目 · {proposal.target}"
        else:
            side_effects.append(f"在本会话打开 workspace/{proposal.target}")
            side_effects.append("不换线、不替换聊天记录")
            title = f"打开项目 · {proposal.target}"
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
    if getattr(session, "_companion_multi_project_orchestration_active", False):
        if proposal.action not in ("project.create", "project.release"):
            from active_work_multi_project import ORCHESTRATION_SWITCH_BLOCKED_MESSAGE

            raise ContextSwitchError(ORCHESTRATION_SWITCH_BLOCKED_MESSAGE)
    if proposal.action == "project.create":
        return _apply_project_create(
            paths,
            session,
            proposal.target,
            template=proposal.template,
        )
    if proposal.action == "project.open":
        return _apply_project_open(paths, session, proposal.target)
    if proposal.action == "project.release":
        return _apply_project_release(session)
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
    from dialogue_archive import archive_dialogue_line

    archive_dialogue_line(
        session,
        paths,
        reason=proposal.reason or "新会话",
        event_id=f"session-new:{session.conversation_id}",
    )
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
    prev_pid = (previous.meta.project_id or "").strip()
    hint = ""
    if prev_pid:
        hint = "普通对话中可使用 write_evolve 沉淀工具；完成后可从会话列表回到项目。"
    if reason and "工具" in reason:
        hint = "新会话已重新加载 evolved 工具清单，之前创建的工具现已可用。" + (
            f" {hint}" if hint else ""
        )
    seed = build_seed_message(
        previous_session_id=previous.conversation_id,
        previous_goal=goal,
        reason=reason,
        hint=hint,
    )
    fresh.append_message(seed, persist=False)
    fresh.save()



def _bind_ordinary_light_entry(session: Session, paths: AgentPaths, project_id: str) -> None:
    """Arm one execute request for a light project without faking persisted approval."""
    from project_mode import read_project_template

    try:
        if read_project_template(paths, project_id) != "light":
            return
    except Exception:
        return
    session.next_work_intent = "execute"
    session.next_task_scope = ""
    session.save()


def _apply_project_create(
    paths: AgentPaths,
    session: Session,
    project_id: str,
    *,
    template: str | None = None,
    project_template: str | None = None,
) -> tuple[Session, str]:
    pid = normalize_project_id(project_id)
    dest = project_dir(paths, pid)
    if dest.exists():
        raise ContextSwitchError(
            f"项目 workspace/{pid} 已存在；请 propose_context_switch · action=project.open · target={pid}"
        )
    try:
        create_project(paths, pid, template=template, project_template=project_template)
    except ProjectModeError as exc:
        if "already exists" in str(exc):
            raise ContextSwitchError(
                f"项目 workspace/{pid} 已存在；请 propose_context_switch · action=project.open · target={pid}"
            ) from exc
        raise ContextSwitchError(str(exc)) from exc

    current_pid = (session.meta.project_id or "").strip()
    # Already on this project in project shell — refresh mapping only.
    if current_pid == pid and session.meta.active_shell == "project":
        record_project_session(paths, pid, session.conversation_id)
        write_last_conversation_id(paths, session.conversation_id)
        try:
            from project_env import ensure_project_env

            ensure_project_env(paths, pid)
        except Exception:
            pass
        return session, f"已创建项目 workspace/{pid}（当前会话）"

    return _register_new_project_workspace(
        paths,
        session,
        pid,
        template=template,
        project_template=project_template,
    )


def _register_new_project_workspace(
    paths: AgentPaths,
    session: Session,
    pid: str,
    *,
    template: str | None = None,
    project_template: str | None = None,
) -> tuple[Session, str]:
    """Register a new workspace on disk without switching session, shell, or chat history."""
    record_project_session(paths, pid, session.conversation_id)
    write_last_conversation_id(paths, session.conversation_id)
    try:
        from project_env import ensure_project_env

        ensure_project_env(paths, pid)
    except Exception:
        pass
    note = _create_template_note(template, project_template)
    current_pid = (session.meta.project_id or "").strip()
    if not current_pid:
        return session, f"已在主界面创建项目 workspace/{pid}（未换线{note}）"
    if current_pid == pid:
        return session, f"已创建项目 workspace/{pid}（当前会话{note}）"
    return session, f"已创建项目 workspace/{pid}（仍留在 {current_pid}{note}）"


def _create_template_note(template: str | None, project_template: str | None) -> str:
    from project_mode import normalize_project_template

    selected = normalize_project_template(project_template)
    raw_recipe = (template or "").strip()
    if raw_recipe in {"light", "standard", "lite", "full"}:
        selected = normalize_project_template(raw_recipe)
    return " · 轻量模板" if selected == "light" else ""


def _apply_project_release(session: Session) -> tuple[Session, str]:
    from project_cli import clear_session_project_binding

    if not clear_session_project_binding(session):
        return session, "当前会话未绑定项目"
    session.save()
    return (
        session,
        "已解除本会话的项目绑定（不换线）。跨项目施工请用 run_project_workers；"
        "批量删除项目请用 trash_workspace_projects。",
    )


def _apply_project_open(
    paths: AgentPaths,
    session: Session,
    project_id: str,
) -> tuple[Session, str]:
    if getattr(session, "_companion_multi_project_orchestration_active", False):
        current_pid = (session.meta.project_id or "").strip()
        target = normalize_project_id(project_id)
        if current_pid and target != current_pid:
            raise ContextSwitchError(
                "正在同时推进多个项目：不能通过切换项目绑定去写其它项目。"
                "若缺少工作目录请先新建项目登记，再为对应项目分别派工施工。"
            )
    try:
        return open_project_on_session(paths, session, project_id)
    except ProjectModeError as exc:
        raise ContextSwitchError(str(exc)) from exc


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
    *,
    template: str | None = None,
    project_template: str | None = None,
) -> tuple[Session, str]:
    """Meta-command 「项目 新建」: create only; existing projects must be opened explicitly."""
    return _apply_project_create(
        paths,
        session,
        project_id,
        template=template,
        project_template=project_template,
    )


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
