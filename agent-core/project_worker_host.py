"""Trusted host boundary for explicitly selected, session-scoped project workers.

Agent dispatch calls this host with explicit project/task/paths and three separate approvals:
scope authorization, exact outbound-request confirmation, and ToolExecutor writes.
No project is inferred from natural language or the parent's current UI selection.
"""
from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event, Lock
from typing import Any, Callable, Literal
from uuid import uuid4
from weakref import WeakSet
import json

from llm_client import LLMClient
from paths import AgentPaths
from project_mode import is_project_dir, list_projects, normalize_project_id, project_dir
from project_worker_execution import ProjectWorkerCoordinator, ProjectWorkerSpec
from project_worker_model_loop import (
    MAX_REQUESTS,
    ProjectWorkerModelOutcome,
    run_project_worker_model_loop,
)
from session import Session, sessions_root
from tools.executor import ExecutorSession, ToolExecutor
from tools.registry import ToolRegistry

_MAX_HOST_TASK_CHARS = 8_000
_MAX_HOST_TARGETS = 32
_MAX_WORKER_BATCH_SIZE = 25
_CLIENTS_LOCK = Lock()
_CLAIMED_CLIENTS: WeakSet[LLMClient] = WeakSet()


@dataclass(frozen=True, slots=True)
class ProjectWorkerHostRequest:
    """An explicit host choice; never populated by worker/model text parsing."""

    project_id: str
    task_id: str
    task: str
    read_targets: tuple[str, ...]
    write_targets: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ProjectWorkerReservation:
    """One-shot host-issued identity reserved before a worker can execute."""

    reservation_id: str
    worker_id: str
    worker_run_id: str
    parent_run_id: str
    turn_epoch: int
    conversation_id: str
    project_id: str
    task_id: str


@dataclass(frozen=True, slots=True)
class ProjectWorkerHostOutcome:
    worker_id: str
    run_id: str  # Legacy alias for parent_run_id; never the worker run identity.
    turn_epoch: int
    conversation_id: str
    project_id: str
    task_id: str
    status: Literal["model_turn_finished", "blocked", "failed", "cancelled", "limit_reached"]
    model_turn: ProjectWorkerModelOutcome | None = field(default=None, repr=False)
    acceptance_status: Literal["not_run"] = "not_run"
    acceptance_evidence: tuple[str, ...] = ()
    error_code: str = ""
    parent_run_id: str = ""
    worker_run_id: str = ""
    reservation_id: str = ""


def project_worker_outcome_matches_reservation(
    reservation: ProjectWorkerReservation,
    outcome: ProjectWorkerHostOutcome,
) -> bool:
    """Verify every worker and parent identity against one exact host reservation."""
    if (
        type(reservation) is not ProjectWorkerReservation
        or type(outcome) is not ProjectWorkerHostOutcome
        or outcome.worker_id != reservation.worker_id
        or outcome.worker_run_id != reservation.worker_run_id
        or outcome.reservation_id != reservation.reservation_id
        or outcome.parent_run_id != reservation.parent_run_id
        or outcome.run_id != reservation.parent_run_id
        or outcome.turn_epoch != reservation.turn_epoch
        or outcome.conversation_id != reservation.conversation_id
        or outcome.project_id != reservation.project_id
        or outcome.task_id != reservation.task_id
        or outcome.status not in {"model_turn_finished", "blocked", "failed", "cancelled", "limit_reached"}
        or outcome.acceptance_status != "not_run"
        or type(outcome.acceptance_evidence) is not tuple
        or outcome.acceptance_evidence != ()
    ):
        return False
    model_turn = outcome.model_turn
    return model_turn is None or (
        type(model_turn) is ProjectWorkerModelOutcome
        and model_turn.project_id == reservation.project_id
        and model_turn.status == outcome.status
    )


def _safe_project_id(value: object) -> str | None:
    if type(value) is not str:
        return None
    try:
        normalized = normalize_project_id(value)
    except Exception:
        return None
    return normalized if value == normalized else None


def _host_failure(session: object, request: object, code: str, *, status: str = "blocked",
                  worker_id: str = "", run_id: str = "", turn_epoch: int = 0,
                  reservation: ProjectWorkerReservation | None = None) -> ProjectWorkerHostOutcome:
    conversation_id = session.conversation_id if type(session) is Session else ""
    project_id = request.project_id if type(request) is ProjectWorkerHostRequest else ""
    task_id = request.task_id if type(request) is ProjectWorkerHostRequest else ""
    if reservation is not None:
        worker_id = reservation.worker_id
        run_id = reservation.parent_run_id
        turn_epoch = reservation.turn_epoch
        conversation_id = reservation.conversation_id
        project_id = reservation.project_id
        task_id = reservation.task_id
    return ProjectWorkerHostOutcome(
        worker_id=reservation.worker_id if reservation is not None else worker_id,
        run_id=run_id, turn_epoch=turn_epoch,
        conversation_id=conversation_id, project_id=project_id, task_id=task_id,
        status=status, error_code=code,
        parent_run_id=run_id,
        worker_run_id=reservation.worker_run_id if reservation is not None else "",
        reservation_id=reservation.reservation_id if reservation is not None else "",
    )  # type: ignore[arg-type]


def _validate_parent_session(session: Session, *, require_execution: bool = True) -> tuple[AgentPaths, str]:
    if type(session) is not Session or type(session.paths) is not AgentPaths:
        raise PermissionError("trusted live Session required")
    if require_execution:
        if session.meta.harness != "desktop" or session.meta.turn_mode != "agent":
            raise PermissionError("project worker host requires an active desktop agent session")
        if getattr(session, "_ordinary_work_intent", None) != "execute":
            raise PermissionError("current host turn is not authorized for execution")
        if getattr(session, "next_work_intent", None) not in (None, "execute"):
            raise PermissionError("pending host intent does not authorize execution")
    conversation_id = session.conversation_id
    if (type(conversation_id) is not str or not conversation_id
            or any(char in conversation_id for char in "\\/:\0")):
        raise PermissionError("invalid parent conversation identity")
    expected = (sessions_root(session.paths) / conversation_id).absolute()
    actual = Path(session.session_dir).absolute()
    ProjectWorkerCoordinator._assert_no_reparse_chain(expected)
    ProjectWorkerCoordinator._assert_no_reparse_chain(actual)
    if (actual.resolve(strict=True) != expected.resolve(strict=True)
            or actual.name != conversation_id or not actual.is_dir()):
        raise PermissionError("parent Session is not bound to its stored conversation directory")
    return session.paths, conversation_id


def _resolve_bound_project(paths: AgentPaths, project_id: str) -> Path:
    if project_id not in list_projects(paths):
        raise PermissionError("project is not an existing registered workspace project")
    declared = paths.workspace / project_id
    ProjectWorkerCoordinator._assert_no_reparse_chain(declared)
    root = Path(project_dir(paths, project_id)).resolve(strict=True)
    expected = declared.resolve(strict=True)
    if root != expected or not is_project_dir(root):
        raise PermissionError("project root is aliased, external, or not a project directory")
    ProjectWorkerCoordinator._secure_directory_identity(root)
    return root


def _validate_targets(root: Path, request: ProjectWorkerHostRequest) -> None:
    if (type(request.read_targets) is not tuple or type(request.write_targets) is not tuple
            or not request.write_targets or len(request.read_targets) > _MAX_HOST_TARGETS
            or len(request.write_targets) > _MAX_HOST_TARGETS):
        raise PermissionError("explicit bounded read/write targets are required")
    for group in (request.read_targets, request.write_targets):
        if any(type(item) is not str for item in group):
            raise PermissionError("invalid target list")
        if len({item.casefold() for item in group}) != len(group):
            raise PermissionError("duplicate or aliased target")
        for relative in group:
            try:
                ProjectWorkerCoordinator._target_identity(root, relative)
            except Exception:
                raise PermissionError("invalid target path or directory scope") from None


def _parent_write_scope_allows(session: Session, project_id: str,
                               write_targets: tuple[str, ...]) -> bool:
    """A worker scope confirmation may narrow grants, never widen Session fences."""
    raw_scope = (session.meta.write_scope_root or "").strip()
    if not raw_scope:
        return True
    try:
        from sandbox_evolution import normalize_write_scope_root
        scope = normalize_write_scope_root(raw_scope).casefold()
    except Exception:
        return False
    root = f"workspace/{project_id}".casefold()
    for target in write_targets:
        relative = f"{root}/{target.replace(chr(92), '/') .strip('/')}".casefold()
        if relative != scope and not relative.startswith(scope.rstrip("/") + "/"):
            return False
    return True


def _scope_preview(session: Session, request: ProjectWorkerHostRequest, reservation: ProjectWorkerReservation,
                   max_requests: int) -> str:
    preview = {
        "action": "authorize_project_worker_scope",
        "worker_id": reservation.worker_id,
        "worker_run_id": reservation.worker_run_id,
        "reservation_id": reservation.reservation_id,
        "parent_run_id": reservation.parent_run_id,
        "run_id": reservation.parent_run_id,
        "turn_epoch": reservation.turn_epoch,
        "conversation_id": session.conversation_id,
        "parent_project_id": session.meta.project_id or None,
        "project_id": request.project_id,
        "task_id": request.task_id,
        "task": request.task,
        "project_root_binding": f"workspace/{request.project_id}",
        "read_targets": list(request.read_targets),
        "write_targets": list(request.write_targets),
        "max_requests": max_requests,
        "acceptance": "not_run_by_host",
    }
    return json.dumps(preview, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class _ProjectWorkerSelfReportReceipt:
    """Opaque host-issued identity token with in-place one-shot state."""

    __slots__ = ("_consumed",)

    def __init__(self) -> None:
        self._consumed = False


class ProjectWorkerHost:
    """Create a dedicated project executor and call the real bounded model loop."""

    def __init__(self, llm_client_factory: Callable[[], LLMClient]) -> None:
        if not callable(llm_client_factory):
            raise ValueError("a dedicated LLMClient factory is required")
        self._llm_client_factory = llm_client_factory
        # WsBridge has one pending request_id slot. All confirmations from this
        # parent Agent share this gate, while workers keep their execution leases.
        self._confirmation_gate = Lock()
        self._reservation_lock = Lock()
        self._reservation_instance_id = str(uuid4())
        self._reservation_sequence = 0
        self._reservations: dict[str, tuple[ProjectWorkerReservation, bool]] = {}
        self._reservations_by_parent: dict[tuple[str, str, int], set[str]] = {}
        self._reserved_task_scopes: set[tuple[str, str, int, str, str]] = set()
        self._reservation_capacity = 10_000
        # In-process one-shot authority for user-authorized project self-reports.
        self._requirement_self_report_epoch = object()
        self._requirement_verification_lock = Lock()
        self._requirement_verification_attempts: set[str] = set()
        self._requirement_verifications: dict[object, tuple[Any, ...]] = {}

    def reserve_many(
        self,
        session: Session,
        requests: list[ProjectWorkerHostRequest],
        *,
        parent_run_id: str,
        turn_epoch: int,
        live_binding_check: Callable[[Session, str, int], bool],
    ) -> tuple[ProjectWorkerReservation, ...]:
        """Issue one-shot identities for this exact request batch before dispatch.

        The parent run remains the live-execution authority. Each worker gets a
        separate host-generated run identity; reservations are retained by this
        host instance and can be consumed only once by a matching dispatch.
        """
        if (type(parent_run_id) is not str or not parent_run_id.strip()
                or len(parent_run_id) > 128 or any(ord(char) < 32 for char in parent_run_id)
                or type(turn_epoch) is not int or turn_epoch <= 0
                or type(requests) is not list or not 1 <= len(requests) <= _MAX_WORKER_BATCH_SIZE
                or any(type(item) is not ProjectWorkerHostRequest for item in requests)):
            raise ValueError("valid parent run, epoch, and bounded explicit requests are required")
        _paths, conversation_id = _validate_parent_session(session, require_execution=False)
        if not callable(live_binding_check):
            raise PermissionError("live parent-run verifier is required to reserve workers")
        seen_tasks: set[tuple[str, str]] = set()
        reservations: list[ProjectWorkerReservation] = []
        for request in requests:
            if (_safe_project_id(request.project_id) is None
                    or type(request.task_id) is not str or not request.task_id.strip()
                    or len(request.task_id) > 128 or any(ord(char) < 32 for char in request.task_id)
                    or type(request.task) is not str or not request.task.strip()
                    or len(request.task) > _MAX_HOST_TASK_CHARS):
                raise ValueError("each reservation requires a valid explicit project task")
            task_key = (request.project_id, request.task_id)
            if task_key in seen_tasks:
                raise ValueError("request task identities must be unique within the batch")
            seen_tasks.add(task_key)
        with self._reservation_lock:
            try:
                if live_binding_check(session, parent_run_id, turn_epoch) is not True:
                    raise PermissionError("parent run is not currently live")
            except Exception as exc:
                raise PermissionError("parent run is not currently live") from exc
            task_scopes = {
                (conversation_id, parent_run_id, turn_epoch, request.project_id, request.task_id)
                for request in requests
            }
            if task_scopes & self._reserved_task_scopes:
                raise ValueError("task identity was already reserved for this parent run")
            if len(self._reservations) + len(reservations) > self._reservation_capacity:
                raise RuntimeError("host worker reservation capacity reached")
            self._reserved_task_scopes.update(task_scopes)
            parent_key = (conversation_id, parent_run_id, turn_epoch)
            parent_reservations = self._reservations_by_parent.setdefault(parent_key, set())
            for request in requests:
                self._reservation_sequence += 1
                reservation = ProjectWorkerReservation(
                    reservation_id=f"{self._reservation_instance_id}:{self._reservation_sequence}",
                    worker_id=str(uuid4()),
                    worker_run_id=str(uuid4()),
                    parent_run_id=parent_run_id,
                    turn_epoch=turn_epoch,
                    conversation_id=conversation_id,
                    project_id=request.project_id,
                    task_id=request.task_id,
                )
                reservations.append(reservation)
                self._reservations[reservation.reservation_id] = (reservation, False)
                parent_reservations.add(reservation.reservation_id)
        return tuple(reservations)

    def release_parent_run(
        self,
        *,
        conversation_id: str,
        parent_run_id: str,
        turn_epoch: int,
    ) -> int:
        """Release only this finalized parent run's reservation state.

        Removed reservation IDs become unrecognized, so retaining their full
        credentials is unnecessary for replay rejection. Other parent runs stay
        live and their reservations/scopes are untouched.
        """
        if (type(conversation_id) is not str or not conversation_id
                or type(parent_run_id) is not str or not parent_run_id
                or type(turn_epoch) is not int or turn_epoch <= 0):
            return 0
        parent_key = (conversation_id, parent_run_id, turn_epoch)
        with self._reservation_lock:
            reservation_ids = self._reservations_by_parent.pop(parent_key, set())
            released = 0
            for reservation_id in reservation_ids:
                stored = self._reservations.pop(reservation_id, None)
                if stored is None:
                    continue
                reservation = stored[0]
                self._reserved_task_scopes.discard((
                    reservation.conversation_id, reservation.parent_run_id,
                    reservation.turn_epoch, reservation.project_id, reservation.task_id,
                ))
                released += 1
        with self._requirement_verification_lock:
            self._requirement_verification_attempts.difference_update(reservation_ids)
            for token, row in list(self._requirement_verifications.items()):
                if row[2].reservation.reservation_id in reservation_ids:
                    if type(token) is _ProjectWorkerSelfReportReceipt:
                        token._consumed = True
                    del self._requirement_verifications[token]
        return released

    def _consume_reservations(
        self,
        session: Session,
        requests: list[ProjectWorkerHostRequest],
        reservations: tuple[ProjectWorkerReservation, ...],
        *,
        parent_run_id: str,
        turn_epoch: int,
    ) -> str | None:
        if (len(reservations) != len(requests)
                or any(type(row) is not ProjectWorkerReservation for row in reservations)):
            return "worker_reservation_mismatch"
        try:
            conversation_id = session.conversation_id
        except Exception:
            return "worker_reservation_mismatch"
        if any(
            row.parent_run_id != parent_run_id or row.turn_epoch != turn_epoch
            or row.conversation_id != conversation_id
            or row.project_id != request.project_id or row.task_id != request.task_id
            for request, row in zip(requests, reservations)
        ):
            return "worker_reservation_mismatch"
        with self._reservation_lock:
            stored_rows = [self._reservations.get(row.reservation_id) for row in reservations]
            if any(stored is None or stored[0] != row for stored, row in zip(stored_rows, reservations)):
                return "worker_reservation_unrecognized"
            if any(stored[1] for stored in stored_rows if stored is not None):
                return "worker_reservation_replayed"
            for row in reservations:
                self._reservations[row.reservation_id] = (row, True)
        return None

    def _serialized_confirm(self, callback: Callable[..., str], cancel_event: Event,
                            *args: Any) -> str:
        while not self._confirmation_gate.acquire(timeout=0.05):
            if cancel_event.is_set():
                return "n"
        try:
            if cancel_event.is_set():
                return "n"
            try:
                choice = callback(*args)
            except Exception:
                return "n"
            if cancel_event.is_set():
                return "n"
            return choice if type(choice) is str else "n"
        finally:
            self._confirmation_gate.release()

    def run_many(
        self,
        session: Session,
        requests: list[ProjectWorkerHostRequest],
        *,
        scope_confirm_fn: Callable[[str], str] | None,
        outbound_request_confirm_fn: Callable[[str], str] | None,
        write_confirm_fn: Callable[[str, bool], str] | None,
        cancel_event: Event,
        run_id: str | None = None,
        parent_run_id: str | None = None,
        turn_epoch: int | None = None,
        live_binding_check: Callable[[Session, str, int], bool] | None = None,
        max_requests: int = 8,
        reservations: tuple[ProjectWorkerReservation, ...] | None = None,
        before_worker_start: Callable[[ProjectWorkerHostRequest, ProjectWorkerReservation], bool] | None = None,
        on_worker_progress: Callable[[str, ProjectWorkerHostRequest, ProjectWorkerReservation], None] | None = None,
    ) -> tuple[ProjectWorkerHostOutcome, ...]:
        """Dispatch up to twenty-five tasks with at most two active workers, consuming exact pre-reserved identities once.

        ``run_id`` remains a compatibility alias for the parent run. The worker
        run IDs are always host-reserved and are never substituted for live-turn
        authorization or the parent lifecycle identity.
        """
        if (type(requests) is not list or not 1 <= len(requests) <= _MAX_WORKER_BATCH_SIZE
                or any(type(item) is not ProjectWorkerHostRequest for item in requests)):
            return (_host_failure(session, None, "worker_batch_limit_or_shape_invalid", status="blocked",
                                  run_id=parent_run_id or run_id or "", turn_epoch=turn_epoch or 0),)
        if parent_run_id is not None and run_id is not None and parent_run_id != run_id:
            return tuple(_host_failure(session, item, "parent_run_identity_mismatch",
                                       run_id=parent_run_id, turn_epoch=turn_epoch or 0) for item in requests)
        parent = parent_run_id if parent_run_id is not None else (run_id or "")
        if type(turn_epoch) is not int or turn_epoch <= 0:
            return tuple(_host_failure(session, item, "live_binding_required",
                                       run_id=parent, turn_epoch=0) for item in requests)
        try:
            active_reservations = reservations if reservations is not None else self.reserve_many(
                session, requests, parent_run_id=parent, turn_epoch=turn_epoch,
                live_binding_check=live_binding_check,
            )
        except Exception:
            return tuple(_host_failure(session, item, "worker_reservation_unavailable",
                                       run_id=parent, turn_epoch=turn_epoch) for item in requests)
        reservation_error = self._consume_reservations(
            session, requests, active_reservations,
            parent_run_id=parent, turn_epoch=turn_epoch,
        )
        if reservation_error is not None:
            return tuple(_host_failure(
                session, request, reservation_error, reservation=reservation,
            ) for request, reservation in zip(requests, active_reservations)) or (
                _host_failure(session, None, reservation_error, run_id=parent, turn_epoch=turn_epoch),
            )

        try:
            paths, _conversation_id = _validate_parent_session(session)
        except Exception:
            return tuple(_host_failure(
                session, item, "parent_session_or_project_binding_invalid", reservation=reservation,
            ) for item, reservation in zip(requests, active_reservations))
        outcomes: list[ProjectWorkerHostOutcome | None] = [None] * len(requests)
        root_keys: list[tuple[int, int, int] | None] = [None] * len(requests)
        pending: list[int] = []
        for index, (item, reservation) in enumerate(zip(requests, active_reservations)):
            try:
                root = _resolve_bound_project(paths, item.project_id)
                _validate_targets(root, item)
                root_keys[index] = ProjectWorkerCoordinator._secure_directory_identity(root)
                pending.append(index)
            except Exception:
                outcomes[index] = _host_failure(
                    session, item, "project_or_target_scope_invalid", reservation=reservation,
                )

        active: dict[Any, tuple[int, tuple[int, int, int]]] = {}
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="project-worker-host") as pool:
            while pending or active:
                if cancel_event.is_set():
                    for index in pending:
                        outcomes[index] = _host_failure(
                            session, requests[index], "cancelled", status="cancelled",
                            reservation=active_reservations[index],
                        )
                    pending.clear()
                active_roots = {root_identity for _idx, root_identity in active.values()}
                for index in list(pending):
                    if len(active) >= 2:
                        break
                    root_identity = root_keys[index]
                    if root_identity is None or root_identity in active_roots:
                        continue
                    pending.remove(index)
                    try:
                        future = pool.submit(
                            self._run_reserved, session, requests[index], active_reservations[index],
                            scope_confirm_fn=scope_confirm_fn,
                            outbound_request_confirm_fn=outbound_request_confirm_fn,
                            write_confirm_fn=write_confirm_fn,
                            cancel_event=cancel_event,
                            live_binding_check=live_binding_check,
                            max_requests=max_requests,
                            before_worker_start=before_worker_start,
                            on_worker_progress=on_worker_progress,
                        )
                    except Exception:
                        # No worker thread was created for this reservation; keep the
                        # host outcome explicit while harvesting other submitted work.
                        outcomes[index] = _host_failure(
                            session, requests[index], "worker_dispatch_submit_failed",
                            status="blocked", reservation=active_reservations[index],
                        )
                        continue
                    active[future] = (index, root_identity)
                    active_roots.add(root_identity)
                if active:
                    done, _ = wait(tuple(active), return_when=FIRST_COMPLETED)
                    for future in done:
                        index, _root_identity = active.pop(future)
                        try:
                            outcomes[index] = future.result()
                        except Exception:
                            outcomes[index] = _host_failure(
                                session, requests[index], "worker_host_failed", status="failed",
                                reservation=active_reservations[index],
                            )
                        if on_worker_progress is not None:
                            try:
                                on_worker_progress("returned", requests[index], active_reservations[index])
                            except Exception:
                                pass
                elif pending:
                    for index in pending:
                        outcomes[index] = _host_failure(
                            session, requests[index], "project_root_identity_unavailable",
                            reservation=active_reservations[index],
                        )
                    pending.clear()
        return tuple(item for item in outcomes if item is not None)

    def run(
        self,
        session: Session,
        request: ProjectWorkerHostRequest,
        *,
        scope_confirm_fn: Callable[[str], str] | None,
        outbound_request_confirm_fn: Callable[[str], str] | None,
        write_confirm_fn: Callable[[str, bool], str] | None,
        cancel_event: Event,
        run_id: str | None = None,
        parent_run_id: str | None = None,
        turn_epoch: int | None = None,
        live_binding_check: Callable[[Session, str, int], bool] | None = None,
        max_requests: int = 8,
        reservation: ProjectWorkerReservation | None = None,
        before_worker_start: Callable[[ProjectWorkerHostRequest, ProjectWorkerReservation], bool] | None = None,
    ) -> ProjectWorkerHostOutcome:
        """Backward-compatible single-worker entry point with host reservation."""
        if parent_run_id is not None and run_id is not None and parent_run_id != run_id:
            return _host_failure(session, request, "parent_run_identity_mismatch",
                                 run_id=parent_run_id, turn_epoch=turn_epoch or 0)
        parent = parent_run_id if parent_run_id is not None else (run_id or "")
        if type(turn_epoch) is not int or turn_epoch <= 0:
            return _host_failure(session, request, "live_binding_required", run_id=parent, turn_epoch=0)
        if reservation is None:
            try:
                reservation = self.reserve_many(
                    session, [request], parent_run_id=parent, turn_epoch=turn_epoch,
                    live_binding_check=live_binding_check,
                )[0]
            except Exception:
                return _host_failure(session, request, "worker_reservation_unavailable",
                                     run_id=parent, turn_epoch=turn_epoch)
        error = self._consume_reservations(
            session, [request], (reservation,), parent_run_id=parent, turn_epoch=turn_epoch,
        )
        if error is not None:
            return _host_failure(session, request, error, reservation=reservation)
        return self._run_reserved(
            session, request, reservation,
            scope_confirm_fn=scope_confirm_fn,
            outbound_request_confirm_fn=outbound_request_confirm_fn,
            write_confirm_fn=write_confirm_fn,
            cancel_event=cancel_event,
            live_binding_check=live_binding_check,
            max_requests=max_requests,
            before_worker_start=before_worker_start,
        )

    def _run_reserved(
        self,
        session: Session,
        request: ProjectWorkerHostRequest,
        reservation: ProjectWorkerReservation,
        *,
        scope_confirm_fn: Callable[[str], str] | None,
        outbound_request_confirm_fn: Callable[[str], str] | None,
        write_confirm_fn: Callable[[str, bool], str] | None,
        cancel_event: Event,
        live_binding_check: Callable[[Session, str, int], bool] | None,
        max_requests: int,
        before_worker_start: Callable[[ProjectWorkerHostRequest, ProjectWorkerReservation], bool] | None = None,
        on_worker_progress: Callable[[str, ProjectWorkerHostRequest, ProjectWorkerReservation], None] | None = None,
    ) -> ProjectWorkerHostOutcome:
        """Execute only against the already-consumed exact host reservation."""
        worker_id = reservation.worker_id
        safe_run_id = reservation.parent_run_id
        safe_epoch = reservation.turn_epoch
        fail = lambda code, status="blocked": _host_failure(
            session, request, code, status=status, reservation=reservation,
        )
        if (type(request) is not ProjectWorkerHostRequest
                or _safe_project_id(request.project_id) is None
                or type(request.task_id) is not str or not request.task_id.strip()
                or len(request.task_id) > 128 or any(ord(c) < 32 for c in request.task_id)
                or type(request.task) is not str or not request.task.strip()
                or len(request.task) > _MAX_HOST_TASK_CHARS):
            return fail("explicit_project_task_required")
        if (type(max_requests) is not int or not 1 <= max_requests <= MAX_REQUESTS
                or not isinstance(cancel_event, Event)):
            return fail("invalid_worker_limits_or_cancel_event")
        if (not safe_run_id.strip() or len(safe_run_id) > 128 or safe_epoch <= 0
                or not callable(live_binding_check)):
            return fail("live_binding_required")
        if cancel_event.is_set():
            return fail("cancelled", "cancelled")
        if (not callable(scope_confirm_fn) or not callable(outbound_request_confirm_fn)
                or not callable(write_confirm_fn)
                or outbound_request_confirm_fn is write_confirm_fn):
            return fail("separate_scope_outbound_and_write_confirmations_required")
        try:
            paths, conversation_id = _validate_parent_session(session)
            captured_parent_scope = (session.meta.project_id, session.meta.project_root,
                                     session.meta.active_shell, session.meta.turn_mode,
                                     session.meta.harness, session.meta.write_scope_root)
            project_id = request.project_id
            root = _resolve_bound_project(paths, project_id)
            _validate_targets(root, request)
            if not _parent_write_scope_allows(session, project_id, request.write_targets):
                raise PermissionError("parent write scope excludes selected worker project")
        except PermissionError as exc:
            message = str(exc)
            code = "project_not_found" if "not an existing registered" in message else (
                "parent_write_scope_excludes_project" if "parent write scope" in message else
                "invalid_target_scope" if "target" in message or "aliased" in message else
                "current_turn_not_write_authorized" if "turn is not authorized" in message else
                "parent_session_or_project_binding_invalid"
            )
            return fail(code)
        except Exception:
            return fail("project_binding_unavailable")

        def live_current() -> bool:
            try:
                if (session.conversation_id != conversation_id
                        or (session.meta.project_id, session.meta.project_root,
                            session.meta.active_shell, session.meta.turn_mode,
                            session.meta.harness, session.meta.write_scope_root) != captured_parent_scope):
                    return False
                return live_binding_check(session, safe_run_id, safe_epoch) is True
            except Exception:
                return False

        if not live_current():
            return fail("live_binding_changed")
        preview = _scope_preview(session, request, reservation, max_requests)
        if len(preview) > 64_000:
            return fail("scope_preview_too_large")
        try:
            scope_choice = self._serialized_confirm(scope_confirm_fn, cancel_event, preview)
        except Exception:
            return fail("scope_confirmation_failed")
        if cancel_event.is_set():
            return fail("cancelled", "cancelled")
        if type(scope_choice) is not str or scope_choice != "y":
            return fail("scope_denied")
        if not live_current():
            return fail("live_binding_changed")
        if cancel_event.is_set():
            return fail("cancelled", "cancelled")

        try:
            # Re-resolve after the human gate; never trust a pre-confirm path snapshot.
            current_paths, current_conversation = _validate_parent_session(session)
            current_root = _resolve_bound_project(current_paths, project_id)
            _validate_targets(current_root, request)
            if (current_paths.agent_root.resolve(strict=True) != paths.agent_root.resolve(strict=True)
                    or current_conversation != conversation_id
                    or current_root != root or not live_current()):
                return fail("live_binding_changed")
        except Exception:
            return fail("project_binding_changed")

        # Called only after the scheduler assigns a slot and all scope/current-turn
        # gates pass. This durable start record is the boundary before worker setup/model execution.
        if cancel_event.is_set():
            return fail("cancelled", "cancelled")
        if not live_current():
            return fail("live_binding_changed")
        if before_worker_start is not None:
            try:
                recorded_start = before_worker_start(request, reservation)
            except Exception:
                recorded_start = False
            if recorded_start is not True:
                return fail("worker_run_start_record_failed")

        if on_worker_progress is not None:
            try:
                on_worker_progress("started", request, reservation)
            except Exception:
                pass

        try:
            client = self._llm_client_factory()
        except Exception:
            return fail("llm_client_unavailable", "failed")
        if type(client) is not LLMClient or client.paths is None:
            return fail("dedicated_llm_client_required")
        try:
            if client.paths.agent_root.resolve(strict=True) != paths.agent_root.resolve(strict=True):
                return fail("llm_client_agent_root_mismatch")
        except Exception:
            return fail("llm_client_agent_root_mismatch")
        if client._cancel_event is not None or client._active_client is not None:
            return fail("shared_or_active_llm_client")
        with _CLIENTS_LOCK:
            if client in _CLAIMED_CLIENTS:
                return fail("shared_llm_client")
            _CLAIMED_CLIENTS.add(client)

        same_parent_project = session.meta.project_id == project_id
        worker_session = ExecutorSession(
            allowed_evolved={"write_text", "patch_file"},
            active_shell="project",
            project_root=f"workspace/{project_id}",
            project_id=project_id,
            project_plan_status=session.meta.project_plan_status if same_parent_project else "",
            project_workflow_stage=session.meta.project_workflow_stage if same_parent_project else "requirements",
            project_active_task_id=request.task_id,
            project_delivery_profile=session.meta.project_delivery_profile if same_parent_project else "solo",
            project_entry=session.meta.project_entry if same_parent_project else "direct",
            turn_mode="agent",
            ordinary_work_intent="execute",
            ordinary_task_scope=request.task_id,
            write_scope_root=session.meta.write_scope_root,
        )
        def guarded_scope_confirm(preview: str) -> str:
            if not live_current():
                return "n"
            choice = self._serialized_confirm(scope_confirm_fn, cancel_event, preview)
            return choice if live_current() and not cancel_event.is_set() else "n"

        def guarded_outbound_confirm(preview: str) -> str:
            if not live_current():
                return "n"
            choice = self._serialized_confirm(outbound_request_confirm_fn, cancel_event, preview)
            return choice if live_current() and not cancel_event.is_set() else "n"

        def guarded_write_confirm(preview: str, allow_all: bool) -> str:
            if not live_current():
                return "n"
            choice = self._serialized_confirm(write_confirm_fn, cancel_event, preview, allow_all)
            return choice if live_current() and not cancel_event.is_set() else "n"

        try:
            executor = ToolExecutor(
                registry=ToolRegistry.load(paths),
                session=worker_session,
                confirm_fn=guarded_write_confirm,
                cancel_event=cancel_event,
            )
        except Exception:
            return fail("worker_executor_unavailable", "failed")

        spec = ProjectWorkerSpec(
            conversation_id=conversation_id,
            project_id=project_id,
            project_root=root,
            write_targets=request.write_targets,
            executor=executor,
            host_binding_check=lambda: (
                session.conversation_id == conversation_id
                and session.paths.agent_root.resolve(strict=True) == paths.agent_root.resolve(strict=True)
                and session.meta.harness == "desktop"
                and session.meta.turn_mode == "agent"
                and live_current()
            ),
            kind="project",
            read_targets=request.read_targets,
            cancel_event=cancel_event,
            outbound_request_confirm_fn=guarded_outbound_confirm,
            live_run_id=safe_run_id,
            turn_epoch=safe_epoch,
        )
        try:
            model_turn = run_project_worker_model_loop(
                spec, request.task, client=client, max_requests=max_requests,
            )
        except Exception:
            return fail("worker_loop_failed", "failed")
        if cancel_event.is_set():
            return ProjectWorkerHostOutcome(
                worker_id=worker_id, run_id=safe_run_id, turn_epoch=safe_epoch,
                conversation_id=conversation_id, project_id=project_id, task_id=request.task_id,
                parent_run_id=safe_run_id, worker_run_id=reservation.worker_run_id,
                reservation_id=reservation.reservation_id,
                status="cancelled", model_turn=model_turn, acceptance_status="not_run",
                acceptance_evidence=(), error_code="cancelled",
            )
        if not live_current():
            return ProjectWorkerHostOutcome(
                worker_id=worker_id,
                run_id=safe_run_id,
                turn_epoch=safe_epoch,
                conversation_id=conversation_id,
                parent_run_id=safe_run_id,
                worker_run_id=reservation.worker_run_id,
                reservation_id=reservation.reservation_id,
                project_id=project_id,
                task_id=request.task_id,
                status="blocked",
                model_turn=model_turn,
                acceptance_status="not_run",
                acceptance_evidence=(),
                error_code="live_binding_changed",
            )
        return ProjectWorkerHostOutcome(
            worker_id=worker_id,
            run_id=safe_run_id,
            turn_epoch=safe_epoch,
            conversation_id=conversation_id,
            parent_run_id=safe_run_id,
            worker_run_id=reservation.worker_run_id,
            reservation_id=reservation.reservation_id,
            project_id=project_id,
            task_id=request.task_id,
            status=model_turn.status,
            model_turn=model_turn,
            acceptance_status="not_run",
            acceptance_evidence=(),
            error_code=model_turn.error_code,
        )


def run_project_worker_host(session: Session, request: ProjectWorkerHostRequest, *,
                            llm_client_factory: Callable[[], LLMClient], **kwargs: Any) -> ProjectWorkerHostOutcome:
    """Functional host boundary; caller must still supply explicit approvals."""
    return ProjectWorkerHost(llm_client_factory).run(session, request, **kwargs)


