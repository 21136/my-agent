"""Single source of truth for runaway v2 continuation decisions."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Literal

from paths import AgentPaths
from project_mode import project_dir, read_project_artifacts
from runaway_v2.checklist import Checklist, ChecklistItem, first_open_item, load_or_build_checklist
from runaway_v2.config import directed_after, max_auto_turns, no_progress_turns
from runaway_v2.phase import PhaseResult, derive_phase
from user_copy import runaway_v2_chain_user_line

ContinuationPhase = Literal["prepare", "implement", "verify"]
ContinuationMode = Literal["auto", "directed"]

_FATAL_FINISH_REASONS = frozenset(
    {
        "cancelled",
        "timeout",
        "error",
        "context_switched",
        "runaway_paused",
        "runaway_duplicate",
        "runaway_verification_exit",
    }
)


@dataclass(frozen=True, slots=True)
class PendingWork:
    """Work that remains after the current turn and can still auto-continue."""

    phase: ContinuationPhase
    focus_item_id: str | None
    active_task_id: str | None
    mode: ContinuationMode
    user_line: str
    chain_user_line: str
    blocked_reason: str | None = None


_IGNORED_DIRS = frozenset({".agent", ".git", ".plan-agent", "node_modules", "__pycache__", ".pytest_cache"})
_IGNORED_FILES = frozenset({"RUNAWAY-PROGRESS.md"})


def project_state_fingerprint(
    paths: AgentPaths,
    project_id: str,
    session: Any,
    *,
    checklist: Checklist | None = None,
) -> str:
    """Hash observable project state while ignoring harness bookkeeping files."""
    pid = str(project_id or "").strip()
    root = project_dir(paths, pid)
    checklist = checklist or load_or_build_checklist(paths, pid)
    phase = derive_phase(
        paths,
        pid,
        plan_status=str(getattr(getattr(session, "meta", session), "project_plan_status", "draft") or "draft"),
        checklist=checklist,
    )
    digest = hashlib.sha256()
    digest.update(
        json.dumps(
            {
                "plan_status": str(
                    getattr(getattr(session, "meta", session), "project_plan_status", "draft")
                    or "draft"
                ),
                "phase": phase.phase,
                "active_task_id": phase.active_task_id,
                "checklist": [
                    {
                        "id": item.id,
                        "status": item.status,
                        "directed_used": item.directed_used,
                    }
                    for item in checklist.items
                ],
                "artifacts": read_project_artifacts(paths, pid),
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    )
    if root.is_dir():
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            relative_parts = path.relative_to(root).parts
            if any(part in _IGNORED_DIRS for part in relative_parts):
                continue
            if path.name in _IGNORED_FILES:
                continue
            try:
                content = path.read_bytes()
            except OSError:
                continue
            digest.update("/".join(relative_parts).encode("utf-8", errors="replace"))
            digest.update(hashlib.sha256(content).digest())
    return digest.hexdigest()[:32]


def reset_continuation_guard(session: Any) -> bool:
    """Clear persisted loop protection after an explicit resume."""
    meta = getattr(session, "meta", session)
    changed = bool(
        getattr(meta, "project_runaway_v2_last_state_fingerprint", "")
        or int(getattr(meta, "project_runaway_v2_no_progress_turns", 0) or 0)
        or int(getattr(meta, "project_runaway_v2_auto_turns", 0) or 0)
    )
    meta.project_runaway_v2_last_state_fingerprint = ""
    meta.project_runaway_v2_no_progress_turns = 0
    meta.project_runaway_v2_auto_turns = 0
    return changed


def _pause_for_continuation_guard(session: Any, reason: str) -> None:
    from runaway_flow import pause_runaway
    from session import utc_now_iso

    meta = getattr(session, "meta", session)
    pause_runaway(meta, reason)
    meta.project_runaway_v2_phase = "human"
    meta.project_runaway_v2_mode = "human"
    meta.project_runaway_v2_user_line = f"狂奔已暂停：{reason}"
    meta.project_runaway_v2_blocked = True
    meta.updated_at = utc_now_iso()
    save = getattr(session, "save", None)
    if callable(save):
        save()


def observe_runaway_turn(
    paths: AgentPaths,
    project_id: str,
    session: Any,
    *,
    automatic: bool,
    checklist: Checklist | None = None,
) -> bool:
    """Record one completed v2 turn and decide whether automatic work may continue."""
    meta = getattr(session, "meta", session)
    if not bool(getattr(meta, "project_runaway_enabled", False)):
        return False
    if str(getattr(meta, "project_runaway_paused_reason", "") or "").strip():
        return False

    pending = pending_runaway_work(paths, project_id, session, checklist=checklist)
    if pending is None:
        return False

    current = project_state_fingerprint(paths, project_id, session, checklist=checklist)
    previous = str(getattr(meta, "project_runaway_v2_last_state_fingerprint", "") or "")
    if automatic:
        no_progress = (
            int(getattr(meta, "project_runaway_v2_no_progress_turns", 0) or 0) + 1
            if previous == current
            else 0
        )
        auto_turns = int(getattr(meta, "project_runaway_v2_auto_turns", 0) or 0) + 1
    else:
        no_progress = 0
        auto_turns = int(getattr(meta, "project_runaway_v2_auto_turns", 0) or 0)
    meta.project_runaway_v2_last_state_fingerprint = current
    meta.project_runaway_v2_no_progress_turns = no_progress
    meta.project_runaway_v2_auto_turns = auto_turns

    if no_progress >= no_progress_turns():
        _pause_for_continuation_guard(
            session,
            f"连续 {no_progress} 个自动回合没有检测到项目状态变化",
        )
        return False
    if auto_turns >= max_auto_turns():
        _pause_for_continuation_guard(session, "自动回合预算已用尽")
        return False

    from session import utc_now_iso

    meta.updated_at = utc_now_iso()
    save = getattr(session, "save", None)
    if callable(save):
        save()
    return True


def repair_stale_v2_plan_status(paths: AgentPaths, session: Any) -> bool:
    """Repair the old compatibility hook's ``plan_dirty`` after v2 finished.

    This is deliberately narrow: persisted runtime state must already identify
    ``release_wait``, and a fresh derivation with a confirmed plan must still
    agree. That makes the repair safe for the stale-state regression without
    hiding a genuinely reopened project.
    """
    from project_mode import snapshot_plan_fingerprints
    from runaway_flow import normalize_checkpoint
    from runaway_v2 import runaway_v2_enabled

    meta = getattr(session, "meta", session)
    if not runaway_v2_enabled(session):
        return False
    if str(getattr(meta, "project_plan_status", "") or "") != "plan_dirty":
        return False
    pid = str(getattr(meta, "project_id", "") or "").strip()
    if not pid:
        return False
    checkpoint = normalize_checkpoint(getattr(meta, "project_runaway_checkpoint", ""))
    v2_phase = str(getattr(meta, "project_runaway_v2_phase", "") or "").strip()
    if checkpoint != "release_wait" and v2_phase != "release_wait":
        return False

    checklist = load_or_build_checklist(paths, pid)
    phase = derive_phase(paths, pid, plan_status="confirmed", checklist=checklist)
    if phase.phase != "release_wait":
        return False

    from session import utc_now_iso

    meta.project_plan_status = "confirmed"
    snapshot_plan_fingerprints(session, paths, pid)
    meta.updated_at = utc_now_iso()
    session.save()
    return True


def _item_mode(item: ChecklistItem) -> ContinuationMode | None:
    threshold = directed_after()
    if item.directed_used and item.attempts >= threshold:
        return None
    if item.attempts >= threshold and not item.directed_used:
        return "directed"
    return "auto"


def _phase_user_line(
    phase: str,
    *,
    phase_result: PhaseResult,
    item: ChecklistItem | None,
) -> str:
    if phase == "prepare":
        missing = "、".join(phase_result.blockers) if phase_result.blockers else "项目文档"
        return f"正在整理项目文档（缺：{missing}）"
    if phase == "implement":
        task_id = phase_result.active_task_id or "下一任务"
        return f"正在实现 {task_id}"
    if item is not None:
        return item.title or f"正在处理验收项 {item.id}"
    return "正在处理验收清单"


def _pending_from_phase(
    phase_result: PhaseResult,
    checklist: Checklist,
) -> PendingWork | None:
    phase = phase_result.phase
    if phase == "prepare":
        user_line = _phase_user_line(phase, phase_result=phase_result, item=None)
        return PendingWork(
            phase="prepare",
            focus_item_id="PREPARE",
            active_task_id=None,
            mode="auto",
            user_line=user_line,
            chain_user_line=runaway_v2_chain_user_line(
                phase="prepare",
                focus=user_line,
                task_hint=user_line,
            ),
        )
    if phase == "implement":
        task_id = phase_result.active_task_id
        if not task_id:
            return None
        user_line = _phase_user_line(phase, phase_result=phase_result, item=None)
        return PendingWork(
            phase="implement",
            focus_item_id=None,
            active_task_id=task_id,
            mode="auto",
            user_line=user_line,
            chain_user_line=runaway_v2_chain_user_line(
                phase="implement",
                focus=task_id,
                task_hint=user_line,
            ),
        )
    if phase != "verify":
        return None

    item = first_open_item(checklist)
    if item is None:
        return None
    mode = _item_mode(item)
    if mode is None:
        return None
    user_line = _phase_user_line(phase, phase_result=phase_result, item=item)
    return PendingWork(
        phase="verify",
        focus_item_id=item.id,
        active_task_id=None,
        mode=mode,
        user_line=user_line,
        chain_user_line=runaway_v2_chain_user_line(
            phase="verify",
            focus=f"{item.id} · {item.title}",
            task_hint=user_line,
        ),
    )


def pending_runaway_work(
    paths: AgentPaths,
    project_id: str,
    session: Any,
    *,
    checklist: Checklist | None = None,
) -> PendingWork | None:
    """Derive remaining v2 work from project artifacts and checklist on disk."""
    meta = getattr(session, "meta", session)
    if not bool(getattr(meta, "project_runaway_enabled", False)):
        return None
    if not str(project_id or getattr(meta, "project_id", "") or "").strip():
        return None
    if str(getattr(meta, "project_runaway_paused_reason", "") or "").strip():
        return None
    cancel_event = getattr(session, "cancel_event", None)
    if cancel_event is not None and cancel_event.is_set():
        return None

    pid = str(project_id or getattr(meta, "project_id", "") or "").strip()
    # Repair the narrow stale mirror before any caller derives continuation.
    # This covers server-side chain decisions as well as direct controller
    # entry, so a legacy plan hook cannot manufacture another prepare turn.
    repair_stale_v2_plan_status(paths, session)
    current = checklist or load_or_build_checklist(paths, pid)
    phase = derive_phase(
        paths,
        pid,
        plan_status=str(getattr(meta, "project_plan_status", "") or "draft"),
        checklist=current,
    )
    if phase.phase == "human":
        return None
    if phase.phase == "release_wait":
        return None
    return _pending_from_phase(phase, current)


def should_continue_runaway(
    paths: AgentPaths,
    project_id: str,
    session: Any,
    *,
    finish_reason: str | None = None,
    in_turn_depth: int = 0,
    max_in_turn_depth: int = 12,
    checklist: Checklist | None = None,
    observe: bool = True,
    automatic: bool = True,
) -> bool:
    """Return whether another v2 turn should be started."""
    if in_turn_depth >= max(0, int(max_in_turn_depth)):
        return False
    if (finish_reason or "").strip() in _FATAL_FINISH_REASONS:
        return False
    if observe:
        return observe_runaway_turn(
            paths,
            project_id,
            session,
            automatic=automatic,
            checklist=checklist,
        )
    return pending_runaway_work(
        paths,
        project_id,
        session,
        checklist=checklist,
    ) is not None


def is_fatal_finish_reason(finish_reason: str | None) -> bool:
    """Expose the channel-level fatal set without duplicating string literals."""
    return (finish_reason or "").strip() in _FATAL_FINISH_REASONS
