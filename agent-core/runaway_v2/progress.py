"""Append-only RUNAWAY-PROGRESS.md entries."""

from __future__ import annotations

from datetime import datetime, timezone

from paths import AgentPaths
from runaway_v2.checklist import ChecklistItem, progress_path


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def append_progress(
    paths: AgentPaths,
    project_id: str,
    *,
    phase: str,
    item: ChecklistItem | None,
    status: str,
    summary: str,
    next_focus: str,
) -> None:
    path = progress_path(paths, project_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    item_id = item.id if item else "-"
    section = (
        f"\n## {_utc_now()} · {phase} · {item_id} · {status}\n"
        f"摘要：{summary.strip() or '（无）'}\n"
        f"下一焦点：{next_focus.strip() or '（无）'}\n"
    )
    if path.is_file():
        existing = path.read_text(encoding="utf-8")
    else:
        existing = f"# {project_id} · Runaway 进度\n"
    path.write_text(existing.rstrip() + section, encoding="utf-8")
