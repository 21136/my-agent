"""Unwired, connection-local linearization primitive for post-turn outbound work.

This neither authorizes content nor verifies a host run. The caller must bind a
trusted Session/run, verify exact consent, and route every cancellation,
rebind and disconnect through this instance before enabling a flow. Network
code and caller callbacks are never executed under the lock.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any


class PostTurnGateCapacityError(RuntimeError):
    """Connection replay protection is full; no candidate was created."""


@dataclass(slots=True)
class _State:
    session: Any
    request_id: str
    run_id: str
    approved: bool = False
    started: bool = False
    valid: bool = True


class PostTurnSendGate:
    """Short-lock ordering point. A started send cannot be recalled.

    Tickets are opaque process-local objects; bounded replay protection is per
    connection and does not provide global deduplication or survive restarts.
    """
    _MAX_TICKETS = 1024

    def __init__(self, session: Any) -> None:
        if session is None:
            raise ValueError('a bound Session is required')
        self._lock = threading.Lock()
        self._session = session
        self._current_ticket: object | None = None
        self._states: dict[object, _State] = {}
        self._closed = False
        self._transitions = 0

    def _current(self, ticket: object) -> _State | None:
        try:
            state = self._states.get(ticket)
        except TypeError:
            return None
        if (ticket is not self._current_ticket or state is None or not state.valid
                or self._closed or self._transitions or state.session is not self._session):
            return None
        return state

    def _invalidate(self) -> None:
        if self._current_ticket is not None:
            self._states[self._current_ticket].valid = False
            self._current_ticket = None

    def create_checked(self, session: Any, request_id: str, run_id: str) -> tuple[str, object | None]:
        """Return an explicit result; failures never discard an existing ticket.

        The cap is a connection-lifetime safety limit, not a concurrency limit.
        Request tombstones are not evicted: after disconnect the entire gate is
        disposable, and this in-memory protection does not extend to reconnects.
        """
        if (session is None or type(request_id) is not str or not request_id or len(request_id) > 200
                or type(run_id) is not str or not run_id or len(run_id) > 200):
            return 'invalid', None
        with self._lock:
            if self._closed:
                return 'closed', None
            if self._transitions:
                return 'transition', None
            if session is not self._session:
                return 'session_mismatch', None
            if any(s.session is session and s.request_id == request_id for s in self._states.values()):
                return 'replay', None
            if len(self._states) >= self._MAX_TICKETS:
                return 'capacity_exhausted', None
            if self._current_ticket is not None:
                return 'busy', None
            ticket = object()
            self._states[ticket] = _State(session, request_id, run_id)
            self._current_ticket = ticket
            return 'created', ticket

    def create(self, session: Any, request_id: str, run_id: str) -> object | None:
        """Compatibility entry point; capacity exhaustion must never be silent."""
        status, ticket = self.create_checked(session, request_id, run_id)
        if status == 'capacity_exhausted':
            raise PostTurnGateCapacityError('post-turn connection capacity exhausted')
        return ticket

    def approve(self, ticket: object) -> bool:
        """Host invokes only after exact, live preview approval has been verified."""
        with self._lock:
            state = self._current(ticket)
            if state is None or state.approved or state.started:
                return False
            state.approved = True
            return True

    def start_send(self, ticket: object) -> bool:
        """Linearize one send; a True result remains historical after revoke."""
        with self._lock:
            state = self._current(ticket)
            if state is None or not state.approved or state.started:
                return False
            state.started = True
            return True

    def is_bound_to(self, session: Any) -> bool:
        with self._lock:
            return not self._closed and not self._transitions and self._session is session

    def is_valid(self, ticket: object) -> bool:
        with self._lock:
            return self._current(ticket) is not None

    def was_started(self, ticket: object) -> bool:
        with self._lock:
            try:
                state = self._states.get(ticket)
            except TypeError:
                return False
            return state is not None and state.started

    def cancel(self) -> None:
        with self._lock:
            self._invalidate()

    def disconnect(self) -> None:
        with self._lock:
            self._closed = True
            self._invalidate()

    def begin_session_change(self) -> None:
        with self._lock:
            self._transitions += 1
            self._invalidate()

    def end_session_change(self, session: Any) -> None:
        with self._lock:
            if self._transitions <= 0:
                raise RuntimeError('no session change is in progress')
            self._transitions -= 1
            # A caller must supply the actually current Session on every exit.
            self._session = session
