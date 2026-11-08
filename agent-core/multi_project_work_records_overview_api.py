"""Confirmed, read-only structured overview of selected project work records.

This is deliberately separate from the current-Session overview. Project IDs are
resolved and authorized by the server; the client never supplies source Session
IDs or file paths. The response is a bounded overview, not a live worker status
or a complete history export.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
from threading import Event
from typing import Any, Mapping, Sequence

from multi_project_work_record_bindings import (
    AuthorizedProjectRecordSource,
    _is_issued_authorized_project_record_source,
)
from multi_project_work_records import _mapping_matches, _read_project_sessions, _validate_project_selection
from session import Session
from work_records import WorkRecordScopeError
from work_records_schedule_preview import build_schedule_preview

_MAX_ITEMS_PER_PROJECT = 5
_MAX_REQUIREMENTS_PER_ITEM = 6
_STATUS_ORDER = ("blocked", "unknown", "waiting_confirmation", "in_progress", "stopped", "queued", "pending", "completed")
_HISTORY_PAGE_MAX = 20
_CURSOR_RE = re.compile(r"^[A-Za-z0-9_-]{1,2048}$")


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _item_status(item: Mapping[str, Any]) -> str:
    statuses = [req.get("status") for req in item.get("requirements", []) if type(req) is dict]
    return next((status for status in _STATUS_ORDER if status in statuses), "no_requirements")


def _result_for(requirement: Mapping[str, Any], runs: list[dict[str, Any]]) -> dict[str, Any] | None:
    current_run_id = requirement.get("current_run_id")
    if type(current_run_id) is not str or not current_run_id:
        return None
    matches = [row for row in runs if type(row) is dict
               and row.get("requirement_id") == requirement.get("requirement_id")
               and row.get("run_id") == current_run_id]
    if len(matches) != 1:
        return None
    row = matches[0]
    result_id = row.get("result_id")
    if type(result_id) is not str or not result_id:
        return None
    return {"requirement_id": requirement["requirement_id"], "result_id": result_id,
            "status": row.get("status", "unknown"), "read_status": "unknown"}


def _history_cursor(payload: dict[str, Any]) -> str:
    raw = _canonical_json(payload)
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _parse_history_cursor(cursor: str) -> dict[str, Any] | None:
    if type(cursor) is not str or _CURSOR_RE.fullmatch(cursor) is None:
        return None
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        payload = json.loads(raw.decode("utf-8"))
        if type(payload) is not dict or _canonical_json(payload) != raw:
            return None
        return payload
    except (ValueError, UnicodeError, json.JSONDecodeError, TypeError):
        return None


def _query_requirement_history(
    current_session: Session, project_id: str, source: AuthorizedProjectRecordSource,
    item_id: str, requirement_id: str, *, cursor: str | None, page_size: int,
    cancel_event: Event, expected_mapping: str,
) -> dict[str, Any]:
    """Read one requirement's bounded run history from one freshly authorized source."""
    if (type(item_id) is not str or not item_id or type(requirement_id) is not str
            or not requirement_id or type(page_size) is not int or isinstance(page_size, bool)
            or not 1 <= page_size <= _HISTORY_PAGE_MAX):
        raise ValueError("invalid history page request")
    if (not _is_issued_authorized_project_record_source(source)
            or source.conversation_id != current_session.conversation_id
            or source.project_id != project_id or source.store.project_id != project_id):
        raise PermissionError
    if cancel_event.is_set():
        raise PermissionError
    source.store.validate_scope()
    snapshot = source.store.snapshot()
    revision = snapshot.get("revision")
    if type(revision) is not int or revision < 0:
        raise PermissionError
    offset = 0
    if cursor is not None:
        decoded = _parse_history_cursor(cursor)
        expected = {"project_id": project_id, "session_id": source.store.session_id,
                    "item_id": item_id, "requirement_id": requirement_id, "revision": revision}
        if (decoded is None or any(decoded.get(key) != value for key, value in expected.items())
                or set(decoded) != set(expected) | {"offset"}
                or type(decoded.get("offset")) is not int or decoded["offset"] < 0):
            raise PermissionError
        offset = decoded["offset"]
    items = [row for row in snapshot.get("items", []) if type(row) is dict and row.get("item_id") == item_id
             and row.get("scope") == {"kind": "project", "project_id": project_id}]
    if len(items) != 1:
        raise PermissionError
    item = items[0]
    requirements = [row for row in item.get("requirements", [])
                    if type(row) is dict and row.get("requirement_id") == requirement_id]
    if len(requirements) != 1:
        raise PermissionError
    events = {}
    for event in item.get("history", []):
        if type(event) is dict and event.get("kind") == "result_recorded" and type(event.get("payload")) is dict:
            payload = event["payload"]
            if payload.get("requirement_id") == requirement_id and type(payload.get("run_id")) is str:
                events[payload["run_id"]] = event
    rows = []
    for run in item.get("runs", []):
        if (type(run) is not dict or run.get("requirement_id") != requirement_id
                or type(run.get("run_id")) is not str or type(run.get("result_id")) is not str):
            continue
        event = events.get(run["run_id"], {})
        verification = run.get("verification")
        rows.append({"run_id": run["run_id"], "result_id": run["result_id"],
                     "status": run.get("status", "unknown"),
                     "summary": run.get("result_summary", "") if type(run.get("result_summary")) is str else "",
                     "verification_status": verification.get("status", "unknown") if type(verification) is dict else "unknown",
                     "recorded_at": event.get("at") if type(event.get("at")) in (int, float) else None})
    rows.sort(key=lambda row: (row["recorded_at"] is None,
                               row["recorded_at"] if row["recorded_at"] is not None else 0,
                               row["run_id"]))
    if cursor is not None and offset >= len(rows):
        raise PermissionError
    selected = rows[offset:offset + page_size]
    next_offset = offset + len(selected)
    next_cursor = None
    if next_offset < len(rows):
        next_cursor = _history_cursor({"project_id": project_id, "session_id": source.store.session_id,
            "item_id": item_id, "requirement_id": requirement_id, "revision": revision, "offset": next_offset})
    source.store.validate_scope()
    after = source.store.snapshot()
    mapping_after = _read_project_sessions(current_session.paths)
    if (cancel_event.is_set() or after.get("revision") != revision
            or mapping_after.get(project_id) != expected_mapping
            or expected_mapping != source.store.session_id
            or not _is_issued_authorized_project_record_source(source)):
        raise PermissionError
    return {"state": "visible", "aggregation_scope": "single_authorized_project_requirement",
            "source": {"project_id": project_id, "session_id": source.store.session_id, "revision": revision},
            "history_project_id": project_id, "history_item_id": item_id,
            "history_requirement_id": requirement_id,
            "item_id": item_id, "requirement_id": requirement_id,
            "history": selected, "history_page": {"page_size": page_size, "offset": offset,
                "has_more": next_cursor is not None, "next_cursor": next_cursor,
                "scope": "this_requirement_only", "total": None}}


def _query_selected_projects_history(
    current_session: Session, project_ids: tuple[str, ...],
    sources: Mapping[str, AuthorizedProjectRecordSource], *, cursor: str | None,
    page_size: int, cancel_event: Event, expected_mappings: Mapping[str, str],
) -> dict[str, Any]:
    """Return one deterministic merged page across freshly authorized selected sources."""
    if type(page_size) is not int or isinstance(page_size, bool) or not 1 <= page_size <= _HISTORY_PAGE_MAX:
        raise ValueError("invalid history page request")
    snapshots: dict[str, dict[str, Any]] = {}
    source_facts: list[dict[str, Any]] = []
    for project_id in project_ids:
        if cancel_event.is_set():
            raise PermissionError
        source = sources[project_id]
        if (not _is_issued_authorized_project_record_source(source)
                or source.conversation_id != current_session.conversation_id
                or source.project_id != project_id or source.store.project_id != project_id
                or source.store.session_id != expected_mappings.get(project_id)):
            raise PermissionError
        source.store.validate_scope()
        snapshot = source.store.snapshot()
        revision = snapshot.get("revision")
        if type(revision) is not int or revision < 0:
            raise PermissionError
        snapshots[project_id] = snapshot
        source_facts.append({"project_id": project_id, "session_id": source.store.session_id,
                             "revision": revision})

    binding = {"current_session_id": current_session.conversation_id,
               "sources": source_facts, "scope": "selected_projects_history"}
    if cursor is None:
        offset = 0
    else:
        decoded = _parse_history_cursor(cursor)
        if (decoded is None or set(decoded) != {"scope", "binding", "offset"}
                or decoded.get("scope") != "selected_projects_history" or decoded.get("binding") != binding
                or type(decoded.get("offset")) is not int or decoded["offset"] < 0):
            raise PermissionError
        offset = decoded["offset"]

    rows: list[dict[str, Any]] = []
    for project_id in project_ids:
        snapshot = snapshots[project_id]
        for item in snapshot.get("items", []):
            if type(item) is not dict or item.get("scope") != {"kind": "project", "project_id": project_id}:
                continue
            events: dict[tuple[str, str], dict[str, Any]] = {}
            for event in item.get("history", []):
                payload = event.get("payload") if type(event) is dict else None
                if (type(payload) is dict and event.get("kind") == "result_recorded"
                        and type(payload.get("requirement_id")) is str and type(payload.get("run_id")) is str):
                    events[(payload["requirement_id"], payload["run_id"])] = event
            requirement_text = {row.get("requirement_id"): row.get("text", "")
                                for row in item.get("requirements", []) if type(row) is dict}
            for run in item.get("runs", []):
                if (type(run) is not dict or type(run.get("requirement_id")) is not str
                        or type(run.get("run_id")) is not str or type(run.get("result_id")) is not str):
                    continue
                event = events.get((run["requirement_id"], run["run_id"]), {})
                recorded_at = event.get("at")
                rows.append({"project_id": project_id, "session_id": sources[project_id].store.session_id,
                    "item_id": item.get("item_id"), "item_title": item.get("title", ""),
                    "requirement_id": run["requirement_id"],
                    "requirement_text": requirement_text.get(run["requirement_id"], ""),
                    "run_id": run["run_id"], "result_id": run["result_id"],
                    "status": run.get("status", "unknown"),
                    "summary": run.get("result_summary", "") if type(run.get("result_summary")) is str else "",
                    "verification_status": run.get("verification", {}).get("status", "unknown")
                        if type(run.get("verification")) is dict else "unknown",
                    "recorded_at": recorded_at if type(recorded_at) in (int, float) else None})
    rows.sort(key=lambda row: (row["recorded_at"] is None,
        row["recorded_at"] if row["recorded_at"] is not None else 0,
        row["project_id"], row["session_id"], row["item_id"] or "",
        row["requirement_id"], row["run_id"]))
    if cursor is not None and offset >= len(rows):
        raise PermissionError
    selected = rows[offset:offset + page_size]
    next_offset = offset + len(selected)
    has_more = next_offset < len(rows)
    next_cursor = (_history_cursor({"scope": "selected_projects_history", "binding": binding,
                                    "offset": next_offset}) if has_more else None)
    for project_id in project_ids:
        source = sources[project_id]
        if (cancel_event.is_set() or not _is_issued_authorized_project_record_source(source)
                or source.store.snapshot().get("revision") != snapshots[project_id].get("revision")):
            raise PermissionError
    after = _read_project_sessions(current_session.paths)
    if cancel_event.is_set() or not _mapping_matches(dict(expected_mappings), after, project_ids):
        raise PermissionError
    return {"state": "visible" if selected else "no_records",
        "aggregation_scope": "selected_project_sessions",
        "history_scope": "selected_projects_history", "history_sources": source_facts,
        "history": selected, "history_page": {"page_size": page_size, "offset": offset,
            "has_more": has_more, "next_cursor": next_cursor, "scope": "selected_projects_history",
            "total": None}}


def query_authorized_project_records_overview(
    current_session: Session,
    project_ids: Sequence[str],
    sources: Mapping[str, AuthorizedProjectRecordSource],
    *,
    cancel_event: Event,
    history_project_id: str | None = None,
    history_item_id: str | None = None,
    history_requirement_id: str | None = None,
    history_cursor: str | None = None,
    history_page_size: int = 10,
    history_scope: str | None = None,
) -> dict[str, Any]:
    """Project only exact, host-issued selected project sources into bounded groups."""
    try:
        if type(current_session) is not Session or not isinstance(cancel_event, Event):
            raise PermissionError
        selected = _validate_project_selection(project_ids)
        if type(sources) is not dict or set(sources) != set(selected) or cancel_event.is_set():
            raise PermissionError
        before = _read_project_sessions(current_session.paths)
        if any(project_id not in before for project_id in selected):
            raise PermissionError
        history_args = (history_project_id, history_item_id, history_requirement_id)
        if history_scope == "selected_projects_history":
            if any(value is not None for value in history_args):
                raise PermissionError
            return _query_selected_projects_history(
                current_session, selected, sources, cursor=history_cursor,
                page_size=history_page_size, cancel_event=cancel_event,
                expected_mappings={project_id: before[project_id] for project_id in selected},
            )
        if history_scope is not None or (history_cursor is not None and not all(value is not None for value in history_args)):
            raise PermissionError
        if any(value is not None for value in history_args):
            if (any(type(value) is not str or not value for value in history_args)
                    or history_project_id not in selected):
                raise PermissionError
            if history_cursor is not None and type(history_cursor) is not str:
                raise PermissionError
            return _query_requirement_history(
                current_session, history_project_id, sources[history_project_id],
                history_item_id, history_requirement_id, cursor=history_cursor,
                page_size=history_page_size, cancel_event=cancel_event,
                expected_mapping=before[history_project_id],
            )
        groups: list[dict[str, Any]] = []
        snapshot_sources: list[dict[str, Any]] = []
        before_revisions: dict[str, int] = {}
        total_items = 0
        total_requirements = 0
        for project_id in selected:
            if cancel_event.is_set():
                raise PermissionError
            source = sources[project_id]
            if (not _is_issued_authorized_project_record_source(source)
                    or source.conversation_id != current_session.conversation_id
                    or source.project_id != project_id or source.store.project_id != project_id
                    or before.get(project_id) != source.store.session_id):
                raise PermissionError
            source.store.validate_scope()
            snapshot = source.store.snapshot()
            source.store.validate_scope()
            items = [row for row in snapshot.get("items", [])
                     if type(row) is dict and row.get("scope") == {"kind": "project", "project_id": project_id}]
            item_rows: list[dict[str, Any]] = []
            status_counts = {status: 0 for status in _STATUS_ORDER}
            for item in items:
                if cancel_event.is_set():
                    raise PermissionError
                requirements = item.get("requirements", [])
                if type(requirements) is not list:
                    raise PermissionError
                status = _item_status(item)
                for req in requirements:
                    req_status = req.get("status", "unknown") if type(req) is dict else "unknown"
                    status_counts[req_status if req_status in status_counts else "unknown"] += 1
                ordered = sorted(requirements, key=lambda req: (
                    _STATUS_ORDER.index(req.get("status", "unknown"))
                    if type(req) is dict and req.get("status") in _STATUS_ORDER else 1,
                    str(req.get("requirement_id", "")) if type(req) is dict else "",
                ))
                selected_requirements = []
                current_results = []
                for req in ordered[:_MAX_REQUIREMENTS_PER_ITEM]:
                    if type(req) is not dict or type(req.get("requirement_id")) is not str:
                        raise PermissionError
                    dependencies = req.get("depends_on", [])
                    if type(dependencies) is not list or any(type(dep) is not str for dep in dependencies):
                        raise PermissionError
                    events_by_run = {
                        event.get("payload", {}).get("run_id"): event
                        for event in item.get("history", []) if type(event) is dict
                        and event.get("kind") == "result_recorded" and type(event.get("payload")) is dict
                        and event["payload"].get("requirement_id") == req["requirement_id"]
                        and type(event["payload"].get("run_id")) is str
                    }
                    history_rows = []
                    for run in item.get("runs", []):
                        if (type(run) is not dict or run.get("requirement_id") != req["requirement_id"]
                                or type(run.get("run_id")) is not str or type(run.get("result_id")) is not str):
                            continue
                        if run.get("status") == "blocked" and req.get("current_run_id") is None:
                            continue
                        verification = run.get("verification")
                        event_row = events_by_run.get(run["run_id"], {})
                        history_rows.append({
                            "run_id": run["run_id"], "result_id": run["result_id"],
                            "status": run.get("status", "unknown"),
                            "summary": run.get("result_summary", "") if type(run.get("result_summary")) is str else "",
                            "verification_status": verification.get("status", "unknown") if type(verification) is dict else "unknown",
                            "recorded_at": event_row.get("at") if type(event_row.get("at")) in (int, float) else None,
                        })
                    selected_requirements.append({
                        "requirement_id": req["requirement_id"],
                        "text": req.get("text", "") if type(req.get("text")) is str else "",
                        "status": req.get("status", "unknown"),
                        "depends_on": dependencies,
                        # Only the state is projected; the claim's private owner and
                        # reservation IDs are not UI data. Any claim excludes repicking.
                        "schedule_claim": ({"state": req["schedule_claim"].get("state")}
                                           if type(req.get("schedule_claim")) is dict else None),
                        "history": history_rows[-10:],
                    })
                    result = _result_for(req, item.get("runs", []))
                    if result is not None:
                        current_results.append(result)
                item_rows.append({
                    "item_id": item.get("item_id"),
                    "title": item.get("title", "") if type(item.get("title")) is str else "",
                    "priority": item.get("priority", 3) if type(item.get("priority", 3)) is int else 3,
                    "status": status,
                    "status_counts": {status: sum(1 for req in requirements
                        if type(req) is dict and req.get("status") == status) for status in _STATUS_ORDER
                        if any(type(req) is dict and req.get("status") == status for req in requirements)},
                    "requirements": selected_requirements,
                    "current_results": current_results,
                    "omitted_requirement_count": max(0, len(requirements) - len(selected_requirements)),
                })
            item_rows.sort(key=lambda row: (
                _STATUS_ORDER.index(row["status"]) if row["status"] in _STATUS_ORDER else 1,
                -row["priority"], row["title"].casefold(), row["item_id"],
            ))
            requirement_count = sum(len(item.get("requirements", [])) for item in items)
            total_items += len(items)
            total_requirements += requirement_count
            source_revision = snapshot.get("revision")
            if type(source_revision) is not int or source_revision < 0:
                raise PermissionError
            before_revisions[project_id] = source_revision
            groups.append({
                "kind": "project", "id": project_id, "visibility": "visible",
                "status": next((status for status in _STATUS_ORDER if status_counts[status]), "no_requirements"),
                "status_counts": {key: value for key, value in status_counts.items() if value},
                "item_count": len(items), "requirement_count": requirement_count,
                "items": item_rows[:_MAX_ITEMS_PER_PROJECT],
                "omitted_item_count": max(0, len(item_rows) - _MAX_ITEMS_PER_PROJECT),
                "source_session_id": source.store.session_id,
                "source_revision": source_revision,
            })
            snapshot_sources.append({"project_id": project_id, "session_id": source.store.session_id,
                                    "revision": source_revision})
        # Recheck every sidecar revision before publishing the aggregate snapshot.
        # The per-session file locks cannot provide a cross-session atomic commit;
        # if any contributor changed while another was being read, discard all rows.
        for project_id in selected:
            source = sources[project_id]
            if cancel_event.is_set() or source.store.snapshot().get("revision") != before_revisions[project_id]:
                raise PermissionError
        after = _read_project_sessions(current_session.paths)
        if cancel_event.is_set() or not _mapping_matches(
                {project_id: before[project_id] for project_id in selected}, after, selected):
            raise PermissionError
        fingerprint = hashlib.sha256(_canonical_json({
            "current_session_id": current_session.conversation_id,
            "sources": snapshot_sources,
            "groups": groups,
        })).hexdigest()
        return {
            "state": "visible" if any(group["item_count"] for group in groups) else "no_records",
            "aggregation_scope": "selected_project_sessions",
            "selected_project_ids": list(selected),
            "groups": groups,
            "schedule_preview": build_schedule_preview(groups),
            "detail_page": None,
            "unread_important_results": [],
            "unread_important_results_total": 0,
            "unread_important_results_truncated": False,
            "unread_status": "unknown",
            "visibility_limitations": ["cross_session_result_read_receipts_not_projected", "bounded_project_items_and_requirements"],
            "visibility_counts": {"visible_groups": len(groups), "visible_items": total_items,
                                   "collapsed_groups": 0, "collapsed_items": 0},
            "snapshot_id": fingerprint,
            "work_record_revision": max((row["revision"] for row in snapshot_sources), default=0),
            "page": {"page_size": len(groups) or 1, "offset": 0, "total_groups": len(groups),
                     "total_visible_groups": len(groups), "total_visible_items": total_items,
                     "total_collapsed_groups": 0, "total_collapsed_items": 0,
                     "has_more": False, "next_cursor": None},
        }
    except Exception:
        return {"state": "error", "reason": "multi_project_source_unavailable", "groups": [],
                "unread_important_results": [], "unread_status": "unknown",
                "visibility_limitations": ["multi_project_source_unavailable"],
                "visibility_counts": {"visible_groups": 0, "visible_items": 0,
                                      "collapsed_groups": 0, "collapsed_items": 0},
                "snapshot_id": None, "work_record_revision": 0,
                "page": {"page_size": 1, "offset": 0, "total_groups": 0,
                         "total_visible_groups": 0, "total_visible_items": 0,
                         "total_collapsed_groups": 0, "total_collapsed_items": 0,
                         "has_more": False, "next_cursor": None}}
