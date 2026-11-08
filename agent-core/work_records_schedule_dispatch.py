"""Pure construction of an explicitly user-filled schedule candidate request.

This module never reserves, authorizes, or runs a worker. Its output must still
be submitted to the existing ProjectWorkerHost lifecycle for live-run checks,
scope confirmation, outbound-request confirmation, and write confirmation.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
import re
from typing import Any

from project_worker_host import ProjectWorkerHostRequest
from work_records_schedule_preview import build_schedule_preview

_MAX_TASK_CHARS = 8_000
_MAX_TARGETS = 32


@dataclass(frozen=True, slots=True)
class PreparedScheduleDispatch:
    request: ProjectWorkerHostRequest
    session_id: str
    parent_run_id: str
    turn_epoch: int


def prepare_schedule_dispatch(
    candidate: Any,
    *,
    task: Any,
    read_targets: Any,
    write_targets: Any,
    session_id: Any,
    parent_run_id: Any,
    turn_epoch: Any,
    authorized_projection: Any,
    live_session: Any,
    live_binding_check: Any,
) -> PreparedScheduleDispatch:
    """Build a side-effect-free request only from a fresh authorized candidate.

    ``authorized_projection`` must be obtained through the existing host
    authorization path immediately before this call. The exact candidate must
    still be eligible in that projection, and the trusted callback must affirm
    this session/run/turn both before and after validation. This is a verifiable
    preparation boundary, not an atomic dispatch: the eventual host operation
    must repeat its live-binding and authorization checks when consuming it.
    """
    if (type(candidate) is not dict
            or set(candidate) != {"project_id", "item_id", "requirement_id", "priority"}
            or any(type(candidate.get(key)) is not str or not candidate[key].strip()
                   for key in ("project_id", "item_id", "requirement_id"))):
        raise ValueError("a complete schedule candidate identity is required")
    if type(task) is not str or not task.strip() or len(task) > _MAX_TASK_CHARS:
        raise ValueError("a non-empty bounded user task is required")
    if (type(session_id) is not str or not session_id.strip()
            or type(parent_run_id) is not str or not parent_run_id.strip()
            or len(parent_run_id) > 128 or any(ord(c) < 32 for c in parent_run_id)
            or type(turn_epoch) is not int or turn_epoch <= 0):
        raise ValueError("a live session, parent run, and positive current turn epoch are required")

    def targets(value: Any, *, required: bool) -> tuple[str, ...]:
        if type(value) not in (list, tuple) or len(value) > _MAX_TARGETS:
            raise ValueError("explicit bounded target lists are required")
        if required and not value:
            raise ValueError("at least one explicit write target is required")
        if any(type(path) is not str or not path.strip() for path in value):
            raise ValueError("target paths must be non-empty strings")
        for path in value:
            pure = PurePosixPath(path)
            if ("\\" in path or ":" in path or any(ord(char) < 32 for char in path)
                    or any(char in '<>"|?*~' for char in path)
                    or pure.is_absolute() or pure.as_posix() != path
                    or any(part in ("", ".", "..") or part.endswith((".", " "))
                           or re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\..*)?", part)
                           for part in pure.parts)):
                raise ValueError("target paths must be safe project-relative paths")
        if len({path.casefold() for path in value}) != len(value):
            raise ValueError("duplicate or aliased target paths are not allowed")
        return tuple(value)

    if (getattr(live_session, "conversation_id", None) != session_id
            or not callable(live_binding_check)):
        raise ValueError("the exact live session and trusted binding checker are required")
    if live_binding_check(live_session, parent_run_id, turn_epoch) is not True:
        raise ValueError("the parent run is not the trusted current session turn")
    preview = build_schedule_preview(authorized_projection)
    if preview.get("state") != "visible" or candidate not in preview.get("candidates", []):
        raise ValueError("the candidate is stale, blocked, or absent from the authorized projection")
    if live_binding_check(live_session, parent_run_id, turn_epoch) is not True:
        raise ValueError("the trusted current session turn changed during validation")

    request = ProjectWorkerHostRequest(
        project_id=candidate["project_id"],
        task_id=candidate["requirement_id"],
        task=task.strip(),
        read_targets=targets(read_targets, required=False),
        write_targets=targets(write_targets, required=True),
    )
    return PreparedScheduleDispatch(request, session_id, parent_run_id, turn_epoch)


def dispatch_prepared_schedule(
    prepared: PreparedScheduleDispatch,
    *,
    live_session: Any,
    host: Any,
    scope_confirm_fn: Any,
    outbound_request_confirm_fn: Any,
    write_confirm_fn: Any,
    cancel_event: Any,
    live_binding_check: Any,
    before_worker_start: Any = None,
) -> Any:
    """Consume the prepared request through the existing trusted host lifecycle.

    Preparation is deliberately not a capability: re-check exact session/turn
    identity here, then delegate reservation, confirmations, and run-start CAS
    to ProjectWorkerHost. Host confirmation callbacks remain separate.
    """
    from threading import Event

    from project_worker_host import ProjectWorkerHost

    if (type(prepared) is not PreparedScheduleDispatch
            or type(live_session).__name__ != "Session"
            or getattr(live_session, "conversation_id", None) != prepared.session_id
            or not isinstance(host, ProjectWorkerHost)
            or type(cancel_event) is not Event
            or not all(callable(callback) for callback in (
                scope_confirm_fn, outbound_request_confirm_fn,
                write_confirm_fn, live_binding_check,
            ))
            or (before_worker_start is not None and not callable(before_worker_start))):
        raise PermissionError("prepared schedule request is not bound to a trusted live host session")
    if live_binding_check(live_session, prepared.parent_run_id, prepared.turn_epoch) is not True:
        raise PermissionError("prepared schedule request has expired or its parent turn changed")
    return host.run(
        live_session,
        prepared.request,
        scope_confirm_fn=scope_confirm_fn,
        outbound_request_confirm_fn=outbound_request_confirm_fn,
        write_confirm_fn=write_confirm_fn,
        cancel_event=cancel_event,
        parent_run_id=prepared.parent_run_id,
        turn_epoch=prepared.turn_epoch,
        live_binding_check=live_binding_check,
        before_worker_start=before_worker_start,
    )
