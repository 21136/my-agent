"""Narrow, one-shot authorization for one cross-session schedule claim.

This capability is separate from the existing read-only source receipt. It can
only claim and start the exact queued requirement disclosed at confirmation;
settlement remains an unverified host lifecycle fact.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import time
from threading import Event, Lock
from typing import Any, Callable
from weakref import WeakKeyDictionary

from multi_project_work_record_bindings import AuthorizedProjectRecordSource, _is_issued_authorized_project_record_source, _reader_for
from project_worker_host import ProjectWorkerHostOutcome, ProjectWorkerHostRequest, ProjectWorkerReservation, project_worker_outcome_matches_reservation
from session import Session
from work_records import WorkRecordStore, _digest
from work_record_settlement import SettlementResult


@dataclass(frozen=True, eq=False)
class CrossSessionScheduleWriteCapability:
    """Opaque authority for a single source claim/start, not general source writes."""
    capability_id: str
    source_authorization_id: str
    source_session_id: str
    parent_session_id: str
    project_id: str
    item_id: str
    requirement_id: str
    requirement_text: str
    source_revision: int
    source_item_digest: str
    reservation: ProjectWorkerReservation
    request: ProjectWorkerHostRequest
    expires_at: float


_CAPABILITIES: WeakKeyDictionary = WeakKeyDictionary()
_LOCK = Lock()


def authorize_cross_session_schedule_write(
    source: AuthorizedProjectRecordSource,
    *,
    item_id: str,
    requirement_id: str,
    task: str,
    request: ProjectWorkerHostRequest,
    reservation: ProjectWorkerReservation,
    confirm_fn: Callable[[str], str],
    cancel_event: Event,
    live_binding_check: Callable[[Session, str, int], bool],
    parent_session: Session,
    expires_in_seconds: int = 300,
) -> CrossSessionScheduleWriteCapability:
    """Ask separately and mint only after exact source/dispatch identity matches."""
    if (type(source) is not AuthorizedProjectRecordSource or not _is_issued_authorized_project_record_source(source)
            or type(parent_session) is not Session or source.conversation_id != parent_session.conversation_id
            or type(cancel_event) is not Event or not callable(confirm_fn) or not callable(live_binding_check)
            or type(expires_in_seconds) is not int or not 1 <= expires_in_seconds <= 300
            or type(request) is not ProjectWorkerHostRequest or type(reservation) is not ProjectWorkerReservation
            or request.project_id != source.project_id or request.task != task
            or reservation.project_id != request.project_id or reservation.task_id != request.task_id
            or reservation.conversation_id != parent_session.conversation_id
            or cancel_event.is_set()):
        raise PermissionError("single-source write authorization inputs are invalid")
    if live_binding_check(parent_session, reservation.parent_run_id, reservation.turn_epoch) is not True:
        raise PermissionError("parent dispatch is no longer live")
    source.store.validate_scope()
    if source.store.session_id == parent_session.conversation_id:
        raise PermissionError("cross-session capability is only for a distinct source Session")
    snapshot = source.store.snapshot()
    items = [row for row in snapshot.get("items", []) if row.get("item_id") == item_id]
    if len(items) != 1:
        raise PermissionError("exact source item is unavailable")
    item = items[0]
    requirements = [row for row in item.get("requirements", []) if row.get("requirement_id") == requirement_id]
    if (item.get("scope") != {"kind": "project", "project_id": source.project_id}
            or len(requirements) != 1 or requirements[0].get("text") != task
            or requirements[0].get("status") not in {"queued", "pending"}
            or requirements[0].get("current_run_id") is not None
            or requirements[0].get("schedule_claim") is not None):
        raise PermissionError("source requirement is stale or not safely queued")
    payload = {
        "operation": "one_cross_session_schedule_claim_and_start",
        "source": {"project_id": source.project_id, "source_session_id": source.store.session_id,
                   "item_id": item_id, "requirement_id": requirement_id,
                   "source_revision": snapshot["revision"], "requirement_text": task},
        "parent_dispatch": {"parent_session_id": parent_session.conversation_id,
                            "parent_run_id": reservation.parent_run_id,
                            "turn_epoch": reservation.turn_epoch,
                            "reservation_id": reservation.reservation_id,
                            "worker_id": reservation.worker_id,
                            "worker_run_id": reservation.worker_run_id},
        "project_io": {"project_id": request.project_id, "read_targets": list(request.read_targets),
                       "write_targets": list(request.write_targets)},
        "limits": {"uses": 1, "expires_in_seconds": expires_in_seconds,
                   "settlement": "host lifecycle only; operation result unknown and verification not verified",
                   "recovery": "unknown claims are never automatically reclaimed"},
    }
    choice = confirm_fn("CROSS_SESSION_SCHEDULE_SOURCE_WRITE_CONFIRM\n" + json.dumps(
        payload, ensure_ascii=False, sort_keys=True, indent=2))
    if (choice != "y" or cancel_event.is_set()
            or live_binding_check(parent_session, reservation.parent_run_id, reservation.turn_epoch) is not True
            or not _is_issued_authorized_project_record_source(source)):
        raise PermissionError("single-source write authorization was declined or became stale")
    source.store.validate_scope()
    after = source.store.snapshot()
    if after["revision"] != snapshot["revision"]:
        raise PermissionError("source revision changed during confirmation")
    capability = CrossSessionScheduleWriteCapability(
        str(__import__("uuid").uuid4()), source.authorization_id, source.store.session_id,
        parent_session.conversation_id, source.project_id, item_id, requirement_id, task,
        snapshot["revision"], _digest(item), reservation, request,
        time.monotonic() + expires_in_seconds,
    )
    with _LOCK:
        _CAPABILITIES[capability] = {"source": source, "used": False}
    return capability


def claim_and_start_cross_session_schedule(
    capability: CrossSessionScheduleWriteCapability, *, parent_session: Session,
    cancel_event: Event, live_binding_check: Callable[[Session, str, int], bool],
) -> dict[str, Any]:
    """Consume authority once, then perform source revision claim and run CAS."""
    with _LOCK:
        record = _CAPABILITIES.get(capability) if type(capability) is CrossSessionScheduleWriteCapability else None
        if record is None or record["used"]:
            raise PermissionError("source-write capability is unknown or already consumed")
        record["used"] = True
        source = record["source"]
    r = capability.reservation
    if (time.monotonic() >= capability.expires_at or cancel_event.is_set()
            or type(parent_session) is not Session
            or parent_session.conversation_id != capability.parent_session_id
            or live_binding_check(parent_session, r.parent_run_id, r.turn_epoch) is not True
            or not _is_issued_authorized_project_record_source(source)
            or source.authorization_id != capability.source_authorization_id
            or source.store.session_id != capability.source_session_id
            or source.project_id != capability.project_id):
        raise PermissionError("source-write capability expired or dispatch/source identity changed")
    source_reader = _reader_for(source.store)
    source_reader._validate_scope()
    store = WorkRecordStore(source_reader._session)
    store._validate_scope()
    item = store.get_item(capability.item_id)
    if (type(item) is not dict or _digest(item) != capability.source_item_digest
            or item.get("scope") != {"kind": "project", "project_id": capability.project_id}):
        raise PermissionError("source item changed after authorization")
    try:
        claim = store.claim_scheduled_requirement(
            capability.project_id, capability.item_id, capability.requirement_id,
            expected_revision=capability.source_revision, owner_id=r.worker_id,
            reservation_id=r.reservation_id, parent_run_id=r.parent_run_id,
        )
        started = store.begin_claimed_schedule_run(
            capability.project_id, capability.item_id, capability.requirement_id,
            run_id=r.worker_run_id, owner_id=r.worker_id, reservation_id=r.reservation_id,
            parent_run_id=r.parent_run_id, expected_requirement_text=capability.requirement_text,
        )
        if (claim.get("state") != "claimed" or started.get("current_run_id") != r.worker_run_id
                or started.get("status") != "in_progress"):
            raise PermissionError("source claim did not start the exact reserved requirement")
        return {"store": store, "claim": claim, "started": started}
    except Exception:
        # Claim persistence and run start are separate source-store transactions.
        # If the second transaction (or its response) fails, retain the claim as
        # unknown so it cannot look safely queued or be retried as fresh work.
        try:
            store.mark_schedule_claim_unknown(
                capability.item_id, capability.requirement_id,
                owner_id=r.worker_id, reservation_id=r.reservation_id,
                parent_run_id=r.parent_run_id,
            )
        except Exception:
            pass
        raise


def recover_cross_session_schedule_unknown(
    capability: CrossSessionScheduleWriteCapability, *,
    source_authorization: AuthorizedProjectRecordSource,
) -> bool:
    """Conservatively retain an interrupted claim as unknown; never release it."""
    with _LOCK:
        issued = _CAPABILITIES.get(capability) if type(capability) is CrossSessionScheduleWriteCapability else None
    if (issued is None or not issued["used"]
            or type(source_authorization) is not AuthorizedProjectRecordSource
            or not _is_issued_authorized_project_record_source(source_authorization)
            or source_authorization.authorization_id != capability.source_authorization_id
            or source_authorization.store.session_id != capability.source_session_id
            or source_authorization.project_id != capability.project_id):
        return False
    try:
        reader = _reader_for(source_authorization.store)
        reader._validate_scope()
        store = WorkRecordStore(reader._session)
        store.mark_schedule_claim_unknown(
            capability.item_id, capability.requirement_id,
            owner_id=capability.reservation.worker_id,
            reservation_id=capability.reservation.reservation_id,
            parent_run_id=capability.reservation.parent_run_id,
        )
        return True
    except Exception:
        return False


def settle_cross_session_schedule(
    source_store: WorkRecordStore, *, capability: CrossSessionScheduleWriteCapability,
    source_authorization: AuthorizedProjectRecordSource, outcome: ProjectWorkerHostOutcome,
    sequence: int,
) -> SettlementResult:
    """Persist an exact host lifecycle only; never infer operation success."""
    r = capability.reservation
    with _LOCK:
        issued = _CAPABILITIES.get(capability) if type(capability) is CrossSessionScheduleWriteCapability else None
    if (issued is None or not issued["used"]
            or type(source_store) is not WorkRecordStore
            or type(source_authorization) is not AuthorizedProjectRecordSource
            or not _is_issued_authorized_project_record_source(source_authorization)
            or source_authorization.authorization_id != capability.source_authorization_id
            or source_authorization.store.session_id != capability.source_session_id
            or source_store._session.conversation_id != capability.source_session_id
            or source_store._bound_project_id != capability.project_id
            or not project_worker_outcome_matches_reservation(r, outcome)
            or outcome.conversation_id != capability.parent_session_id
            or outcome.project_id != capability.project_id or outcome.task_id != capability.request.task_id
            or type(sequence) is not int or sequence != r.turn_epoch):
        return SettlementResult("rejected", record_status="unknown", reason="cross_session_settlement_identity_mismatch")
    try:
        source_store._validate_scope()
        item = source_store.get_item(capability.item_id)
        reqs = item.get("requirements") if type(item) is dict else None
        req = next((row for row in reqs if row.get("requirement_id") == capability.requirement_id), None) if type(reqs) is list else None
        claim = req.get("schedule_claim") if type(req) is dict else None
        if (item.get("scope") != {"kind": "project", "project_id": capability.project_id}
                or req.get("text") != capability.requirement_text
                or type(claim) is not dict
                or any(claim.get(key) != value for key, value in (
                    ("source_session_id", capability.source_session_id),
                    ("owner_id", r.worker_id), ("reservation_id", r.reservation_id),
                    ("parent_run_id", r.parent_run_id), ("project_id", r.project_id),
                    ("item_id", capability.item_id), ("requirement_id", capability.requirement_id),
                ))
                or req.get("current_run_id") != r.worker_run_id):
            return SettlementResult("rejected", record_status="unknown", reason="cross_session_source_claim_mismatch")
        result_id = "cross-worker-" + hashlib.sha256("\0".join((
            capability.source_session_id, capability.item_id, capability.requirement_id,
            r.worker_run_id, r.reservation_id,
        )).encode()).hexdigest()
        source_store.record_result_if_current_run(
            capability.item_id, capability.requirement_id, run_id=r.worker_run_id,
            result_id=result_id, status="unknown",
            summary="Cross-session worker host lifecycle recorded; side effects and requirement completion remain unknown.",
            verification={"status": "not_verified", "evidence_refs": [], "host_execution": {
                "sequence": sequence, "host_status": outcome.status,
                "source_session_id": capability.source_session_id,
                "parent_session_id": capability.parent_session_id,
                "project_id": capability.project_id, "item_id": capability.item_id,
                "requirement_id": capability.requirement_id, "task_id": r.task_id,
                "worker_id": r.worker_id, "worker_run_id": r.worker_run_id,
                "parent_run_id": r.parent_run_id, "reservation_id": r.reservation_id,
            }}, sources=[],
        )
        return SettlementResult("recorded", result_id, "unknown", reason="cross_session_lifecycle_only")
    except Exception:
        return SettlementResult("executed_not_recorded", record_status="unknown", reason="cross_session_settlement_unavailable")
