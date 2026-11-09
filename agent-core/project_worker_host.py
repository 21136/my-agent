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
import time
from typing import Any, Callable, Literal
from urllib.parse import urlsplit
from uuid import uuid4
from weakref import WeakSet
import json

from llm_client import LLMClient
from paths import AgentPaths
from project_mode import is_project_dir, list_projects, normalize_project_id, project_dir
from llm_models import get_registry
from project_worker_execution import (
    ProjectWorkerCoordinator, ProjectWorkerSpec, _log_project_worker_batch_event,
    _project_worker_batch_time,
)
from project_worker_model_loop import (
    MAX_REQUESTS,
    MAX_UNDECLARED_READ_CORRECTIONS,
    MAX_UNDECLARED_WRITE_CORRECTIONS,
    MODEL_ID, PROVIDER_HOST, PROVIDER_MODEL,
    ProjectWorkerModelOutcome, TOOL_DEFINITIONS, build_worker_system_prompt,
    run_project_worker_model_loop,
)
from project_worker_scope_diagnostics import (
    batch_outbound_undeclared_read_correction_matches,
    batch_outbound_undeclared_write_correction_matches,
    is_safe_worker_relative_path,
)
from session import Session, sessions_root
from tools.executor import ExecutorSession, ToolExecutor
from tools.registry import ToolRegistry

_MAX_HOST_TASK_CHARS = 8_000
_MAX_HOST_TARGETS = 32
_MAX_WORKER_BATCH_SIZE = 25
_CLIENTS_LOCK = Lock()
_CLAIMED_CLIENTS: WeakSet[LLMClient] = WeakSet()


class _ReservationCancelEvent(Event):
    """A worker-local cancel signal that also observes its parent batch signal."""

    def __init__(self, parent: Event) -> None:
        super().__init__()
        self._parent = parent

    def is_set(self) -> bool:
        return super().is_set() or self._parent.is_set()

    def wait(self, timeout: float | None = None) -> bool:
        deadline = None if timeout is None else time.monotonic() + max(0.0, timeout)
        while not self.is_set():
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                return self.is_set()
            super().wait(0.05 if remaining is None else min(0.05, remaining))
        return True


@dataclass(frozen=True, slots=True)
class ProjectWorkerTerminalStopReceipt:
    """Opaque, process-local proof that an accepted exact reservation terminated cancelled."""

    reservation_id: str
    user_action_id: str
    _nonce: object = field(repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class ProjectWorkerCancelTarget:
    """Exact work-record identity required by the internal cancellation path."""

    conversation_id: str
    parent_run_id: str
    turn_epoch: int
    project_id: str
    item_id: str
    requirement_id: str
    task_id: str
    worker_id: str
    worker_run_id: str
    reservation_id: str


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
class ProjectWorkerBatchAuthorization:
    """Opaque process-local grant for one explicitly displayed worker batch."""

    grant_id: str
    conversation_id: str
    parent_run_id: str
    turn_epoch: int
    expires_at: float
    model_id: str
    provider: str
    provider_model: str
    endpoint: str
    rows: tuple[tuple[ProjectWorkerHostRequest, ProjectWorkerReservation, tuple[int, int, int]], ...]


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
        and (
            model_turn.status == outcome.status
            # The host may observe the exact reservation's cancellation after the
            # model loop exits for any reason (including an interrupted request).
            # The model-loop status remains intact; host status owns this terminal
            # lifecycle decision, but only for cancelled host outcomes.
            or (outcome.status == "cancelled" and model_turn.status in {
                "model_turn_finished", "blocked", "failed", "cancelled", "limit_reached",
            })
        )
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

    # Adopted external projects have a separately validated server-side binding.
    # Do not follow the workspace junction as the worker root: use the canonical
    # registered target, then apply the same no-reparse and handle-identity checks.
    try:
        from external_project import validate_external_binding
        external = validate_external_binding(paths, project_id)
    except Exception:
        external = None
    if external is not None:
        try:
            root = Path(external.external_root).resolve(strict=True)
            ProjectWorkerCoordinator._assert_no_reparse_chain(root)
            if not root.is_dir() or str(root) != external.external_root:
                raise PermissionError("external project binding is not canonical")
            ProjectWorkerCoordinator._secure_directory_identity(root)
            return root
        except PermissionError:
            raise
        except Exception:
            raise PermissionError("external project root is unavailable or unsafe") from None

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


def _worker_model_snapshot(paths: AgentPaths) -> tuple[str, str, str, str]:
    entry = get_registry(paths).resolve(MODEL_ID)
    if (entry is None or entry.id != MODEL_ID or entry.vendor.casefold() != "0x567"
            or entry.provider_model != PROVIDER_MODEL or not entry.supports_tool_call):
        raise PermissionError("project worker model profile is unavailable")
    endpoint = entry.chat_completions_url()
    parsed = urlsplit(endpoint)
    if (parsed.scheme != "https" or not parsed.hostname
            or parsed.hostname.casefold() != PROVIDER_HOST or parsed.username or parsed.password
            or parsed.query or parsed.fragment):
        raise PermissionError("project worker endpoint is outside the permitted provider")
    return entry.id, entry.vendor, entry.provider_model, endpoint


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
        self._active_worker_cancels: dict[str, tuple[ProjectWorkerReservation, ProjectWorkerCancelTarget, Event, Callable[[ProjectWorkerCancelTarget, ProjectWorkerReservation], bool]]] = {}
        self._accepted_worker_stops: dict[str, ProjectWorkerCancelTarget] = {}
        self._terminal_user_stops: dict[str, tuple[ProjectWorkerHostOutcome, ProjectWorkerTerminalStopReceipt]] = {}
        self._reserved_task_scopes: set[tuple[str, str, int, str, str]] = set()
        self._batch_authorization_lock = Lock()
        self._batch_authorizations: dict[str, tuple[ProjectWorkerBatchAuthorization, set[str]]] = {}
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

    def authorize_batch(
        self,
        session: Session,
        requests: list[ProjectWorkerHostRequest],
        reservations: tuple[ProjectWorkerReservation, ...],
        *,
        confirm_fn: Callable[[str], str],
        cancel_event: Event,
        parent_run_id: str,
        turn_epoch: int,
        live_binding_check: Callable[[Session, str, int], bool],
        expires_in_seconds: int = 3600,
    ) -> ProjectWorkerBatchAuthorization | None:
        """Show one complete, exact disclosure and mint a short-lived in-memory grant.

        The grant is deliberately partitioned by reservation: approval of a batch
        never becomes a cross-project worker capability.
        """
        if (type(requests) is not list or not requests or len(requests) > _MAX_WORKER_BATCH_SIZE
                or len(reservations) != len(requests) or type(cancel_event) is not Event
                or not callable(confirm_fn) or not callable(live_binding_check)
                or type(expires_in_seconds) is not int or not 1 <= expires_in_seconds <= 3600
                or type(turn_epoch) is not int or turn_epoch <= 0):
            return None
        try:
            paths, conversation_id = _validate_parent_session(session)
            if cancel_event.is_set() or live_binding_check(session, parent_run_id, turn_epoch) is not True:
                return None
            rows = []
            projects = []
            for request, reservation in zip(requests, reservations):
                if (type(request) is not ProjectWorkerHostRequest
                        or type(reservation) is not ProjectWorkerReservation
                        or reservation.parent_run_id != parent_run_id
                        or reservation.turn_epoch != turn_epoch
                        or reservation.conversation_id != conversation_id
                        or reservation.project_id != request.project_id
                        or reservation.task_id != request.task_id):
                    return None
                with self._reservation_lock:
                    reserved = self._reservations.get(reservation.reservation_id)
                    if reserved is None or reserved[0] != reservation or reserved[1]:
                        return None
                root = _resolve_bound_project(paths, request.project_id)
                _validate_targets(root, request)
                identity = ProjectWorkerCoordinator._secure_directory_identity(root)
                rows.append((request, reservation, identity))
                projects.append({
                    "project_id": request.project_id,
                    "task_id": request.task_id,
                    "task": request.task,
                    "project_root": f"workspace/{request.project_id}",
                    "read_targets": list(request.read_targets),
                    "write_targets": list(request.write_targets),
                    "generated_content_write": (
                        "Model-generated content may be written only to write_targets through "
                        "ToolExecutor/write_policy and existing lease/source-snapshot/readback checks."
                    ),
                })
            model_id, provider, provider_model, endpoint = _worker_model_snapshot(paths)
            payload = {
                "schema_version": 1,
                "purpose": "project_worker_batch_authorization",
                "endpoint": {"provider": provider, "model_id": model_id,
                             "model": provider_model, "url": endpoint},
                "projects": projects,
                "disclosed_data_categories": [
                    "listed_worker_tasks", "worker_system_and_developer_instructions",
                    "contents_of_listed_read_targets_only",
                    "worker_multi_turn_model_and_tool_exchange_with_named_endpoint",
                    "model_generated_content_for_listed_write_targets",
                ],
                "not_disclosed": ["parent_conversation", "memory", "unlisted_project_data"],
                "authorization": {
                    "scope": "per_project_per_worker_only",
                    "expires_in_seconds": expires_in_seconds,
                    "requests_per_worker": "bounded_by_existing_worker_request_limit",
                    "effects_on_cancel": "blocks_future_posts_and_write_starts; cannot recall already-sent request or undo an already-started write",
                },
            }
            preview = "PROJECT_WORKER_BATCH_AUTHORIZATION_CONFIRM\n" + json.dumps(
                payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            )
            choice = self._serialized_confirm(confirm_fn, cancel_event, preview)
            if choice != "y" or cancel_event.is_set() or live_binding_check(session, parent_run_id, turn_epoch) is not True:
                return None
            # Re-resolve every root after the human gate. Target lists are immutable request data;
            # project-worker read snapshots and existing lease checks protect file identities later.
            for request, _reservation, identity in rows:
                root = _resolve_bound_project(paths, request.project_id)
                _validate_targets(root, request)
                if ProjectWorkerCoordinator._secure_directory_identity(root) != identity:
                    return None
            if _worker_model_snapshot(paths) != (model_id, provider, provider_model, endpoint):
                return None
            grant = ProjectWorkerBatchAuthorization(
                grant_id=f"batch:{uuid4()}", conversation_id=conversation_id,
                parent_run_id=parent_run_id, turn_epoch=turn_epoch,
                expires_at=time.monotonic() + expires_in_seconds,
                model_id=model_id, provider=provider, provider_model=provider_model,
                endpoint=endpoint, rows=tuple(rows),
            )
            with self._batch_authorization_lock:
                self._batch_authorizations[grant.grant_id] = (grant, set())
            return grant
        except Exception:
            return None

    def batch_authorization_allows(
        self, grant: ProjectWorkerBatchAuthorization, session: Session,
        request: ProjectWorkerHostRequest, reservation: ProjectWorkerReservation, *,
        parent_run_id: str, turn_epoch: int,
        live_binding_check: Callable[[Session, str, int], bool],
        cancel_event: Event | None = None,
    ) -> bool:
        """Revalidate one worker's grant without widening it to sibling projects."""
        if cancel_event is not None and cancel_event.is_set():
            return False
        with self._batch_authorization_lock:
            stored = self._batch_authorizations.get(getattr(grant, "grant_id", ""))
        if (stored is None or stored[0] is not grant or type(grant) is not ProjectWorkerBatchAuthorization
                or time.monotonic() >= grant.expires_at
                or grant.conversation_id != getattr(session, "conversation_id", None)
                or grant.parent_run_id != parent_run_id or grant.turn_epoch != turn_epoch):
            return False
        row = next((row for row in grant.rows if row[1] == reservation), None)
        if row is None or row[0] != request:
            return False
        with self._reservation_lock:
            host_reservation = self._reservations.get(reservation.reservation_id)
            if host_reservation is None or host_reservation[0] != reservation:
                return False
        try:
            if live_binding_check(session, parent_run_id, turn_epoch) is not True:
                return False
            paths, conversation_id = _validate_parent_session(session)
            if _worker_model_snapshot(paths) != (
                    grant.model_id, grant.provider, grant.provider_model, grant.endpoint):
                return False
            root = _resolve_bound_project(paths, request.project_id)
            _validate_targets(root, request)
            return (conversation_id == grant.conversation_id
                    and ProjectWorkerCoordinator._secure_directory_identity(root) == row[2])
        except Exception:
            return False

    def _batch_outbound_preview_matches(
        self, preview: str, grant: ProjectWorkerBatchAuthorization,
        session: Session, request: ProjectWorkerHostRequest,
        reservation: ProjectWorkerReservation,
    ) -> bool:
        """Validate canonical request metadata and every transcript source before reuse."""
        try:
            value = json.loads(preview)
            if (type(value) is not dict
                    or set(value) != {"method", "model_id", "vendor", "endpoint", "headers", "session_id", "scope", "provider_payload"}
                    or value.get("method") != "POST"
                    or value.get("model_id") != grant.model_id
                    or value.get("vendor") != grant.provider
                    or value.get("endpoint") != grant.endpoint
                    or value.get("session_id") != grant.conversation_id
                    or value.get("headers") != {"Authorization": "[redacted]", "Content-Type": "application/json"}):
                return False
            payload = value["provider_payload"]
            if (type(payload) is not dict or set(payload) != {"model", "messages", "temperature", "stream", "tools"}
                    or payload.get("model") != grant.provider_model or payload.get("temperature") != 0.0
                    or payload.get("stream") is not False or payload.get("tools") != TOOL_DEFINITIONS):
                return False
            messages = payload.get("messages")
            if (type(messages) is not list or len(messages) < 2
                    or type(messages[0]) is not dict or set(messages[0]) != {"role", "content"}
                    or messages[0].get("role") != "system"
                    or messages[0].get("content") != build_worker_system_prompt(
                        request.project_id, request.read_targets, request.write_targets,
                    )
                    or type(messages[1]) is not dict or set(messages[1]) != {"role", "content"}
                    or messages[1] != {"role": "user", "content": request.task.strip()}):
                return False
            scope = value.get("scope")
            expected_scope = {
                "project_id": request.project_id,
                "project_root": f"workspace/{request.project_id}",
                "read_targets": list(request.read_targets),
                "write_targets": list(request.write_targets),
                "run_id": reservation.parent_run_id,
                "turn_epoch": reservation.turn_epoch,
            }
            if (type(scope) is not dict
                    or set(scope) != set(expected_scope) | {"request_number"}
                    or any(scope.get(key) != expected for key, expected in expected_scope.items())
                    or type(scope.get("request_number")) is not int
                    or scope["request_number"] < 1):
                return False

            # The worker model loop only creates assistant tool calls and tool results after
            # the immutable task. Validate each pair and each path, not merely their role.
            pending: dict[str, tuple[str, str]] = {}
            undeclared_read_corrections = 0
            undeclared_write_corrections = 0
            for message in messages[2:]:
                if type(message) is not dict:
                    return False
                role = message.get("role")
                if role == "assistant":
                    if set(message) - {"role", "content", "tool_calls"}:
                        return False
                    calls = message.get("tool_calls", [])
                    if type(calls) is not list or len(calls) != 1:
                        return False
                    call = calls[0]
                    if (type(call) is not dict or call.get("type") != "function"
                            or type(call.get("id")) is not str or not call["id"]
                            or type(call.get("function")) is not dict):
                        return False
                    call_id = call["id"]
                    name = call["function"].get("name")
                    raw_args = call["function"].get("arguments")
                    args = raw_args if type(raw_args) is dict else json.loads(raw_args)
                    if type(args) is not dict or call_id in pending:
                        return False
                    path = args.get("path")
                    if (name == "read_project_file" and set(args) == {"path"}
                            and type(path) is str and path in request.read_targets):
                        pending[call_id] = (name, path)
                    elif (name == "read_project_file" and set(args) == {"path"}
                          and type(path) is str and path not in request.read_targets
                          and is_safe_worker_relative_path(path)):
                        pending[call_id] = (name, path)
                    elif (name == "write_project_file" and set(args) == {"path", "content"}
                          and type(path) is str and path in request.write_targets
                          and type(args.get("content")) is str):
                        pending[call_id] = (name, path)
                    elif (name == "write_project_file" and set(args) == {"path", "content"}
                          and type(path) is str and path not in request.write_targets
                          and is_safe_worker_relative_path(path)
                          and type(args.get("content")) is str):
                        pending[call_id] = (name, path)
                    elif (name == "patch_project_file" and set(args) == {"path", "find", "replacement"}
                          and type(path) is str and path in request.write_targets
                          and path in request.read_targets and type(args.get("find")) is str
                          and type(args.get("replacement")) is str):
                        pending[call_id] = (name, path)
                    else:
                        return False
                elif role == "tool":
                    call_id = message.get("tool_call_id")
                    source = pending.pop(call_id, None)
                    if (source is None or set(message) != {"role", "tool_call_id", "content"}
                            or type(message.get("content")) is not str):
                        return False
                    result = json.loads(message["content"])
                    name, path = source
                    if name == "read_project_file":
                        if (type(result) is dict and set(result) == {"ok", "path", "content", "truncated"}
                                and result.get("ok") is True and result.get("path") == path
                                and type(result.get("content")) is str
                                and type(result.get("truncated")) is bool):
                            pass
                        elif batch_outbound_undeclared_read_correction_matches(
                            attempted_path=path,
                            tool_result=result,
                            project_id=request.project_id,
                            read_targets=request.read_targets,
                            write_targets=request.write_targets,
                        ):
                            undeclared_read_corrections += 1
                            if undeclared_read_corrections > MAX_UNDECLARED_READ_CORRECTIONS:
                                return False
                        else:
                            return False
                    elif name == "write_project_file":
                        if (type(result) is dict and set(result) == {"ok", "path", "written", "readback_verified"}
                                and result.get("ok") is True and result.get("path") == path
                                and result.get("written") is True
                                and result.get("readback_verified") is True):
                            pass
                        elif batch_outbound_undeclared_write_correction_matches(
                            attempted_path=path,
                            tool_result=result,
                            project_id=request.project_id,
                            read_targets=request.read_targets,
                            write_targets=request.write_targets,
                        ):
                            undeclared_write_corrections += 1
                            if undeclared_write_corrections > MAX_UNDECLARED_WRITE_CORRECTIONS:
                                return False
                        else:
                            return False
                    else:
                        return False
                else:
                    return False
            return not pending
        except Exception:
            return False

    def revoke_batch_authorization(self, grant: ProjectWorkerBatchAuthorization | None) -> None:
        if type(grant) is not ProjectWorkerBatchAuthorization:
            return
        with self._batch_authorization_lock:
            self._batch_authorizations.pop(grant.grant_id, None)

    def active_writing_project_ids(self, conversation_id: str) -> frozenset[str]:
        """Parent-session projects that currently hold a started worker reservation."""
        if type(conversation_id) is not str or not conversation_id:
            return frozenset()
        with self._reservation_lock:
            return frozenset(
                reservation.project_id
                for reservation, started in self._reservations.values()
                if started and reservation.conversation_id == conversation_id
            )

    def request_cancel_for_record(
        self, session: Session, *, item_id: str, requirement_id: str,
    ) -> str:
        """Resolve and signal the unique active worker for a recorded requirement.

        The caller supplies only the user-selected record identity. Parent run,
        epoch, reservation, worker, project and task identities are resolved from
        host-owned active state while holding the reservation lock. The retained
        current-record verifier is re-run under that same lock before signalling.
        A returned ``signal_set`` means only that cancellation was accepted; the
        worker's terminal outcome and receipt remain the source of truth.
        """
        if (type(item_id) is not str or not item_id.strip()
                or type(requirement_id) is not str or not requirement_id.strip()):
            return "identity_mismatch"
        try:
            _paths, conversation_id = _validate_parent_session(session, require_execution=False)
        except Exception:
            return "identity_mismatch"
        with self._reservation_lock:
            matches = [entry for entry in self._active_worker_cancels.values()
                       if entry[0].conversation_id == conversation_id
                       and entry[1].item_id == item_id
                       and entry[1].requirement_id == requirement_id]
            if not matches:
                return "not_found"
            if len(matches) != 1:
                return "ambiguous"
            reservation, target, event, current_check = matches[0]
            if self._reservations.get(reservation.reservation_id) != (reservation, True):
                return "already_finished"
            try:
                if current_check(target, reservation) is not True:
                    return "already_finished"
            except Exception:
                return "identity_mismatch"
            event.set()
            self._accepted_worker_stops[reservation.reservation_id] = target
            return "signal_set"

    def cancel_worker(self, session: Session, target: ProjectWorkerCancelTarget) -> str:
        """Set one active worker's signal only after exact host and record validation.

        This is an internal execution primitive, not proof that the worker stopped.
        The trusted dispatch integration supplies a record-current verifier and the
        host retains it only while that reservation is executing.
        """
        if (type(target) is not ProjectWorkerCancelTarget
                or type(target.turn_epoch) is not int or target.turn_epoch <= 0
                or any(type(value) is not str or not value.strip()
                       for value in (target.conversation_id, target.parent_run_id,
                                     target.project_id, target.item_id, target.requirement_id,
                                     target.task_id, target.worker_id, target.worker_run_id,
                                     target.reservation_id))):
            return "identity_mismatch"
        try:
            _paths, conversation_id = _validate_parent_session(session, require_execution=False)
        except Exception:
            return "identity_mismatch"
        if conversation_id != target.conversation_id:
            return "identity_mismatch"
        with self._reservation_lock:
            entry = self._active_worker_cancels.get(target.reservation_id)
            if entry is None:
                return "not_found"
            reservation, bound_target, event, current_check = entry
            host_entry = self._reservations.get(reservation.reservation_id)
            if (host_entry is None or host_entry != (reservation, True)
                    or bound_target != target
                    or reservation.reservation_id != target.reservation_id
                    or reservation.worker_id != target.worker_id
                    or reservation.worker_run_id != target.worker_run_id
                    or reservation.parent_run_id != target.parent_run_id
                    or reservation.turn_epoch != target.turn_epoch
                    or reservation.conversation_id != target.conversation_id
                    or reservation.project_id != target.project_id
                    or reservation.task_id != target.task_id):
                return "identity_mismatch"
            try:
                if current_check(target, reservation) is not True:
                    return "already_finished"
            except Exception:
                return "identity_mismatch"
            event.set()
            self._accepted_worker_stops[target.reservation_id] = target
            return "signal_set"

    def consume_accepted_worker_stop(
        self, target: ProjectWorkerCancelTarget, reservation: ProjectWorkerReservation,
        outcome: ProjectWorkerHostOutcome,
    ) -> ProjectWorkerTerminalStopReceipt | None:
        """Consume exact user intent only alongside this host's actual cancelled outcome."""
        if (type(target) is not ProjectWorkerCancelTarget
                or type(reservation) is not ProjectWorkerReservation
                or type(outcome) is not ProjectWorkerHostOutcome
                or not project_worker_outcome_matches_reservation(reservation, outcome)
                or outcome.status != "cancelled"):
            return None
        with self._reservation_lock:
            terminal = self._terminal_user_stops.get(reservation.reservation_id)
            accepted = self._accepted_worker_stops.get(reservation.reservation_id)
            if (terminal is None or terminal[0] != outcome
                    or terminal[1].reservation_id != reservation.reservation_id
                    or accepted != target
                    or self._reservations.get(reservation.reservation_id) != (reservation, True)):
                return None
            # Validate the full identity and actual terminal outcome before consuming
            # either one-shot entry; a stale/forged target must not invalidate the
            # legitimate stop receipt for this reservation.
            del self._terminal_user_stops[reservation.reservation_id]
            del self._accepted_worker_stops[reservation.reservation_id]
            return terminal[1]

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
                self._accepted_worker_stops.pop(reservation_id, None)
                self._terminal_user_stops.pop(reservation_id, None)
                self._reserved_task_scopes.discard((
                    reservation.conversation_id, reservation.parent_run_id,
                    reservation.turn_epoch, reservation.project_id, reservation.task_id,
                ))
                released += 1
        with self._batch_authorization_lock:
            for grant_id, (grant, _claimed) in list(self._batch_authorizations.items()):
                if (grant.conversation_id, grant.parent_run_id, grant.turn_epoch) == parent_key:
                    del self._batch_authorizations[grant_id]
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
        batch_authorization: ProjectWorkerBatchAuthorization | None = None,
        before_worker_start: Callable[[ProjectWorkerHostRequest, ProjectWorkerReservation], bool] | None = None,
        on_worker_progress: Callable[[str, ProjectWorkerHostRequest, ProjectWorkerReservation], None] | None = None,
        cancel_target_for_worker: Callable[[ProjectWorkerHostRequest, ProjectWorkerReservation], ProjectWorkerCancelTarget | None] | None = None,
        cancel_target_is_current: Callable[[ProjectWorkerCancelTarget, ProjectWorkerReservation], bool] | None = None,
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
        batch_id = uuid4().hex
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
        if batch_authorization is not None and not cancel_event.is_set():
            if any(not self.batch_authorization_allows(
                    batch_authorization, session, request, reservation,
                    parent_run_id=parent, turn_epoch=turn_epoch,
                    live_binding_check=live_binding_check or (lambda *_: False), cancel_event=cancel_event,
                ) for request, reservation in zip(requests, active_reservations)):
                return tuple(_host_failure(session, request, "batch_authorization_invalid", reservation=reservation)
                             for request, reservation in zip(requests, active_reservations))
            with self._batch_authorization_lock:
                stored = self._batch_authorizations.get(batch_authorization.grant_id)
                if stored is None or any(row.reservation_id in stored[1] for row in active_reservations):
                    return tuple(_host_failure(session, request, "batch_authorization_replayed", reservation=reservation)
                                 for request, reservation in zip(requests, active_reservations))
                stored[1].update(row.reservation_id for row in active_reservations)
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
        queued_at = _project_worker_batch_time()
        started_indexes: set[int] = set()
        started_indexes_lock = Lock()
        ended_indexes: set[int] = set()

        def record_end(index: int, outcome: ProjectWorkerHostOutcome) -> None:
            with started_indexes_lock:
                if index not in started_indexes or index in ended_indexes:
                    return
                ended_indexes.add(index)
            project_id = _safe_project_id(outcome.project_id)
            if project_id is not None:
                _log_project_worker_batch_event("ended", batch_id=batch_id, project_id=project_id,
                                    index=index, queued_at=queued_at, at=_project_worker_batch_time(),
                                    status=outcome.status)

        def finish_batch(outcomes: tuple[ProjectWorkerHostOutcome, ...]) -> tuple[ProjectWorkerHostOutcome, ...]:
            for index, outcome in enumerate(outcomes):
                record_end(index, outcome)
            return outcomes

        if cancel_event.is_set():
            return finish_batch(tuple(_host_failure(
                session, request, "cancelled", status="cancelled", reservation=reservation,
            ) for request, reservation in zip(requests, active_reservations)))

        try:
            paths, _conversation_id = _validate_parent_session(session)
        except Exception:
            return finish_batch(tuple(_host_failure(
                session, item, "parent_session_or_project_binding_invalid", reservation=reservation,
            ) for item, reservation in zip(requests, active_reservations)))
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
                record_end(index, outcomes[index])

        active: dict[Any, tuple[int, tuple[int, int, int]]] = {}
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="project-worker-host") as pool:
            while pending or active:
                if cancel_event.is_set():
                    for index in pending:
                        outcomes[index] = _host_failure(
                            session, requests[index], "cancelled", status="cancelled",
                            reservation=active_reservations[index],
                        )
                        record_end(index, outcomes[index])
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
                        worker_cancel = _ReservationCancelEvent(cancel_event)
                        target = None
                        if cancel_target_for_worker is not None:
                            target = cancel_target_for_worker(requests[index], active_reservations[index])
                            if (type(target) is not ProjectWorkerCancelTarget
                                    or cancel_target_is_current is None):
                                raise ValueError("worker cancellation binding is incomplete")
                        if target is not None:
                            if (target.conversation_id != active_reservations[index].conversation_id
                                    or target.parent_run_id != active_reservations[index].parent_run_id
                                    or target.turn_epoch != active_reservations[index].turn_epoch
                                    or target.project_id != active_reservations[index].project_id
                                    or target.task_id != active_reservations[index].task_id
                                    or not all(type(value) is str and value.strip() for value in (
                                        target.item_id, target.requirement_id, target.worker_id,
                                        target.worker_run_id, target.reservation_id,
                                    ))
                                    or target.worker_id != active_reservations[index].worker_id
                                    or target.worker_run_id != active_reservations[index].worker_run_id
                                    or target.reservation_id != active_reservations[index].reservation_id):
                                raise ValueError("worker cancellation target does not match reservation")
                            with self._reservation_lock:
                                if target.reservation_id in self._active_worker_cancels:
                                    raise ValueError("worker cancellation identity is already active")
                                self._active_worker_cancels[target.reservation_id] = (
                                    active_reservations[index], target, worker_cancel, cancel_target_is_current,
                                )
                        future = pool.submit(
                            self._run_reserved, session, requests[index], active_reservations[index],
                            scope_confirm_fn=scope_confirm_fn,
                            outbound_request_confirm_fn=outbound_request_confirm_fn,
                            write_confirm_fn=write_confirm_fn,
                            cancel_event=worker_cancel,
                            live_binding_check=live_binding_check,
                            max_requests=max_requests,
                            before_worker_start=before_worker_start,
                            on_worker_progress=on_worker_progress,
                            batch_authorization=batch_authorization,
                            batch_trace=(batch_id, queued_at, index, started_indexes, started_indexes_lock),
                        )
                    except Exception:
                        if target is not None:
                            with self._reservation_lock:
                                self._active_worker_cancels.pop(target.reservation_id, None)
                        # No worker thread was created for this reservation; keep the
                        # host outcome explicit while harvesting other submitted work.
                        outcomes[index] = _host_failure(
                            session, requests[index], "worker_dispatch_submit_failed",
                            status="blocked", reservation=active_reservations[index],
                        )
                        record_end(index, outcomes[index])
                        continue
                    active[future] = (index, root_identity)
                    active_roots.add(root_identity)
                if active:
                    done, _ = wait(tuple(active), return_when=FIRST_COMPLETED)
                    for future in done:
                        index, _root_identity = active.pop(future)
                        reservation = active_reservations[index]
                        with self._reservation_lock:
                            active_cancel = self._active_worker_cancels.pop(reservation.reservation_id, None)
                        try:
                            outcomes[index] = future.result()
                        except Exception:
                            outcomes[index] = _host_failure(
                                session, requests[index], "worker_host_failed", status="failed",
                                reservation=active_reservations[index],
                            )
                        terminal_outcome = outcomes[index]
                        if (type(active_cancel) is tuple and len(active_cancel) == 4
                                and project_worker_outcome_matches_reservation(reservation, terminal_outcome)
                                and terminal_outcome.status == "cancelled"):
                            target = active_cancel[1]
                            with self._reservation_lock:
                                if self._accepted_worker_stops.get(reservation.reservation_id) == target:
                                    self._terminal_user_stops[reservation.reservation_id] = (
                                        terminal_outcome,
                                        ProjectWorkerTerminalStopReceipt(reservation.reservation_id, str(uuid4()), object()),
                                    )
                        record_end(index, terminal_outcome)
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
                        record_end(index, outcomes[index])
                    pending.clear()
        return finish_batch(tuple(item for item in outcomes if item is not None))

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
        batch_authorization: ProjectWorkerBatchAuthorization | None = None,
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
            batch_authorization=batch_authorization,
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
        batch_authorization: ProjectWorkerBatchAuthorization | None = None,
        batch_trace: tuple[str, str, int, set[int], Any] | None = None,
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
            root_identity = ProjectWorkerCoordinator._directory_stat_identity(root)
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

        grant_allows_worker = (
            batch_authorization is not None and self.batch_authorization_allows(
                batch_authorization, session, request, reservation,
                parent_run_id=safe_run_id, turn_epoch=safe_epoch,
                live_binding_check=live_binding_check, cancel_event=cancel_event,
            )
        )
        if not live_current():
            return fail("live_binding_changed")
        if batch_authorization is not None and not grant_allows_worker:
            return fail("batch_authorization_invalid")
        preview = _scope_preview(session, request, reservation, max_requests)
        if len(preview) > 64_000:
            return fail("scope_preview_too_large")
        if not grant_allows_worker:
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
                    or current_root != root
                    or ProjectWorkerCoordinator._directory_stat_identity(current_root) != root_identity):
                return fail("project_binding_changed")
            if not live_current():
                return fail("live_binding_changed")
        except Exception:
            return fail("project_binding_changed")

        # Called only after the scheduler assigns a slot and all scope/current-turn
        # gates pass. This durable start record is the boundary before worker setup/model execution.
        if cancel_event.is_set():
            return fail("cancelled", "cancelled")
        if not live_current():
            return fail("live_binding_changed")
        authorized_at = _project_worker_batch_time()
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
        if batch_trace is not None:
            batch_id, queued_at, batch_index, started_indexes, started_indexes_lock = batch_trace
            with started_indexes_lock:
                started_indexes.add(batch_index)
            _log_project_worker_batch_event("queued", batch_id=batch_id, project_id=project_id,
                                index=batch_index, queued_at=queued_at, at=queued_at)
            _log_project_worker_batch_event("authorized", batch_id=batch_id, project_id=project_id,
                                index=batch_index, queued_at=queued_at, at=authorized_at)

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
            if not live_current() or cancel_event.is_set():
                return "n"
            if batch_authorization is not None:
                return "y" if (
                    self.batch_authorization_allows(
                        batch_authorization, session, request, reservation,
                        parent_run_id=safe_run_id, turn_epoch=safe_epoch,
                        live_binding_check=live_binding_check, cancel_event=cancel_event,
                    ) and self._batch_outbound_preview_matches(
                        preview, batch_authorization, session, request, reservation,
                    )
                ) else "n"
            choice = self._serialized_confirm(outbound_request_confirm_fn, cancel_event, preview)
            return choice if live_current() and not cancel_event.is_set() else "n"

        def guarded_write_confirm(preview: str, allow_all: bool) -> str:
            if not live_current() or cancel_event.is_set():
                return "n"
            if batch_authorization is not None:
                # The grant is narrower than ToolExecutor's allow-all affordance: the
                # executor has already constrained this write to the declared target.
                # Never propagate an approve-all decision beyond this worker grant.
                # ToolExecutor and ScopedProjectTools have already constrained this write
                # to the request's declared target; the grant only replaces the UI prompt.
                return "y" if self.batch_authorization_allows(
                    batch_authorization, session, request, reservation,
                    parent_run_id=safe_run_id, turn_epoch=safe_epoch,
                    live_binding_check=live_binding_check, cancel_event=cancel_event,
                ) else "n"
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
                and ProjectWorkerCoordinator._directory_stat_identity(root) == root_identity
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


