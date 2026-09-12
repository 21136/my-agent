"""Runaway v2 phase derivation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from paths import AgentPaths
from project_mode import (
    formal_task_id_from_checkbox_line,
    formal_task_stats,
    count_archive_entries,
    iter_tasks_lines_skipping_closed,
    next_open_task,
    plan_allows_code_writes,
    project_dir,
    read_project_artifacts,
    task_dependency_blockers,
)
from project_release import load_release_acceptance
from project_manifest import load_manifest, project_manifest_path
from runaway_v2.checklist import (
    Checklist,
    checklist_all_passed,
    load_or_build_checklist,
)

PhaseName = Literal["prepare", "implement", "verify", "release_wait", "human"]


@dataclass(frozen=True, slots=True)
class PhaseResult:
    phase: PhaseName
    human_reason: str = ""
    blockers: tuple[str, ...] = field(default_factory=tuple)
    active_task_id: str | None = None
    release_accepted: bool = False


def _release_revision(paths: AgentPaths, project_id: str) -> str | None:
    try:
        manifest = load_manifest(project_manifest_path(paths, project_id))
    except Exception:
        return None
    for artifact in (manifest or {}).get("artifacts", []):
        if (
            isinstance(artifact, dict)
            and artifact.get("path") == "RELEASE.md"
            and artifact.get("status") == "current"
        ):
            revision = str(artifact.get("revision") or "").strip()
            return revision or None
    return None


def _documentation_ready(
    artifacts: dict[str, str],
    *,
    archived_done: int = 0,
) -> tuple[bool, list[str]]:
    tasks_text = artifacts.get("TASKS.md", "")
    visible_task_lines = iter_tasks_lines_skipping_closed(tasks_text)
    checks = {
        "PROJECT.md": all(token in artifacts.get("PROJECT.md", "") for token in ("REQ-", "AC-")),
        "DESIGN.md": all(token in artifacts.get("DESIGN.md", "") for token in ("UX-", "TD-")),
        "TASKS.md": any(
            formal_task_id_from_checkbox_line(line)
            for _line_no, line in visible_task_lines
        ) or archived_done > 0,
        "VERIFY.md": all(token in artifacts.get("VERIFY.md", "") for token in ("V-", "AC-")),
    }
    missing = [name for name, ok in checks.items() if not ok]
    return not missing, missing


def derive_phase(
    paths: AgentPaths,
    project_id: str,
    *,
    plan_status: str,
    checklist: Checklist | None = None,
) -> PhaseResult:
    pid = str(project_id or "").strip()
    if not pid:
        return PhaseResult(phase="human", human_reason="未绑定项目")

    artifacts = read_project_artifacts(paths, pid)
    tasks_text = artifacts.get("TASKS.md", "")
    root = project_dir(paths, pid)
    archived_done = count_archive_entries(root / "TASKS.archive.md", reason="done")
    stats = formal_task_stats(tasks_text, archive_done=archived_done)
    checklist = checklist or load_or_build_checklist(paths, pid)

    release = load_release_acceptance(
        project_dir(paths, pid),
        pid,
        release_revision=_release_revision(paths, pid),
    )
    docs_ready, missing_docs = _documentation_ready(artifacts, archived_done=archived_done)
    if (
        docs_ready
        and plan_allows_code_writes(plan_status)
        and checklist_all_passed(checklist)
        and stats.open_count == 0
    ):
        return PhaseResult(
            phase="release_wait",
            release_accepted=bool(release.get("accepted")),
        )

    _line_index, _body, next_task = next_open_task(tasks_text)
    if next_task:
        return PhaseResult(phase="implement", active_task_id=next_task)

    blockers = task_dependency_blockers(tasks_text)
    if blockers:
        lines = [f"{task_id} 依赖未满足：{', '.join(deps)}" for task_id, deps in sorted(blockers.items())]
        return PhaseResult(
            phase="human",
            human_reason="存在开放任务但依赖未满足，无法自动推进",
            blockers=tuple(lines),
        )

    blocked_item = next(
        (item for item in checklist.items if item.status == "blocked"),
        None,
    )
    if blocked_item is not None:
        return PhaseResult(
            phase="human",
            human_reason=f"验收项 {blocked_item.id} 已阻塞，需要人工处理",
            blockers=(blocked_item.id,),
        )

    if not docs_ready:
        return PhaseResult(
            phase="prepare",
            blockers=tuple(missing_docs),
        )
    if not plan_allows_code_writes(plan_status):
        return PhaseResult(
            phase="prepare",
            blockers=("plan_status",),
        )

    return PhaseResult(phase="verify")
