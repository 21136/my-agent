"""State and checkpoint primitives for the project runaway runner."""

from __future__ import annotations

import hashlib
import re
from typing import Any

_RUNAWAY_VERIFY_ID_RE = re.compile(r"\bV-\d+(?:-\d+)*\b", re.IGNORECASE)

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


def checkpoint_resume_path(source: Any, destination: Any) -> list[str]:
    """Shortest allowed checkpoint hops from ``source`` to ``destination``."""
    start = normalize_checkpoint(source)
    goal = normalize_checkpoint(destination)
    if start == goal:
        return []
    queue: list[tuple[str, list[str]]] = [(start, [])]
    seen = {start}
    while queue:
        node, hops = queue.pop(0)
        for neighbor in sorted(_ALLOWED_TRANSITIONS[node]):
            if neighbor in seen:
                continue
            if neighbor == "paused" and start != "paused" and goal != "paused":
                continue
            next_hops = hops + [neighbor]
            if neighbor == goal:
                return next_hops
            seen.add(neighbor)
            queue.append((neighbor, next_hops))
    return []


def sync_checkpoint_to_target(meta: Any, target: Any) -> str:
    """Walk allowed transitions until ``meta`` reaches ``target``."""
    destination = normalize_checkpoint(target)
    for hop in checkpoint_resume_path(
        getattr(meta, "project_runaway_checkpoint", ""),
        destination,
    ):
        transition_checkpoint(meta, hop)
    return normalize_checkpoint(getattr(meta, "project_runaway_checkpoint", ""))


def sync_checkpoint_to_stage(meta: Any, stage: Any) -> str:
    """Resume ``meta`` checkpoint to match an authoritative workflow stage."""
    return sync_checkpoint_to_target(meta, checkpoint_for_stage(stage))


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


def verify_ids_recorded(verify_text: str, verify_ids: list[str]) -> bool:
    """True when every verify id appears in VERIFY.md (partial pass counts)."""
    haystack = (verify_text or "").upper()
    if not haystack:
        return False
    for raw in verify_ids:
        token = str(raw or "").strip().upper()
        if not token or token not in haystack:
            return False
    return True


def verify_task_documented(
    verify_text: str,
    task_id: str,
    verify_ids: list[str] | None = None,
) -> bool:
    """True when VERIFY.md records this task (and optional V-* ids)."""
    tid = str(task_id or "").strip().upper()
    if not tid or not (verify_text or "").strip():
        return False
    wanted = [str(value).strip().upper() for value in (verify_ids or []) if str(value).strip()]
    for line in (verify_text or "").splitlines():
        upper = line.upper()
        if tid not in upper:
            continue
        if wanted:
            if all(token in upper for token in wanted):
                return True
            continue
        if _RUNAWAY_VERIFY_ID_RE.search(line):
            return True
    if wanted:
        return verify_ids_recorded(verify_text, wanted)
    return False


def runaway_task_has_advance_evidence(
    *,
    task_id: str,
    tasks_text: str,
    turn_evidence: list[dict[str, Any]],
    verify_text: str = "",
) -> bool:
    """Require VERIFY documentation or successful bound tool evidence before advance."""
    from progress_gate import classify_task_evidence_kind, evidence_satisfies, task_evidence_contract
    from project_mode import inline_verify_ids

    tid = str(task_id or "").strip().upper()
    if not tid:
        return False
    contract = task_evidence_contract(tasks_text, task_id=tid) or {}
    task_text = str(contract.get("task_text") or tid)
    ac_ids = list(contract.get("ac_ids") or [])
    verify_ids = list(contract.get("verify_ids") or []) or inline_verify_ids(task_text)
    kind = classify_task_evidence_kind(task_text)
    ok, _note = evidence_satisfies(
        kind,
        turn_evidence,
        task_id=tid,
        ac_ids=ac_ids or None,
        verify_ids=verify_ids or None,
    )
    if ok:
        return True
    for entry in turn_evidence:
        if not entry.get("ok"):
            continue
        if str(entry.get("task_id") or "").strip().upper() == tid:
            return True
    if verify_task_documented(verify_text, tid, verify_ids or None):
        return True
    return False


def runaway_task_number(task_id: str | None) -> int | None:
    match = re.search(r"T-(\d+)", str(task_id or ""), re.IGNORECASE)
    if not match:
        return None
    return int(match.group(1))


def strip_nonformal_open_task_lines(tasks_text: str) -> tuple[str, int]:
    """Drop open checkbox lines that do not start with a formal ``T-*`` id."""
    from project_mode import _TASK_OPEN_RE, formal_task_id_from_checkbox_line

    stripped = 0
    kept: list[str] = []
    for line in (tasks_text or "").splitlines():
        if _TASK_OPEN_RE.match(line) and not formal_task_id_from_checkbox_line(line):
            stripped += 1
            continue
        kept.append(line)
    suffix = "\n" if (tasks_text or "").endswith("\n") else ""
    return "\n".join(kept) + suffix, stripped


def revert_out_of_order_runaway_checkoffs(
    tasks_text: str,
    active_task_id: str,
) -> tuple[str, list[str]]:
    """Re-open formal tasks done ahead of the current active task."""
    from project_mode import _TASK_CHECKBOX_RE, _TASK_DONE_RE, formal_task_id_from_checkbox_line

    active_number = runaway_task_number(active_task_id)
    if active_number is None:
        return tasks_text, []
    reverted: list[str] = []
    lines: list[str] = []
    for line in (tasks_text or "").splitlines():
        if not _TASK_DONE_RE.match(line):
            lines.append(line)
            continue
        task_id = formal_task_id_from_checkbox_line(line)
        task_number = runaway_task_number(task_id)
        if task_number is not None and task_number > active_number:
            match = _TASK_CHECKBOX_RE.match(line)
            body = match.group(1).strip() if match else line.strip()
            lines.append(f"- [ ] {body}")
            if task_id:
                reverted.append(task_id)
            continue
        lines.append(line)
    suffix = "\n" if (tasks_text or "").endswith("\n") else ""
    return "\n".join(lines) + suffix, reverted


def sanitize_runaway_tasks_artifact(
    paths: Any,
    project_id: str,
    active_task_id: str,
) -> dict[str, Any]:
    """Normalize TASKS.md after runaway plan adoption."""
    from project_mode import project_dir

    pid = str(project_id or "").strip()
    active = str(active_task_id or "").strip().upper()
    if not pid or not active:
        return {"changed": False, "reverted": [], "stripped": 0}
    tasks_path = project_dir(paths, pid) / "TASKS.md"
    if not tasks_path.is_file():
        return {"changed": False, "reverted": [], "stripped": 0}
    original = tasks_path.read_text(encoding="utf-8")
    text, reverted = revert_out_of_order_runaway_checkoffs(original, active)
    text, stripped = strip_nonformal_open_task_lines(text)
    changed = text != original
    if changed:
        tasks_path.write_text(text, encoding="utf-8")
    return {
        "changed": changed,
        "reverted": reverted,
        "stripped": stripped,
    }


def sync_runaway_task_checkoff_from_verify(
    paths: Any,
    project_id: str,
    task_id: str,
    *,
    verify_text: str | None = None,
) -> bool:
    """When VERIFY documents a task but TASKS is still open, Harness checks it off (R7-23)."""
    from project_mode import (
        _TASK_CHECKBOX_RE,
        _TASK_OPEN_RE,
        inline_verify_ids,
        parse_tasks_metadata,
        project_dir,
        read_project_artifacts,
    )
    from progress_gate import task_evidence_contract

    tid = str(task_id or "").strip().upper()
    pid = str(project_id or "").strip()
    if not tid or not pid:
        return False
    tasks_path = project_dir(paths, pid) / "TASKS.md"
    if not tasks_path.is_file():
        return False
    tasks_text = tasks_path.read_text(encoding="utf-8")
    if verify_text is None:
        verify_text = read_project_artifacts(paths, pid).get("VERIFY.md", "")
    contract = task_evidence_contract(tasks_text, task_id=tid) or {}
    task_text = str(contract.get("task_text") or tid)
    verify_ids = list(contract.get("verify_ids") or []) or inline_verify_ids(task_text)
    if not verify_task_documented(verify_text, tid, verify_ids or None):
        return False

    lines = tasks_text.splitlines()
    changed = False
    for task in parse_tasks_metadata(tasks_text):
        if str(task.get("id") or "").upper() != tid:
            continue
        idx = int(task.get("line", -1))
        if idx < 0 or idx >= len(lines):
            continue
        line = lines[idx]
        if not _TASK_OPEN_RE.match(line):
            break
        match = _TASK_CHECKBOX_RE.match(line)
        if not match:
            break
        lines[idx] = f"- [x] {match.group(1).strip()}"
        changed = True
        break

    if not changed:
        return False
    suffix = "\n" if tasks_text.endswith("\n") else ""
    tasks_path.write_text("\n".join(lines) + suffix, encoding="utf-8")
    return True


def sync_all_runaway_task_checkoffs_from_verify(
    paths: Any,
    project_id: str,
    *,
    verify_text: str | None = None,
) -> list[str]:
    """Batch VERIFY→TASKS sync for every open formal ``T-*`` row (R7-28)."""
    from project_mode import (
        _TASK_OPEN_RE,
        formal_task_id_from_checkbox_line,
        parse_tasks_metadata,
        project_dir,
        read_project_artifacts,
    )

    pid = str(project_id or "").strip()
    if not pid:
        return []
    tasks_path = project_dir(paths, pid) / "TASKS.md"
    if not tasks_path.is_file():
        return []
    tasks_text = tasks_path.read_text(encoding="utf-8")
    if verify_text is None:
        verify_text = read_project_artifacts(paths, pid).get("VERIFY.md", "")

    synced: list[str] = []
    lines = tasks_text.splitlines()
    for task in parse_tasks_metadata(tasks_text):
        idx = int(task.get("line", -1))
        if idx < 0 or idx >= len(lines):
            continue
        line = lines[idx]
        if not _TASK_OPEN_RE.match(line):
            continue
        tid = formal_task_id_from_checkbox_line(line) or str(task.get("id") or "").strip().upper()
        if not tid.startswith("T-"):
            continue
        if sync_runaway_task_checkoff_from_verify(
            paths,
            pid,
            tid,
            verify_text=verify_text,
        ):
            synced.append(tid)
            tasks_text = tasks_path.read_text(encoding="utf-8")
            lines = tasks_text.splitlines()
    return synced


def reconcile_runaway_tasks_artifact(
    paths: Any,
    project_id: str,
    active_task_id: str,
) -> dict[str, Any]:
    """Sanitize TASKS and sync VERIFY-documented checkoffs for all open formal tasks."""
    result = sanitize_runaway_tasks_artifact(paths, project_id, active_task_id)
    synced_ids = sync_all_runaway_task_checkoffs_from_verify(paths, project_id)
    if synced_ids:
        result = {**result, "changed": True, "checkoff_synced": True, "synced_task_ids": synced_ids}
    return result
