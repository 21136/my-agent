"""Read-only, bounded overview of work records in one exact current Session."""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from session import Session
from work_records import WorkRecordError, WorkRecordStore, _digest
from work_record_visibility_projection import project_visible_items, trusted_worker_batch_projection

_MAX_GROUPS = 6
_MAX_ITEMS_PER_GROUP = 5
_MAX_REQUIREMENTS_PER_ITEM = 6
_MAX_TITLE_CHARS = 80
_MAX_REQUIREMENT_CHARS = 120
_MAX_ORIGINAL_REQUEST_CHARS = 1200
_WORKER_BATCH_KEY = re.compile(r"^worker-batch-task:([0-9a-f]{64}):(0|[1-9][0-9]*)$")
_WORKER_BATCH_ORIGINAL_KEY = re.compile(r"^worker-batch-original:([0-9a-f]{64})$")

_STATUS_RANK = {
    "blocked": 0,
    "unknown": 1,
    "in_progress": 2,
    "stopped": 3,
    "pending": 4,
    "completed": 5,
}


@dataclass(frozen=True)
class _OverviewGroup:
    key: tuple[str, str]
    label: str
    items: tuple[dict[str, Any], ...]
    priority: int
    requirement_count: int
    item_count: int


def _status_rank(item: dict[str, Any]) -> int:
    requirements = item.get("requirements")
    if not isinstance(requirements, list) or not requirements:
        return _STATUS_RANK["pending"]
    return min(
        (_STATUS_RANK.get(req.get("status"), 1)
         for req in requirements if isinstance(req, dict)),
        default=1,
    )


def _requirement_rank(requirement: dict[str, Any]) -> int:
    return _STATUS_RANK.get(requirement.get("status"), 1)


def _safe_text(value: Any, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    normalized = " ".join(value.split())
    if len(normalized) <= limit:
        return normalized
    omitted = len(normalized) - limit
    return f"{normalized[:limit]}…（文本截断，另有 {omitted} 字）"


def _requirement_status(status: Any) -> str:
    if status == "blocked":
        return "受阻"
    if status == "unknown":
        return "结果待确认"
    if status == "in_progress":
        # A persisted start/current_run_id is not evidence that a process is still alive.
        return "执行状态待确认"
    if status == "stopped":
        return "已停止"
    if status == "completed":
        return "已记录完成（不代表验证通过）"
    if status == "pending":
        return "待开始"
    return "状态待确认"


def _aggregate_status(items: tuple[dict[str, Any], ...]) -> str:
    statuses = {
        req.get("status")
        for item in items
        for req in item.get("requirements", [])
        if isinstance(req, dict)
    }
    if not statuses:
        return "暂无已记录要求"
    if "blocked" in statuses:
        return "有要求受阻"
    if "unknown" in statuses:
        return "有要求结果待确认"
    if "in_progress" in statuses:
        return "有要求执行状态待确认"
    if "stopped" in statuses:
        return "有要求已停止"
    if "pending" in statuses:
        return "有要求待开始"
    if statuses == {"completed"}:
        return "所有已记录要求均标记完成（不代表验证通过）"
    return "状态待确认"



def _status_counts(items: tuple[dict[str, Any], ...]) -> str:
    """Count all persisted requirements, including those omitted from details."""
    labels = {
        "blocked": "受阻", "unknown": "结果待确认", "in_progress": "执行状态待确认",
        "stopped": "已停止", "pending": "待开始", "completed": "已记录完成",
        "other": "状态待确认",
    }
    counts = {key: 0 for key in labels}
    for item in items:
        for requirement in item.get("requirements", []):
            if not isinstance(requirement, dict):
                continue
            status = requirement.get("status")
            key = status if isinstance(status, str) and status in _STATUS_RANK else "other"
            counts[key] += 1
    parts = [f"{label} {counts[key]}" for key, label in labels.items() if counts[key]]
    if not parts:
        return "无已记录要求"
    return "、".join(parts) + ("（已记录完成不代表验证通过）" if counts["completed"] else "")


def _trusted_worker_batch_projects(
    item: dict[str, Any],
) -> tuple[dict[str, str], tuple[str, str] | None]:
    """Return trusted project links and the trace-only original request, if any."""
    scope = item.get("scope")
    if type(scope) is not dict or scope != {"kind": "independent", "object_ref": None}:
        return {}, None
    requirements = item.get("requirements")
    history = item.get("history")
    if type(requirements) is not list or type(history) is not list:
        return {}, None
    requirements_by_id = {
        row.get("requirement_id"): row for row in requirements
        if type(row) is dict and type(row.get("requirement_id")) is str
    }
    events_by_key: dict[str, list[dict[str, Any]]] = {}
    for event in history:
        if type(event) is dict and type(event.get("idempotency_key")) is str:
            events_by_key.setdefault(event["idempotency_key"], []).append(event)

    valid_originals: list[tuple[str, tuple[str, str]]] = []
    for key, events in events_by_key.items():
        match = _WORKER_BATCH_ORIGINAL_KEY.fullmatch(key)
        if not match or len(events) != 1:
            continue
        event = events[0]
        payload = event.get("payload")
        requirement = requirements_by_id.get(payload.get("requirement_id")) if type(payload) is dict else None
        candidate_digest = match.group(1)
        title = item.get("title")
        text = requirement.get("text") if type(requirement) is dict else None
        scope = item.get("scope")
        if (event.get("kind") == "requirement_added" and type(payload) is dict
                and type(requirement) is dict and type(text) is str and text.strip()
                and requirement.get("text") == payload.get("text")
                and "source_ref" not in requirement
                and type(title) is str and title.strip() == "多项目任务批次"
                and event.get("event_id") == f"worker-batch-original-{candidate_digest}"
                and event.get("digest") == _digest({
                    "kind": "requirement_added", "text": text, "source_ref": None,
                })
                and event.get("idempotency_digest") == _digest({
                    "kind": "create_item_with_requirement", "title": title,
                    "scope": scope, "requirement": text,
                })):
            requirement_id = requirement.get("requirement_id")
            if type(requirement_id) is str and requirement_id:
                valid_originals.append((candidate_digest, (requirement_id, text)))
    if len(valid_originals) != 1:
        return {}, None
    digest, original_request = valid_originals[0]

    project_by_requirement: dict[str, str] = {}
    seen_indexes: set[int] = set()
    for key, events in events_by_key.items():
        match = _WORKER_BATCH_KEY.fullmatch(key)
        if not match or match.group(1) != digest or len(events) != 1:
            continue
        index = int(match.group(2))
        if index in seen_indexes:
            continue
        event = events[0]
        payload = event.get("payload")
        requirement = requirements_by_id.get(payload.get("requirement_id")) if type(payload) is dict else None
        source_ref = requirement.get("source_ref") if type(requirement) is dict else None
        if (event.get("kind") != "requirement_added" or type(payload) is not dict
                or type(requirement) is not dict or requirement.get("text") != payload.get("text")
                or type(source_ref) is not dict or set(source_ref) != {"kind", "id"}
                or source_ref.get("kind") != "project" or type(source_ref.get("id")) is not str
                or not source_ref["id"].strip()
                or event.get("event_id") != f"worker-batch-task-{digest}-{index}"
                or event.get("digest") != _digest({
                    "kind": "requirement_added", "text": requirement.get("text"),
                    "source_ref": source_ref,
                })
                or event.get("idempotency_digest") != _digest({
                    "kind": "append_requirement", "item_id": item.get("item_id"),
                    "text": requirement.get("text"), "source_ref": source_ref,
                })):
            continue
        seen_indexes.add(index)
        project_by_requirement[requirement["requirement_id"]] = source_ref["id"]
    return project_by_requirement, original_request if project_by_requirement else None


def _overview_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Split trusted batch requirements into display-only project entries."""
    projected: list[dict[str, Any]] = []
    for item in items:
        if type(item) is not dict:
            projected.append(item)
            continue
        trusted_projection = trusted_worker_batch_projection(item)
        if trusted_projection is None:
            project_by_requirement, original_request = _trusted_worker_batch_projects(item)
        else:
            project_by_requirement, original_request = trusted_projection
        if not project_by_requirement or original_request is None:
            projected.append(item)
            continue
        requirements = item.get("requirements", [])
        by_project: dict[str, list[dict[str, Any]]] = {}
        independent: list[dict[str, Any]] = []
        original_requirement_id, original_text = original_request
        for requirement in requirements:
            requirement_id = requirement.get("requirement_id") if type(requirement) is dict else None
            if requirement_id == original_requirement_id:
                continue
            project_id = project_by_requirement.get(requirement_id)
            if project_id is None:
                independent.append(requirement)
            else:
                by_project.setdefault(project_id, []).append(requirement)
        title = item.get("title", "未命名事项")
        original_marker = {"requirement_id": original_requirement_id, "text": original_text}
        for index, (project_id, project_requirements) in enumerate(by_project.items()):
            projected_item = {
                **item, "title": f"{title} · 项目任务",
                "scope": {"kind": "project", "project_id": project_id},
                "requirements": project_requirements,
            }
            if index == 0:
                projected_item["_overview_original_request"] = original_marker
            projected.append(projected_item)
        if independent:
            independent_item = {**item, "title": f"{title} · 原始/未标记要求", "requirements": independent}
            if not by_project:
                independent_item["_overview_original_request"] = original_marker
            projected.append(independent_item)
    return projected


def _make_groups(items: list[dict[str, Any]]) -> list[_OverviewGroup]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in items:
        scope = item.get("scope") if isinstance(item, dict) else None
        if not isinstance(scope, dict):
            # WorkRecordStore validates persisted data. Fail closed if a custom/future
            # store returns a shape outside that contract instead of misgrouping it.
            raise ValueError("invalid work-record scope")
        kind = scope.get("kind")
        if kind == "project":
            project_id = scope.get("project_id")
            if not isinstance(project_id, str) or not project_id.strip():
                raise ValueError("invalid project identity")
            key = ("project", project_id)
        elif kind == "independent":
            item_id = item.get("item_id")
            if not isinstance(item_id, str) or not item_id:
                raise ValueError("invalid independent item identity")
            key = ("independent", item_id)
        else:
            raise ValueError("unsupported work-record scope")
        grouped.setdefault(key, []).append(item)

    groups: list[_OverviewGroup] = []
    for key, members in grouped.items():
        members.sort(key=lambda item: (_status_rank(item), str(item.get("title", "")).casefold(), item.get("item_id", "")))
        label = f"项目 {_safe_text(key[1], _MAX_TITLE_CHARS)}" if key[0] == "project" else "独立事项"
        member_tuple = tuple(members)
        priorities = [_status_rank(item) for item in member_tuple]
        groups.append(_OverviewGroup(
            key=key,
            label=label,
            items=member_tuple,
            priority=min(priorities, default=_STATUS_RANK["pending"]),
            requirement_count=sum(
                len(item.get("requirements", []))
                for item in member_tuple
                if isinstance(item.get("requirements", []), list)
            ),
            item_count=len({item.get("item_id") for item in member_tuple}),
        ))
    groups.sort(key=lambda group: (
        group.priority, group.label.casefold(),
        str(group.items[0].get("title", "")).casefold() if group.items else "",
        group.key,
    ))
    return groups


def _omitted_requirements(items: tuple[dict[str, Any], ...]) -> int:
    return sum(
        len(item.get("requirements", []))
        for item in items
        if isinstance(item.get("requirements", []), list)
    )


def render_work_records_overview(session: Session) -> str:
    """Read and render only the supplied exact Session after an explicit request.

    Work records and historical cards are not mutated. Acquiring the work-record
    lock may create its lock file; advancing verified result timers may update the
    presentation sidecar. No model or external service is called.
    """
    store = WorkRecordStore(session)
    if store._session is not session:
        raise ValueError("work-record store is not bound to the supplied Session")
    try:
        recorded_items = store.list_items()
        items = project_visible_items(session, store, recorded_items)
    except (WorkRecordError, OSError):
        return "当前会话工作记录暂不可读取；未读取其他会话，也未修改工作记录数据。读取时可能创建锁文件。"
    if recorded_items and not items:
        return ("当前概况中符合条件的已完成事项或项目任务均已逐项查看并满足收起等待；"
                "它们仅从当前概况收起，工作记录、历史卡片和原对话仍保留。")
    return _format_overview(items)



def render_project_work_records_overview(session: Session, project_id: str) -> str:
    """Render only trusted project-scoped entries from the exact bound Session."""
    store = WorkRecordStore(session)
    if store._session is not session:
        raise ValueError("work-record store is not bound to the supplied Session")
    bound_project_id = getattr(getattr(session, "meta", None), "project_id", "")
    if (type(project_id) is not str or not project_id.strip()
            or project_id != bound_project_id or project_id != store._bound_project_id):
        raise ValueError("project overview does not match the current Session binding")
    try:
        recorded_items = store.list_items()
        store._validate_scope()
        visible_items = project_visible_items(session, store, recorded_items)
    except (WorkRecordError, OSError):
        return "当前项目工作记录暂不可读取；未读取其他会话，也未修改工作记录数据。读取时可能创建锁文件。"

    all_project_items = [
        item for item in _overview_items(recorded_items)
        if type(item) is dict and item.get("scope") == {"kind": "project", "project_id": project_id}
    ]
    project_items = [
        {key: value for key, value in item.items() if key != "_overview_original_request"}
        for item in _overview_items(visible_items)
        if type(item) is dict and item.get("scope") == {"kind": "project", "project_id": project_id}
    ]
    if not all_project_items:
        return "当前项目没有已记录事项；这不代表相关工作没有发生。未修改工作记录数据；读取可能创建 .work-records.lock。"
    if not project_items:
        return "当前项目中符合条件的已完成事项或项目任务均已逐项查看并满足收起等待；它们仅从当前概况收起，工作记录、历史卡片和原对话仍保留。"
    return _format_overview(project_items)

def render_current_batch_project_overview(session: Session, project_id: str) -> str:
    """Render one project only when bound here or named by a trusted current-session batch."""
    if (type(project_id) is not str or re.fullmatch(r"[a-z][a-z0-9-]{0,63}", project_id) is None):
        return "项目名称格式无法核对；未读取其他会话或工作记录。"
    store = WorkRecordStore(session)
    if store._session is not session:
        raise ValueError("work-record store is not bound to the supplied Session")
    bound_project_id = getattr(getattr(session, "meta", None), "project_id", "")
    if project_id == bound_project_id == store._bound_project_id:
        return render_project_work_records_overview(session, project_id)
    try:
        recorded_items = store.list_items()
        store._validate_scope()
        trusted_items = _overview_items(recorded_items)
        if not any(type(item) is dict and item.get("scope") == {"kind": "project", "project_id": project_id}
                   for item in trusted_items):
            return "当前会话没有可核验的该项目可信批次记录；未读取其他会话。"
        visible_items = project_visible_items(session, store, recorded_items)
        project_items = [
            {key: value for key, value in item.items() if key != "_overview_original_request"}
            for item in _overview_items(visible_items)
            if type(item) is dict and item.get("scope") == {"kind": "project", "project_id": project_id}
        ]
    except (WorkRecordError, OSError):
        return "当前会话可信批次暂不可读取；未读取其他会话，也未修改工作记录数据。读取可能创建锁文件。"
    if not project_items:
        return "该项目在当前会话可信批次中的事项均已满足收起条件；历史记录仍保留。"
    return _format_overview(project_items)

def _format_overview(items: list[dict[str, Any]]) -> str:
    """Purely format an already-read sidecar projection; performs no I/O."""
    if not items:
        return "当前会话侧车没有已记录事项；这不代表相关工作没有发生。未修改工作记录数据；读取可能创建 .work-records.lock。"

    overview_items = _overview_items(items)
    groups = _make_groups(overview_items)
    total_items = len({item.get("item_id") for group in groups for item in group.items})
    total_requirements = sum(group.requirement_count for group in groups)
    project_groups = sum(group.key[0] == "project" for group in groups)
    independent_groups = len(groups) - project_groups
    shown_groups = groups[:_MAX_GROUPS]
    omitted_groups = groups[_MAX_GROUPS:]

    lines = [
        "当前会话已记录事项概况（工作记录和历史卡片不修改；已读结果的单调计时 checkpoint 可能写入 presentation sidecar；读取可能创建 .work-records.lock；不是执行或验收结论）：",
        f"共 {len(groups)} 组（{project_groups} 个项目组、{independent_groups} 个独立事项组），"
        f"{total_items} 个事项、{total_requirements} 项要求。",
        f"全部已记录要求状态计数：{_status_counts(tuple(overview_items))}。",
    ]
    original_requests = {
        row["_overview_original_request"]["requirement_id"]: row["_overview_original_request"]["text"]
        for row in overview_items
        if type(row.get("_overview_original_request")) is dict
        and type(row["_overview_original_request"].get("requirement_id")) is str
        and type(row["_overview_original_request"].get("text")) is str
    }
    for text in original_requests.values():
        lines.append(
            f"多项目原始请求（仅追溯，不计入事项/要求）：{_safe_text(text, _MAX_ORIGINAL_REQUEST_CHARS)}"
        )

    for group in shown_groups:
        lines.append(
            f"\n{group.label}（关联 {group.item_count} 个事项、{group.requirement_count} 项要求；"
            f"关注：{_aggregate_status(group.items)}；状态计数：{_status_counts(group.items)}）"
        )
        shown_items = group.items[:_MAX_ITEMS_PER_GROUP]
        omitted_items = group.items[_MAX_ITEMS_PER_GROUP:]
        for item in shown_items:
            title = _safe_text(item.get("title", "未命名事项"), _MAX_TITLE_CHARS) or "未命名事项"
            requirements = item.get("requirements", [])
            if not isinstance(requirements, list):
                requirements = []
            sorted_requirements = sorted(
                (req for req in requirements if isinstance(req, dict)),
                key=lambda req: (_requirement_rank(req), str(req.get("text", "")).casefold(), str(req.get("requirement_id", ""))),
            )
            item_status = _aggregate_status((item,))
            lines.append(f"- 事项：{title}；状态概况：{item_status}；状态计数：{_status_counts((item,))}")
            for requirement in sorted_requirements[:_MAX_REQUIREMENTS_PER_ITEM]:
                text = _safe_text(requirement.get("text", ""), _MAX_REQUIREMENT_CHARS) or "（要求文本为空）"
                lines.append(f"  - 要求：{text}；状态：{_requirement_status(requirement.get('status'))}")
            omitted_requirements = len(sorted_requirements) - min(len(sorted_requirements), _MAX_REQUIREMENTS_PER_ITEM)
            if omitted_requirements:
                lines.append(f"  - 还有 {omitted_requirements} 项要求未展开。")
        if omitted_items:
            lines.append(
                f"- 还有 {len(omitted_items)} 个事项未展开，包含 {_omitted_requirements(tuple(omitted_items))} 项要求未展开。"
            )

    if omitted_groups:
        omitted_group_items = len({item.get("item_id") for group in omitted_groups for item in group.items})
        omitted_group_requirements = sum(group.requirement_count for group in omitted_groups)
        lines.append(
            f"\n还有 {len(omitted_groups)} 组未展开，包含 {omitted_group_items} 个事项、"
            f"{omitted_group_requirements} 项要求未展开；以上不是全部记录。"
        )
    else:
        lines.append("\n以上已展开全部记录组；记录缺失不代表相关工作没有发生。")
    return "\n".join(lines)


