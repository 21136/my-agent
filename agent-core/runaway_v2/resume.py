"""Runaway v2 resume — code bootstrap before LLM (user clicked 继续狂奔)."""

from __future__ import annotations

from paths import AgentPaths
from runaway_v2.acceptance import run_acceptance
from runaway_v2.checklist import Checklist, first_open_item, save_checklist
from user_copy import harness_resume_user_line


def bootstrap_on_resume(
    paths: AgentPaths,
    project_id: str,
    checklist: Checklist,
) -> tuple[str, bool]:
    """Run idempotent harness fixes for the current open item. Returns system hint + changed."""
    item = first_open_item(checklist)
    if item is None:
        return "", False

    hints: list[str] = []
    changed = False
    rule_id = str((item.acceptance or {}).get("rule_id") or item.id).upper()
    item_id = item.id.upper()

    if item_id.startswith("MX-5") or rule_id.startswith("MX-5"):
        from runaway_verification import ensure_env_quality_commands

        if ensure_env_quality_commands(paths, project_id):
            hints.append("已自动补齐 ENV.md 的 quality.commands。")
            changed = True
        hook = run_acceptance(paths, project_id, item)
        if hook.ok:
            item.status = "passed"
            hints.append(f"验收项 {item.id} 经 bootstrap 已通过。")
            changed = True
        elif hook.tail:
            hints.append(f"MX-5 仍失败：{hook.tail}")

    if item.last_failure:
        tail = str(item.last_failure.get("tail") or "").strip()
        command = str(item.last_failure.get("command") or "").strip()
        if tail:
            hints.append(f"上轮失败（{command or item.id}）：{tail}")
        elif command:
            hints.append(f"上轮命令：{command}")

    hints.append(
        "用户已点击「继续狂奔」——你必须在本轮用工具直接修复或补证据，"
        "不要要求用户再点继续，也不要说等待 Harness 刷新。"
    )
    return "\n".join(hints), changed


def resume_user_line(paths: AgentPaths, project_id: str, checklist: Checklist) -> str:
    item = first_open_item(checklist)
    focus = f"验收项 {item.id}（{item.title}）" if item else "当前检查点"
    tail = ""
    if item and item.last_failure:
        tail = str(item.last_failure.get("tail") or "").strip()
    return harness_resume_user_line(focus=focus, failure_tail=tail)


def apply_resume(paths: AgentPaths, project_id: str) -> tuple[Checklist, str]:
    from runaway_v2.checklist import load_or_build_checklist

    checklist = load_or_build_checklist(paths, project_id)
    hint, changed = bootstrap_on_resume(paths, project_id, checklist)
    if changed:
        save_checklist(paths, checklist)
    user_line = resume_user_line(paths, project_id, checklist)
    return checklist, f"{hint}\n{user_line}" if hint else user_line
