"""Pure, read-only scheduling preview over an authorized overview projection."""
from __future__ import annotations

from typing import Any

_ELIGIBLE = frozenset({"queued", "pending"})


def build_schedule_preview(groups: Any) -> dict[str, Any]:
    """Describe ready and blocked requirements without changing work-record state.

    Input must be the exact structured projection already authorized by the host.
    The preview deliberately remains bounded: omissions are reported and never
    treated as evidence that unseen dependencies are satisfied.
    """
    if type(groups) is not list:
        return {"state": "unavailable", "candidates": [], "blocked": [],
                "limitations": ["authorized_projection_unavailable"]}

    known: dict[tuple[str, str, str], dict[str, Any]] = {}
    item_requirements: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
    omissions: list[str] = []
    for group in groups:
        if type(group) is not dict or group.get("kind") != "project":
            continue
        project_id = group.get("id")
        if type(project_id) is not str:
            continue
        if group.get("omitted_item_count", 0):
            omissions.append(f"{project_id}:omitted_items")
        for item in group.get("items", []):
            if type(item) is not dict:
                continue
            item_id = item.get("item_id")
            if type(item_id) is not str:
                continue
            requirements_by_id: dict[str, dict[str, Any]] = {}
            for req in item.get("requirements", []):
                if type(req) is not dict or type(req.get("requirement_id")) is not str:
                    continue
                key = (project_id, item_id, req["requirement_id"])
                priority = item.get("priority", 3)
                known[key] = {"project_id": project_id, "item_id": item_id,
                              "requirement_id": req["requirement_id"], "status": req.get("status"),
                              "dependencies": req.get("depends_on"),
                              "has_schedule_claim_field": "schedule_claim" in req,
                              "schedule_claim": req.get("schedule_claim"),
                              "priority_valid": type(priority) is int and 0 <= priority <= 5,
                              "priority": priority if type(priority) is int and 0 <= priority <= 5 else 3}
                requirements_by_id[req["requirement_id"]] = req
            item_requirements[(project_id, item_id)] = requirements_by_id
            if item.get("omitted_requirement_count", 0):
                omissions.append(f"{project_id}:{item_id}:omitted_requirements")

    candidates = []
    blocked = []
    for row in known.values():
        if row["status"] not in _ELIGIBLE:
            continue
        reasons = []
        if not row["priority_valid"]:
            reasons.append("优先级无效，无法确认调度顺序")
        if not row["has_schedule_claim_field"]:
            reasons.append("claim 状态字段缺失，无法确认要求可运行")
        elif row["schedule_claim"] is not None:
            # Any persisted claim, including an uncertain or malformed one, is a durable
            # exclusion marker. A read-only picker must never retry it.
            reasons.append("已存在持久调度 claim（可能仍在运行或结果未知），禁止自动重派")
        dependencies = row["dependencies"]
        if type(dependencies) is not list or any(type(dep) is not str for dep in dependencies):
            reasons.append("依赖信息无效")
        else:
            requirements = item_requirements.get((row["project_id"], row["item_id"]), {})
            for dependency in dependencies:
                dep = requirements.get(dependency)
                if dep is None:
                    reasons.append(f"依赖 {dependency} 不在已授权预览中，状态无法核实")
                elif dep.get("status") != "completed":
                    reasons.append(f"依赖 {dependency} 状态为 {dep.get('status', '未知')}，尚未完成")
        candidate = {key: row[key] for key in ("project_id", "item_id", "requirement_id", "priority")}
        if reasons:
            blocked.append({**candidate, "reasons": reasons})
        else:
            candidates.append(candidate)

    # If any authorized page is incomplete, visible queue entries cannot be
    # advertised as runnable: an omitted row may be a higher-priority task or
    # a dependency. Keep the visible identities as blocked display rows only.
    if omissions:
        blocked.extend({**row, "reasons": ["概况分页有省略，无法确认完整优先级与依赖"]}
                       for row in candidates)
        candidates = []

    # Higher explicit priority first; deterministic identity tie-breakers. This is
    # a display order only and does not alter the persisted queue.
    ordering = lambda row: (-row["priority"], row["project_id"], row["item_id"], row["requirement_id"])
    candidates.sort(key=ordering)
    blocked.sort(key=ordering)
    limitations = [
        "只读预览；不会启动任务或修改事项状态",
        "仅基于已授权且有界的项目概况",
        "候选不代表运行授权或满足启动门槛；实际启动仍须通过当前运行与需求文本校验",
    ]
    if omissions:
        limitations.append("部分项目事项或要求被概况分页省略，不能据此确认完整调度顺序")
    return {"state": "visible", "candidates": candidates, "blocked": blocked,
            "limitations": limitations, "omissions": omissions}


def pick_schedule_batch(preview: Any, *, enabled: Any, limit: Any = 2) -> tuple[dict[str, Any], ...]:
    """Select a small priority-ordered batch only after explicit per-turn opt-in.

    This is a picker, not a queue lease or execution grant. It refuses truncated
    projections so hidden higher-priority tasks cannot be silently bypassed; the
    caller must still bind each selected task to a live user turn, obtain the
    existing confirmations, and CAS-claim each exact source before start.
    """
    if enabled is not True:
        raise PermissionError("schedule batch picking requires explicit user-turn opt-in")
    if type(limit) is not int or not 1 <= limit <= 2:
        raise ValueError("schedule batch limit must be one or two")
    if (type(preview) is not dict or preview.get("state") != "visible"
            or preview.get("omissions") or type(preview.get("candidates")) is not list):
        raise ValueError("complete authorized schedule preview is required for batch picking")
    candidates = preview["candidates"]
    if any(type(row) is not dict or set(row) != {
            "project_id", "item_id", "requirement_id", "priority",
    } or any(type(row.get(key)) is not str or not row[key].strip()
             for key in ("project_id", "item_id", "requirement_id"))
            or type(row.get("priority")) is not int or not 0 <= row["priority"] <= 5
            for row in candidates):
        raise ValueError("schedule preview candidate shape or priority is invalid")
    selected = sorted(candidates, key=lambda row: (
        -row["priority"], row["project_id"], row["item_id"], row["requirement_id"],
    ))[:limit]
    identities = [(row["project_id"], row["item_id"], row["requirement_id"]) for row in selected]
    if len(set(identities)) != len(identities):
        raise ValueError("schedule preview contains duplicate task identities")
    return tuple(dict(row) for row in selected)
