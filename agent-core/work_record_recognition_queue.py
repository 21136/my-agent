from __future__ import annotations

import threading
import uuid
from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Generic, TypeVar


T = TypeVar("T")


class QueueStatus(str, Enum):
    """Closed, explicit outcomes for authorization, admission, and commit operations."""

    AUTHORIZED = "authorized"
    UNAUTHORIZED = "unauthorized"
    BINDING_MISMATCH = "binding_mismatch"
    ENQUEUED = "enqueued"
    FULL = "full"
    CANDIDATE_READY = "candidate_ready"
    NO_CANDIDATE = "no_candidate"
    REVOKED = "revoked"
    INVALID = "invalid"
    INVALID_LEASE = "invalid_lease"
    INVALID_TICKET = "invalid_ticket"
    DISCARDED = "discarded"
    COMMITTED = "committed"
    COMMIT_FAILED = "commit_failed"


@dataclass(frozen=True, slots=True)
class QueueAdmission:
    status: QueueStatus
    request_token: str | None = None


@dataclass(frozen=True, slots=True)
class AuthorizationResult:
    status: QueueStatus
    generation: int


@dataclass(frozen=True, slots=True)
class RecognitionLease:
    """Opaque one-use recognition claim; contains no request or candidate content."""

    request_token: str
    generation: int


@dataclass(frozen=True, slots=True)
class CandidateTicket:
    """Opaque candidate handle. Candidate data remains with the host caller."""

    token: str
    generation: int


@dataclass(frozen=True, slots=True)
class RecognitionCompletion:
    status: QueueStatus
    ticket: CandidateTicket | None = None


@dataclass(frozen=True, slots=True)
class CommitResult(Generic[T]):
    status: QueueStatus
    value: T | None = None
    error_type: str | None = None


@dataclass(slots=True)
class _Entry:
    generation: int
    stage: str


class RecognitionQueue:
    """Internal session authorization and bounded serial recognition queue.

    The queue stores only opaque tokens and stage/generation metadata. It never
    accepts message bodies, recognition payloads, candidates, or confirmation text.
    Callers retain those values outside this object and invoke external recognition
    only after claiming a lease; no queue lock is held across that call.

    ``commit_if_current`` holds the same short, non-reentrant lock used by revoke
    and authorize while checking the ticket and running one bounded local commit
    callback. That callback must not perform network/model I/O, wait for user input,
    or call back into this queue.
    """

    def __init__(self, *, capacity: int = 16) -> None:
        if type(capacity) is not int or capacity < 1:
            raise ValueError("capacity must be a positive integer")
        self._capacity = capacity
        self._lock = threading.Lock()
        self._generation = 0
        self._authorized = False
        self._connection: object | None = None
        self._session: object | None = None
        self._entries: dict[str, _Entry] = {}
        self._ready: deque[str] = deque()
        self._active_request: str | None = None

    def __repr__(self) -> str:
        # Do not include host object reprs or any caller-owned text.
        with self._lock:
            return (f"RecognitionQueue(capacity={self._capacity}, authorized={self._authorized}, "
                    f"pending={len(self._entries)}, generation={self._generation})")

    @property
    def pending_count(self) -> int:
        with self._lock:
            return len(self._entries)

    @property
    def pending_candidate_count(self) -> int:
        with self._lock:
            return sum(entry.stage == "candidate" for entry in self._entries.values())

    def authorize(self, connection: object, session: object) -> AuthorizationResult:
        """Grant request-scoped recording permission for exact host object identities."""
        if connection is None or session is None:
            return AuthorizationResult(QueueStatus.INVALID, self._generation)
        with self._lock:
            self._generation += 1
            self._authorized = True
            self._connection = connection
            self._session = session
            # Re-authorization invalidates queued work and all pending candidates.
            # Keep a currently running external request as a stale active slot until
            # its caller returns, preserving the single-recognizer invariant.
            self._invalidate_non_active_locked()
            return AuthorizationResult(QueueStatus.AUTHORIZED, self._generation)

    def revoke(self, connection: object, session: object) -> bool:
        """Revoke only the exact current connection/session binding and invalidate tickets."""
        with self._lock:
            if not self._matches_locked(connection, session):
                return False
            self._generation += 1
            self._authorized = False
            self._connection = None
            self._session = None
            self._invalidate_non_active_locked()
            return True

    def enqueue(self, connection: object, session: object) -> QueueAdmission:
        """Reserve one bounded no-body slot; FULL is explicit and never evicts work."""
        with self._lock:
            status = self._binding_status_locked(connection, session)
            if status is not QueueStatus.AUTHORIZED:
                return QueueAdmission(status)
            if len(self._entries) >= self._capacity:
                return QueueAdmission(QueueStatus.FULL)
            token = uuid.uuid4().hex
            self._entries[token] = _Entry(self._generation, "queued")
            self._ready.append(token)
            return QueueAdmission(QueueStatus.ENQUEUED, token)

    def claim_next(self) -> RecognitionLease | None:
        """Claim the next serialized recognition slot; callers make external calls after return."""
        with self._lock:
            if not self._authorized or self._active_request is not None:
                return None
            while self._ready:
                token = self._ready.popleft()
                entry = self._entries.get(token)
                if entry is None or entry.stage != "queued" or entry.generation != self._generation:
                    continue
                entry.stage = "recognizing"
                self._active_request = token
                return RecognitionLease(token, entry.generation)
            return None

    def finish_recognition(
        self,
        lease: RecognitionLease,
        connection: object,
        session: object,
        *,
        candidate: bool,
    ) -> RecognitionCompletion:
        """Release the serial slot and optionally mint a ticket for caller-held candidate data."""
        if type(candidate) is not bool:
            return RecognitionCompletion(QueueStatus.INVALID)
        if type(lease) is not RecognitionLease:
            return RecognitionCompletion(QueueStatus.INVALID_LEASE)
        with self._lock:
            if self._active_request != lease.request_token:
                return RecognitionCompletion(QueueStatus.INVALID_LEASE)
            self._active_request = None
            entry = self._entries.pop(lease.request_token, None)
            if (entry is None or entry.stage != "recognizing"
                    or entry.generation != lease.generation
                    or not self._authorized or entry.generation != self._generation
                    or not self._matches_locked(connection, session)):
                return RecognitionCompletion(QueueStatus.REVOKED)
            if not candidate:
                return RecognitionCompletion(QueueStatus.NO_CANDIDATE)
            # The same reserved slot changes into a pending-confirmation ticket. It
            # remains capacity-bounded but does not occupy the serial recognition slot.
            ticket_token = uuid.uuid4().hex
            self._entries[ticket_token] = _Entry(self._generation, "candidate")
            return RecognitionCompletion(
                QueueStatus.CANDIDATE_READY,
                CandidateTicket(ticket_token, self._generation),
            )

    def ticket_status(
        self, ticket: CandidateTicket, connection: object, session: object,
    ) -> QueueStatus:
        """Check a ticket without exposing payload, under the authorization lock."""
        if type(ticket) is not CandidateTicket:
            return QueueStatus.INVALID_TICKET
        with self._lock:
            entry = self._entries.get(ticket.token)
            if not self._authorized or ticket.generation != self._generation:
                return QueueStatus.REVOKED
            if not self._matches_locked(connection, session):
                return QueueStatus.BINDING_MISMATCH
            if entry is None or entry.generation != self._generation:
                return QueueStatus.INVALID_TICKET
            return QueueStatus.CANDIDATE_READY if entry.stage == "candidate" else QueueStatus.INVALID_TICKET

    def discard_candidate(
        self, ticket: CandidateTicket, connection: object, session: object,
    ) -> QueueStatus:
        """Release one denied/abandoned candidate slot; stale tickets cannot affect current work."""
        if type(ticket) is not CandidateTicket:
            return QueueStatus.INVALID_TICKET
        with self._lock:
            entry = self._entries.get(ticket.token)
            if not self._authorized or ticket.generation != self._generation:
                return QueueStatus.REVOKED
            if not self._matches_locked(connection, session):
                return QueueStatus.BINDING_MISMATCH
            if entry is None or entry.generation != self._generation:
                return QueueStatus.INVALID_TICKET
            if entry.stage != "candidate":
                return QueueStatus.INVALID_TICKET
            del self._entries[ticket.token]
            return QueueStatus.DISCARDED

    def commit_if_current(
        self,
        ticket: CandidateTicket,
        connection: object,
        session: object,
        local_commit: Callable[[], T],
    ) -> CommitResult[T]:
        """Linearize ticket validation, one-shot consumption, and a local commit.

        The callback is invoked while holding the shared revocation/authorization
        lock. A failed callback consumes the ticket too: callers must not replay a
        possibly partially completed local side effect from the same approval.
        """
        if type(ticket) is not CandidateTicket or not callable(local_commit):
            return CommitResult(QueueStatus.INVALID_TICKET)
        with self._lock:
            entry = self._entries.get(ticket.token)
            if not self._authorized or ticket.generation != self._generation:
                return CommitResult(QueueStatus.REVOKED)
            if not self._matches_locked(connection, session):
                return CommitResult(QueueStatus.BINDING_MISMATCH)
            if entry is None or entry.generation != self._generation:
                return CommitResult(QueueStatus.INVALID_TICKET)
            if entry.stage != "candidate":
                return CommitResult(QueueStatus.INVALID_TICKET)
            del self._entries[ticket.token]
            try:
                return CommitResult(QueueStatus.COMMITTED, value=local_commit())
            except Exception as exc:
                # Expose only an error class, never provider/local exception text.
                return CommitResult(QueueStatus.COMMIT_FAILED, error_type=type(exc).__name__)

    def _binding_status_locked(self, connection: object, session: object) -> QueueStatus:
        if not self._authorized:
            return QueueStatus.UNAUTHORIZED
        if not self._matches_locked(connection, session):
            return QueueStatus.BINDING_MISMATCH
        return QueueStatus.AUTHORIZED

    def _matches_locked(self, connection: object, session: object) -> bool:
        return connection is self._connection and session is self._session

    def _invalidate_non_active_locked(self) -> None:
        active = self._active_request
        if active is None:
            self._entries.clear()
        else:
            active_entry = self._entries.get(active)
            self._entries.clear()
            if active_entry is not None:
                self._entries[active] = active_entry
        self._ready.clear()

