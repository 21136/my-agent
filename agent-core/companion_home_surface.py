"""Companion home (小魔王空间) SHOW/HIDE — durable notice filters (server).

Mirrors ``desktop/src/shells/unified/system-notice-copy.ts``; keep patterns in sync.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from companion_home_chat import companion_home_unbound_desktop

if TYPE_CHECKING:
    from session import Session

# HIDE: automation / confirm routing / stale-card spam must not land in companion chat.
_SILENT_COMPANION_CHAT_PREFIX = re.compile(
    r"^(?:"
    r"需要在当前小魔王空间确认|"
    r"已在主界面创建项目\s+workspace/|"
    r"\[Goal\]\s*多项目|"
    r"多项目 worker 批次|"
    r"并行多项目编排[：:]|"
    r"会话已保存|"
    r"正在为 \d+ 个项目准备 worker 派工|"
    r"授权与写入确认只会在当前小魔王空间出现|"
    r"忽略过期确认|"
    r"另有一项授权确认"
    r")",
    re.UNICODE,
)

SESSION_BINDING_BUSY_QUERY = (
    "当前主会话仍在编排或确认中，暂时无法切换项目查询；"
    "请在输入区下方的确认条完成授权，或等待本轮结束。"
)
SESSION_BINDING_BUSY_SWITCH = (
    "当前主会话仍在编排或确认中，暂时无法换线；"
    "请在输入区下方的确认条完成待授权项，或等待本轮结束后再切换。"
)

CONFIRM_ALREADY_IN_FLIGHT_COMPANION = (
    "另有一项授权确认正在进行；请先在输入区下方的确认条完成现有授权。"
)
CONFIRM_ALREADY_IN_FLIGHT_DEFAULT = (
    "另有一项授权确认正在进行；请先完成当前确认卡。"
)

STALE_CONFIRM_INGRESS_COMPANION = "确认已过期，请以输入区下方的确认条为准。"
STALE_CONFIRM_INGRESS_DEFAULT = "忽略过期确认（请点最新一张工具确认卡）"


def companion_home_silences_chat_notice(text: str) -> bool:
    line = (text or "").replace("\r\n", "\n").strip()
    if not line:
        return True
    return bool(_SILENT_COMPANION_CHAT_PREFIX.match(line))


def polish_notice_for_session_chat(session: Session | None, text: str) -> str | None:
    """Drop companion-home HIDE lines; other sessions pass through trimmed."""
    line = (text or "").strip()
    if not line:
        return None
    if session is not None and companion_home_unbound_desktop(session):
        if companion_home_silences_chat_notice(line):
            return None
    return line


def confirm_in_flight_notice(session: Session | None) -> str:
    if session is not None and companion_home_unbound_desktop(session):
        return CONFIRM_ALREADY_IN_FLIGHT_COMPANION
    return CONFIRM_ALREADY_IN_FLIGHT_DEFAULT


def stale_confirm_ingress_notice(session: Session | None) -> str:
    if session is not None and companion_home_unbound_desktop(session):
        return STALE_CONFIRM_INGRESS_COMPANION
    return STALE_CONFIRM_INGRESS_DEFAULT
