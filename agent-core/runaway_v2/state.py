"""Runaway v2 project.state payload helpers."""

from __future__ import annotations

from typing import Any

from paths import AgentPaths
from runaway_v2.checklist import (
    Checklist,
    checklist_counts,
    first_open_item,
    load_or_build_checklist,
)
from runaway_v2.config import directed_after
from runaway_v2.phase import PhaseResult, derive_phase


def build_v2_state_fields(
    paths: AgentPaths,
    *,
    project_id: str,
    plan_status: str,
    checklist: Checklist | None = None,
    paused_reason: str = "",
) -> dict[str, Any]:
    pid = str(project_id or "").strip()
    if not pid:
        return {}
    checklist = checklist or load_or_build_checklist(paths, pid)
    phase = derive_phase(paths, pid, plan_status=plan_status, checklist=checklist)
    paused_reason = str(paused_reason or "").strip()
    if paused_reason:
        phase = PhaseResult(
            phase="human",
            human_reason=paused_reason,
            blockers=(paused_reason,),
        )
    passed, total = checklist_counts(checklist)
    acceptance_items = [item for item in checklist.items if item.kind == "acceptance"]
    acceptance_passed = bool(acceptance_items) and all(
        item.status == "passed" for item in acceptance_items
    )
    current = first_open_item(checklist)
    failed = next((item for item in checklist.items if item.status == "failed"), None)
    focus = current or failed
    threshold = directed_after()
    if phase.phase == "human":
        mode = "human"
    elif focus is not None and focus.directed_used and focus.attempts >= threshold:
        mode = "human"
    elif focus is not None and focus.attempts >= threshold and not focus.directed_used:
        mode = "directed"
    else:
        mode = "auto"
    blocked = phase.phase == "human" or (
        focus is not None and focus.directed_used and focus.attempts >= threshold
    )
    block: dict[str, Any] | None = None
    if blocked and failed is not None:
        block = {
            "item_id": failed.id,
            "reason": phase.human_reason or "同一验收项定向修复后仍失败",
            "command": (failed.last_failure or {}).get("command", ""),
            "tail": (failed.last_failure or {}).get("tail", ""),
        }
    elif blocked and phase.blockers:
        block = {
            "item_id": None,
            "reason": phase.human_reason,
            "command": "",
            "tail": "\n".join(phase.blockers),
        }
    user_line = str(getattr(phase, "human_reason", "") or "").strip()
    if phase.phase == "implement" and phase.active_task_id:
        user_line = f"正在实现 {phase.active_task_id}"
    elif phase.phase == "verify" and current is not None:
        user_line = current.title or f"正在处理验收项 {current.id}"
    elif phase.phase == "release_wait":
        user_line = (
            "项目已完成"
            if phase.release_accepted
            else "验收清单已全部通过，等待发布确认"
        )
    elif phase.phase == "prepare":
        user_line = "正在整理项目文档"
    elif not user_line and focus is not None:
        user_line = focus.title or f"验收项 {focus.id}"
    if passed and total and passed == total and phase.phase == "verify":
        user_line = "验收清单已全部通过，等待发布确认"
    return {
        "runaway_version": 2,
        "runaway_acceptance_passed": acceptance_passed,
        "runaway_phase": phase.phase,
        "runaway_user_line": user_line,
        "runaway_mode": mode,
        "runaway_blocked": blocked,
        "runaway_checklist": {
            "passed": passed,
            "total": total,
            "current_id": current.id if current else None,
            "current_title": current.title if current else None,
            "failed_id": failed.id if failed else None,
        },
        "runaway_block": block,
    }
