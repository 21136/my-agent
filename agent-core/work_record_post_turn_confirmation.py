"""Audit-only persistence for one post-turn confirmation per Session.

A stored decision is never an outbound authorization. Pending entries become
``unknown`` after a process restart because the exact preview is intentionally
not durable; callers must initiate a new confirmation instead of replaying one.
The host-facing caller must supply a run ID already proven by its trusted host.
"""

from __future__ import annotations

import contextlib
import datetime as _datetime
import json
import os
import re
import uuid
from pathlib import Path
from typing import Any, Iterator

from file_guard import atomic_write_text
from session import Session
from work_records import (
    WorkRecordCorrupt,
    WorkRecordLockUnavailable,
    WorkRecordScopeError,
    WorkRecordWriteError,
    _canonical_session,
    _file_identity,
    _file_lock,
    _require_private_regular_file,
)

_STATE_FILENAME = "post-turn-confirmation.v1.json"
_LOCK_FILENAME = ".post-turn-confirmation.lock"
_SCHEMA_VERSION = 1
_MAX_BYTES = 512 * 1024
_MAX_RECORDS = 4096
_PROCESS_EPOCH = uuid.uuid4().hex
_RUN_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,159}\Z")
_PURPOSE_RE = re.compile(r"[a-z][a-z0-9_]{0,47}\Z")
_RECORD_KEYS = frozenset({
    "confirmation_id", "conversation_id", "request_id", "run_id", "purpose",
    "deadline_at", "status", "created_at", "decision", "decided_at",
})
_TERMINAL = frozenset({"accepted", "rejected", "cancelled", "expired", "unknown"})


class PostTurnConfirmationError(RuntimeError):
    """Base error for this session-bound audit store."""


class PostTurnConfirmationScopeError(PostTurnConfirmationError):
    """The store no longer refers to its original canonical Session."""


class PostTurnConfirmationCorrupt(PostTurnConfirmationError):
    """The sidecar is unsafe, malformed, or belongs to another Session."""


class PostTurnConfirmationConflict(PostTurnConfirmationError):
    """An identity was reused with different content or a conflicting decision."""


class PostTurnConfirmationReplayError(PostTurnConfirmationError):
    """The item is terminal or restart-invalidated and cannot be replayed."""


class PostTurnConfirmationNotFound(PostTurnConfirmationError):
    """No item with the supplied confirmation UUID exists in this Session."""


class PostTurnConfirmationExpired(PostTurnConfirmationError):
    """The deadline passed; the item cannot be confirmed."""


class PostTurnConfirmationWriteError(PostTurnConfirmationError):
    """A durable snapshot could not be published."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _utc_datetime(value: Any, *, field: str) -> _datetime.datetime:
    if isinstance(value, _datetime.datetime):
        parsed = value
    elif type(value) is str:
        try:
            parsed = _datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"{field} must be an ISO-8601 timestamp") from exc
    else:
        raise ValueError(f"{field} must be an aware datetime or ISO-8601 timestamp")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed.astimezone(_datetime.UTC)


def _timestamp(value: _datetime.datetime) -> str:
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _uuid(value: Any, *, field: str) -> str:
    if type(value) is not str:
        raise ValueError(f"{field} must be a UUID string")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"{field} must be a UUID string") from exc
    normalized = str(parsed)
    if value.lower() != normalized:
        raise ValueError(f"{field} must use canonical UUID form")
    return normalized


class PostTurnConfirmationStore:
    """Atomic, single-Session audit state; it never stores preview or outbound text."""

    def __init__(self, session: Session, *, lock_timeout: float = 1.0) -> None:
        if lock_timeout < 0:
            raise ValueError("lock_timeout must be nonnegative")
        try:
            self._session_dir, self._agent_root, self._conversation_id = _canonical_session(session)
        except (WorkRecordScopeError, TypeError) as exc:
            raise PostTurnConfirmationScopeError("a canonical current Session is required") from exc
        self._session = session
        self._lock_timeout = lock_timeout
        self._path = self._session_dir / _STATE_FILENAME
        self._lock_path = self._session_dir / _LOCK_FILENAME

    def _validate_scope(self) -> None:
        try:
            current = _canonical_session(self._session)
        except (WorkRecordScopeError, TypeError) as exc:
            raise PostTurnConfirmationScopeError("Session storage binding is no longer valid") from exc
        if current != (self._session_dir, self._agent_root, self._conversation_id):
            raise PostTurnConfirmationScopeError("Session binding changed")

    def _empty(self) -> dict[str, Any]:
        return {
            "schema_version": _SCHEMA_VERSION,
            "conversation_id": self._conversation_id,
            "process_epoch": _PROCESS_EPOCH,
            "revision": 0,
            "records": [],
        }

    def _validate_payload(self, payload: Any) -> None:
        if (type(payload) is not dict
                or set(payload) != {"schema_version", "conversation_id", "process_epoch", "revision", "records"}
                or type(payload.get("schema_version")) is not int or payload["schema_version"] != _SCHEMA_VERSION
                or payload.get("conversation_id") != self._conversation_id
                or type(payload.get("process_epoch")) is not str or not payload["process_epoch"]
                or type(payload.get("revision")) is not int or payload["revision"] < 0
                or type(payload.get("records")) is not list or len(payload["records"]) > _MAX_RECORDS):
            raise PostTurnConfirmationCorrupt("sidecar schema or Session identity is invalid")
        seen: set[str] = set()
        for record in payload["records"]:
            if type(record) is not dict or set(record) != _RECORD_KEYS:
                raise PostTurnConfirmationCorrupt("sidecar record fields are invalid")
            try:
                confirmation_id = _uuid(record["confirmation_id"], field="confirmation_id")
                _uuid(record["request_id"], field="request_id")
                deadline = _utc_datetime(record["deadline_at"], field="deadline_at")
                created = _utc_datetime(record["created_at"], field="created_at")
                if record["decided_at"] is not None:
                    _utc_datetime(record["decided_at"], field="decided_at")
            except (ValueError, TypeError) as exc:
                raise PostTurnConfirmationCorrupt("sidecar identity or timestamp is invalid") from exc
            if (confirmation_id in seen or record["conversation_id"] != self._conversation_id
                    or type(record["run_id"]) is not str or not _RUN_ID_RE.fullmatch(record["run_id"])
                    or type(record["purpose"]) is not str or not _PURPOSE_RE.fullmatch(record["purpose"])
                    or type(record["status"]) is not str or record["status"] not in {"pending", *_TERMINAL}
                    or (record["decision"] is not None and type(record["decision"]) is not str)
                    or (record["decided_at"] is not None and type(record["decided_at"]) is not str)
                    or (record["status"] == "pending" and (record["decision"] is not None or record["decided_at"] is not None))
                    or (record["status"] in _TERMINAL and record["status"] != "unknown"
                        and (record["decision"] != record["status"] or record["decided_at"] is None))
                    or (record["status"] == "unknown" and (record["decision"] is not None or record["decided_at"] is not None))
                    or deadline <= created):
                raise PostTurnConfirmationCorrupt("sidecar record identity or state is invalid")
            seen.add(confirmation_id)

    def _read(self) -> dict[str, Any]:
        self._validate_scope()
        try:
            before = self._path.lstat()
        except FileNotFoundError:
            return self._empty()
        try:
            _require_private_regular_file(before, error=WorkRecordCorrupt, label="post-turn confirmation sidecar")
            if before.st_size > _MAX_BYTES:
                raise WorkRecordCorrupt("sidecar exceeds size limit")
            fd = os.open(self._path, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0))
            try:
                opened = os.fstat(fd)
                _require_private_regular_file(opened, error=WorkRecordCorrupt, label="opened post-turn sidecar")
                current = self._path.lstat()
                if _file_identity(before) != _file_identity(opened) or _file_identity(opened) != _file_identity(current):
                    raise WorkRecordCorrupt("sidecar path changed while opening")
                with os.fdopen(fd, "r", encoding="utf-8") as stream:
                    fd = -1
                    payload = json.load(stream, object_pairs_hook=_reject_duplicate_keys)
            finally:
                if fd >= 0:
                    os.close(fd)
        except WorkRecordCorrupt as exc:
            raise PostTurnConfirmationCorrupt("sidecar is not a private regular file") from exc
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
            raise PostTurnConfirmationCorrupt("sidecar cannot be read; refusing overwrite") from exc
        self._validate_payload(payload)
        return payload

    def _publish(self, payload: dict[str, Any]) -> None:
        self._validate_payload(payload)
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n"
        if len(raw.encode("utf-8")) > _MAX_BYTES:
            raise PostTurnConfirmationWriteError("sidecar capacity reached")
        self._validate_scope()
        try:
            try:
                info = self._path.lstat()
            except FileNotFoundError:
                info = None
            if info is not None:
                _require_private_regular_file(info, error=WorkRecordCorrupt, label="post-turn confirmation sidecar")
            atomic_write_text(self._path, raw, agent_root=self._agent_root, backup=True)
        except (OSError, WorkRecordCorrupt, ValueError) as exc:
            raise PostTurnConfirmationWriteError("post-turn confirmation snapshot was not published") from exc

    def _load_current_epoch(self) -> dict[str, Any]:
        payload = self._read()
        if payload["process_epoch"] != _PROCESS_EPOCH:
            for record in payload["records"]:
                if record["status"] == "pending":
                    record["status"] = "unknown"
                    record["decision"] = None
                    record["decided_at"] = None
            payload["process_epoch"] = _PROCESS_EPOCH
            payload["revision"] += 1
            self._publish(payload)
        return payload

    @contextlib.contextmanager
    def _locked_payload(self) -> Iterator[dict[str, Any]]:
        try:
            with _file_lock(self._lock_path, self._lock_timeout):
                yield self._load_current_epoch()
        except (WorkRecordLockUnavailable, OSError) as exc:
            raise PostTurnConfirmationWriteError("post-turn confirmation lock unavailable") from exc

    def create_pending(
        self,
        *,
        confirmation_id: str,
        request_id: str,
        trusted_host_run_id: str,
        purpose: str,
        deadline_at: str | _datetime.datetime,
        now: str | _datetime.datetime | None = None,
    ) -> dict[str, Any]:
        """Create an audit entry for the original ``user.message`` request UUID.

        ``trusted_host_run_id`` must already have been proven by the trusted host;
        this module cannot establish that proof itself. No preview, candidate summary,
        outbound content, or arbitrary metadata is accepted.
        Repeating the same immutable binding is idempotent; different content conflicts.
        """
        confirmation_id = _uuid(confirmation_id, field="confirmation_id")
        request_id = _uuid(request_id, field="request_id")
        if type(trusted_host_run_id) is not str or not _RUN_ID_RE.fullmatch(trusted_host_run_id):
            raise ValueError("trusted_host_run_id must be a bounded host run identifier")
        if type(purpose) is not str or not _PURPOSE_RE.fullmatch(purpose):
            raise ValueError("purpose must be a bounded lowercase identifier")
        created = _utc_datetime(now if now is not None else _datetime.datetime.now(_datetime.UTC), field="now")
        deadline = _utc_datetime(deadline_at, field="deadline_at")
        if deadline <= created:
            raise PostTurnConfirmationExpired("deadline has already passed")
        candidate = {
            "confirmation_id": confirmation_id,
            "conversation_id": self._conversation_id,
            "request_id": request_id,
            "run_id": trusted_host_run_id,
            "purpose": purpose,
            "deadline_at": _timestamp(deadline),
            "status": "pending",
            "created_at": _timestamp(created),
            "decision": None,
            "decided_at": None,
        }
        with self._locked_payload() as payload:
            existing = next((item for item in payload["records"] if item["confirmation_id"] == confirmation_id), None)
            if existing is not None:
                immutable = ("confirmation_id", "conversation_id", "request_id", "run_id", "purpose", "deadline_at")
                if any(existing[key] != candidate[key] for key in immutable):
                    raise PostTurnConfirmationConflict("confirmation UUID is already bound to different content")
                return json.loads(json.dumps(existing))
            if len(payload["records"]) >= _MAX_RECORDS:
                raise PostTurnConfirmationWriteError("confirmation record capacity reached")
            payload["records"].append(candidate)
            payload["revision"] += 1
            self._publish(payload)
            return json.loads(json.dumps(candidate))

    def decide(
        self,
        confirmation_id: str,
        decision: str,
        *,
        now: str | _datetime.datetime | None = None,
    ) -> dict[str, Any]:
        """Record one user decision; this is an audit transition, not outbound authorization."""
        confirmation_id = _uuid(confirmation_id, field="confirmation_id")
        if type(decision) is not str or decision not in {"accepted", "rejected", "cancelled"}:
            raise ValueError("decision must be accepted, rejected, or cancelled")
        decided = _utc_datetime(now if now is not None else _datetime.datetime.now(_datetime.UTC), field="now")
        with self._locked_payload() as payload:
            record = next((item for item in payload["records"] if item["confirmation_id"] == confirmation_id), None)
            if record is None:
                raise PostTurnConfirmationNotFound("confirmation UUID is not present in this Session")
            if record["status"] == "unknown":
                raise PostTurnConfirmationReplayError("restart-invalidated confirmation must be initiated again")
            if record["status"] == "expired":
                raise PostTurnConfirmationExpired("confirmation deadline has passed")
            if record["status"] in {"accepted", "rejected", "cancelled"}:
                if record["status"] == decision:
                    return json.loads(json.dumps(record))
                raise PostTurnConfirmationConflict("confirmation already has a different terminal decision")
            deadline = _utc_datetime(record["deadline_at"], field="deadline_at")
            if decided >= deadline:
                record["status"] = "expired"
                record["decision"] = "expired"
                record["decided_at"] = _timestamp(decided)
                payload["revision"] += 1
                self._publish(payload)
                raise PostTurnConfirmationExpired("confirmation deadline has passed")
            record["status"] = decision
            record["decision"] = decision
            record["decided_at"] = _timestamp(decided)
            payload["revision"] += 1
            self._publish(payload)
            return json.loads(json.dumps(record))

    def list_records(self) -> list[dict[str, Any]]:
        """Return copied audit records; restart invalidation is persisted before exposure."""
        with self._locked_payload() as payload:
            return json.loads(json.dumps(payload["records"]))
