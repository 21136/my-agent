"""Validate explicit workbench schedule dispatch against authorized source previews."""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from multi_project_work_record_bindings import AuthorizedProjectRecordSource, _is_issued_authorized_project_record_source
from project_worker_host import ProjectWorkerHost
from project_worker_limits import schedule_dispatch_batch_max
from work_records_schedule_preview import build_schedule_preview, pick_schedule_batch

_MAX_DISPATCH = schedule_dispatch_batch_max()
_MAX_TASK_CHARS = 8_000
_MAX_TARGETS = 32
_SAFE_RELATIVE_PATH = __import__("re").compile(
    r"^(?!/)(?!.*\.\.)(?!.*[\\:])(?!.*[\0<>:\"|?*~])[A-Za-z0-9._\-/]+$"
)


def active_writing_project_ids(host: ProjectWorkerHost, conversation_id: str) -> frozenset[str]:
    """Distinct project_ids with a live worker slot reserved on this host for one parent session."""
    return host.active_writing_project_ids(conversation_id)


def schedule_advance_limit(host: ProjectWorkerHost, conversation_id: str, *, requested: int) -> int:
    del host, conversation_id  # host retains queueing beyond the live concurrent cap
    if type(requested) is not int or isinstance(requested, bool) or not 1 <= requested <= _MAX_DISPATCH:
        raise ValueError(f"schedule advance limit must be 1..{_MAX_DISPATCH}")
    return min(requested, _MAX_DISPATCH)


def _target_paths(value: Any, *, required: bool) -> tuple[str, ...]:
    if type(value) is not list or len(value) > _MAX_TARGETS:
        raise ValueError("explicit bounded target lists are required")
    if required and not value:
        raise ValueError("at least one explicit write target is required")
    if any(type(path) is not str or not path.strip() for path in value):
        raise ValueError("target paths must be non-empty strings")
    for path in value:
        if len(path) > 240 or not _SAFE_RELATIVE_PATH.fullmatch(path):
            raise ValueError("target paths must be safe project-relative paths")
    if len({path.casefold() for path in value}) != len(value):
        raise ValueError("duplicate target paths are not allowed")
    return tuple(value)


def _schedule_groups(sources: Mapping[str, AuthorizedProjectRecordSource]) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    for project_id, source in sources.items():
        if (not _is_issued_authorized_project_record_source(source)
                or source.project_id != project_id):
            raise PermissionError("authorized source projection is stale")
        source.store.validate_scope()
        source_items = source.store.list_items()
        source.store.validate_scope()
        groups.append({
            "kind": "project", "id": project_id, "omitted_item_count": 0,
            "items": [{
                "item_id": item.get("item_id"),
                "priority": item.get("priority", 3),
                "omitted_requirement_count": 0,
                "requirements": [{
                    "requirement_id": req.get("requirement_id"),
                    "text": req.get("text", "") if type(req.get("text")) is str else "",
                    "status": req.get("status"),
                    "depends_on": req.get("depends_on", []),
                    "schedule_claim": req.get("schedule_claim"),
                } for req in item.get("requirements", [])],
            } for item in source_items if type(item) is dict
                and item.get("scope") == {"kind": "project", "project_id": project_id}],
        })
    return groups


def _worker_shape(worker: Any) -> dict[str, Any]:
    if type(worker) is not dict:
        raise ValueError("each worker dispatch row must be an object")
    required = {"project_id", "item_id", "requirement_id", "priority", "task", "read_targets", "write_targets"}
    if set(worker) != required:
        raise ValueError("worker dispatch row has unexpected fields")
    if any(type(worker.get(key)) is not str or not worker[key].strip()
           for key in ("project_id", "item_id", "requirement_id")):
        raise ValueError("worker identity fields must be non-empty strings")
    if type(worker.get("priority")) is not int or not 0 <= worker["priority"] <= 5:
        raise ValueError("worker priority must be 0..5")
    task = worker["task"]
    if type(task) is not str or not task.strip() or len(task) > _MAX_TASK_CHARS:
        raise ValueError("worker task must be a bounded non-empty string")
    return {
        "project_id": worker["project_id"].strip(),
        "item_id": worker["item_id"].strip(),
        "requirement_id": worker["requirement_id"].strip(),
        "priority": worker["priority"],
        "task": task.strip(),
        "read_targets": list(_target_paths(worker.get("read_targets"), required=False)),
        "write_targets": list(_target_paths(worker.get("write_targets"), required=True)),
    }


def validate_schedule_dispatch(
    sources: Mapping[str, AuthorizedProjectRecordSource],
    workers: Sequence[Any],
    *,
    enabled: bool = True,
) -> tuple[dict[str, Any], ...]:
    """Return normalized worker rows aligned to a fresh authorized priority preview."""
    if enabled is not True:
        raise PermissionError("schedule dispatch requires explicit user opt-in")
    if type(workers) is not list or not 1 <= len(workers) <= _MAX_DISPATCH:
        raise ValueError(f"schedule dispatch batch must contain 1..{_MAX_DISPATCH} workers")
    normalized = [_worker_shape(row) for row in workers]
    if len({row["project_id"] for row in normalized}) != len(normalized):
        raise ValueError("schedule dispatch cannot start two workers for the same project")
    project_ids = {row["project_id"] for row in normalized}
    if set(sources) != project_ids:
        raise PermissionError("each worker project requires a fresh authorized source")
    preview = build_schedule_preview(_schedule_groups(sources))
    schedule_candidates = [
        {"project_id": row["project_id"], "item_id": row["item_id"],
         "requirement_id": row["requirement_id"], "priority": row["priority"]}
        for row in normalized
    ]
    picked = pick_schedule_batch(preview, enabled=True, limit=len(schedule_candidates))
    eligible = {(row["project_id"], row["item_id"], row["requirement_id"], row["priority"]) for row in picked}
    requested = {(row["project_id"], row["item_id"], row["requirement_id"], row["priority"]) for row in schedule_candidates}
    if not requested <= eligible or (len(schedule_candidates) > 1 and eligible != requested):
        raise ValueError("workers are not the next authorized schedule candidates")
    for row in normalized:
        source = sources[row["project_id"]]
        item = next((entry for entry in source.store.list_items()
                     if entry.get("item_id") == row["item_id"]), None)
        if item is None:
            raise PermissionError("source item is unavailable")
        requirement = next((entry for entry in item.get("requirements", [])
                            if entry.get("requirement_id") == row["requirement_id"]), None)
        if requirement is None or requirement.get("text") != row["task"]:
            raise PermissionError("worker task must match the exact authorized requirement text")
    return tuple(normalized)


def build_run_project_workers_arguments(
    normalized_workers: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """Shape arguments for the trusted Agent host dispatch entrypoint."""
    schedule_candidates = [
        {"project_id": row["project_id"], "item_id": row["item_id"],
         "requirement_id": row["requirement_id"], "priority": row["priority"]}
        for row in normalized_workers
    ]
    workers = [
        {"project_id": row["project_id"], "task": row["task"],
         "read_targets": row["read_targets"], "write_targets": row["write_targets"]}
        for row in normalized_workers
    ]
    return {"workers": workers, "schedule_candidates": schedule_candidates}
