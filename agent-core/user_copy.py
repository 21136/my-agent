"""User-facing copy — plain Chinese, actionable, no internal jargon.

Internal-only markers like ``[Harness]`` belong in model routing prompts, not in
``turn.notice`` or assistant explanations shown to desktop users.
"""

from __future__ import annotations

from session import Session
from tools.registry import ToolRegistry



def format_tool_loop_exceeded_message(
    session: Session,
    *,
    tool_rounds: int,
    tool_loop_max: int,
    registry: ToolRegistry | None = None,
    segment: int | None = None,
    total_tool_rounds: int | None = None,
) -> str:
    """When the tool inner loop ends without a final assistant reply."""
    from loader import session_evolved_allowlist

    if tool_rounds <= 0:
        usage = f"本回合未成功调用工具（上限 {tool_loop_max} 次）"
    else:
        usage = f"本回合已调用工具 {tool_rounds} 次（上限 {tool_loop_max} 次）"

    seg_note = ""
    if segment is not None and segment > 1:
        seg_note = f"，第 {segment} 段"
        if total_tool_rounds is not None:
            seg_note += f"，累计 {total_tool_rounds} 次"

    lines = [
        f"本回合工具预算已用完（{usage}{seg_note}），当前步骤尚未完成。",
        "",
    ]
    lines.extend(
        [
            "请发「继续」或给出更具体的下一步（要改哪个文件、跑哪条命令）。",
            "",
            "常见原因：",
            "· 反复观察代码却没有写入或运行测试",
            "· 缺少完成该步骤所需的工具能力",
        ]
    )

    reg = registry or ToolRegistry.load(session.paths)
    allowed = sorted(session_evolved_allowlist(session, registry=reg))
    if allowed:
        lines.extend(["", f"本会话可用工具：{', '.join(allowed[:12])}" + ("…" if len(allowed) > 12 else "")])

    return "\n".join(lines)


def format_segment_pause_message(
    *,
    segment: int,
    total_tool_rounds: int,
    auto_continue: bool,
) -> str:
    lines = [
        f"本回合工具预算已用完（第 {segment} 段，累计 {total_tool_rounds} 次调用），已有部分进展。",
        "",
        "已完成部分见上方的工具结果与助手回复。",
    ]
    if auto_continue:
        lines.extend(["", "将自动继续下一段。"])
    else:
        lines.extend(["", "请发「继续」开始下一轮工具预算。"])
    return "\n".join(lines)


