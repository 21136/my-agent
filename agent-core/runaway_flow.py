"""State and checkpoint primitives for the project runaway runner."""

from __future__ import annotations

import hashlib
from typing import Any

RUNAWAY_CHECKPOINTS = frozenset(
    {
        "idle",
        "preparing",
        "implementing",
        "verifying",
        "repairing",
        "release_wait",
        "paused",
        "completed",
    }
)

_LEGACY_CHECKPOINTS = {
    "implementation": "implementing",
    "verification": "verifying",
    "verification_passed": "release_wait",
}

_ALLOWED_TRANSITIONS = {
    "idle": {"idle", "preparing", "paused"},
    "preparing": {"preparing", "implementing", "verifying", "paused"},
    "implementing": {"implementing", "verifying", "paused"},
    "verifying": {"verifying", "repairing", "release_wait", "paused"},
    "repairing": {"repairing", "verifying", "paused"},
    "release_wait": {"release_wait", "completed", "paused"},
    "paused": {"paused", "preparing", "implementing", "verifying"},
    "completed": {"completed", "idle"},
}

_CHECKPOINT_LABELS = {
    "idle": "已授权，等待启动",
    "preparing": "正在准备项目",
    "implementing": "正在实现任务",
    "verifying": "正在验证交付",
    "repairing": "正在修复验证问题",
    "release_wait": "验证通过，等待发布确认",
    "paused": "已暂停",
    "completed": "已完成",
}


def normalize_checkpoint(value: Any) -> str:
    """Return a canonical checkpoint while accepting pre-state-machine values."""
    raw = str(value or "").strip().lower()
    if not raw:
        return "idle"
    canonical = _LEGACY_CHECKPOINTS.get(raw, raw)
    return canonical if canonical in RUNAWAY_CHECKPOINTS else "idle"


def checkpoint_label(value: Any) -> str:
    return _CHECKPOINT_LABELS[normalize_checkpoint(value)]


def checkpoint_transition_allowed(current: Any, target: str) -> bool:
    if not str(current or "").strip():
        return normalize_checkpoint(target) in RUNAWAY_CHECKPOINTS
    source = normalize_checkpoint(current)
    destination = normalize_checkpoint(target)
    return destination in _ALLOWED_TRANSITIONS[source]


def transition_checkpoint(
    meta: Any,
    target: str,
    *,
    paused_reason: str | None = None,
) -> str:
    """Atomically update a checkpoint and its user-actionable pause reason."""
    destination = normalize_checkpoint(target)
    current_raw = getattr(meta, "project_runaway_checkpoint", "")
    if not checkpoint_transition_allowed(current_raw, destination):
        source = normalize_checkpoint(current_raw)
        raise ValueError(f"invalid runaway checkpoint transition: {source} -> {destination}")
    meta.project_runaway_checkpoint = destination
    if destination == "paused":
        meta.project_runaway_paused_reason = str(paused_reason or "需要人工处理").strip()
    elif paused_reason is not None or destination != "paused":
        meta.project_runaway_paused_reason = ""
    return destination


def pause_runaway(
    meta: Any,
    reason: str,
    *,
    error: str = "",
    error_fingerprint: str = "",
) -> str:
    """Persist a consistent pause record for an operator-actionable stop."""
    transition_checkpoint(meta, "paused", paused_reason=reason)
    if error:
        meta.project_runaway_last_error = str(error).strip()[:500]
    if error_fingerprint:
        meta.project_runaway_last_error_fingerprint = str(error_fingerprint).strip()
    return "paused"


def task_fingerprint(task_id: str | None, task_text: str | None) -> str:
    payload = f"{str(task_id or '').strip().upper()}|{str(task_text or '').strip()}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def checkpoint_for_stage(stage: Any) -> str:
    return {
        "requirements": "preparing",
        "documentation": "preparing",
        "design": "preparing",
        "implementation": "implementing",
        "verification": "verifying",
        "release": "release_wait",
    }.get(str(stage or "").strip(), "idle")
