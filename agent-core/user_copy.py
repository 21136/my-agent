"""User-facing copy — plain Chinese, actionable, no internal jargon.

Internal-only markers like ``[Harness]`` belong in model routing prompts, not in
``turn.notice`` or assistant explanations shown to desktop users.
"""

from __future__ import annotations

from session import Session
from tools.registry import ToolRegistry


def runaway_auto_chain_notice() -> str:
    return "交付尚未完成，正在自动续接下一回合…"


def harness_stale_proposals_chain_line() -> str:
    return (
        "[Harness] 上一轮 plan_partner 提案未能写入磁盘。"
        "请用 read_text + patch_file/write_text 直接修改 VERIFY.md、MAP.md 等计划域文件；"
        "不要调用 plan_partner，不要等待用户审阅。"
    )


def runaway_mode_toggle_notice(*, enabled: bool, resumed: bool) -> str:
    if not enabled:
        return "狂奔已关闭：后续步骤需你在聊天中手动推进。"
    if resumed:
        return "狂奔已恢复：将自动在当前项目内继续执行。"
    return "狂奔已开启：将自动在当前项目内连续执行。"


def runaway_duplicate_lease_notice() -> str:
    return (
        "狂奔租约未释放，无法启动新回合（界面可能显示待命）。"
        "已尝试自动清理；请再点一次「继续狂奔」，或重启桌面端。"
    )


def runaway_resume_busy_notice() -> str:
    return "狂奔已在执行中，请查看上方活动区；无需重复点击继续。"


def runaway_resume_lock_timeout_notice() -> str:
    return "上一回合仍占用狂奔通道，可点「停止续接」取消等待，或稍后再试「继续狂奔」。"


def runaway_turn_start_notice() -> str:
    return "正在启动狂奔回合…"


def runaway_paused_notice(reason: str) -> str:
    text = (reason or "需要人工处理").strip()
    return f"狂奔已暂停：{text}"


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

    runaway = bool(getattr(session.meta, "project_runaway_enabled", False))
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
    if runaway:
        lines.extend(
            [
                "狂奔若仍开启会自动续接；也可点侧栏「继续狂奔」或「定向再试」。",
                "",
                "常见原因：",
                "· 只在查看文件/搜索，没有写入 PROJECT/DESIGN/TASKS/VERIFY 或运行验收命令",
                "· run_command 的工作目录无效或被安全策略拦截",
                "· 反复口头描述计划，但没有实际调用工具",
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


def plan_domain_write_blocked(*, runaway: bool = False) -> str:
    if runaway:
        return (
            "当前验收项不允许直接修改该计划文件。"
            "请查看侧栏当前焦点与允许修改的范围（如 VERIFY.md、ENV.md）；"
            "或点「定向再试」缩小修改范围。"
        )
    return (
        "计划文件（PROJECT / TASKS / ENV 等）须通过侧栏「方案搭档」提案并采纳后修改；"
        "或使用 report_progress 勾选已完成任务。"
    )


def harness_resume_user_line(*, focus: str, failure_tail: str = "") -> str:
    """Model routing line — keeps ``[Harness]`` prefix for intent detection."""
    line = (
        f"[Harness] 用户已点继续狂奔。请直接处理 {focus}："
        "用 read_text / write_text / patch_file / run_command 修复并补充 VERIFY 证据。"
        "不要要求用户等待或再次点击继续。"
    )
    if failure_tail:
        line += f" 上轮失败摘要：{failure_tail[:240]}"
    return line


def harness_chain_user_line(*, task_hint: str, verifying: bool = False) -> str:
    if verifying:
        return (
            "[Harness] 实现任务已清空，进入验收阶段。"
            "请处理验收清单中的未通过项，补齐 ENV/VERIFY 证据并运行 PROJECT 中的验收命令。"
            "直接调用工具，不要等待用户回复。"
        )
    return (
        f"[Harness] 狂奔继续。当前任务：{task_hint}。"
        "请完成实现、测试与 VERIFY 证据；直接调用工具，不要等待用户回复。"
    )


def runaway_v2_chain_user_line(*, phase: str, focus: str, task_hint: str) -> str:
    """Stable v2 continuation ritual; the Harness prefix keeps routing compatible."""
    return (
        f"[Harness] [狂奔续接] 先读 RUNAWAY-PROGRESS.md 与验收清单，不要重复已完成项。"
        f"当前阶段：{phase}。焦点：{focus}。{task_hint}"
        "直接调用工具，不要等待用户回复。"
    )


def harness_hard_verify_passed_notice(*, target: str) -> str:
    if target == "release_wait":
        return "硬验收已通过，进入发布等待。"
    return "硬验收已通过，进入验收收尾。"


def harness_verify_passed_notice() -> str:
    return "验收已通过，正在进入验收收尾阶段。"


def harness_disk_sync_notice() -> str:
    return "磁盘验收已满足，状态已同步。"


def runaway_release_wait_turn_notice() -> str:
    return "狂奔已到发布等待，本回合跳过模型调用。"


def runaway_release_wait_assistant_text() -> str:
    return (
        "狂奔已到发布等待：正式任务与硬验收均已完成。"
        "本回合不再调用模型；等待发布确认或手动关闭狂奔即可。"
    )


def runaway_verification_exit_notice() -> str:
    return "狂奔正在处理验收收尾（验收矩阵、质量命令与硬验收）。"


def runaway_verification_handled_notice() -> str:
    return "狂奔已处理验收配置与自动修复，正在复验。"


def runaway_verification_harness_skip_notice() -> str:
    return "狂奔已处理验收配置与自动修复，本回合跳过主对话（避免重复修改计划文件）。"


def runaway_verification_harness_assistant_text() -> str:
    return (
        f"{runaway_verification_exit_notice()}"
        "本回合已跳过主对话；若仍阻塞请查看过程里的自动修复或重启 Desktop。"
    )


def harness_process_resume_line() -> str:
    return (
        "[Harness] 进程已恢复。请从当前狂奔进度继续执行，"
        "直接调用工具，不要等待用户回复。"
    )


def runaway_bugfix_start_notice() -> str:
    return "验收未通过，正在自动修复并复验…"


def runaway_bugfix_spawn_notice() -> str:
    return "启动自动修复子代理…"


def runaway_chain_continue_line(*, task_hint: str, final_text: str = "") -> str:
    """Model routing line for v1 auto-chain continuation."""
    line = (
        f"[Harness] 狂奔继续。当前任务：{task_hint}。"
        "请完成实现、测试与 VERIFY 证据；直接调用工具，不要等待用户回复。"
    )
    if final_text:
        line += f" 上一段回复：{final_text[:400]}"
    return line


def runaway_chain_verifying_line() -> str:
    return (
        "[Harness] 实现任务已清空，进入验收阶段。"
        "请处理验收矩阵、ENV 质量命令与硬验收；"
        "主 Agent 勿直接写 PROJECT/ENV，不要调用 plan_partner。"
    )
