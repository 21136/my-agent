"""Runaway v2 turn plan."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from paths import AgentPaths
from project_mode import next_open_task, read_project_artifacts
from runaway_v2.checklist import (
    Checklist,
    ChecklistItem,
    first_open_item,
    prepare_write_scope,
)
from runaway_v2.config import directed_after
from runaway_v2.phase import PhaseResult

PhaseName = Literal["prepare", "implement", "verify", "release_wait"]
ModeName = Literal["auto", "directed", "human"]

_RUNAWAY_V2_LOCALE_APPEND = (
    "思考过程与对用户说明请使用中文。"
    "不要对用户说 Harness、刷新路径、checkpoint 等内部词；"
    "狂奔模式下禁止要求用户审阅或采纳计划提案（系统会自动采纳或直接写入）。"
    "若文档未齐，用 write_text/patch_file 直接改 VERIFY/MAP/TASKS，不要调用 plan_partner。"
)


def _with_locale_append(text: str) -> str:
    base = (text or "").strip()
    if not base:
        return _RUNAWAY_V2_LOCALE_APPEND
    if _RUNAWAY_V2_LOCALE_APPEND in base:
        return base
    return f"{base}\n{_RUNAWAY_V2_LOCALE_APPEND}"


@dataclass(frozen=True, slots=True)
class TurnPlan:
    phase: PhaseName
    mode: ModeName
    user_line: str
    focus_item_id: str | None
    active_task_id: str | None
    allowed_write_globs: tuple[str, ...]
    forbid_plan_partner: bool
    system_append: str
    acceptance_item: ChecklistItem | None
    max_tool_rounds: int
    chain_after_ok: bool


def _user_requested_item(user_text: str, checklist: Checklist) -> ChecklistItem | None:
    text = (user_text or "").strip()
    if not text:
        return None
    lowered = text.lower()
    if "补 env" in lowered or "quality.commands" in lowered:
        for item in checklist.items:
            if item.id.upper().startswith("MX-5"):
                return item
    for item in checklist.items:
        if item.id and item.id.upper() in text.upper():
            return item
    return None


def _mode_for_item(item: ChecklistItem | None) -> ModeName:
    if item is None:
        return "auto"
    if item.directed_used and item.attempts >= directed_after():
        return "human"
    if item.attempts >= directed_after() and not item.directed_used:
        return "directed"
    return "auto"


def _system_append_for_item(item: ChecklistItem, *, mode: ModeName) -> str:
    scope = ", ".join(item.write_scope) or "（只读）"
    directed = "定向" if mode == "directed" else "自动"
    return (
        f"本轮（{directed}）只处理验收项 {item.id}：{item.title}。\n"
        f"允许修改：{scope}。不要改 MAP.md / TASKS.archive，不要调用 plan_partner。\n"
        f"回合结束后系统会重新检查该项；你不必宣布项目已交付。"
    )


def build_turn_plan(
    *,
    paths: AgentPaths,
    project_id: str,
    phase: PhaseResult,
    checklist: Checklist,
    user_text: str,
    intent: str,
    allow_user_request: bool = False,
) -> TurnPlan:
    if phase.phase == "human":
        reason = phase.human_reason or "需要人工处理"
        return TurnPlan(
            phase="verify",
            mode="human",
            user_line=reason,
            focus_item_id=None,
            active_task_id=None,
            allowed_write_globs=(),
            forbid_plan_partner=True,
            system_append=reason,
            acceptance_item=None,
            max_tool_rounds=0,
            chain_after_ok=False,
        )

    if phase.phase == "release_wait":
        if allow_user_request:
            return TurnPlan(
                # A user-requested maintenance turn is deliberately separate
                # from the automatic continuation state. The next disk-derived
                # mirror will still return the project to release_wait.
                phase="implement",
                mode="auto",
                user_line="正在处理你的新请求",
                focus_item_id=None,
                active_task_id=None,
                allowed_write_globs=(),
                forbid_plan_partner=True,
                system_append=(
                    "项目已经通过验收并处于发布等待。用户主动提出了新的工作请求；"
                    "这是一次用户主动维护回合，不是自动续接。当前回合已临时切换到"
                    "implementation/implementing，允许在当前项目内修改业务代码。请直接围绕"
                    "用户请求检查、定位并修复问题：先运行结构化测试或 run_command，发现缺陷后"
                    "必须用 write_text/patch_file 实际修改并重新验证，不要只给总结。不要调用"
                    "项目阶段切换命令，也不要等待发布确认或要求用户再次发送继续；PROJECT/ENV/"
                    "TASKS/VERIFY 等计划文件仍保持保护，本回合完成后不要自动延伸到下一回合。"
                ),
                acceptance_item=None,
                max_tool_rounds=12,
                chain_after_ok=False,
            )
        completed = phase.release_accepted
        return TurnPlan(
            phase="release_wait",
            mode="auto",
            user_line=("项目已完成" if completed else "验收清单已全部通过，等待发布确认"),
            focus_item_id=None,
            active_task_id=None,
            allowed_write_globs=(),
            forbid_plan_partner=True,
            system_append="",
            acceptance_item=None,
            max_tool_rounds=0,
            chain_after_ok=False,
        )

    if phase.phase == "prepare":
        missing = "、".join(phase.blockers) if phase.blockers else "四件套"
        if phase.blockers == ("plan_status",):
            missing = "计划未确认（将自动推进）"
        prepare_scope = prepare_write_scope()
        return TurnPlan(
            phase="prepare",
            mode="auto",
            user_line=f"正在整理项目文档（缺：{missing}）",
            focus_item_id=None,
            active_task_id=None,
            allowed_write_globs=prepare_scope,
            forbid_plan_partner=True,
            system_append=(
                "狂奔准备阶段：用 read_text / write_text / patch_file 整理 "
                "PROJECT / DESIGN / TASKS / VERIFY / MAP，补齐 REQ/UX/T/V 标识。"
                "不要写业务代码；不要调用 plan_partner（直接写入，系统不经过人工审阅）。"
            ),
            acceptance_item=ChecklistItem(
                id="PREPARE",
                kind="matrix",
                title="四件套就绪",
                write_scope=list(prepare_scope),
                acceptance={"type": "prepare_ready"},
            ),
            max_tool_rounds=8,
            chain_after_ok=True,
        )

    if phase.phase == "implement":
        artifacts = read_project_artifacts(paths, project_id)
        _line_index, body, task_id = next_open_task(artifacts.get("TASKS.md", ""))
        requested = _user_requested_item(user_text, checklist)
        if requested is not None:
            mode = _mode_for_item(requested)
            if mode == "human":
                return TurnPlan(
                    phase="implement",
                    mode="human",
                    user_line=f"验收项 {requested.id} 定向修复后仍失败，需要人工处理",
                    focus_item_id=requested.id,
                    active_task_id=task_id,
                    allowed_write_globs=tuple(requested.write_scope),
                    forbid_plan_partner=True,
                    system_append=_system_append_for_item(requested, mode=mode),
                    acceptance_item=requested,
                    max_tool_rounds=6,
                    chain_after_ok=False,
                )
            return TurnPlan(
                phase="verify",
                mode=mode,
                user_line=f"正在处理验收项 {requested.id}",
                focus_item_id=requested.id,
                active_task_id=task_id,
                allowed_write_globs=tuple(requested.write_scope),
                forbid_plan_partner=True,
                system_append=_system_append_for_item(requested, mode=mode),
                acceptance_item=requested,
                max_tool_rounds=6,
                chain_after_ok=True,
            )
        label = f"{task_id} {body}" if task_id and body else (task_id or "下一任务")
        return TurnPlan(
            phase="implement",
            mode="auto",
            user_line=f"正在实现 {label}",
            focus_item_id=None,
            active_task_id=task_id,
            allowed_write_globs=("VERIFY.md",),
            forbid_plan_partner=True,
            system_append=(
                f"狂奔实现阶段：只推进 {task_id}，实际修改业务代码并运行项目测试。"
                "完成后运行结构化测试和 PROJECT.md 中的硬验收命令，"
                "把通过的命令、结果和对应 V/AC 证据写入 VERIFY.md，"
                "再调用 report_progress(task_id=当前任务) 关闭任务。"
                "不要只写总结；失败必须修复后重跑。"
                "文档类修补用 patch_file/write_text 直接写，不要调用 plan_partner。"
            ),
            acceptance_item=None,
            max_tool_rounds=12,
            chain_after_ok=True,
        )

    focus = first_open_item(checklist)
    requested = _user_requested_item(user_text, checklist) or focus
    mode = _mode_for_item(requested)
    if mode == "human" and requested is not None:
        return TurnPlan(
            phase="verify",
            mode="human",
            user_line=f"验收项 {requested.id} 定向修复后仍失败，需要人工处理",
            focus_item_id=requested.id,
            active_task_id=None,
            allowed_write_globs=tuple(requested.write_scope),
            forbid_plan_partner=True,
            system_append=_system_append_for_item(requested, mode=mode),
            acceptance_item=requested,
            max_tool_rounds=0,
            chain_after_ok=False,
        )
    if requested is None:
        return TurnPlan(
            phase="verify",
            mode="auto",
            user_line="验收清单已全部通过",
            focus_item_id=None,
            active_task_id=None,
            allowed_write_globs=(),
            forbid_plan_partner=True,
            system_append="",
            acceptance_item=None,
            max_tool_rounds=0,
            chain_after_ok=False,
        )
    return TurnPlan(
        phase="verify",
        mode=mode,
        user_line=f"正在处理验收项 {requested.id}",
        focus_item_id=requested.id,
        active_task_id=None,
        allowed_write_globs=tuple(requested.write_scope),
        forbid_plan_partner=True,
        system_append=_system_append_for_item(requested, mode=mode),
        acceptance_item=requested,
        max_tool_rounds=6,
        chain_after_ok=True,
    )


def _checklist_item(
    checklist: Checklist,
    item: ChecklistItem | None,
) -> ChecklistItem | None:
    if item is None:
        return None
    return next((entry for entry in checklist.items if entry.id == item.id), None)


def _escalated_to_human(checklist: Checklist, item: ChecklistItem | None) -> bool:
    target = _checklist_item(checklist, item)
    if target is None:
        return False
    return (
        target.status == "failed"
        and target.directed_used
        and target.attempts >= directed_after()
    )


def should_chain(
    *,
    plan: TurnPlan,
    hook_ok: bool,
    runaway_enabled: bool,
    cancelled: bool,
    intent: str,
    checklist: Checklist,
    phase: PhaseResult,
) -> bool:
    if not runaway_enabled or cancelled:
        return False
    if plan.mode == "human":
        return False
    if not plan.chain_after_ok:
        return False
    if phase.phase in {"release_wait", "human"}:
        return False
    if plan.phase == "release_wait":
        return False
    if _escalated_to_human(checklist, plan.acceptance_item):
        return False
    if phase.phase == "prepare":
        return True
    if phase.phase == "implement":
        return True
    if phase.phase == "verify":
        return first_open_item(checklist) is not None
    return False
