"""Explore scope rails (Phase 50 · EXPLORE-SCOPE-RAILS)."""

from __future__ import annotations

from typing import Literal

ExploreScopeRail = Literal["general", "grow", "project"]


def explore_scope_rail(
    *,
    project_id: str = "",
    active_shell: str = "",
    scaffold_tool_turn: bool = False,
) -> ExploreScopeRail:
    """Return scope rail for kernel auto explore task packaging."""
    pid = (project_id or "").strip()
    shell = (active_shell or "").strip()
    if pid and shell == "project":
        return "project"
    if shell == "grow" or scaffold_tool_turn:
        return "grow"
    return "general"


def build_kernel_auto_explore_task(
    user_text: str,
    *,
    project_id: str = "",
    active_shell: str = "",
    scaffold_tool_turn: bool = False,
) -> str:
    """Wrap user text with scope rail hints (T-5001 · S7)."""
    user = (user_text or "").strip()
    if not user:
        raise ValueError("auto explore user_text is empty")

    rail = explore_scope_rail(
        project_id=project_id,
        active_shell=active_shell,
        scaffold_tool_turn=scaffold_tool_turn,
    )
    if rail == "grow":
        header = (
            "【内核 auto explore · scope_rail=grow】\n"
            "默认范围：evolve/tools 范例、tool.toml、main.py；按用户意图查造工具所需文件。"
        )
    elif rail == "project":
        header = (
            "【内核 auto explore · scope_rail=project】\n"
            "默认范围：workspace 已绑项目目录与三件套；不要读 agent 根 docs/ 除非用户点名。"
        )
    else:
        header = (
            "【内核 auto explore · scope_rail=general】\n"
            "默认范围：agent 仓库内核（docs/、agent-core/、evolve/）。"
            "未绑项目时不要改去读 workspace/ 下用户项目，除非用户原话点名路径。"
        )
    return f"{header}\n用户原意：\n{user}"


def build_explore_continue_task(prior_task: str, paths_cited: list[str]) -> str:
    """Continuation task after explore hits cap (T-5002 · S8)."""
    paths = [p.strip() for p in paths_cited if p.strip()]
    if paths:
        joined = "、".join(paths[:40])
        if len(paths) > 40:
            joined += f" …等 {len(paths)} 项"
        paths_line = f"已读：{joined}\n"
    else:
        paths_line = ""
    task_preview = (prior_task or "").strip()
    if len(task_preview) > 500:
        task_preview = task_preview[:497] + "…"
    return (
        "【explore 续跑 · 首轮已达 cap】\n"
        f"{paths_line}"
        "请只读尚未覆盖的关键路径，产出补充摘要；勿重复已读文件。\n"
        f"原任务：\n{task_preview}"
    )
