"""Project-specific projections of current-dialogue worker records.

This module reads only the exact current Session's WorkRecordStore. It never opens,
mutates, or infers another project's Session. A project requirement is eligible only
when its current unknown/not-verified result contains a matching host reservation
receipt; model completion is not acceptance or verification.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from evolution.contracts import is_conversation_id
from project_mode import ProjectModeError, normalize_project_id
from session import Session
from work_record_cards import (
    IndependentWorkRecordCardsSnapshot,
    ProjectWorkRecordCard,
    WorkCardChangeFocus,
    RequirementWorkRecordCard,
    SourceReference,
    StoredResultCard,
    WorkRecordCardsError,
    _build_independent_snapshot,
)
from work_records import WorkRecordError, WorkRecordStore


@dataclass(frozen=True, slots=True)
class ProjectScopedWorkRecordCardsSnapshot:
    kind: Literal["project_scoped"]
    project_id: str
    session_id: str
    captured_at: str
    cards: tuple[ProjectWorkRecordCard, ...]
    completeness: Literal["complete", "incomplete"]
    item_count: int
    requirement_count: int
    result_count: int
    omitted_item_count: int = 0
    omitted_requirement_count: int = 0
    omitted_result_count: int = 0
    incomplete_reason: str | None = None
    persisted: bool = False
    history_complete: bool = True
    missing_current_result_count: int | None = 0
    change_focus: WorkCardChangeFocus | None = None


def _incomplete(project_id: str, session_id: str, reason: str, *,
                item_count: int = 0, requirement_count: int = 0,
                result_count: int = 0) -> ProjectScopedWorkRecordCardsSnapshot:
    return ProjectScopedWorkRecordCardsSnapshot(
        kind="project_scoped", project_id=project_id, session_id=session_id,
        captured_at=datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        cards=(), completeness="incomplete", item_count=item_count,
        requirement_count=requirement_count, result_count=result_count,
        omitted_item_count=item_count, omitted_requirement_count=requirement_count,
        omitted_result_count=result_count, incomplete_reason=reason,
        history_complete=False, missing_current_result_count=None,
    )


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate summary keys")
        result[key] = value
    return result


def _task_identity(session_id: str, item_id: str, requirement_id: str, project_id: str) -> str:
    raw = "\0".join((session_id, item_id, requirement_id, project_id)).encode("utf-8")
    return "task-" + hashlib.sha256(raw).hexdigest()


def _valid_receipt(
    requirement: Any,
    result: Any,
    *,
    session_id: str,
    item_id: str,
    project_id: str,
    require_current: bool = True,
) -> bool:
    if (type(requirement) is not dict or type(result) is not dict
            or result.get("status") != "unknown"
            or (require_current and (
                result.get("run_id") != requirement.get("current_run_id")
                or type(requirement.get("current_run_id")) is not str
            ))
            or result.get("verification", {}).get("status") != "not_verified"):
        return False
    verification = result.get("verification")
    host_execution = verification.get("host_execution") if type(verification) is dict else None
    if (type(host_execution) is not dict or set(host_execution) != {"sequence", "finish_reason"}
            or type(host_execution.get("sequence")) is not int or host_execution["sequence"] < 1):
        return False
    try:
        receipt = json.loads(result.get("result_summary", ""), object_pairs_hook=_strict_object)
    except (TypeError, ValueError, json.JSONDecodeError, RecursionError):
        return False
    legacy_keys = {
        "kind", "result_status", "project_id", "task_id", "worker_id", "worker_run_id",
        "reservation_id", "parent_run_id", "parent_epoch", "host_status", "acceptance_status",
        "write_receipts_available", "write_receipts",
    }
    read_attempt_keys = {"read_attempts_available", "read_attempts"}
    if (type(receipt) is not dict or set(receipt) not in (legacy_keys, legacy_keys | read_attempt_keys)
            or receipt.get("kind") != "project_worker_host_lifecycle"
            or receipt.get("result_status") != "unknown"
            or receipt.get("project_id") != project_id
            or receipt.get("task_id") != _task_identity(
                session_id, item_id, requirement["requirement_id"], project_id,
            )
            or receipt.get("worker_run_id") != result.get("run_id")
            or receipt.get("acceptance_status") != "not_run"
            or type(receipt.get("write_receipts_available")) is not bool
            or type(receipt.get("write_receipts")) is not list
            or len(receipt["write_receipts"]) > 32
            or type(receipt.get("parent_epoch")) is not int
            or receipt["parent_epoch"] < 1
            or receipt["parent_epoch"] != host_execution["sequence"]
            or type(receipt.get("worker_id")) is not str or not receipt["worker_id"]
            or type(receipt.get("reservation_id")) is not str or not receipt["reservation_id"]
            or type(receipt.get("parent_run_id")) is not str
            or type(receipt.get("host_status")) is not str):
        return False
    if receipt["host_status"] not in {"model_turn_finished", "blocked", "failed", "cancelled", "limit_reached"}:
        return False
    finish_reasons = {
        "model_turn_finished": "completed", "blocked": "error", "failed": "error",
        "cancelled": "cancelled", "limit_reached": "timeout",
    }
    # Existing host lifecycle persistence intentionally uses "unknown" for
    # statuses without a dedicated finish-reason mapping. Keep that historical
    # value valid without upgrading the result or verification status.
    if host_execution.get("finish_reason") not in {finish_reasons[receipt["host_status"]], "unknown"}:
        return False
    try:
        parent_uuid = uuid.UUID(receipt["parent_run_id"])
        if receipt["parent_run_id"] not in {str(parent_uuid), parent_uuid.hex}:
            return False
    except (ValueError, AttributeError, TypeError):
        return False
    if receipt["write_receipts"] and not receipt["write_receipts_available"]:
        return False
    if read_attempt_keys.issubset(receipt):
        from project_worker_read_evidence import validate_project_worker_read_attempts
        if not validate_project_worker_read_attempts(
            receipt["read_attempts_available"], receipt["read_attempts"],
        ):
            return False
    for row in receipt["write_receipts"]:
        if (type(row) is not dict or set(row) != {
                "path", "status", "verification", "characters_read_back", "error_code", "readback_succeeded",
            }
                or type(row["path"]) is not str or not row["path"] or len(row["path"].encode("utf-8")) > 512
                or Path(row["path"]).is_absolute() or ".." in Path(row["path"]).parts
                or type(row["status"]) is not str or not row["status"]
                or type(row["verification"]) is not str or not row["verification"]
                or type(row["characters_read_back"]) is not int or row["characters_read_back"] < 0
                or type(row["error_code"]) is not str or len(row["error_code"]) > 128
                or type(row["readback_succeeded"]) is not bool):
            return False
    return result.get("result_id") == "worker-result-" + hashlib.sha256(
        "\0".join((session_id, item_id, requirement["requirement_id"], result["run_id"])).encode("utf-8")
    ).hexdigest()


def _valid_prestart_blocked_receipt(
    requirement: Any,
    result: Any,
    *,
    session_id: str,
    item_id: str,
    project_id: str,
    history: Any,
) -> bool:
    """Validate the exact host-authored preflight-blocked fact (never a worker run)."""
    if (type(requirement) is not dict or type(result) is not dict
            or requirement.get("source_ref") != {"kind": "project", "id": project_id}
            or "current_run_id" in requirement
            or requirement.get("status") != "blocked"
            or result.get("status") != "blocked"
            or result.get("verification") != {"status": "not_verified", "evidence_refs": []}
            or result.get("sources") != []):
        return False
    run_id = result.get("run_id")
    requirement_id = requirement.get("requirement_id")
    if (type(run_id) is not str or type(requirement_id) is not str
            or result.get("result_id") != "worker-result-" + hashlib.sha256(
                "\0".join((session_id, item_id, requirement_id, run_id)).encode("utf-8")
            ).hexdigest()):
        return False
    try:
        receipt = json.loads(result.get("result_summary", ""), object_pairs_hook=_strict_object)
    except (TypeError, ValueError, json.JSONDecodeError, RecursionError):
        return False
    expected_keys = {
        "kind", "result_status", "session_id", "item_id", "requirement_id", "project_id",
        "task_id", "worker_id", "worker_run_id", "reservation_id", "parent_run_id",
        "parent_epoch", "host_status", "host_error_code", "worker_execution_started",
        "worker_result_claimed", "acceptance_status",
    }
    if (type(receipt) is not dict or set(receipt) != expected_keys
            or receipt.get("kind") != "project_worker_host_lifecycle"
            or receipt.get("result_status") != "blocked"
            or receipt.get("session_id") != session_id
            or receipt.get("item_id") != item_id
            or receipt.get("requirement_id") != requirement_id
            or receipt.get("project_id") != project_id
            or receipt.get("task_id") != _task_identity(session_id, item_id, requirement_id, project_id)
            or receipt.get("worker_run_id") != run_id
            or receipt.get("host_status") != "blocked"
            or receipt.get("worker_execution_started") is not False
            or receipt.get("worker_result_claimed") is not False
            or receipt.get("acceptance_status") != "not_run"
            or type(receipt.get("host_error_code")) is not str
            or len(receipt["host_error_code"].encode("utf-8")) > 128
            or type(receipt.get("parent_epoch")) is not int
            or receipt["parent_epoch"] < 1):
        return False
    for key in ("worker_id", "reservation_id"):
        value = receipt.get(key)
        if type(value) is not str or not value or len(value.encode("utf-8")) > 128:
            return False
    try:
        parent_uuid = uuid.UUID(receipt["parent_run_id"])
        if receipt["parent_run_id"] not in {str(parent_uuid), parent_uuid.hex}:
            return False
    except (ValueError, AttributeError, TypeError):
        return False
    # A pre-start receipt must not be accompanied by a run-start event for that run.
    if type(history) is not list:
        return False
    for event in history:
        if (type(event) is dict and event.get("kind") == "requirement_run_started"
                and type(event.get("payload")) is dict
                and event["payload"].get("run_id") == run_id):
            return False
    return True


def _build_project_snapshot(
    store: WorkRecordStore,
    session_id: str,
    project_id: str,
    items: list[dict[str, Any]],
) -> ProjectScopedWorkRecordCardsSnapshot:
    captured_at = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    cards: list[ProjectWorkRecordCard] = []
    requirement_count = result_count = omitted_result_count = 0
    for item in items:
        if type(item) is not dict or type(item.get("scope")) is not dict:
            return _incomplete(project_id, session_id, "invalid_current_dialogue_record")
        if item["scope"].get("kind") != "independent":
            continue
        try:
            independent = _build_independent_snapshot(session_id, item.get("item_id"), item)
        except (WorkRecordCardsError, TypeError, ValueError, UnicodeError):
            return _incomplete(project_id, session_id, "invalid_current_dialogue_record")
        if independent.card is None:
            continue
        selected = [
            req for req in independent.card.requirements
            if req.source_ref is not None and req.source_ref.kind == "project"
            and req.source_ref.id == project_id
        ]
        if not selected:
            continue
        raw_requirements = {row.get("requirement_id"): row for row in item.get("requirements", [])
                            if type(row) is dict}
        raw_results = {row.get("requirement_id"): [] for row in item.get("runs", [])
                       if type(row) is dict}
        for row in item.get("runs", []):
            if type(row) is dict and row.get("requirement_id") in raw_results:
                raw_results[row["requirement_id"]].append(row)
        projected_requirements: list[RequirementWorkRecordCard] = []
        for req in selected:
            raw_req = raw_requirements.get(req.requirement_id)
            raw_history = raw_results.get(req.requirement_id, [])
            latest_raw = raw_history[-1] if raw_history else None
            current_result = (
                latest_raw is not None and req.current_run_id is not None
                and latest_raw.get("run_id") == req.current_run_id
            )
            if current_result:
                if (not _valid_receipt(
                        raw_req, latest_raw, session_id=session_id,
                        item_id=independent.card.item_id, project_id=project_id,
                    )
                        or req.latest_result is None
                        or req.latest_result_is_current is not True):
                    return _incomplete(
                        project_id, session_id, "untrusted_task_receipt",
                        item_count=len(cards) + 1,
                        requirement_count=requirement_count + len(selected),
                        result_count=result_count + sum(len(rows) for rows in raw_results.values()),
                    )
                projected_result = StoredResultCard(
                    project_id=project_id, session_id=session_id,
                    item_id=independent.card.item_id, requirement_id=req.requirement_id,
                    result_id=req.latest_result.result_id, run_id=req.latest_result.run_id,
                    status="unknown",
                    summary=req.latest_result.summary,
                    verification_status="not_verified",
                    sources=req.latest_result.sources,
                )
                projected_status = "unknown"
                result_is_current: bool | None = True
            elif (req.current_run_id is None and latest_raw is not None
                  and _valid_prestart_blocked_receipt(
                      raw_req, latest_raw, session_id=session_id,
                      item_id=independent.card.item_id, project_id=project_id,
                      history=item.get("history"),
                  )
                  and req.latest_result is not None
                  and req.latest_result_is_current is None):
                # Host preflight rejection is a blocked result, but not a started run.
                projected_result = StoredResultCard(
                    project_id=project_id, session_id=session_id,
                    item_id=independent.card.item_id, requirement_id=req.requirement_id,
                    result_id=req.latest_result.result_id, run_id=req.latest_result.run_id,
                    status="blocked", summary=req.latest_result.summary,
                    verification_status="not_verified", sources=req.latest_result.sources,
                )
                projected_status = "blocked"
                result_is_current = None
            elif raw_req.get("status") in {"pending", "in_progress"}:
                # A queued/running requirement is a real source fact, not a result.
                # Do not project an older run as the current outcome or fabricate a receipt.
                projected_result = None
                projected_status = raw_req["status"]
                result_is_current = False if req.current_run_id is not None else None
            else:
                return _incomplete(
                    project_id, session_id, "untrusted_task_receipt",
                    item_count=len(cards) + 1,
                    requirement_count=requirement_count + len(selected),
                    result_count=result_count + sum(len(rows) for rows in raw_results.values()),
                )
            # Hidden stale results are all omitted from this projection, including the
            # previously latest one; never expose it as the current run's result.
            projected_omitted = req.result_count if projected_result is None else req.omitted_result_count
            projected_requirements.append(RequirementWorkRecordCard(
                project_id=project_id, session_id=session_id,
                item_id=independent.card.item_id, requirement_id=req.requirement_id,
                text=req.text, status=projected_status, source_ref=SourceReference("project", project_id),
                latest_result=projected_result, result_count=req.result_count,
                omitted_result_count=projected_omitted, current_result_missing=False,
                current_run_id=req.current_run_id, latest_result_is_current=result_is_current,
            ))
            result_count += req.result_count
            omitted_result_count += projected_omitted
        requirement_count += len(projected_requirements)
        cards.append(ProjectWorkRecordCard(
            project_id=project_id, session_id=session_id,
            item_id=independent.card.item_id, title=independent.card.title,
            status="unknown", source_refs=(SourceReference("project", project_id),),
            requirements=tuple(projected_requirements),
        ))
    if not cards:
        return _incomplete(project_id, session_id, "no_project_requirements")
    return ProjectScopedWorkRecordCardsSnapshot(
        kind="project_scoped", project_id=project_id, session_id=session_id,
        captured_at=captured_at, cards=tuple(cards), completeness="complete",
        item_count=len(cards), requirement_count=requirement_count,
        result_count=result_count, omitted_result_count=omitted_result_count,
        history_complete=(omitted_result_count == 0), missing_current_result_count=0,
    )


def list_project_scoped_project_ids(store: WorkRecordStore) -> tuple[str, ...]:
    """List only canonical project references in this exact dialogue's independent records."""
    if type(store) is not WorkRecordStore:
        raise WorkRecordCardsError("a real current-dialogue WorkRecordStore is required")
    try:
        store._validate_scope()
        items = store.list_items()
        store._validate_scope()
    except WorkRecordError as exc:
        raise WorkRecordCardsError("current-dialogue project task records are unavailable") from exc
    projects: set[str] = set()
    for item in items:
        if type(item) is not dict or type(item.get("scope")) is not dict:
            raise WorkRecordCardsError("current-dialogue record scope is invalid")
        if item["scope"].get("kind") != "independent":
            continue
        requirements = item.get("requirements")
        if type(requirements) is not list:
            raise WorkRecordCardsError("current-dialogue requirements are invalid")
        for requirement in requirements:
            if type(requirement) is not dict:
                raise WorkRecordCardsError("current-dialogue requirement is invalid")
            source = requirement.get("source_ref")
            if type(source) is not dict or source.get("kind") != "project":
                continue
            project_id = source.get("id")
            try:
                if type(project_id) is str and normalize_project_id(project_id) == project_id:
                    projects.add(project_id)
            except ProjectModeError:
                continue
    return tuple(sorted(projects))


def project_scoped_snapshot_request_id(snapshot: ProjectScopedWorkRecordCardsSnapshot) -> str:
    """Return an immutable replay identity for exact project facts, excluding capture time."""
    if type(snapshot) is not ProjectScopedWorkRecordCardsSnapshot or snapshot.completeness != "complete":
        raise WorkRecordCardsError("a complete trusted project-scoped snapshot is required")
    payload = asdict(snapshot)
    if payload.get("change_focus") is None:
        payload.pop("change_focus", None)
    payload["captured_at"] = ""
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    identity = f"my-agent-project-scoped-card:{snapshot.session_id}:{snapshot.project_id}:{digest}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, identity))


def build_project_scoped_work_record_cards(
    store: WorkRecordStore, project_id: str,
) -> ProjectScopedWorkRecordCardsSnapshot:
    """Group all current-dialogue independent task requirements for one exact project."""
    if type(store) is not WorkRecordStore:
        raise WorkRecordCardsError("a real current-dialogue WorkRecordStore is required")
    session = store._session
    if type(session) is not Session or not is_conversation_id(session.conversation_id):
        raise WorkRecordCardsError("current dialogue Session identity is invalid")
    if type(project_id) is not str:
        raise WorkRecordCardsError("project id must be canonical text")
    try:
        if normalize_project_id(project_id) != project_id:
            raise WorkRecordCardsError("project id must already be normalized")
    except ProjectModeError as exc:
        raise WorkRecordCardsError("project id is invalid") from exc

    def capture(data: dict[str, Any]) -> ProjectScopedWorkRecordCardsSnapshot:
        store._validate_scope()
        if store._session is not session or session.conversation_id != store._conversation_id:
            raise WorkRecordCardsError("current dialogue binding changed")
        snapshot = _build_project_snapshot(store, session.conversation_id, project_id, data["items"])
        rows = data.get("project_scoped_card_intents", [])
        focus_keys = {"latest_item_id", "latest_requirement_id", "latest_run_id", "latest_result_id"}
        current_intent = max((row for row in rows if type(row) is dict
                              and row.get("project_id") == project_id),
                             key=lambda row: row["generation"], default=None)
        if current_intent is not None and focus_keys.issubset(current_intent):
            from work_record_cards import bind_work_card_change_focus
            snapshot = bind_work_card_change_focus(snapshot, current_intent)
        return snapshot

    try:
        store._validate_scope()
        snapshot = store.with_locked_data(capture)
        store._validate_scope()
        return snapshot
    except WorkRecordCardsError:
        raise
    except WorkRecordError as exc:
        raise WorkRecordCardsError("current-dialogue project task records are unavailable") from exc
