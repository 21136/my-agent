"""Fail-closed bridge from explicit project-worker outcomes to work records.

This records host lifecycle only. Worker text, turn completion, and write receipts
never establish requirement completion or verification; card publication stays off.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import re
from typing import Any, Literal

from project_worker_host import ProjectWorkerHostOutcome, ProjectWorkerHostRequest, ProjectWorkerReservation
from project_worker_model_loop import ProjectWorkerModelOutcome
from session import Session
from work_record_settlement import (
    BoundExecution,
    ExecutionReceipt,
    SettlementResult,
    WorkRecordSettlementAdapter,
)


@dataclass(frozen=True, slots=True)
class ProjectWorkerWorkRecordBinding:
    """Trusted pre-dispatch mapping from an explicit task to one recorded run.

    Construct and retain this from the approved host request and requirement/run
    mapping before dispatch. Its request is the explicit pre-dispatch host choice;
    never derive or reconstruct that request from worker output.
    """

    execution: BoundExecution
    request: ProjectWorkerHostRequest
    reservation: ProjectWorkerReservation | None = None


@dataclass(frozen=True, slots=True)
class ProjectWorkerCancellationReceipt:
    """Host-captured user stop action bound to the exact reserved worker run.

    The host must source this from its user-action path, never infer it from a
    generic cancellation Event (which also covers timeout/parent teardown).
    """

    session_id: str
    item_id: str
    requirement_id: str
    run_id: str
    project_id: str
    task_id: str
    reservation_id: str
    user_action_id: str
    source: Literal["user_action"]


@dataclass(frozen=True, slots=True)
class ProjectWorkerVerificationReceipt:
    """Receipt shape; ordinary construction does not authenticate host execution.

    Ordinary construction is not an authenticated host result. Project test
    reports are self-reports and cannot prove overall requirement completion.
    """

    session_id: str
    item_id: str
    requirement_id: str
    run_id: str
    project_id: str
    task_id: str
    reservation_id: str
    requirement_text: str
    verifier_id: str
    status: Literal["passed", "failed", "not_run"]
    evidence_refs: tuple[str, ...]


_VERIFIER_ID_RE = re.compile(r"host_[a-z][a-z0-9_]{0,47}\Z")
_EVIDENCE_REF_RE = re.compile(r"[A-Za-z0-9_.:/#-]{1,256}\Z")
_WORKER_BATCH_TASK_KEY_RE = re.compile(r"worker-batch-task:([0-9a-f]{64}):(0|[1-9][0-9]*)\Z")


def _requirement_matches_bound_scope(store: Any, item: Any,
                                     binding: ProjectWorkerWorkRecordBinding) -> bool:
    """Allow project items or independently scoped, marked current-dialogue batches.

    A project-valued ``source_ref`` alone is intentionally insufficient. The latter
    path requires the creator's paired original/task idempotency history and digest
    chain, plus the deterministic task identity used by the host dispatcher.
    """
    session = getattr(store, "_session", None)
    if (not isinstance(session, Session)
            or session.conversation_id != binding.execution.session_id):
        return False
    if type(item) is not dict or type(item.get("requirements")) is not list:
        return False
    matches = [row for row in item["requirements"] if type(row) is dict
               and row.get("requirement_id") == binding.execution.requirement_id]
    if len(matches) != 1:
        return False
    requirement = matches[0]
    request = binding.request
    if type(requirement.get("text")) is not str or requirement["text"] != request.task:
        return False
    scope = item.get("scope")
    if (type(scope) is dict and scope.get("kind") == "project"
            and scope.get("project_id") == request.project_id):
        return requirement.get("source_ref") in (None, {"kind": "project", "id": request.project_id})

    if (type(scope) is not dict or scope != {"kind": "independent", "object_ref": None}
            or item.get("title") != "多项目任务批次"
            or type(item.get("history")) is not list):
        return False
    try:
        from project_mode import normalize_project_id
        from work_records import _digest
        if normalize_project_id(request.project_id) != request.project_id:
            return False
        expected_task_id = "task-" + hashlib.sha256(
            "\0".join((session.conversation_id, item["item_id"],
                         binding.execution.requirement_id, request.project_id)).encode("utf-8")
        ).hexdigest()
        if request.task_id != expected_task_id:
            return False
        source_ref = {"kind": "project", "id": request.project_id}
        if requirement.get("source_ref") != source_ref:
            return False
        events = item["history"]
        task_events = []
        batch_digest = None
        for event in events:
            if type(event) is not dict:
                continue
            key = event.get("idempotency_key")
            match = _WORKER_BATCH_TASK_KEY_RE.fullmatch(key) if type(key) is str else None
            if match is None:
                continue
            payload = event.get("payload")
            if (type(payload) is dict
                    and payload.get("requirement_id") == binding.execution.requirement_id):
                task_events.append((event, match))
        if len(task_events) != 1:
            return False
        task_event, task_match = task_events[0]
        batch_digest = task_match.group(1)
        task_index = task_match.group(2)
        payload = task_event.get("payload")
        task_key = f"worker-batch-task:{batch_digest}:{task_index}"
        if (
            task_event.get("event_id") != f"worker-batch-task-{batch_digest}-{task_index}"
            or payload.get("text") != request.task
            or task_event.get("digest") != _digest({
                "kind": "requirement_added", "text": request.task, "source_ref": source_ref,
            })
            or task_event.get("idempotency_digest") != _digest({
                "kind": "append_requirement", "item_id": item["item_id"],
                "text": request.task, "source_ref": source_ref,
            })
        ):
            return False
        original_key = f"worker-batch-original:{batch_digest}"
        original_events = [event for event in events if type(event) is dict
                           and event.get("idempotency_key") == original_key]
        if len(original_events) != 1:
            return False
        original_event = original_events[0]
        original_payload = original_event.get("payload")
        if type(original_payload) is not dict:
            return False
        original_requirement_id = original_payload.get("requirement_id")
        original_text = original_payload.get("text")
        original_requirements = [row for row in item["requirements"] if type(row) is dict
                                 and row.get("requirement_id") == original_requirement_id]
        return (
            type(original_text) is str and bool(original_text.strip())
            and len(original_requirements) == 1
            and original_requirements[0].get("text") == original_text
            and "source_ref" not in original_requirements[0]
            and original_event.get("event_id") == f"worker-batch-original-{batch_digest}"
            and original_event.get("digest") == _digest({
                "kind": "requirement_added", "text": original_text, "source_ref": None,
            })
            and original_event.get("idempotency_digest") == _digest({
                "kind": "create_item_with_requirement", "title": "多项目任务批次",
                "scope": scope, "requirement": original_text,
            })
        )
    except Exception:
        return False


def project_worker_outcome_matches_binding(
    binding: ProjectWorkerWorkRecordBinding,
    outcome: ProjectWorkerHostOutcome,
) -> bool:
    """Check host identity against a pre-dispatch, trusted request/record binding."""
    if (
        type(binding) is not ProjectWorkerWorkRecordBinding
        or type(binding.execution) is not BoundExecution
        or type(binding.request) is not ProjectWorkerHostRequest
        or type(binding.request.project_id) is not str
        or not binding.request.project_id
        or type(binding.request.task_id) is not str
        or not binding.request.task_id
        or type(outcome) is not ProjectWorkerHostOutcome
        or outcome.conversation_id != binding.execution.session_id
        or outcome.project_id != binding.request.project_id
        or outcome.task_id != binding.request.task_id
        or type(outcome.status) is not str
        or outcome.status not in {"model_turn_finished", "blocked", "failed", "cancelled", "limit_reached"}
        or outcome.acceptance_status != "not_run"
        or type(outcome.acceptance_evidence) is not tuple
        or outcome.acceptance_evidence != ()
    ):
        return False
    reservation = binding.reservation
    if reservation is not None:
        if (
            type(reservation) is not ProjectWorkerReservation
            or reservation.conversation_id != binding.execution.session_id
            or reservation.project_id != binding.request.project_id
            or reservation.task_id != binding.request.task_id
            or binding.execution.run_id not in (reservation.worker_run_id, reservation.parent_run_id)
            or outcome.worker_id != reservation.worker_id
            or outcome.worker_run_id != reservation.worker_run_id
            or outcome.reservation_id != reservation.reservation_id
            or outcome.parent_run_id != reservation.parent_run_id
            or outcome.run_id != reservation.parent_run_id
            or outcome.turn_epoch != reservation.turn_epoch
        ):
            return False
    elif outcome.run_id != binding.execution.run_id:
        return False
    model_turn = outcome.model_turn
    return model_turn is None or (
        type(model_turn) is ProjectWorkerModelOutcome
        and model_turn.project_id == outcome.project_id
        and model_turn.status in {"model_turn_finished", "failed", "blocked", "cancelled", "limit_reached"}
    )


def _finish_reason(status: str) -> str:
    # A model turn ending is deliberately not a task-completion assertion.
    return {
        "failed": "error",
        "cancelled": "cancelled",
        "limit_reached": "timeout",
    }.get(status, "unknown")


def _record_explicit_lifecycle_result(store: Any, binding: ProjectWorkerWorkRecordBinding,
                                      *, status: Literal["blocked", "stopped"],
                                      sequence: int,
                                      project_worker_read_attempts: dict[str, Any] | None,
                                      publish_project_update: bool = False,
                                      publication_window_seconds: float = 30.0) -> SettlementResult:
    execution = binding.execution
    raw = "\0".join((execution.session_id, execution.item_id, execution.requirement_id,
                       execution.run_id, status, str(sequence)))
    result_id = f"worker-{status}-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()
    summary = ("Host reported the project worker as blocked; side effects, if any, are not asserted absent."
               if status == "blocked" else
               "User requested the project worker stop; side effects, if any, remain unverified.")
    try:
        publish_blocked = status == "blocked" and publish_project_update
        record = (store.record_blocked_worker_settlement_if_current_run if publish_blocked
                  else store.record_result_if_current_run)
        options = ({"publication_window_seconds": publication_window_seconds} if publish_blocked
                   else {"status": status})
        record(
            execution.item_id, execution.requirement_id, run_id=execution.run_id,
            result_id=result_id, **options, summary=summary,
            verification={"status": "not_verified", "evidence_refs": [],
                          "host_execution": {
                              "sequence": sequence, "host_status": status,
                              **({} if project_worker_read_attempts is None else {
                                  "project_worker_read_attempts": project_worker_read_attempts,
                              }),
                          }},
            sources=[],
        )
    except Exception as exc:
        from work_records import WorkRecordConflict, WorkRecordStaleRun
        if isinstance(exc, WorkRecordStaleRun):
            return SettlementResult("rejected", result_id, "unknown", False, "stale_run")
        if isinstance(exc, WorkRecordConflict):
            return SettlementResult("conflict", result_id, "unknown", False, "result_id_fact_conflict")
        return SettlementResult("executed_not_recorded", result_id, "unknown", False,
                                f"{status}_lifecycle_not_recorded")
    return SettlementResult("recorded", result_id, status)


def _record_explicit_blocked_result(store: Any, binding: ProjectWorkerWorkRecordBinding,
                                    outcome: ProjectWorkerHostOutcome, sequence: int,
                                    project_worker_read_attempts: dict[str, Any] | None,
                                    publish_project_update: bool, publication_window_seconds: float) -> SettlementResult:
    return _record_explicit_lifecycle_result(
        store, binding, status="blocked", sequence=sequence,
        project_worker_read_attempts=project_worker_read_attempts,
        publish_project_update=publish_project_update, publication_window_seconds=publication_window_seconds,
    )


def _user_cancellation_matches(binding: ProjectWorkerWorkRecordBinding,
                               outcome: ProjectWorkerHostOutcome,
                               receipt: ProjectWorkerCancellationReceipt) -> bool:
    reservation = binding.reservation
    if type(receipt) is not ProjectWorkerCancellationReceipt or type(reservation) is not ProjectWorkerReservation:
        return False
    return (
        outcome.status == "cancelled"
        and receipt.source == "user_action"
        and type(receipt.source) is str
        and type(receipt.user_action_id) is str and bool(receipt.user_action_id.strip())
        and len(receipt.user_action_id) <= 128
        and not any(ord(char) < 32 for char in receipt.user_action_id)
        and receipt.session_id == binding.execution.session_id == reservation.conversation_id
        and receipt.item_id == binding.execution.item_id
        and receipt.requirement_id == binding.execution.requirement_id
        and receipt.run_id == binding.execution.run_id == reservation.worker_run_id
        and receipt.project_id == binding.request.project_id == reservation.project_id
        and receipt.task_id == binding.request.task_id == reservation.task_id
        and receipt.reservation_id == reservation.reservation_id == outcome.reservation_id
    )


def settle_project_worker_work_record(
    store: Any,
    binding: ProjectWorkerWorkRecordBinding,
    outcome: ProjectWorkerHostOutcome,
    *,
    sequence: int,
    execution_receipt: ExecutionReceipt | None = None,
    verification_receipt: Any | None = None,
    verification_host: Any | None = None,
    verification_bridge: Any | None = None,
    cancellation_receipt: ProjectWorkerCancellationReceipt | None = None,
    publish_project_update: bool = False,
    publication_window_seconds: float = 30.0,
) -> SettlementResult:
    """Persist a matching worker lifecycle receipt against an existing bound run.

    The caller must pass a trusted task-to-requirement binding captured before
    dispatch, plus the server lifecycle sequence. Host outcome task/project/session/run
    identities must match that binding. This function never creates a requirement/run
    and never publishes a work card.
    """
    if type(sequence) is not int or sequence < 1:
        return SettlementResult("rejected", record_status="unknown", reason="invalid_execution_sequence")
    if not project_worker_outcome_matches_binding(binding, outcome):
        return SettlementResult("rejected", record_status="unknown", reason="worker_outcome_binding_mismatch")
    worker_read_receipt = None
    if outcome.model_turn is not None:
        from project_worker_read_evidence import serialize_project_worker_read_attempts
        read_available, read_attempts = serialize_project_worker_read_attempts(outcome.model_turn)
        worker_read_receipt = {"available": read_available, "attempts": read_attempts}

    # Verify the explicitly selected record belongs to this worker's project.
    try:
        item = store.get_item(binding.execution.item_id)
        scope = item.get("scope") if type(item) is dict else None
        if not _requirement_matches_bound_scope(store, item, binding):
            return SettlementResult("rejected", record_status="unknown", reason="worker_record_scope_mismatch")
    except Exception:
        return SettlementResult("rejected", record_status="unknown", reason="worker_record_unavailable")

    if execution_receipt is not None:
        if (
            type(execution_receipt) is not ExecutionReceipt
            or sequence != execution_receipt.sequence
            or any(getattr(execution_receipt, name) != getattr(binding.execution, name)
                   for name in ("session_id", "item_id", "requirement_id", "run_id"))
        ):
            return SettlementResult("rejected", record_status="unknown", reason="worker_settlement_receipt_mismatch")
        receipt = execution_receipt
    else:
        receipt = ExecutionReceipt(
            session_id=binding.execution.session_id,
            item_id=binding.execution.item_id,
            requirement_id=binding.execution.requirement_id,
            run_id=binding.execution.run_id,
            sequence=sequence,
            # The settlement adapter intentionally does not persist this broad outcome.
            outcome="succeeded" if outcome.status == "model_turn_finished" else (
                "cancelled" if outcome.status == "cancelled" else
                "timeout" if outcome.status == "limit_reached" else "failed"
            ),
            finish_reason=_finish_reason(outcome.status),
        )
    if outcome.status == "blocked" and binding.reservation is not None:
        return _record_explicit_blocked_result(
            store, binding, outcome, sequence, worker_read_receipt,
            publish_project_update, publication_window_seconds,
        )
    if outcome.status == "cancelled" and cancellation_receipt is not None:
        if not _user_cancellation_matches(binding, outcome, cancellation_receipt):
            return SettlementResult("rejected", record_status="unknown", reason="worker_cancellation_receipt_mismatch")
        return _record_explicit_lifecycle_result(
            store, binding, status="stopped", sequence=sequence,
            project_worker_read_attempts=worker_read_receipt,
        )
    if verification_receipt is not None:
        from project_worker_host import (
            ProjectWorkerHost, _ProjectWorkerSelfReportReceipt, _resolve_bound_project,
        )
        from project_verify import requirement_pytest_input_snapshot
        if type(verification_host) is not ProjectWorkerHost or verification_bridge is None:
            return SettlementResult("rejected", record_status="unknown", reason="worker_requirement_verifier_not_integrated")
        try:
            with verification_bridge._result_view_write_lock:
                with verification_host._requirement_verification_lock:
                    if (type(verification_receipt) is _ProjectWorkerSelfReportReceipt
                            and verification_receipt._consumed):
                        return SettlementResult(
                            "rejected", record_status="unknown",
                            reason="worker_self_report_receipt_replayed",
                        )
                    row = verification_host._requirement_verifications.pop(verification_receipt, None)
                    if row is None:
                        return SettlementResult(
                            "rejected", record_status="unknown",
                            reason=("worker_self_report_stale"
                                    if type(verification_receipt) is _ProjectWorkerSelfReportReceipt
                                    else "worker_self_report_receipt_unknown"),
                        )
                    if type(verification_receipt) is _ProjectWorkerSelfReportReceipt:
                        verification_receipt._consumed = True
                    snapshot = verification_bridge.lifecycle.snapshot()
                    if (type(verification_receipt) is not _ProjectWorkerSelfReportReceipt
                            or type(row) is not tuple or len(row) != 8
                            or row[7] is not verification_host._requirement_self_report_epoch
                            or row[:4] != (verification_bridge, store, binding, outcome)
                            or verification_bridge._session_provider() is not store._session
                            or verification_bridge.cancel_event.is_set() or verification_bridge._connection_closed.is_set()
                            or snapshot is None or binding.reservation is None
                            or snapshot.run_id != binding.reservation.parent_run_id or snapshot.sequence != sequence
                            or snapshot.state != "settled" or snapshot.ok is not True or snapshot.cancel_requested
                            or snapshot.finish_reason not in {"completed", "stop"}
                            or verification_bridge.lifecycle_conversation_id != binding.execution.session_id):
                        return SettlementResult("rejected", record_status="unknown", reason="worker_self_report_stale")
                    target, root, identity = row[4:7]
                    with verification_host._reservation_lock:
                        if verification_host._reservations.get(binding.reservation.reservation_id) != (binding.reservation, True):
                            return SettlementResult("rejected", record_status="unknown", reason="worker_self_report_stale")
                    if (_resolve_bound_project(store._session.paths, binding.request.project_id) != root
                            or requirement_pytest_input_snapshot(root, target) != identity
                            or not _requirement_matches_bound_scope(store, store.get_item(binding.execution.item_id), binding)):
                        return SettlementResult("rejected", record_status="unknown", reason="worker_self_report_stale")
                    e = binding.execution
                    raw = "\0".join((e.session_id, e.item_id, e.requirement_id, e.run_id, target))
                    result_id = "worker-self-report-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()
                    verification = {"status": "not_verified", "evidence_refs": [],
                        "project_test": {"status": "passed", "source": "user_authorized_project_self_report",
                                         "pytest_target": target, "exit_code": 0},
                        "host_execution": {"sequence": sequence, "session_id": e.session_id,
                            "project_id": binding.request.project_id, "item_id": e.item_id,
                            "requirement_id": e.requirement_id, "parent_run_id": binding.reservation.parent_run_id,
                            "worker_run_id": binding.reservation.worker_run_id,
                            "worker_id": binding.reservation.worker_id, "task_id": binding.reservation.task_id,
                            "reservation_id": binding.reservation.reservation_id}}
                    store.record_project_self_reported_worker_settlement_if_current_run(
                        e.item_id, e.requirement_id, run_id=e.run_id, result_id=result_id,
                        verification=verification, expected_project_id=binding.request.project_id,
                        expected_requirement_text=binding.request.task,
                        publish_project_update=publish_project_update,
                        publication_window_seconds=publication_window_seconds)
                    return SettlementResult("recorded", result_id, "unknown", reason="project_self_reported_pass")
        except Exception:
            return SettlementResult("rejected", record_status="unknown", reason="worker_self_report_not_recorded")
    receipt = replace(receipt, project_worker_read_attempts=worker_read_receipt)
    return WorkRecordSettlementAdapter(store, binding.execution).settle(
        receipt,
        publish_project_update=publish_project_update,
        publication_window_seconds=publication_window_seconds,
    )





