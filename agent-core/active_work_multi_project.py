"""Lexical detection for simultaneous multi-project work during an active companion turn."""
from __future__ import annotations

import re

_SIMULTANEITY_RE = re.compile(
    r"(?:\u540c\u65f6|\u4e00\u8d77|\u4e00\u5e76|\u5e76\u884c|"
    r"at\s+the\s+same\s+time|together|in\s+parallel|parallel(?:ly)?)",
    re.IGNORECASE,
)
_MULTI_PROJECT_COUNT_RE = re.compile(
    r"(?:\u4e24\u4e2a?|\u4e09\u4e2a?|\u56db\u4e2a?|\u4e94\u4e2a?|\u591a\u4e2a?|\u51e0\u4e2a?|"
    r"\b(?:two|three|four|five|2|3|4|5)\b)"
    r".{0,12}(?:\u4e2a\u7684?)?\u9879\u76ee|"
    r"\u591a\u4e2a\u9879\u76ee|"
    r"\u591a\u9879\u76ee|"
    r"(?:\u518d)?(?:\u518d\u5199|\u518d\u505a|\u518d\u5f00\u59cb).{0,24}\u9879\u76ee",
    re.IGNORECASE,
)
_NEW_OR_WRITE_PROJECT_RE = re.compile(
    r"(?:\u5199|\u505a|\u521b\u5efa|\u65b0\u5efa|\u751f\u6210|\u642d\u5efa|\u5f00\u4e00\u4e2a?|"
    r"write|create|build|make|start|scaffold|implement)"
    r".{0,120}\u9879\u76ee|"
    r"\u9879\u76ee.{0,40}(?:\u5199|\u505a|\u521b\u5efa|\u65b0\u5efa|\u751f\u6210|"
    r"write|create|build|make)",
    re.IGNORECASE,
)
_SINGLE_SUPPLEMENT_RE = re.compile(
    r"^\s*(?:\u8865\u5145|\u9644\u52a0|\u7ee7\u7eed\u5f53\u524d|\u6539\u5f53\u524d|\u8bf7\u518d)",
    re.IGNORECASE,
)


def is_companion_multi_project_orchestration_request(text: str) -> bool:
    """True when the user asks to work on multiple projects at once while another turn is active."""
    if not isinstance(text, str):
        return False
    body = text.strip()
    if not body or len(body) > 20_000:
        return False
    if _SINGLE_SUPPLEMENT_RE.search(body):
        return False
    if not _SIMULTANEITY_RE.search(body):
        return False
    return bool(_MULTI_PROJECT_COUNT_RE.search(body) or _NEW_OR_WRITE_PROJECT_RE.search(body))


COMPANION_ORCHESTRATION_UI_SKIP_PREFIX = "【主会话并行多项目编排"


_ORCHESTRATION_PREAMBLE = """\
【主会话并行多项目编排 — 必须遵守】
- 每个项目各有独立 workspace/<project_id>/ 目录；并行施工 = 主会话只编排，每个项目各派一个 run_project_workers worker，在各自 workspace 与租约内写文件。
- 主会话 companion 不换线；project.open 只登记 workspace、不绑定写租约；跨项目删除用 trash_workspace_projects，跨项目施工用 run_project_workers。
- 缺少 workspace/<id> 时只用 propose_context_switch · action=project.create 就地登记目录；禁止随后 project.open 切换绑定后在父会话 run_evolved 写项目文件（会被硬拦截）。
- 每个 worker 必须钉死 project_id、read_targets、write_targets；父 Agent 不得对 workspace 下任何项目目录直接 run_evolved 写工具。
- 缺项目 id、任务边界或路径时先问用户；不要臆造 worker 结果。
- 用简短口吻汇总各 worker 回执；并继续回应用户在本回合的其它要求（如有）。

用户原话：
"""


def build_companion_orchestration_instruction(user_text: str) -> str:
    """Turn surface text into an orchestration-safe work instruction."""
    body = user_text.strip() if isinstance(user_text, str) else ""
    if not body:
        return _ORCHESTRATION_PREAMBLE.rstrip()
    return f"{_ORCHESTRATION_PREAMBLE}{body}"


def companion_multi_project_orchestration_active(session: object | None) -> bool:
    if session is None:
        return False
    return bool(getattr(session, "_companion_multi_project_orchestration_active", False))


def orchestration_active_for_executor(executor: object) -> bool:
    """Read orchestration state from the live Session and mirrored executor session."""
    if executor is None:
        return False
    bound = getattr(executor, "bound_session", None)
    if companion_multi_project_orchestration_active(bound):
        return True
    ex_session = getattr(executor, "session", None)
    return bool(getattr(ex_session, "companion_multi_project_orchestration_active", False))


ORCHESTRATION_USE_WORKERS_MESSAGE = (
    "正在同时推进多个项目：请为对应项目分别启动独立施工，"
    "不要在本会话里直接修改各项目目录下的文件。"
)


ORCHESTRATION_SWITCH_BLOCKED_MESSAGE = (
    "正在同时推进多个项目：此时不能切换或新开会话线。"
    "如需登记新项目请用「新建项目」；各项目的具体实现请分别派工处理。"
)


def mark_session_companion_multi_project_orchestration(
    session: object,
    *,
    active: bool,
    executor: object | None = None,
) -> None:
    """Ephemeral flag for one companion turn; cleared when the host run ends."""
    if active:
        setattr(session, "_companion_multi_project_orchestration_active", True)
    elif hasattr(session, "_companion_multi_project_orchestration_active"):
        delattr(session, "_companion_multi_project_orchestration_active")
    if executor is not None and getattr(executor, "bound_session", None) is session:
        ex_session = getattr(executor, "session", None)
        if ex_session is not None:
            ex_session.companion_multi_project_orchestration_active = active
