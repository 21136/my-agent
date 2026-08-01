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
    if isinstance(resolved_line, int) and resolved_line >= 0:
        try:
            agent.toggle_task(resolved_line, True)
        except Exception as exc:
            return {
                "ok": False,
                "error": f"toggle_task line {resolved_line}: {exc}",
                "resolved_line": resolved_line,
                "resolve_note": resolve_note,
            }

    # 2. Add subtasks discovered during execution
    for desc in subtasks:
        if isinstance(desc, str) and desc.strip():
            agent._record_change("add", desc.strip(), reason=f"subtask of: {summary[:60]}")

    # 3. Add newly discovered tasks
    for desc in add_tasks:
        if isinstance(desc, str) and desc.strip():
            agent._record_change("add", desc.strip(), reason=f"discovered: {summary[:60]}")

    # 4. Skip tasks
    for line in skip_tasks:
        if isinstance(line, int) and line >= 0:
            try:
                agent.skip_task(line)
            except Exception:
                pass

    # 5. Run quality checks
    warnings = agent.quality_check()
    if resolve_note:
        warnings = list(warnings) + [resolve_note]

    # 6. Get next task
    next_task = agent.next_task_text()

    # 7. Save state
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
    }


if __name__ == "__main__":
    try:
        raw = sys.stdin.read()
        args = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        args = {}
    result = run(args)
    print(json.dumps(result, ensure_ascii=False))
