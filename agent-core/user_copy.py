"""User-facing copy — plain Chinese, actionable, no internal jargon.

Internal-only markers like ``[Harness]`` belong in model routing prompts, not in
``turn.notice`` or assistant explanations shown to desktop users.
"""

from __future__ import annotations

from session import Session
from tools.registry import ToolRegistry

_TOOL_USER_LABELS: dict[str, str] = {
    "run_command": "命令",
    "run_evolved": "扩展工具",
    "run_python": "Python 脚本",
    "repair_node_modules": "依赖修复",
    "patch_file": "文件修补",
    "write_text": "写入文件",
    "read_file": "读取文件",
    "grep": "内容搜索",
    "glob_file_search": "文件查找",
    "explore": "代码探索",
    "evolved": "扩展工具",
}


def humanize_tool_user_label(tool: str) -> str:
    """Short noun for user-visible notices (not canonical tool ids)."""
    key = (tool or "").strip().lower()
    if not key:
        return "该步骤"
    return _TOOL_USER_LABELS.get(key, "该步骤")



def format_tool_loop_exceeded_message(
    session: Session,
    *,
    tool_rounds: int,
    tool_loop_max: int,
    registry: ToolRegistry | None = None,
    segment: int | None = None,
    total_tool_rounds: int | None = None,
    turn_intent: str | None = None,
) -> str:
    """When the tool inner loop ends without a final assistant reply."""
    intent = (turn_intent or getattr(session, "turn_intent", None) or "").strip()
    mode = getattr(session.meta, "turn_mode", "agent")

    if tool_rounds <= 0 and intent in {"qa", "recall", "requirements"}:
        return (
            "本轮已自动重试，仍未收到完整回答（多为模型超时或上游繁忙）。\n"
            "\n"
            "你可以再发一次同样的问题；若连续失败，建议切换 Flash 或其它模型。"
        )

    if tool_rounds <= 0 and mode == "ask":
        return (
            "本轮已自动重试，仍未收到完整回答。\n"
            "\n"
            "需要改代码或跑命令时，请切换到「动手」模式。"
        )

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
    if intent in {"qa", "recall"}:
        lines.extend(
            [
                "这类问题通常可以直接根据对话和附件回答；请重发或说「别查了直接说」。",
            ]
        )
    else:
        lines.extend(
            [
                "请发「继续」或给出更具体的下一步（要改哪个文件、跑哪条命令）。",
                "",
                "常见原因：",
                "· 反复观察代码却没有写入或运行测试",
                "· 缺少完成该步骤所需的工具能力",
            ]
        )

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


