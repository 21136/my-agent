"""report_progress — tell Plan Agent what was accomplished so it updates TASKS.md.

Called by the main agent instead of writing TASKS.md directly.
Plan Agent validates the report, updates checkboxes, adds subtasks,
and runs quality checks (duplicates, granularity, anomalies).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

_AGENT_CORE = Path(__file__).resolve().parents[4] / "agent-core"
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from paths import AgentPaths
from plan_agent import get_plan_agent
from project_mode import resolve_progress_task_line


def run(args: dict[str, Any]) -> dict[str, Any]:
    paths = AgentPaths.discover()
    project_id = args.get("project_id", "").strip()
    if not project_id:
        return {"ok": False, "error": "project_id is required"}

    agent = get_plan_agent(paths, project_id)
    summary = args.get("summary", "").strip()
    task_line = args.get("task_line")
    task_id = args.get("task_id")
    subtasks = args.get("subtasks", [])
    add_tasks = args.get("add_tasks", [])
    skip_tasks = args.get("skip_tasks", [])

    raw_line = task_line if isinstance(task_line, int) else None
    tid = task_id.strip() if isinstance(task_id, str) else None
    task_text = args.get("task_text")
    text = task_text.strip() if isinstance(task_text, str) else None
    resolved_line, resolve_note = resolve_progress_task_line(
        paths,
        project_id,
        task_line=raw_line,
        task_id=tid,
        summary=summary,
        task_text=text,
    )

    # 1. Toggle the completed task (identity-stable: T-xxx beats stale line)
    toggle_result: dict[str, Any] | None = None
    if isinstance(resolved_line, int) and resolved_line >= 0:
        try:
            toggle_result = agent.toggle_task(resolved_line, True)
        except Exception as exc:
            return {
                "ok": False,
                "error": f"toggle_task line {resolved_line}: {exc}",
                "resolved_line": resolved_line,
                "resolve_note": resolve_note,
            }
    if toggle_result:
        from project_mode import normalize_delivery_profile

        profile = normalize_delivery_profile(args.get("delivery_profile") or "solo")
        agent._emit_milestone_review_if_needed(
            toggle_result, delivery_profile=profile
        )

    # 2. Propose subtasks / discovered tasks (PLAN-ARCH Q1 — no auto write)
    proposed: list[str] = []
    for desc in subtasks:
        if isinstance(desc, str) and desc.strip():
            d = desc.strip()
            key = f"rp-sub-{abs(hash(d)) % 10_000_000:x}"
            sug = agent._suggestion(
                kind="add_task",
                title="子任务（待采纳）",
                body=f"主 Agent 建议子任务：{d[:80]}",
                key=key,
                risk="gate",
                action="add_task",
                payload={
                    "phase": "",
                    "description": d,
                    "source": "report_progress_subtask",
                },
            )
            agent.park_gated_suggestion(sug)
            proposed.append(d)

    for desc in add_tasks:
        if isinstance(desc, str) and desc.strip():
            d = desc.strip()
            key = f"rp-add-{abs(hash(d)) % 10_000_000:x}"
            sug = agent._suggestion(
                kind="add_task",
                title="新增任务（待采纳）",
                body=f"主 Agent 建议添加：{d[:80]}",
                key=key,
                risk="gate",
                action="add_task",
                payload={
                    "phase": "",
                    "description": d,
                    "source": "report_progress_add",
                },
            )
            agent.park_gated_suggestion(sug)
            proposed.append(d)

    # 3. Skip tasks — also gated via suggestion when from report_progress
    for line in skip_tasks:
        if isinstance(line, int) and line >= 0:
            sug = agent._suggestion(
                kind="skip_task",
                title="暂缓任务（待采纳）",
                body=f"主 Agent 建议暂缓行 {line}",
                key=f"rp-skip-{line}",
                risk="gate",
                action="skip_task",
                payload={"line": line, "source": "report_progress"},
            )
            agent.park_gated_suggestion(sug)

    # 4. Run quality checks
    warnings = agent.quality_check()
    if resolve_note:
        warnings = list(warnings) + [resolve_note]
    if proposed:
        warnings = list(warnings) + [
            f"已提案 {len(proposed)} 条新增（未写盘）；审阅后才写入 TASKS.md"
        ]

    # 5. Get next task
    next_task = agent.next_task_text()

    # 6. Save state
    agent._save_state()

    return {
        "ok": True,
        "summary": summary,
        "tasks_done": agent._current_stats().done,
        "tasks_total": agent._current_stats().total,
        "next_task": next_task,
        "warnings": warnings,
        "resolved_line": resolved_line,
        "resolve_note": resolve_note,
        "proposed_adds": proposed,
        "pending_gated": len(agent._pending_gated),
    }


if __name__ == "__main__":
    try:
        raw = sys.stdin.read()
        args = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        args = {}
    result = run(args)
    print(json.dumps(result, ensure_ascii=False))
