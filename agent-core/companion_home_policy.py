"""Main home companion must stay project-agnostic (register_only + workers)."""

from __future__ import annotations

from session import Session

MAIN_HOME_SESSION_BIND_DENIED = (
    "主界面小魔王会话不能把当前聊天绑定为「会话项目」。"
    "请用 propose_context_switch · action=project.open 仅登记 workspace；"
    "跨项目施工用 run_project_workers。"
    "若误绑单项目，用 action=project.release。"
    "不要向用户声称「之后还能把本会话绑定到其他项目」。"
)

TRASH_CLEARED_BINDING_HINT = (
    "主界面会话保持无单项目写租约；"
    "需要别的 workspace 时用 project.open（仅登记）或 run_project_workers，"
    "不要把当前聊天绑定成会话项目。"
)


def is_main_home_companion_session(session: Session) -> bool:
    """Ordinary 小魔王空间 chat with no project binding on this session."""
    return not (session.meta.project_id or "").strip()


def companion_home_rejects_session_bind(session: Session) -> bool:
    """True when the current chat must not receive a project write lease binding."""
    shell = (session.meta.active_shell or "grow").strip() or "grow"
    return shell != "project"
