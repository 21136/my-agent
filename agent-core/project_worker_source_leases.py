"""Standalone source-session worker reservation ledger; NOT wired to execution.

Trusted host code must reserve before dispatch, call start after all confirmations,
execute only after start succeeds, and release only against a host-authenticated
terminal receipt. A caller-supplied terminal validator is a trust boundary, not
an authenticator supplied by this module. Unknown/crashed runs remain occupied.
The ledger uses the source WorkRecordStore lock as its only lock; callers must
not hold parent-dialog locks while invoking it. It never writes source records.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
from pathlib import Path
from typing import Any, Callable

from session import Session
from work_records import (
    WorkRecordConflict, WorkRecordCorrupt, WorkRecordScopeError, WorkRecordStore,
    WorkRecordWriteError, _canonical_session, _file_identity, _file_lock,
    _require_private_regular_file,
)

_NAME = ".project-worker-source-leases.v1.json"
_MAX_BYTES = 4 * 1024 * 1024
_MAX_ROWS = 10000
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_ID_KEYS = ("item_id", "requirement_id", "parent_session_id", "parent_run_id",
            "reservation_id", "worker_id", "worker_run_id")
_KEYS = frozenset((*_ID_KEYS, "source_session_id", "project_id", "turn_epoch", "text_sha256", "state"))
_STATES = {"reserved", "started", "cancelled", "finished", "blocked", "stopped"}
_ACTIVE = {"reserved", "started"}


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise WorkRecordCorrupt("duplicate lease field")
        result[key] = value
    return result


def _text_digest(value: str) -> str:
    if type(value) is not str or not value.strip() or len(value) > 24000:
        raise ValueError("expected requirement text is invalid")
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class SourceWorkerLeaseLedger:
    """Host-private namespace beside an already-bound source session.

    This object does not grant permission to read source records or run a worker.
    Integrators must enforce those separate permissions before using it.
    """
    def __init__(self, source: Session, *, lock_timeout: float = 1.0) -> None:
        if type(lock_timeout) not in (int, float) or not 0 <= lock_timeout <= 60:
            raise ValueError("invalid lock timeout")
        self._store = WorkRecordStore(source, lock_timeout=lock_timeout)
        self._session = source
        self._directory, self._root, self._session_id = _canonical_session(source)
        self._project_id = source.meta.project_id
        if type(self._project_id) is not str or _ID.fullmatch(self._project_id) is None:
            raise WorkRecordScopeError("a source project Session is required")
        self._path = self._directory / _NAME
        self._lock_path = self._store._lock_path
        self._timeout = lock_timeout

    def _validate_scope(self) -> None:
        self._store._validate_scope()
        if _canonical_session(self._session) != (self._directory, self._root, self._session_id):
            raise WorkRecordScopeError("source Session identity changed")
        if self._session.meta.project_id != self._project_id:
            raise WorkRecordScopeError("source project identity changed")

    def _empty(self) -> dict[str, Any]:
        return {"schema_version": 1, "source_session_id": self._session_id,
                "project_id": self._project_id, "revision": 0, "leases": []}

    def _validate(self, data: Any) -> None:
        if (type(data) is not dict or set(data) != {"schema_version", "source_session_id", "project_id", "revision", "leases"}
                or data["schema_version"] != 1 or data["source_session_id"] != self._session_id
                or data["project_id"] != self._project_id or type(data["revision"]) is not int
                or data["revision"] < 0 or type(data["leases"]) is not list or len(data["leases"]) > _MAX_ROWS):
            raise WorkRecordCorrupt("source lease namespace is invalid")
        ids, active = set(), set()
        for row in data["leases"]:
            if (type(row) is not dict or set(row) != _KEYS
                    or any(type(row[key]) is not str or _ID.fullmatch(row[key]) is None for key in _ID_KEYS)
                    or row["source_session_id"] != self._session_id or row["project_id"] != self._project_id
                    or type(row["turn_epoch"]) is not int or row["turn_epoch"] < 1
                    or type(row["text_sha256"]) is not str or _HASH.fullmatch(row["text_sha256"]) is None
                    or type(row["state"]) is not str or row["state"] not in _STATES
                    or row["reservation_id"] in ids):
                raise WorkRecordCorrupt("source lease record is invalid")
            ids.add(row["reservation_id"])
            identity = (row["item_id"], row["requirement_id"])
            if row["state"] in _ACTIVE:
                if identity in active:
                    raise WorkRecordCorrupt("source requirement has ambiguous active leases")
                active.add(identity)

    def _read(self) -> dict[str, Any]:
        self._validate_scope()
        try:
            before = self._path.lstat()
        except FileNotFoundError:
            return self._empty()
        _require_private_regular_file(before, error=WorkRecordCorrupt, label="source lease path")
        if before.st_size > _MAX_BYTES:
            raise WorkRecordCorrupt("source lease namespace exceeds size limit")
        fd = -1
        try:
            fd = os.open(self._path, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0))
            opened = os.fstat(fd)
            _require_private_regular_file(opened, error=WorkRecordCorrupt, label="opened source lease")
            current = self._path.lstat()
            if _file_identity(before) != _file_identity(opened) or _file_identity(current) != _file_identity(opened):
                raise WorkRecordCorrupt("source lease path changed while opening")
            with os.fdopen(fd, "r", encoding="utf-8") as stream:
                fd = -1
                data = json.load(stream, object_pairs_hook=_unique_pairs)
        except WorkRecordCorrupt:
            raise
        except (OSError, UnicodeError, ValueError, RecursionError) as exc:
            raise WorkRecordCorrupt("source lease namespace cannot be read") from exc
        finally:
            if fd >= 0: os.close(fd)
        self._validate(data)
        return data

    def _publish(self, data: dict[str, Any]) -> None:
        self._validate_scope()
        self._validate(data)
        raw = (json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n").encode("utf-8")
        if len(raw) > _MAX_BYTES: raise WorkRecordWriteError("source lease namespace exceeds size limit")
        try:
            try: existing = self._path.lstat()
            except FileNotFoundError: existing = None
            if existing is not None:
                _require_private_regular_file(existing, error=WorkRecordWriteError, label="source lease target")
            fd, name = tempfile.mkstemp(prefix=".project-worker-source-leases.", suffix=".tmp", dir=self._directory)
            tmp = Path(name)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(raw); stream.flush(); os.fsync(stream.fileno())
                self._validate_scope()
                os.replace(tmp, self._path)
            finally:
                try: tmp.unlink(missing_ok=True)
                except OSError: pass
        except WorkRecordWriteError:
            raise
        except OSError as exc:
            raise WorkRecordWriteError("source lease could not be atomically persisted") from exc

    def _require_source_text(self, row: dict[str, Any], expected_text: str) -> None:
        if _text_digest(expected_text) != row["text_sha256"]:
            raise WorkRecordConflict("reservation text expectation changed")
        source = self._store._read()  # source lock is already held
        items = [item for item in source["items"] if item["item_id"] == row["item_id"]]
        if len(items) != 1 or items[0].get("scope") != {"kind": "project", "project_id": self._project_id}:
            raise WorkRecordConflict("source item scope changed")
        reqs = [req for req in items[0]["requirements"] if req["requirement_id"] == row["requirement_id"]]
        if len(reqs) != 1 or reqs[0]["text"] != expected_text:
            raise WorkRecordConflict("source requirement changed before dispatch")
        req = reqs[0]
        status, current_run = req["status"], req.get("current_run_id")
        if status == "pending" and current_run is None:
            return
        if status not in {"completed", "blocked", "stopped"} or current_run is None:
            raise WorkRecordConflict("source run is active, unknown, or lacks a reliable terminal")
        matching = [run for run in items[0]["runs"] if run["requirement_id"] == row["requirement_id"]
                    and run["run_id"] == current_run]
        if len(matching) != 1 or matching[0]["status"] != status:
            raise WorkRecordConflict("source terminal run is missing or ambiguous")

    def reserve(self, *, item_id: str, requirement_id: str, expected_text: str,
                parent_session_id: str, parent_run_id: str, turn_epoch: int,
                reservation_id: str, worker_id: str, worker_run_id: str) -> dict[str, Any]:
        row = {"item_id": item_id, "requirement_id": requirement_id, "parent_session_id": parent_session_id,
               "parent_run_id": parent_run_id, "turn_epoch": turn_epoch, "reservation_id": reservation_id,
               "worker_id": worker_id, "worker_run_id": worker_run_id, "source_session_id": self._session_id,
               "project_id": self._project_id, "text_sha256": _text_digest(expected_text), "state": "reserved"}
        self._validate({**self._empty(), "leases": [row]})
        self._validate_scope()
        with _file_lock(self._lock_path, self._timeout):
            data = self._read()
            existing = next((entry for entry in data["leases"] if entry["reservation_id"] == reservation_id), None)
            if existing is not None:
                if any(existing[key] != row[key] for key in _KEYS if key != "state"):
                    raise WorkRecordConflict("reservation identity was reused")
                if existing["state"] in _ACTIVE:
                    self._require_source_text(existing, expected_text)
                return dict(existing)
            self._require_source_text(row, expected_text)
            if any(entry["state"] in _ACTIVE and (entry["item_id"], entry["requirement_id"]) == (item_id, requirement_id)
                   for entry in data["leases"]):
                raise WorkRecordConflict("source requirement already reserved or started")
            data["leases"].append(row); data["revision"] += 1
            self._publish(data)
            return dict(row)

    def _find_unresolved_for_parent(
        self, *, parent_session_id: str, parent_run_id: str, turn_epoch: int,
        limit: int = 50, cursor: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Internal host recovery discovery for this source only, without mutation.

        Not an authorization interface. The integrating host must authenticate the
        caller and bind the source and parent before invoking this private method.
        A changed ledger invalidates cursors rather than silently skipping rows.
        """
        if (type(parent_session_id) is not str or _ID.fullmatch(parent_session_id) is None
                or type(parent_run_id) is not str or _ID.fullmatch(parent_run_id) is None
                or type(turn_epoch) is not int or turn_epoch < 1
                or type(limit) is not int or not 1 <= limit <= 100):
            raise ValueError("invalid parent recovery identity or page limit")
        scope = {"source_session_id": self._session_id, "project_id": self._project_id,
                 "parent_session_id": parent_session_id, "parent_run_id": parent_run_id,
                 "turn_epoch": turn_epoch}
        if cursor is not None:
            if (type(cursor) is not dict or set(cursor) != set(scope) | {"revision", "after"}
                    or any(type(cursor[key]) is not type(value) or cursor[key] != value
                           for key, value in scope.items())
                    or type(cursor["revision"]) is not int or cursor["revision"] < 0
                    or type(cursor["after"]) is not str or _ID.fullmatch(cursor["after"]) is None):
                raise ValueError("invalid parent recovery cursor")
        with _file_lock(self._lock_path, self._timeout):
            data = self._read()
            if cursor is not None and cursor["revision"] != data["revision"]:
                raise WorkRecordConflict("lease ledger changed during recovery pagination")
            rows = sorted((row for row in data["leases"]
                           if row["state"] in _ACTIVE
                           and row["parent_session_id"] == parent_session_id
                           and row["parent_run_id"] == parent_run_id
                           and row["turn_epoch"] == turn_epoch),
                          key=lambda row: row["reservation_id"])
            if cursor is not None:
                positions = [i for i, row in enumerate(rows) if row["reservation_id"] == cursor["after"]]
                if len(positions) != 1:
                    raise ValueError("recovery cursor does not identify an unresolved lease")
                rows = rows[positions[0] + 1:]
            page = rows[:limit]
            next_cursor = ({**scope, "revision": data["revision"], "after": page[-1]["reservation_id"]}
                           if len(rows) > limit else None)
            return {"leases": [dict(row) for row in page], "next_cursor": next_cursor}

    def get(self, reservation_id: str) -> dict[str, Any] | None:
        if type(reservation_id) is not str or _ID.fullmatch(reservation_id) is None:
            raise ValueError("invalid reservation identity")
        with _file_lock(self._lock_path, self._timeout):
            row = next((r for r in self._read()["leases"] if r["reservation_id"] == reservation_id), None)
            return None if row is None else dict(row)

    def _transition(self, identity: dict[str, Any], state: str, *, expected_text: str | None = None,
                    trusted_terminal_check: Callable[..., bool] | None = None) -> dict[str, Any]:
        if type(identity) is not dict or set(identity) != _KEYS:
            raise ValueError("exact captured lease identity is required")
        self._validate({**self._empty(), "leases": [identity]})

        def matching(data):
            row = next((entry for entry in data["leases"] if entry["reservation_id"] == identity["reservation_id"]), None)
            if row is None or any(row[key] != identity[key] for key in _KEYS if key != "state"):
                raise WorkRecordConflict("lease identity mismatch")
            return row

        terminal = state in {"finished", "blocked", "stopped"}
        observed = None
        if terminal:
            with _file_lock(self._lock_path, self._timeout):
                observed = dict(matching(self._read()))
                if observed["state"] not in {"started", state}:
                    raise WorkRecordConflict("invalid or uncertain source lease transition")
            # Host proof may consult this ledger or other stores. Never execute
            # external callbacks while holding the source filesystem lock.
            try:
                trusted = callable(trusted_terminal_check) and trusted_terminal_check(dict(observed), state) is True
            except Exception:
                trusted = False
            if not trusted:
                raise WorkRecordConflict("trusted matching terminal proof required")

        with _file_lock(self._lock_path, self._timeout):
            data = self._read()
            row = matching(data)
            if terminal and row != observed:
                raise WorkRecordConflict("lease changed during terminal proof verification")
            if row["state"] == state:
                if state == "started":
                    raise WorkRecordConflict("already-started lease cannot authorize re-execution")
                return dict(row)
            if ((state == "started" and row["state"] != "reserved")
                    or (state == "cancelled" and row["state"] != "reserved")
                    or (terminal and row["state"] != "started")):
                raise WorkRecordConflict("invalid or uncertain source lease transition")
            if state == "started": self._require_source_text(row, expected_text)
            row["state"] = state; data["revision"] += 1
            self._publish(data)
            return dict(row)

    def start(self, identity: dict[str, Any], *, expected_text: str) -> dict[str, Any]:
        return self._transition(identity, "started", expected_text=expected_text)

    def cancel_unstarted(self, identity: dict[str, Any]) -> dict[str, Any]:
        return self._transition(identity, "cancelled")

    def finish(self, identity: dict[str, Any], *, terminal_status: str,
               trusted_terminal_check: Callable[..., bool]) -> dict[str, Any]:
        if terminal_status not in {"finished", "blocked", "stopped"}:
            raise WorkRecordConflict("unknown terminal state cannot release a source lease")
        return self._transition(identity, terminal_status, trusted_terminal_check=trusted_terminal_check)
