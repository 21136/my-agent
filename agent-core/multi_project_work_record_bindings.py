"""Per-conversation bindings from explicit worker tasks to existing project records.
This namespace is intentionally separate from WorkRecordStore: its owner is one
conversation, while each referenced source remains bound to its own project
Session. Callers must supply read-only project record sources explicitly authorized
by the trusted host; parent Session.project_id is not cross-project authority.
"""
from __future__ import annotations
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
import re
import stat
import tempfile
import uuid
from pathlib import Path
from typing import Any, Mapping, Sequence
from weakref import WeakKeyDictionary

from session import Session
from work_records import (
    WorkRecordConflict,
    WorkRecordCorrupt,
    WorkRecordError,
    WorkRecordScopeError,
    WorkRecordStore,
    WorkRecordWriteError,
    _canonical_session,
    _file_lock,
    _digest,
)

_FILENAME = "multi-project-work-record-bindings.v1.json"
_MAX_BYTES = 4 * 1024 * 1024
_MAX_BINDINGS = 2000
_MAX_INLINE_REFRESHES = 100
_MAX_REFRESH_SEGMENTS = 20_000
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_BATCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
_LIFECYCLE_STATUSES = {"model_turn_finished", "blocked", "failed", "cancelled", "limit_reached", "unknown"}
_FINISH_REASONS = {"completed", "cancelled", "timeout", "error", "paused", "unknown"}
_READ_ONLY_READERS: WeakKeyDictionary = WeakKeyDictionary()


def _reader_for(source: ReadOnlyWorkRecordStore) -> WorkRecordStore:
    try:
        return _READ_ONLY_READERS[source]
    except KeyError:
        raise PermissionError("read-only source is no longer valid") from None


class ReadOnlyWorkRecordStore:
    """Narrow reader for one project Session; exposes no WorkRecordStore mutators."""
    __slots__ = ("__weakref__",)

    def __init__(self, session: Session) -> None:
        if type(session) is not Session:
            raise TypeError("a validated project Session is required")
        _READ_ONLY_READERS[self] = WorkRecordStore(session)

    @property
    def session_id(self) -> str:
        return _reader_for(self)._conversation_id

    @property
    def project_id(self) -> str:
        return _reader_for(self)._bound_project_id

    @property
    def agent_root(self) -> Path:
        return _reader_for(self)._agent_root

    @property
    def lock_path(self) -> Path:
        return _reader_for(self)._lock_path

    def validate_scope(self) -> None:
        _reader_for(self)._validate_scope()

    def list_items(self) -> list[dict[str, Any]]:
        return _reader_for(self).list_items()

    def get_item(self, item_id: str) -> dict[str, Any] | None:
        return _reader_for(self).get_item(item_id)

    def _read_items_locked(self) -> list[dict[str, Any]]:
        """Internal binding path; caller holds the associated OS lock."""
        reader = _reader_for(self)
        reader._validate_scope()
        return reader._read()["items"]


@dataclass(frozen=True, eq=False)
class AuthorizedProjectRecordSource:
    """Host-issued read-only source receipt; it grants no project-record writes."""
    authorization_id: str
    conversation_id: str
    project_id: str
    store: ReadOnlyWorkRecordStore


_ISSUED_SOURCE_CLAIMS: WeakKeyDictionary = WeakKeyDictionary()


def _issue_authorized_project_record_source(
    authorization_id: str,
    conversation_id: str,
    project_id: str,
    store: ReadOnlyWorkRecordStore,
) -> AuthorizedProjectRecordSource:
    """Record an immutable host-side claim; direct dataclass construction is not authority."""
    if (type(authorization_id) is not str or not authorization_id.strip()
            or type(conversation_id) is not str or not conversation_id.strip()
            or type(project_id) is not str or not project_id.strip()
            or type(store) is not ReadOnlyWorkRecordStore):
        raise PermissionError("invalid read-only source receipt fields")
    store.validate_scope()
    if store.project_id != project_id:
        raise PermissionError("read-only source scope does not match its project")
    source = AuthorizedProjectRecordSource(authorization_id, conversation_id, project_id, store)
    _ISSUED_SOURCE_CLAIMS[source] = (
        authorization_id, conversation_id, project_id,
        store.session_id, store.project_id, str(store.agent_root),
    )
    return source


def _is_issued_authorized_project_record_source(source: AuthorizedProjectRecordSource) -> bool:
    try:
        if not any(issued is source for issued in _ISSUED_SOURCE_CLAIMS):
            return False
        claim = _ISSUED_SOURCE_CLAIMS.get(source)
        store = source.store
        return claim == (
            source.authorization_id, source.conversation_id, source.project_id,
            store.session_id, store.project_id, str(store.agent_root),
        )
    except Exception:
        return False


@dataclass(frozen=True, slots=True)
class ExistingTaskBinding:
    """Explicit, unmodified task input and exact identity of an existing requirement."""
    project_id: str
    item_id: str
    requirement_id: str
    task_id: str
    task_input: str
    worker_id: str
    run_id: str
    source_item_digest: str | None = None


class WorkRecordBindingConflict(WorkRecordConflict):
    """A batch/binding identity was reused with different content or receipt."""


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise WorkRecordCorrupt("binding namespace contains duplicate JSON keys")
        result[key] = value
    return result


def _valid_id(value: Any, *, batch: bool = False) -> bool:
    return type(value) is str and (_BATCH_RE if batch else _ID_RE).fullmatch(value) is not None


def _task_lineage(row: dict[str, Any]) -> tuple[str, str, str, str, str]:
    """Immutable task-to-requirement identity shared by retries/continuations."""
    return (row["source_session_id"], row["item_id"], row["requirement_id"],
            row["requirement_text"], row["task_input"])


def _has_unresolved_started_binding(row: dict[str, Any]) -> bool:
    lifecycle = row.get("lifecycle")
    return (row.get("start_receipt") is not None
            and (lifecycle is None or lifecycle.get("status") == "unknown"))


def _replace_parent_from_handle(handle: int, temporary_path: Path, target: Path,
                                *, replace_if_exists: bool = True) -> None:
    """Rename the opened file itself on Windows, never a re-resolved temp path."""
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    call = kernel.SetFileInformationByHandle
    call.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    call.restype = wintypes.BOOL
    name = str(target)
    class RenameInfo(ctypes.Structure):
        _fields_ = [("ReplaceIfExists", wintypes.BOOL), ("RootDirectory", wintypes.HANDLE),
                    ("FileNameLength", wintypes.DWORD), ("FileName", wintypes.WCHAR * (len(name) + 1))]
    info = RenameInfo(replace_if_exists, None, len(name.encode("utf-16-le")), name)
    if not call(handle, 3, ctypes.byref(info), ctypes.sizeof(info)):
        error = ctypes.get_last_error()
        if error in {80, 183}:
            raise FileExistsError(error, "destination already exists")
        raise OSError(error, "parent atomic handle rename failed")


def _delete_open_file_handle(kernel, handle) -> None:
    import ctypes
    from ctypes import wintypes
    setinfo = kernel.SetFileInformationByHandle
    setinfo.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    setinfo.restype = wintypes.BOOL
    class Disposition(ctypes.Structure):
        _fields_ = [("DeleteFile", wintypes.BOOL)]
    disposition = Disposition(True)
    if not setinfo(handle, 4, ctypes.byref(disposition), ctypes.sizeof(disposition)):
        raise OSError(ctypes.get_last_error(), "handle-identity delete failed")


def _read_refresh_segment_raw(path: Path, expected_sha256: str) -> bytes:
    before = path.lstat()
    if (not stat.S_ISREG(before.st_mode) or getattr(before, "st_file_attributes", 0) & 0x400
            or getattr(before, "st_nlink", 1) != 1 or before.st_size > _MAX_BYTES):
        raise WorkRecordCorrupt("unsafe refresh segment target")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(fd); current = path.lstat()
        if ((opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
                or (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino)
                or not stat.S_ISREG(opened.st_mode) or getattr(opened, "st_nlink", 1) != 1
                or getattr(current, "st_file_attributes", 0) & 0x400):
            raise WorkRecordCorrupt("refresh segment changed during read")
        with os.fdopen(fd, "rb") as stream:
            fd = -1
            raw = stream.read(_MAX_BYTES + 1)
    finally:
        if fd >= 0:
            os.close(fd)
    if len(raw) > _MAX_BYTES or hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise WorkRecordCorrupt("refresh segment digest mismatch")
    return raw


def _publish_refresh_segment_by_handle(namespace, raw: bytes, target: Path) -> None:
    """Publish a complete content-addressed segment with Windows no-replace rename."""
    if os.name != "nt":
        raise WorkRecordWriteError("safe refresh segment publication is Windows-only")
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle; close.argtypes = [wintypes.HANDLE]; close.restype = wintypes.BOOL
    write = kernel.WriteFile
    write.argtypes = [wintypes.HANDLE, wintypes.LPCVOID, wintypes.DWORD,
                      ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
    write.restype = wintypes.BOOL
    flush = kernel.FlushFileBuffers; flush.argtypes = [wintypes.HANDLE]; flush.restype = wintypes.BOOL
    invalid = wintypes.HANDLE(-1).value
    handle = invalid
    for _ in range(8):
        temporary = namespace._session_dir / (".project-refresh-stage." + uuid.uuid4().hex + ".tmp")
        handle = create(str(temporary), 0x40000000 | 0x00010000, 7, None, 1, 0x80 | 0x00200000, None)
        if handle != invalid:
            break
        if ctypes.get_last_error() not in {80, 183}:
            raise OSError(ctypes.get_last_error(), "refresh temporary file creation failed")
    if handle == invalid:
        raise WorkRecordWriteError("refresh temporary file name collision")
    rename_attempted = False
    delete_requested = False
    try:
        namespace._validate_scope()
        buf = ctypes.create_string_buffer(raw)
        written = wintypes.DWORD()
        if not write(handle, buf, len(raw), ctypes.byref(written), None) or written.value != len(raw):
            raise OSError(ctypes.get_last_error(), "refresh temporary file write failed")
        if not flush(handle):
            raise OSError(ctypes.get_last_error(), "refresh temporary file flush failed")
        namespace._validate_scope()
        rename_attempted = True
        try:
            _replace_parent_from_handle(handle, temporary, target, replace_if_exists=False)
        except FileExistsError:
            expected = hashlib.sha256(raw).hexdigest()
            try:
                existing = _read_refresh_segment_raw(target, expected)
            except FileNotFoundError:
                _delete_open_file_handle(kernel, handle)
                delete_requested = True
                raise
            _delete_open_file_handle(kernel, handle)
            delete_requested = True
            if existing != raw:
                raise WorkRecordCorrupt("concurrent refresh segment target differs")
    finally:
        try:
            # Before rename, delete only this opened temp handle. After an uncertain
            # rename error, retain the path; a later locked retry reaps its reserved name.
            if not rename_attempted and not delete_requested:
                _delete_open_file_handle(kernel, handle)
        finally:
            # Cleanup errors must propagate, but never strand this process's handle.
            close(handle)


def _publish_parent_by_handle(namespace, raw: bytes) -> None:
    """Create, write and rename a file held by identity; no pathname cleanup."""
    if os.name != "nt":
        raise WorkRecordWriteError("safe parent handle publication is unavailable on this platform")
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle; close.argtypes = [wintypes.HANDLE]; close.restype = wintypes.BOOL
    write = kernel.WriteFile
    write.argtypes = [wintypes.HANDLE, wintypes.LPCVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
    write.restype = wintypes.BOOL
    flush = kernel.FlushFileBuffers; flush.argtypes = [wintypes.HANDLE]; flush.restype = wintypes.BOOL
    invalid = wintypes.HANDLE(-1).value
    handle = invalid
    for _ in range(8):
        temporary = namespace._session_dir / (".multi-project-bindings." + uuid.uuid4().hex + ".tmp")
        handle = create(str(temporary), 0x40000000 | 0x00010000, 7, None, 1, 0x80 | 0x00200000, None)
        if handle != invalid:
            break
        if ctypes.get_last_error() not in {80, 183}:
            raise OSError(ctypes.get_last_error(), "parent temporary file creation failed")
    if handle == invalid:
        raise OSError("parent temporary file name collision")
    rename_attempted = False
    try:
        namespace._validate_scope()
        buf = ctypes.create_string_buffer(raw)
        written = wintypes.DWORD()
        if not write(handle, buf, len(raw), ctypes.byref(written), None) or written.value != len(raw):
            raise OSError(ctypes.get_last_error(), "parent temporary file write failed")
        if not flush(handle):
            raise OSError(ctypes.get_last_error(), "parent temporary file flush failed")
        namespace._validate_scope()
        # On an uncertain rename error retain the file. Removing it could delete
        # a successfully committed parent; never delete any path by name.
        rename_attempted = True
        _replace_parent_from_handle(handle, temporary, namespace._path)
    finally:
        if not rename_attempted:
            setinfo = kernel.SetFileInformationByHandle
            setinfo.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
            setinfo.restype = wintypes.BOOL
            class Disposition(ctypes.Structure):
                _fields_ = [("DeleteFile", wintypes.BOOL)]
            disposition = Disposition(True)
            # If the OS refuses deletion, preserve the orphan instead of falling
            # back to an unsafe path-based unlink.
            setinfo(handle, 4, ctypes.byref(disposition), ctypes.sizeof(disposition))
        close(handle)


class ConversationWorkRecordBindings:
    """Atomic per-dialogue index of explicitly authorized cross-project task bindings.
    ``authorized_sources`` is a read-only capability map, not something to infer
    from the parent Session or project_sessions. Each source must follow explicit
    user confirmation of its project Session work-record read scope. This class
    writes only this dialogue's binding index: it does not create or modify project
    WorkRecord items/requirements, change project-session bindings, or create UI
    sessions. It only links exact pre-existing item/requirement IDs and stores the
    exact task input plus independent worker/run identities. Continuations currently
    require task_input and requirement_text to remain exactly unchanged; edited
    requirements with retained version history are not supported by this namespace.
    """

    def __init__(self, session: Session, *, lock_timeout: float = 1.0) -> None:
        if type(lock_timeout) not in (int, float) or lock_timeout < 0:
            raise ValueError("lock_timeout must be nonnegative")
        self._session = session
        self._session_dir, self._agent_root, self._conversation_id = _canonical_session(session)
        self._parent_project_id = getattr(session.meta, "project_id", "")
        self._lock_timeout = float(lock_timeout)
        self._path = self._session_dir / _FILENAME
        self._dialog_lock_path = self._session_dir / ".work-records.lock"

    def _validate_scope(self) -> None:
        session_dir, agent_root, conversation_id = _canonical_session(self._session)
        if (session_dir, agent_root, conversation_id) != (
            self._session_dir, self._agent_root, self._conversation_id
        ):
            raise WorkRecordScopeError("current dialogue binding changed")
        if getattr(self._session.meta, "project_id", "") != self._parent_project_id:
            raise WorkRecordScopeError("parent Session project binding changed")

    def _empty(self) -> dict[str, Any]:
        return {"schema_version": 1, "conversation_id": self._conversation_id,
                "revision": 0, "batches": []}

    def _read(self) -> dict[str, Any]:
        self._validate_scope()
        try:
            before = self._path.lstat()
        except FileNotFoundError:
            return self._empty()
        if (not stat.S_ISREG(before.st_mode)
                or getattr(before, "st_file_attributes", 0) & 0x400
                or getattr(before, "st_nlink", 1) != 1
                or before.st_size > _MAX_BYTES):
            raise WorkRecordCorrupt("binding namespace is not a bounded private regular file")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(self._path, flags)
            try:
                opened = os.fstat(fd)
                current = self._path.lstat()
                if ((opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
                        or (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino)
                        or not stat.S_ISREG(opened.st_mode) or getattr(opened, "st_nlink", 1) != 1):
                    raise WorkRecordCorrupt("binding namespace changed while opening")
                with os.fdopen(fd, "r", encoding="utf-8") as stream:
                    fd = -1
                    data = json.load(stream, object_pairs_hook=_reject_duplicate_pairs)
            finally:
                if fd >= 0:
                    os.close(fd)
        except WorkRecordError:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError, RecursionError) as exc:
            raise WorkRecordCorrupt("binding namespace cannot be read") from exc
        self._validate_payload(data)
        if data.get("schema_version") in (2, 3):
            self._refresh_rows_locked(data)
        return data

    def _validate_payload(self, data: Any) -> None:
        if (type(data) is not dict or not (
                data.get("schema_version") == 1 and set(data) in ({"schema_version", "conversation_id", "revision", "batches"},
                    {"schema_version", "conversation_id", "revision", "batches", "project_refreshes"})
                or data.get("schema_version") == 2 and set(data) == {"schema_version", "conversation_id", "revision", "batches", "project_refreshes", "refresh_segments"}
                or data.get("schema_version") == 3 and set(data) == {"schema_version", "conversation_id", "revision", "batches", "project_refreshes", "refresh_segments", "refresh_prepared"})
                or data["conversation_id"] != self._conversation_id
                or type(data["revision"]) is not int or data["revision"] < 0
                or type(data["batches"]) is not list or len(data["batches"]) > _MAX_BINDINGS):
            raise WorkRecordCorrupt("binding namespace schema or dialogue identity is invalid")
        batch_ids: set[str] = set()
        binding_ids: set[str] = set()
        task_lineages: dict[tuple[str, str], tuple[str, str, str, str, str]] = {}
        for batch in data["batches"]:
            if (type(batch) is not dict or set(batch) != {"batch_id", "digest", "bindings"}
                    or not _valid_id(batch["batch_id"], batch=True)
                    or batch["batch_id"] in batch_ids or type(batch["digest"]) is not str
                    or re.fullmatch(r"[0-9a-f]{64}", batch["digest"]) is None
                    or type(batch["bindings"]) is not list or not batch["bindings"]):
                raise WorkRecordCorrupt("binding batch shape is invalid")
            batch_ids.add(batch["batch_id"])
            for row in batch["bindings"]:
                required = {"binding_id", "authorization_id", "project_id", "source_session_id", "item_id", "requirement_id",
                            "requirement_text", "task_id", "task_input", "worker_id", "run_id",
                            "record_status", "verification_status", "lifecycle"}
                read_attempt_keys = {"read_attempts_available", "read_attempts"}
                optional = read_attempt_keys | {"start_receipt", "source_item_digest", "card_copy", "project_session_copy"}
                if (type(row) is not dict or not required.issubset(row)
                        or not set(row).issubset(required | optional)
                        or bool(set(row) & read_attempt_keys) != read_attempt_keys.issubset(row)
                        or not all(_valid_id(row[key]) for key in (
                            "binding_id", "authorization_id", "project_id", "source_session_id", "item_id", "requirement_id",
                            "task_id", "worker_id", "run_id"))
                        or row["binding_id"] in binding_ids
                        or type(row["requirement_text"]) is not str or not row["requirement_text"].strip()
                        or type(row["task_input"]) is not str or not row["task_input"].strip()
                        or row["record_status"] != "unknown"
                        or row["verification_status"] != "not_verified"):
                    raise WorkRecordCorrupt("task binding shape is invalid")
                if "source_item_digest" in row and (type(row["source_item_digest"]) is not str
                        or re.fullmatch(r"[0-9a-f]{64}", row["source_item_digest"]) is None):
                    raise WorkRecordCorrupt("source item snapshot digest is invalid")
                if read_attempt_keys.issubset(row):
                    from project_worker_read_evidence import validate_project_worker_read_attempts
                    if not validate_project_worker_read_attempts(
                        row["read_attempts_available"], row["read_attempts"],
                    ):
                        raise WorkRecordCorrupt("project worker read attempt receipt is invalid")
                binding_ids.add(row["binding_id"])
                task_key = (row["project_id"], row["task_id"])
                lineage = _task_lineage(row)
                if task_key in task_lineages and task_lineages[task_key] != lineage:
                    raise WorkRecordCorrupt("persisted task continuation changed its requirement binding")
                task_lineages[task_key] = lineage
                start = row.get("start_receipt")
                if "start_receipt" in row and (
                    type(start) is not dict
                    or set(start) != {"reservation_id", "parent_run_id", "turn_epoch"}
                    or not _valid_id(start["reservation_id"])
                    or not _valid_id(start["parent_run_id"])
                    or type(start["turn_epoch"]) is not int or start["turn_epoch"] < 1
                ):
                    raise WorkRecordCorrupt("worker start receipt is invalid")
                lifecycle = row["lifecycle"]
                if type(lifecycle) is dict and lifecycle.get("status") == "unknown" and (
                    start is None or lifecycle.get("finish_reason") != "unknown"
                    or lifecycle.get("sequence") != start["turn_epoch"]
                ):
                    raise WorkRecordCorrupt("unknown lifecycle lacks matching start evidence")
                if (read_attempt_keys.issubset(row) and lifecycle is None):
                    raise WorkRecordCorrupt("read-attempt receipt has no worker lifecycle")
                if lifecycle is not None and (
                    type(lifecycle) is not dict
                    or set(lifecycle) != {"sequence", "status", "finish_reason"}
                    or type(lifecycle["sequence"]) is not int or lifecycle["sequence"] < 1
                    or lifecycle["status"] not in _LIFECYCLE_STATUSES
                    or lifecycle["finish_reason"] not in _FINISH_REASONS
                ):
                    raise WorkRecordCorrupt("host lifecycle receipt is invalid")

                if "card_copy" in row:
                    saved = row["card_copy"]
                    if (type(saved) is not dict or set(saved) not in ({"snapshot", "intent"}, {"snapshot", "intent", "confirmed_at"})
                            or type(saved["snapshot"]) is not dict or type(saved["intent"]) is not dict):
                        raise WorkRecordCorrupt("linked item copy shape is invalid")
                    snap, intent = saved["snapshot"], saved["intent"]
                    if "confirmed_at" in saved:
                        try:
                            timestamp = saved["confirmed_at"]
                            if type(timestamp) is not str or len(timestamp) > 40 or not timestamp.endswith("Z"):
                                raise ValueError("invalid confirmation time")
                            parsed = datetime.fromisoformat(timestamp[:-1] + "+00:00")
                            if parsed.tzinfo is None:
                                raise ValueError("missing timezone")
                        except (ValueError, TypeError):
                            raise WorkRecordCorrupt("invalid linked copy confirmation time") from None
                    if (set(snap) - {"item_status"} != {"binding_id", "project_id", "source_session_id", "item_id", "item_title",
                                      "requirement_id", "requirement_text", "requirement_status", "source_item_digest",
                                      "worker_id", "worker_run_id", "host_start", "host_lifecycle", "operation_result", "verification"}
                            or any(snap.get(k) != row.get(k) for k in
                                   ("binding_id", "project_id", "source_session_id", "item_id", "requirement_id", "requirement_text", "source_item_digest", "worker_id"))
                            or type(row.get("source_item_digest")) is not str
                            or type(row.get("start_receipt")) is not dict
                            or type(row.get("lifecycle")) is not dict
                            or row["start_receipt"]["turn_epoch"] != row["lifecycle"]["sequence"]
                            or snap.get("worker_run_id") != row["run_id"]
                            or snap.get("host_start") != row.get("start_receipt")
                            or snap.get("host_lifecycle") != row.get("lifecycle")
                            or ("item_status" in snap and (type(snap["item_status"]) is not str or snap["item_status"] not in {"active", "completed", "in_progress", "unknown", "blocked", "stopped"}))
                            or ("confirmed_at" in saved and "item_status" not in snap)
                            or type(snap.get("item_title")) is not str or not snap["item_title"].strip()
                            or len(snap["item_title"].encode("utf-8")) > 4096
                            or type(snap.get("requirement_text")) is not str or len(snap["requirement_text"].encode("utf-8")) > 4096
                            or type(snap.get("requirement_status")) is not str
                            or snap["requirement_status"] not in {"pending", "in_progress", "unknown", "blocked", "stopped", "completed"}
                            or snap.get("operation_result") != "unknown" or snap.get("verification") != "not_verified"
                            or set(intent) != {"copy_id", "state"} or intent.get("state") not in {"pending", "reference_queued"}
                            or intent.get("copy_id") != _digest(snap)):
                        raise WorkRecordCorrupt("linked item copy identity or lifecycle is invalid")

        # A copied source-session view is an immutable host-authored sidecar,
        # not a project-wide assertion and not a worker operation result.
        all_rows = [r for batch in data["batches"] for r in batch["bindings"]]
        for row in all_rows:
            if "project_session_copy" not in row:
                continue
            copy = row["project_session_copy"]
            if (type(copy) is not dict or set(copy) != {"snapshot", "confirmed_at", "intent"}
                    or type(copy["snapshot"]) is not dict or type(copy["intent"]) is not dict
                    or set(copy["intent"]) != {"copy_id", "state"}
                    or copy["intent"].get("state") not in {"pending", "reference_queued"}
                    or copy["intent"].get("copy_id") != _digest(copy["snapshot"])):
                raise WorkRecordCorrupt("invalid project session copy")
            snap = copy["snapshot"]
            if (set(snap) not in ({"scope", "project_id", "source_session_id", "source_revision", "items", "worker_facts"},
                                     {"scope", "project_id", "source_session_id", "source_revision", "items", "worker_facts", "mapping_claim"})
                    or ("mapping_claim" in snap and snap["mapping_claim"] != "not_asserted")
                    or snap["scope"] != "authorized_source_session_only"
                    or snap["project_id"] != row["project_id"] or snap["source_session_id"] != row["source_session_id"]
                    or type(snap["source_revision"]) is not int or snap["source_revision"] < 0
                    or type(snap["items"]) is not list or type(snap["worker_facts"]) is not list
                    or not snap["worker_facts"] or len(snap["worker_facts"]) > 20):
                raise WorkRecordCorrupt("project session copy scope invalid")
            try:
                ts = copy["confirmed_at"]
                if type(ts) is not str or len(ts) > 40 or not ts.endswith("Z"):
                    raise ValueError
                datetime.fromisoformat(ts[:-1] + "+00:00")
                if len(json.dumps(snap, ensure_ascii=False).encode("utf-8")) > 65536:
                    raise ValueError
                ids = set()
                for item in snap["items"]:
                    if (type(item) is not dict or set(item) != {"item_id", "title", "status", "requirements"}
                            or not _valid_id(item["item_id"]) or item["item_id"] in ids
                            or type(item["title"]) is not str or type(item["status"]) is not str
                            or type(item["requirements"]) is not list):
                        raise ValueError
                    ids.add(item["item_id"])
                    req_ids = set()
                    for req in item["requirements"]:
                        if (type(req) is not dict or set(req) != {"requirement_id", "text", "status"}
                                or not _valid_id(req["requirement_id"]) or req["requirement_id"] in req_ids
                                or type(req["text"]) is not str or type(req["status"]) is not str):
                            raise ValueError
                        req_ids.add(req["requirement_id"])
                seen = set()
                for fact in snap["worker_facts"]:
                    if (type(fact) is not dict or set(fact) != {"binding_id", "item_id", "requirement_id", "worker_id", "worker_run_id", "host_start", "host_lifecycle", "operation_result", "verification"}
                            or fact["binding_id"] in seen):
                        raise ValueError
                    seen.add(fact["binding_id"])
                    matches = [r for r in all_rows if r["binding_id"] == fact["binding_id"]]
                    if len(matches) != 1:
                        raise ValueError
                    r = matches[0]
                    if (r["project_id"] != row["project_id"] or r["source_session_id"] != row["source_session_id"]
                            or fact["item_id"] != r["item_id"] or fact["requirement_id"] != r["requirement_id"]
                            or fact["worker_id"] != r["worker_id"] or fact["worker_run_id"] != r["run_id"]
                            or fact["host_start"] != r.get("start_receipt") or fact["host_lifecycle"] != r.get("lifecycle")
                            or fact["operation_result"] != "unknown" or fact["verification"] != "not_verified"):
                        raise ValueError
                if row["binding_id"] not in seen:
                    raise ValueError
            except (KeyError, TypeError, ValueError, OverflowError):
                raise WorkRecordCorrupt("invalid project session copy contents") from None

        refreshes = data.get("project_refreshes", [])
        segments = data.get("refresh_segments", [])
        if (type(refreshes) is not list or len(refreshes) > _MAX_INLINE_REFRESHES
                or type(segments) is not list or len(segments) > _MAX_REFRESH_SEGMENTS):
            raise WorkRecordCorrupt("invalid refresh collection or segment index")
        prepared = data.get("refresh_prepared")
        if prepared is not None and type(prepared) is not dict:
            raise WorkRecordCorrupt("invalid prepared refresh")
        refresh_ids = set()
        for refresh in [*refreshes, *([] if prepared is None else [prepared])]:
            try:
                if (type(refresh) is not dict or set(refresh) != {"refresh_id", "run_id", "turn_epoch", "binding_id", "authorization_id", "copy"}
                        or type(refresh["refresh_id"]) is not str or str(uuid.UUID(refresh["refresh_id"])) != refresh["refresh_id"]
                        or refresh["refresh_id"] in refresh_ids or not _valid_id(refresh["authorization_id"])
                        or type(refresh["run_id"]) is not str or str(uuid.UUID(refresh["run_id"])) != refresh["run_id"]
                        or type(refresh["turn_epoch"]) is not int or refresh["turn_epoch"] < 1):
                    raise ValueError
                refresh_ids.add(refresh["refresh_id"])
                shadow = json.loads(json.dumps({k: v for k, v in data.items() if k not in {"project_refreshes", "refresh_segments", "refresh_prepared"}}))
                shadow["schema_version"] = 1
                anchors = [r for b in shadow["batches"] for r in b["bindings"] if r["binding_id"] == refresh["binding_id"]]
                if len(anchors) != 1:
                    raise ValueError
                anchors[0]["project_session_copy"] = refresh["copy"]
                self._validate_payload(shadow)
            except (ValueError, TypeError, KeyError, AttributeError) as exc:
                raise WorkRecordCorrupt("invalid project refresh") from exc

    def _refresh_rows_locked(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        """Unified view; caller holds the parent work-record lock."""
        rows = list(data.get("project_refreshes", []))
        segments = data.get("refresh_segments", [])
        if type(segments) is not list or len(segments) > _MAX_REFRESH_SEGMENTS:
            raise WorkRecordCorrupt("invalid refresh segment index")
        identities = {row["refresh_id"] for row in rows}
        for entry in segments:
            if (type(entry) is not dict or set(entry) != {"refresh_id", "sha256"}
                    or type(entry["refresh_id"]) is not str or type(entry["sha256"]) is not str
                    or re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]) is None):
                raise WorkRecordCorrupt("invalid refresh segment identity")
            try:
                if str(uuid.UUID(entry["refresh_id"])) != entry["refresh_id"]:
                    raise ValueError()
            except (ValueError, AttributeError):
                raise WorkRecordCorrupt("invalid refresh segment UUID") from None
            if entry["refresh_id"] in identities:
                raise WorkRecordCorrupt("duplicate refresh identity")
            path = self._session_dir / (".project-refresh-" + entry["sha256"] + ".json")
            try:
                before = path.lstat()
                if (not stat.S_ISREG(before.st_mode) or getattr(before, "st_file_attributes", 0) & 0x400
                        or getattr(before, "st_nlink", 1) != 1 or before.st_size > _MAX_BYTES):
                    raise WorkRecordCorrupt("unsafe refresh segment")
                fd = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0))
                try:
                    opened = os.fstat(fd); current = path.lstat()
                    if ((opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
                            or (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino)
                            or not stat.S_ISREG(opened.st_mode) or getattr(opened, "st_nlink", 1) != 1):
                        raise WorkRecordCorrupt("refresh segment changed")
                    with os.fdopen(fd, "rb") as stream:
                        fd = -1
                        raw = stream.read(_MAX_BYTES + 1)
                finally:
                    if fd >= 0: os.close(fd)
                if len(raw) > _MAX_BYTES or hashlib.sha256(raw).hexdigest() != entry["sha256"]:
                    raise WorkRecordCorrupt("refresh segment digest mismatch")
                row = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_pairs)
                if type(row) is not dict or row.get("refresh_id") != entry["refresh_id"]:
                    raise WorkRecordCorrupt("refresh segment identity mismatch")
                shadow = {k: v for k, v in data.items() if k not in {"project_refreshes", "refresh_segments", "refresh_prepared"}}
                shadow["schema_version"] = 1
                shadow["project_refreshes"] = [row]
                self._validate_payload(shadow)
            except WorkRecordError:
                raise
            except (OSError, UnicodeError, ValueError, TypeError, RecursionError) as exc:
                raise WorkRecordCorrupt("refresh segment unreadable") from exc
            identities.add(entry["refresh_id"])
            rows.append(row)
        prepared = data.get("refresh_prepared")
        if prepared is not None:
            if prepared["refresh_id"] in identities:
                raise WorkRecordCorrupt("duplicate prepared refresh identity")
            rows.append(prepared)
        return rows

    def _write_refresh_segment_locked(self, row: dict[str, Any]) -> dict[str, str]:
        """Write a complete content-addressed row without replacing an existing file."""
        self._validate_scope()
        if type(row) is not dict or type(row.get("refresh_id")) is not str:
            raise ValueError("invalid refresh segment row")
        raw = (json.dumps(row, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n").encode("utf-8")
        if len(raw) > _MAX_BYTES:
            raise WorkRecordWriteError("refresh segment size limit reached")
        digest = hashlib.sha256(raw).hexdigest()
        entry = {"refresh_id": row["refresh_id"], "sha256": digest}
        target = self._session_dir / (".project-refresh-" + digest + ".json")
        try:
            existing = _read_refresh_segment_raw(target, digest)
        except FileNotFoundError:
            existing = None
        if existing is not None:
            if existing != raw:
                raise WorkRecordCorrupt("refresh segment digest target contains different content")
            return entry
        _publish_refresh_segment_by_handle(self, raw, target)
        if _read_refresh_segment_raw(target, digest) != raw:
            raise WorkRecordCorrupt("published refresh segment does not match its identity")
        return entry

    def _delete_unindexed_refresh_segment_locked(self, entry: dict[str, str], data: dict[str, Any]) -> None:
        """Delete only the exact opened, complete segment if no parent index references it."""
        digest = entry["sha256"]
        if any(row.get("sha256") == digest for row in data.get("refresh_segments", [])):
            return
        if os.name != "nt":
            raise WorkRecordWriteError("safe refresh segment cleanup is Windows-only")
        path = self._session_dir / (".project-refresh-" + digest + ".json")
        try:
            before = path.lstat()
        except FileNotFoundError:
            return
        if (not stat.S_ISREG(before.st_mode) or getattr(before, "st_file_attributes", 0) & 0x400
                or getattr(before, "st_nlink", 1) != 1 or before.st_size > _MAX_BYTES):
            raise WorkRecordWriteError("unindexed refresh segment identity is unsafe")
        import ctypes
        import msvcrt
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        create = kernel.CreateFileW
        create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                           wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        create.restype = wintypes.HANDLE
        invalid = wintypes.HANDLE(-1).value
        handle = create(str(path), 0x80000000 | 0x00010000, 7, None, 3, 0x00200000, None)
        if handle == invalid:
            raise OSError(ctypes.get_last_error(), "unindexed refresh segment open failed")
        fd = -1
        try:
            fd = msvcrt.open_osfhandle(int(handle), os.O_RDONLY | getattr(os, "O_BINARY", 0))
            handle = invalid
            opened = os.fstat(fd); current = path.lstat()
            if ((opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
                    or (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino)
                    or not stat.S_ISREG(opened.st_mode) or getattr(opened, "st_nlink", 1) != 1
                    or getattr(current, "st_file_attributes", 0) & 0x400):
                raise WorkRecordWriteError("unindexed refresh segment changed before cleanup")
            raw = os.read(fd, _MAX_BYTES + 1)
            if (len(raw) > _MAX_BYTES or hashlib.sha256(raw).hexdigest() != digest
                    or json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_pairs).get("refresh_id") != entry["refresh_id"]):
                raise WorkRecordWriteError("unindexed refresh segment contents do not match")
            if any(row.get("sha256") == digest for row in data.get("refresh_segments", [])):
                return
            h = msvcrt.get_osfhandle(fd)
            setinfo = kernel.SetFileInformationByHandle
            setinfo.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
            setinfo.restype = wintypes.BOOL
            class Disposition(ctypes.Structure):
                _fields_ = [("DeleteFile", wintypes.BOOL)]
            disposition = Disposition(True)
            if not setinfo(h, 4, ctypes.byref(disposition), ctypes.sizeof(disposition)):
                raise OSError(ctypes.get_last_error(), "unindexed refresh segment identity cleanup failed")
        finally:
            if fd >= 0:
                os.close(fd)
            elif handle != invalid:
                kernel.CloseHandle(handle)

    def _view_locked(self, data: dict[str, Any]) -> dict[str, Any]:
        view = dict(data)
        view["project_refreshes"] = self._refresh_rows_locked(data)
        return view

    def _publish(self, data: dict[str, Any]) -> None:
        raw = (json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n").encode("utf-8")
        if len(raw) > _MAX_BYTES:
            raise WorkRecordWriteError("binding namespace size limit reached")
        self._validate_scope()
        try:
            try:
                info = self._path.lstat()
            except FileNotFoundError:
                info = None
            if info is not None and (not stat.S_ISREG(info.st_mode)
                                     or getattr(info, "st_file_attributes", 0) & 0x400
                                     or getattr(info, "st_nlink", 1) != 1):
                raise WorkRecordWriteError("binding namespace target is unsafe")
            _publish_parent_by_handle(self, raw)
        except WorkRecordError:
            raise
        except OSError as exc:
            raise WorkRecordWriteError("binding namespace was not atomically published") from exc

    @staticmethod
    def _binding_digest(tasks: Sequence[ExistingTaskBinding]) -> str:
        payload = [{name: getattr(task, name) for name in (
            "project_id", "item_id", "requirement_id", "task_id", "task_input", "worker_id", "run_id"
        )} for task in tasks]
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def bind_existing_tasks(
        self,
        *,
        batch_id: str,
        tasks: Sequence[ExistingTaskBinding],
        authorized_sources: Mapping[str, AuthorizedProjectRecordSource],
    ) -> tuple[dict[str, Any], ...]:
        """Atomically bind exact existing records to unique worker/run identities.
        Authorization is deliberately supplied per project by trusted host wiring;
        this method never treats the parent Session.project_id as cross-project
        permission, and never infers the user's task split or creates a record.
        """
        if not _valid_id(batch_id, batch=True):
            raise ValueError("batch_id must be a stable non-empty caller identity")
        if isinstance(tasks, (str, bytes)) or not isinstance(tasks, (list, tuple)) or not tasks:
            raise ValueError("tasks must be a non-empty explicit sequence")
        if len(tasks) > _MAX_BINDINGS or any(type(task) is not ExistingTaskBinding for task in tasks):
            raise ValueError("tasks must contain bounded ExistingTaskBinding values")
        digest = self._binding_digest(tasks)
        projects = {task.project_id for task in tasks}
        if (type(authorized_sources) is not dict or set(authorized_sources) != projects
                or any(type(source) is not AuthorizedProjectRecordSource
                       or not _is_issued_authorized_project_record_source(source)
                       for source in authorized_sources.values())):
            raise PermissionError("each task project requires an explicitly authorized source store")
        self._validate_scope()
        task_ids: set[tuple[str, str]] = set()
        worker_ids: set[str] = set()
        run_ids: set[str] = set()
        record_ids: set[tuple[str, str, str]] = set()
        for task in tasks:
            for name in ("project_id", "item_id", "requirement_id", "task_id", "worker_id", "run_id"):
                if not _valid_id(getattr(task, name)):
                    raise ValueError(f"{name} must be a stable non-empty identity")
            if task.source_item_digest is not None and (type(task.source_item_digest) is not str
                    or re.fullmatch(r"[0-9a-f]{64}", task.source_item_digest) is None):
                raise ValueError("invalid source item snapshot digest")
            if type(task.task_input) is not str or not task.task_input.strip():
                raise ValueError("task_input must preserve an explicit non-empty input")
            identity = (task.project_id, task.task_id)
            record = (task.project_id, task.item_id, task.requirement_id)
            if identity in task_ids or task.worker_id in worker_ids or task.run_id in run_ids or record in record_ids:
                raise ValueError("task, worker, run, and record identities must be unique")
            task_ids.add(identity); worker_ids.add(task.worker_id); run_ids.add(task.run_id); record_ids.add(record)
        stores: dict[str, ReadOnlyWorkRecordStore] = {}
        authorization_ids: set[str] = set()
        for project_id, source in authorized_sources.items():
            store = source.store
            if (not _valid_id(source.authorization_id)
                    or source.authorization_id in authorization_ids
                    or source.conversation_id != self._conversation_id
                    or source.project_id != project_id):
                raise PermissionError("project authorization does not match this dialogue and project")
            authorization_ids.add(source.authorization_id)
            if type(store) is not ReadOnlyWorkRecordStore:
                raise PermissionError("authorized source must expose a read-only record interface")
            store.validate_scope()
            if (store.project_id != project_id
                    or store.agent_root != self._agent_root
                    or not _valid_id(store.session_id)):
                raise PermissionError("authorized source store is not bound to its declared project")
            stores[project_id] = store
        lock_paths = {str(self._dialog_lock_path.resolve(strict=False)).casefold(): self._dialog_lock_path}
        for store in stores.values():
            lock_paths[str(store.lock_path.resolve(strict=False)).casefold()] = store.lock_path
        with ExitStack() as stack:
            for path in sorted(lock_paths.values(), key=lambda value: str(value).casefold()):
                stack.enter_context(_file_lock(path, self._lock_timeout))
            self._validate_scope()
            source_items = {project_id: store._read_items_locked() for project_id, store in stores.items()}
            prepared: list[dict[str, Any]] = []
            for task in tasks:
                items = source_items[task.project_id]
                item_matches = [item for item in items if item.get("item_id") == task.item_id]
                if len(item_matches) != 1:
                    raise KeyError(f"existing item is missing or ambiguous for project {task.project_id}")
                item = item_matches[0]
                scope = item.get("scope")
                if type(scope) is not dict or scope.get("kind") != "project" or scope.get("project_id") != task.project_id:
                    raise PermissionError("existing record project scope does not match its authorized store")
                req_matches = [req for req in item["requirements"] if req.get("requirement_id") == task.requirement_id]
                if len(req_matches) != 1:
                    raise KeyError(f"existing requirement is missing or ambiguous for project {task.project_id}")
                req = req_matches[0]
                if task.source_item_digest is not None and _digest(item) != task.source_item_digest:
                    raise WorkRecordBindingConflict("confirmed source item snapshot changed")
                prepared.append({
                    "binding_id": hashlib.sha256(
                        f"{self._conversation_id}\0{batch_id}\0{task.project_id}\0{task.task_id}".encode("utf-8")
                    ).hexdigest(),
                    "authorization_id": authorized_sources[task.project_id].authorization_id,
                    "project_id": task.project_id,
                    "source_session_id": stores[task.project_id].session_id,
                    "item_id": task.item_id,
                    "requirement_id": task.requirement_id,
                    "requirement_text": req["text"],
                    "task_id": task.task_id,
                    "task_input": task.task_input,
                    "worker_id": task.worker_id,
                    "run_id": task.run_id,
                    "record_status": "unknown",
                    "verification_status": "not_verified",
                    "lifecycle": None,
                    **({"source_item_digest": task.source_item_digest} if task.source_item_digest is not None else {}),
                })
            data = self._read()
            prior = next((row for row in data["batches"] if row["batch_id"] == batch_id), None)
            if prior is not None:
                if prior["digest"] != digest:
                    raise WorkRecordBindingConflict("batch_id reused with different task bindings")
                prior_sources = {
                    row["project_id"]: row["source_session_id"]
                    for row in prior["bindings"]
                }
                current_sources = {
                    project_id: store.session_id
                    for project_id, store in stores.items()
                }
                if prior_sources != current_sources:
                    raise WorkRecordBindingConflict(
                        "batch_id reused with a different authorized source Session"
                    )
                for previous, current in zip(prior["bindings"], prepared):
                    if previous.get("source_item_digest") != current.get("source_item_digest"):
                        raise WorkRecordBindingConflict("confirmed source snapshot differs from existing binding")
                return tuple(json.loads(json.dumps(prior["bindings"])))
            existing = [row for batch in data["batches"] for row in batch["bindings"]]
            existing_workers = {row["worker_id"] for row in existing}
            existing_runs = {row["run_id"] for row in existing}
            existing_tasks = {
                (row["project_id"], row["task_id"]): _task_lineage(row)
                for row in existing
            }
            existing_by_task: dict[tuple[str, str], list[dict[str, Any]]] = {}
            for existing_row in existing:
                existing_by_task.setdefault(
                    (existing_row["project_id"], existing_row["task_id"]), []
                ).append(existing_row)
            for row in prepared:
                if row["worker_id"] in existing_workers or row["run_id"] in existing_runs:
                    raise WorkRecordBindingConflict("worker/run identity is already bound")
                task_key = (row["project_id"], row["task_id"])
                prior_lineage = existing_tasks.get(task_key)
                if prior_lineage is not None and prior_lineage != _task_lineage(row):
                    raise WorkRecordBindingConflict(
                        "task continuation must retain its original project/session/item/requirement and input"
                    )
                prior_bindings = existing_by_task.get(task_key, ())
                if any(_has_unresolved_started_binding(prior) for prior in prior_bindings):
                    raise WorkRecordBindingConflict(
                        "prior linked worker start is unresolved; automatic recovery and re-execution are blocked; "
                        "inspect the project and settle this binding explicitly"
                    )
            if len(existing) + len(prepared) > _MAX_BINDINGS:
                raise WorkRecordWriteError("binding namespace capacity reached")
            data["batches"].append({"batch_id": batch_id, "digest": digest, "bindings": prepared})
            data["revision"] += 1
            self._publish(data)
            return tuple(json.loads(json.dumps(prepared)))

    def list_bindings(self) -> list[dict[str, Any]]:
        self._validate_scope()
        with _file_lock(self._dialog_lock_path, self._lock_timeout):
            data = self._read()
            return json.loads(json.dumps([row for batch in data["batches"] for row in batch["bindings"]]))

    def get_binding(self, binding_id: str) -> dict[str, Any] | None:
        if not _valid_id(binding_id):
            return None
        return next((row for row in self.list_bindings() if row["binding_id"] == binding_id), None)

    def mark_item_copy_reference_queued(self, *, binding_id: str, copy_id: str,
                                        card_id: str, presentation_id: str,
                                        run_id: str, is_current: Callable[[], bool]) -> str:
        """Advance only after exact durable ledger and receipt-tracked presentation exist.

        Caller persists the history reference first, with the message lock released.
        No message lock or source lock is acquired under the shared work-record lock.
        """
        from dataclasses import asdict
        from work_records import WorkRecordStore
        from work_card_ledger import WorkCardLedger, build_linked_item_copy_snapshot, linked_item_copy_request_id
        from work_card_presentations import WorkCardPresentations
        store = WorkRecordStore(self._session)
        ledger = WorkCardLedger(self._session, store)
        presentations = WorkCardPresentations(self._session, store, ledger)
        self._validate_scope()
        with _file_lock(self._dialog_lock_path, self._lock_timeout):
            if not is_current():
                return "rejected"
            data = self._read()
            rows = [r for b in data["batches"] for r in b["bindings"] if r["binding_id"] == binding_id]
            if len(rows) != 1 or type(rows[0].get("card_copy")) is not dict:
                return "rejected"
            copy = rows[0]["card_copy"]
            if copy["intent"]["copy_id"] != copy_id:
                return "rejected"
            queued = copy["intent"]["state"] == "reference_queued"
            checked_copy = {**copy, "intent": {**copy["intent"], "state": "pending"}}
            expected = asdict(build_linked_item_copy_snapshot(self._conversation_id, checked_copy))
            cards = [r for r in ledger._ledger_store._read()["items"] if r["card_id"] == card_id]
            facts = [r for r in presentations._presentations_store._read()["items"]
                     if r["presentation_id"] == presentation_id]
            if (len(cards) != 1 or cards[0]["snapshot"] != expected
                    or cards[0]["request_id"] != linked_item_copy_request_id(self._conversation_id, copy_id)
                    or len(facts) != 1 or facts[0]["card_id"] != card_id
                    or facts[0].get("run_id") != run_id
                    or facts[0].get("receipt_state") == "legacy_untracked" or not is_current()):
                return "rejected"
            if queued:
                return "already_queued"
            copy["intent"]["state"] = "reference_queued"
            data["revision"] += 1
            self._publish(data)
            return "queued"

    def mark_project_session_copy_reference_queued(self, *, binding_id: str, copy_id: str,
                                        card_id: str, presentation_id: str,
                                        run_id: str, is_current: Callable[[], bool]) -> str:
        """Advance only after exact durable ledger and receipt-tracked presentation exist.

        Caller persists the history reference first, with the message lock released.
        No message lock or source lock is acquired under the shared work-record lock.
        """
        from dataclasses import asdict
        from work_records import WorkRecordStore
        from work_card_ledger import WorkCardLedger, build_linked_project_session_copy_snapshot, linked_project_session_copy_request_id
        from work_card_presentations import WorkCardPresentations
        store = WorkRecordStore(self._session)
        ledger = WorkCardLedger(self._session, store)
        presentations = WorkCardPresentations(self._session, store, ledger)
        self._validate_scope()
        with _file_lock(self._dialog_lock_path, self._lock_timeout):
            if not is_current():
                return "rejected"
            data = self._read()
            rows = [r for b in data["batches"] for r in b["bindings"] if r["binding_id"] == binding_id]
            if len(rows) != 1 or type(rows[0].get("project_session_copy")) is not dict:
                return "rejected"
            copy = rows[0]["project_session_copy"]
            if copy["intent"]["copy_id"] != copy_id:
                return "rejected"
            queued = copy["intent"]["state"] == "reference_queued"
            checked_copy = {**copy, "intent": {**copy["intent"], "state": "pending"}}
            expected = asdict(build_linked_project_session_copy_snapshot(self._conversation_id, binding_id, checked_copy))
            cards = [r for r in ledger._ledger_store._read()["items"] if r["card_id"] == card_id]
            facts = [r for r in presentations._presentations_store._read()["items"]
                     if r["presentation_id"] == presentation_id]
            if (len(cards) != 1 or cards[0]["snapshot"] != expected
                    or cards[0]["request_id"] != linked_project_session_copy_request_id(self._conversation_id, copy_id)
                    or len(facts) != 1 or facts[0]["card_id"] != card_id
                    or facts[0].get("run_id") != run_id
                    or facts[0].get("receipt_state") == "legacy_untracked" or not is_current()):
                return "rejected"
            if queued:
                return "already_queued"
            copy["intent"]["state"] = "reference_queued"
            data["revision"] += 1
            self._publish(data)
            return "queued"

    def list_project_session_refreshes(self) -> list[dict[str, Any]]:
        self._validate_scope()
        with _file_lock(self._dialog_lock_path, self._lock_timeout):
            return json.loads(json.dumps(self._refresh_rows_locked(self._read())))

    def mark_project_refresh_reference_queued(self, *, refresh_id: str, card_id: str,
                                              presentation_id: str, is_current: Any) -> str:
        from dataclasses import asdict
        from work_records import WorkRecordStore
        from work_card_ledger import WorkCardLedger, build_linked_project_session_copy_snapshot, linked_project_refresh_request_id
        from work_card_presentations import WorkCardPresentations
        store = WorkRecordStore(self._session)
        ledger = WorkCardLedger(self._session, store)
        presentations = WorkCardPresentations(self._session, store, ledger)
        with _file_lock(self._dialog_lock_path, self._lock_timeout):
            if is_current() is not True:
                return "rejected"
            data = self._read()
            rows = [r for r in self._refresh_rows_locked(data) if r["refresh_id"] == refresh_id]
            if len(rows) != 1:
                return "rejected"
            row = rows[0]; copy = row["copy"]
            prepared = data.get("refresh_prepared")
            is_prepared = type(prepared) is dict and prepared.get("refresh_id") == refresh_id
            expected_presentation = str(uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"my-agent-work-card:{self._conversation_id}:{uuid.UUID(row['run_id']).hex}:{card_id}",
            ))
            if presentation_id != expected_presentation:
                return "rejected"
            # The builder accepts only pending intents; normalize a detached copy
            # for validation without mutating or re-queuing the durable record.
            queued = copy["intent"]["state"] == "reference_queued"
            copy_for_validation = {**copy, "intent": {**copy["intent"], "state": "pending"}}
            expected = asdict(build_linked_project_session_copy_snapshot(self._conversation_id, row["binding_id"], copy_for_validation))
            cards = [r for r in ledger._ledger_store._read()["items"] if r["card_id"] == card_id]
            facts = [r for r in presentations._presentations_store._read()["items"] if r["presentation_id"] == presentation_id]
            if (len(cards) != 1 or cards[0]["snapshot"] != expected
                    or cards[0]["request_id"] != linked_project_refresh_request_id(self._conversation_id, refresh_id)
                    or len(facts) != 1 or facts[0]["card_id"] != card_id
                    or facts[0].get("run_id") != row["run_id"]
                    or facts[0].get("receipt_state") == "legacy_untracked" or is_current() is not True):
                return "rejected"
            segment_index = next((i for i, entry in enumerate(data.get("refresh_segments", [])) if entry["refresh_id"] == refresh_id), None)
            needs_segment = segment_index is not None or is_prepared
            if queued and not needs_segment:
                return "already_queued"
            if not queued:
                copy["intent"]["state"] = "reference_queued"
            if needs_segment:
                segment = self._write_refresh_segment_locked(row)
                if is_current() is not True:
                    self._delete_unindexed_refresh_segment_locked(segment, data)
                    return "rejected"
                if is_prepared:
                    segments = data.setdefault("refresh_segments", [])
                    if len(segments) >= _MAX_REFRESH_SEGMENTS:
                        raise WorkRecordWriteError("refresh segment index capacity reached; prepared intent retained")
                    data.pop("refresh_prepared", None)
                    data["schema_version"] = 2
                    segments.append(segment)
                else:
                    data["refresh_segments"][segment_index] = segment
            data["revision"] += 1
            self._validate_payload(data)
            if is_current() is not True:
                raise WorkRecordBindingConflict("refresh authorization changed")
            self._publish(data)
            return "already_queued" if queued else "queued"

    def prepare_project_session_copy(self, *, project_id: str, binding_ids: list[str],
                                     source: AuthorizedProjectRecordSource,
                                     expected: dict[str, Any] | None = None,
                                     is_current: Any = None, commit: bool = False) -> dict[str, Any]:
        """Build/recheck minimal project records in ONE authorized source Session.

        Preview alone grants no consent. Commit requires a matching confirmed
        preview and a live host predicate; it atomically stores a pending intent.
        Neither mode proves completeness across other Sessions or publishes a card.
        """
        if (type(source) is not AuthorizedProjectRecordSource
                or not _is_issued_authorized_project_record_source(source)
                or source.conversation_id != self._conversation_id
                or source.project_id != project_id or source.store.project_id != project_id
                or source.store.agent_root != self._agent_root):
            raise PermissionError("exact authorized source required")
        if (commit and (expected is None or not callable(is_current))):
            raise ValueError("commit requires confirmation and current turn")
        if (type(binding_ids) is not list or not binding_ids or len(binding_ids) > 20
                or len(set(binding_ids)) != len(binding_ids)
                or any(not _valid_id(value) for value in binding_ids)
                or (expected is not None and type(expected) is not dict)):
            raise ValueError("invalid project copy request")
        locks = {str(p.resolve(strict=False)).casefold(): p for p in
                 (self._dialog_lock_path, source.store.lock_path)}
        with ExitStack() as stack:
            for path in sorted(locks.values(), key=lambda p: str(p).casefold()):
                stack.enter_context(_file_lock(path, self._lock_timeout))
            self._validate_scope()
            if not _is_issued_authorized_project_record_source(source):
                raise PermissionError("source authorization changed")
            data = self._read()
            reader = _reader_for(source.store)
            reader._validate_scope()
            source_payload = reader._read()
            source_items = [item for item in source_payload["items"]
                            if item.get("scope") == {"kind": "project", "project_id": project_id}]
            items = [{"item_id": item["item_id"], "title": item["title"], "status": item["status"],
                      "requirements": [{"requirement_id": req["requirement_id"], "text": req["text"],
                                        "status": req["status"]} for req in item["requirements"]]}
                     for item in source_items]
            facts = []
            for binding_id in binding_ids:
                rows = [row for batch in data["batches"] for row in batch["bindings"]
                        if row["binding_id"] == binding_id]
                if len(rows) != 1:
                    raise WorkRecordBindingConflict("binding missing")
                row = rows[0]
                if (row["project_id"] != project_id or row["authorization_id"] != source.authorization_id
                        or row["source_session_id"] != source.store.session_id):
                    raise PermissionError("binding source mismatch")
                matches = [item for item in source_items if item["item_id"] == row["item_id"]]
                if (len(matches) != 1 or type(row.get("source_item_digest")) is not str
                        or _digest(matches[0]) != row["source_item_digest"]
                        or type(row.get("start_receipt")) is not dict or type(row.get("lifecycle")) is not dict
                        or row["start_receipt"]["turn_epoch"] != row["lifecycle"]["sequence"]):
                    raise WorkRecordBindingConflict("source or host lifecycle changed")
                facts.append({"binding_id": binding_id, "item_id": row["item_id"],
                              "requirement_id": row["requirement_id"], "worker_id": row["worker_id"],
                              "worker_run_id": row["run_id"], "host_start": row["start_receipt"],
                              "host_lifecycle": row["lifecycle"], "operation_result": "unknown",
                              "verification": "not_verified"})
            preview = {"scope": "authorized_source_session_only", "mapping_claim": "not_asserted", "project_id": project_id,
                       "source_session_id": source.store.session_id, "source_revision": source_payload["revision"],
                       "items": items, "worker_facts": facts}
            preview = json.loads(json.dumps(preview, ensure_ascii=False))
            if len(json.dumps(preview, ensure_ascii=False).encode("utf-8")) > 65536:
                raise WorkRecordBindingConflict("project session copy exceeds confirmation limit")
            # Do not ask for consent or persist an intent that the immutable card
            # ledger cannot represent. Run the same validator under the source lock
            # both before preview and again at commit; this does not write a card.
            from work_card_ledger import WorkCardLedgerCorrupt, _validate_linked_project_session_copy
            candidate = {"kind": "linked_project_session_copy", "session_id": self._conversation_id,
                         "captured_at": datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z"),
                         "copy_id": _digest(preview), "binding_id": binding_ids[0], "source": preview}
            try:
                _validate_linked_project_session_copy(candidate, self._conversation_id)
            except (WorkCardLedgerCorrupt, TypeError, ValueError, UnicodeError) as exc:
                raise WorkRecordBindingConflict("source session snapshot is not representable as a work card") from exc
            if expected is not None and expected != preview:
                raise WorkRecordBindingConflict("project session copy changed after preview")
            # This legacy slot cannot store a second immutable version. Refuse
            # before displaying a confirmation that could never be committed.
            anchor = next(r for b in data["batches"] for r in b["bindings"] if r["binding_id"] == binding_ids[0])
            previous = anchor.get("project_session_copy")
            if previous is not None and previous["snapshot"] != preview:
                raise WorkRecordBindingConflict("immutable project session copy exists; refresh is not available")
            if commit:
                if is_current() is not True or not _is_issued_authorized_project_record_source(source):
                    raise WorkRecordBindingConflict("copy confirmation is no longer current")
                anchor = next(r for b in data["batches"] for r in b["bindings"] if r["binding_id"] == binding_ids[0])
                previous = anchor.get("project_session_copy")
                if previous is not None:
                    if previous["snapshot"] != preview:
                        raise WorkRecordBindingConflict("immutable project session copy exists")
                    return previous
                copy = {"snapshot": preview, "confirmed_at": datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z"),
                        "intent": {"copy_id": _digest(preview), "state": "pending"}}
                anchor["project_session_copy"] = copy
                data["revision"] += 1
                self._validate_payload(data)
                if is_current() is not True or not _is_issued_authorized_project_record_source(source):
                    raise WorkRecordBindingConflict("copy confirmation changed")
                self._publish(data)
                return copy
            return preview

    def prepare_project_session_refresh(self, *, project_id: str, binding_ids: list[str],
                                     source: AuthorizedProjectRecordSource,
                                     expected: dict[str, Any] | None = None,
                                     is_current: Any = None, commit: bool = False,
                                     refresh_id: str, refresh_run_id: str, refresh_turn_epoch: int) -> dict[str, Any]:
        """Build/recheck minimal project records in ONE authorized source Session.

        Preview alone grants no consent. Commit requires a matching confirmed
        preview and a live host predicate; it atomically stores a pending intent.
        Neither mode proves completeness across other Sessions or publishes a card.
        """
        if (type(source) is not AuthorizedProjectRecordSource
                or not _is_issued_authorized_project_record_source(source)
                or source.conversation_id != self._conversation_id
                or source.project_id != project_id or source.store.project_id != project_id
                or source.store.agent_root != self._agent_root):
            raise PermissionError("exact authorized source required")
        try:
            if (type(refresh_id) is not str or str(uuid.UUID(refresh_id)) != refresh_id
                    or type(refresh_run_id) is not str or str(uuid.UUID(refresh_run_id)) != refresh_run_id
                    or type(refresh_turn_epoch) is not int or refresh_turn_epoch < 1):
                raise ValueError
        except (ValueError, TypeError, AttributeError):
            raise ValueError("invalid refresh identity") from None
        if (commit and (expected is None or not callable(is_current))):
            raise ValueError("commit requires confirmation and current turn")
        if (type(binding_ids) is not list or not binding_ids or len(binding_ids) > 20
                or len(set(binding_ids)) != len(binding_ids)
                or any(not _valid_id(value) for value in binding_ids)
                or (expected is not None and type(expected) is not dict)):
            raise ValueError("invalid project copy request")
        locks = {str(p.resolve(strict=False)).casefold(): p for p in
                 (self._dialog_lock_path, source.store.lock_path)}
        with ExitStack() as stack:
            for path in sorted(locks.values(), key=lambda p: str(p).casefold()):
                stack.enter_context(_file_lock(path, self._lock_timeout))
            self._validate_scope()
            if not _is_issued_authorized_project_record_source(source):
                raise PermissionError("source authorization changed")
            data = self._read()
            prior_refreshes = self._refresh_rows_locked(data)
            prior_rows = [r for batch in data["batches"] for r in batch["bindings"] if r["binding_id"] in binding_ids]
            if (any(r["authorization_id"] == source.authorization_id for r in prior_rows)
                    or any(r["authorization_id"] == source.authorization_id and r["refresh_id"] != refresh_id for r in prior_refreshes)):
                raise PermissionError("refresh requires a new source authorization")
            reader = _reader_for(source.store)
            reader._validate_scope()
            source_payload = reader._read()
            source_items = [item for item in source_payload["items"]
                            if item.get("scope") == {"kind": "project", "project_id": project_id}]
            items = [{"item_id": item["item_id"], "title": item["title"], "status": item["status"],
                      "requirements": [{"requirement_id": req["requirement_id"], "text": req["text"],
                                        "status": req["status"]} for req in item["requirements"]]}
                     for item in source_items]
            facts = []
            for binding_id in binding_ids:
                rows = [row for batch in data["batches"] for row in batch["bindings"]
                        if row["binding_id"] == binding_id]
                if len(rows) != 1:
                    raise WorkRecordBindingConflict("binding missing")
                row = rows[0]
                if (row["project_id"] != project_id
                        or row["source_session_id"] != source.store.session_id):
                    raise PermissionError("binding source mismatch")
                matches = [item for item in source_items if item["item_id"] == row["item_id"]]
                if (len(matches) != 1 or type(row.get("source_item_digest")) is not str
                        or type(row.get("start_receipt")) is not dict or type(row.get("lifecycle")) is not dict
                        or row["start_receipt"]["turn_epoch"] != row["lifecycle"]["sequence"]):
                    raise WorkRecordBindingConflict("source or host lifecycle changed")
                facts.append({"binding_id": binding_id, "item_id": row["item_id"],
                              "requirement_id": row["requirement_id"], "worker_id": row["worker_id"],
                              "worker_run_id": row["run_id"], "host_start": row["start_receipt"],
                              "host_lifecycle": row["lifecycle"], "operation_result": "unknown",
                              "verification": "not_verified"})
            preview = {"scope": "authorized_source_session_only", "mapping_claim": "not_asserted", "project_id": project_id,
                       "source_session_id": source.store.session_id, "source_revision": source_payload["revision"],
                       "items": items, "worker_facts": facts}
            preview = json.loads(json.dumps(preview, ensure_ascii=False))
            if len(json.dumps(preview, ensure_ascii=False).encode("utf-8")) > 65536:
                raise WorkRecordBindingConflict("project session copy exceeds confirmation limit")
            # Do not ask for consent or persist an intent that the immutable card
            # ledger cannot represent. Run the same validator under the source lock
            # both before preview and again at commit; this does not write a card.
            from work_card_ledger import WorkCardLedgerCorrupt, _validate_linked_project_session_copy
            candidate = {"kind": "linked_project_session_copy", "session_id": self._conversation_id,
                         "captured_at": datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z"),
                         "copy_id": _digest(preview), "binding_id": binding_ids[0], "source": preview}
            try:
                _validate_linked_project_session_copy(candidate, self._conversation_id)
            except (WorkCardLedgerCorrupt, TypeError, ValueError, UnicodeError) as exc:
                raise WorkRecordBindingConflict("source session snapshot is not representable as a work card") from exc
            if expected is not None and expected != preview:
                raise WorkRecordBindingConflict("project session copy changed after preview")
            if commit:
                if is_current(self._session, uuid.UUID(refresh_run_id).hex, refresh_turn_epoch) is not True or not _is_issued_authorized_project_record_source(source):
                    raise WorkRecordBindingConflict("refresh authorization is no longer current")
                previous = next((r for r in prior_refreshes if r["refresh_id"] == refresh_id), None)
                if previous is not None:
                    if (previous["binding_id"] != binding_ids[0] or previous["authorization_id"] != source.authorization_id
                            or previous["run_id"] != refresh_run_id
                            or previous["turn_epoch"] != refresh_turn_epoch or previous["copy"]["snapshot"] != preview):
                        raise WorkRecordBindingConflict("refresh identity conflict")
                    return previous
                # A previous confirmed intent may have survived a segment-publication
                # interruption. Promote it unchanged before using the single staging
                # slot for this newly confirmed refresh; never discard or re-read it.
                prepared = data.get("refresh_prepared")
                if prepared is not None:
                    segments = data.setdefault("refresh_segments", [])
                    if len(segments) >= _MAX_REFRESH_SEGMENTS:
                        raise WorkRecordWriteError("refresh segment index capacity reached; prepared intent retained")
                    prior_segment = self._write_refresh_segment_locked(prepared)
                    if (is_current(self._session, uuid.UUID(refresh_run_id).hex, refresh_turn_epoch) is not True
                            or not _is_issued_authorized_project_record_source(source)):
                        self._delete_unindexed_refresh_segment_locked(prior_segment, data)
                        raise WorkRecordBindingConflict("new refresh authorization changed during prepared promotion")
                    data.pop("refresh_prepared", None)
                    data["schema_version"] = 2
                    segments.append(prior_segment)
                    data["revision"] += 1
                    self._validate_payload(data)
                    if (is_current(self._session, uuid.UUID(refresh_run_id).hex, refresh_turn_epoch) is not True
                            or not _is_issued_authorized_project_record_source(source)):
                        self._delete_unindexed_refresh_segment_locked(prior_segment, data)
                        raise WorkRecordBindingConflict("new refresh authorization changed before prepared promotion commit")
                    self._publish(data)
                refreshes = data.setdefault("project_refreshes", [])
                copy = {"snapshot": preview, "confirmed_at": datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z"),
                        "intent": {"copy_id": _digest(preview), "state": "pending"}}
                refresh = {"refresh_id": refresh_id, "run_id": refresh_run_id, "turn_epoch": refresh_turn_epoch,
                           "binding_id": binding_ids[0], "authorization_id": source.authorization_id, "copy": copy}
                segmented = not (len(refreshes) < _MAX_INLINE_REFRESHES and data.get("schema_version") == 1)
                if not segmented:
                    refreshes.append(refresh)
                else:
                    segments = data.setdefault("refresh_segments", [])
                    if len(segments) >= _MAX_REFRESH_SEGMENTS:
                        raise WorkRecordWriteError("refresh segment index capacity reached; no refresh was committed")
                    if data.get("refresh_prepared") is not None:
                        raise WorkRecordWriteError("a confirmed refresh awaits safe segment publication")
                    # Persist the confirmed intent first. A crash during segment
                    # publication leaves this exact pending row recoverable.
                    data["schema_version"] = 3
                    data["refresh_prepared"] = refresh
                data["revision"] += 1
                self._validate_payload(data)
                if is_current(self._session, uuid.UUID(refresh_run_id).hex, refresh_turn_epoch) is not True or not _is_issued_authorized_project_record_source(source):
                    raise WorkRecordBindingConflict("refresh authorization changed")
                self._publish(data)
                if segmented:
                    segment = self._write_refresh_segment_locked(refresh)
                    if (is_current(self._session, uuid.UUID(refresh_run_id).hex, refresh_turn_epoch) is not True
                            or not _is_issued_authorized_project_record_source(source)):
                        self._delete_unindexed_refresh_segment_locked(segment, data)
                        raise WorkRecordBindingConflict("refresh authorization changed during segment publication")
                    data.pop("refresh_prepared", None)
                    data["schema_version"] = 2
                    data["refresh_segments"].append(segment)
                    data["revision"] += 1
                    self._validate_payload(data)
                    if (is_current(self._session, uuid.UUID(refresh_run_id).hex, refresh_turn_epoch) is not True
                            or not _is_issued_authorized_project_record_source(source)):
                        self._delete_unindexed_refresh_segment_locked(segment, data)
                        raise WorkRecordBindingConflict("refresh authorization changed before segment index commit")
                    self._publish(data)
                return refresh
            return preview

    def prepare_item_copies(self, *, binding_ids: list[str], authorized_sources: Mapping[str, AuthorizedProjectRecordSource]) -> list[dict[str, Any]]:
        return self._item_copy_transaction(binding_ids, authorized_sources, proposed=None, is_current=lambda: True)

    def commit_confirmed_item_copies(self, *, proposed: list[dict[str, Any]],
                                     authorized_sources: Mapping[str, AuthorizedProjectRecordSource],
                                     is_current: Any) -> list[dict[str, Any]]:
        if type(proposed) is not list or not proposed or any(type(row) is not dict for row in proposed) or not callable(is_current):
            raise ValueError("invalid confirmed copy request")
        return self._item_copy_transaction([row.get("binding_id") for row in proposed], authorized_sources,
                                           proposed=proposed, is_current=is_current)

    def _item_copy_transaction(self, binding_ids, authorized_sources, *, proposed, is_current):
        if (type(binding_ids) is not list or not binding_ids or len(binding_ids) > 20
                or any(not _valid_id(value) for value in binding_ids) or len(set(binding_ids)) != len(binding_ids)
                or type(authorized_sources) is not dict):
            raise ValueError("invalid copy identities")
        for key, source in authorized_sources.items():
            if (type(source) is not AuthorizedProjectRecordSource or not _is_issued_authorized_project_record_source(source)
                    or source.conversation_id != self._conversation_id or source.project_id != key
                    or source.store.agent_root != self._agent_root or source.store.project_id != key):
                raise PermissionError("invalid source authorization")
        locks = {str(p.resolve(strict=False)).casefold(): p for p in
                 [self._dialog_lock_path, *(s.store.lock_path for s in authorized_sources.values())]}
        with ExitStack() as stack:
            for path in sorted(locks.values(), key=lambda p: str(p).casefold()):
                stack.enter_context(_file_lock(path, self._lock_timeout))
            self._validate_scope()
            if is_current() is not True:
                raise WorkRecordBindingConflict("parent execution binding changed")
            if any(not _is_issued_authorized_project_record_source(source) for source in authorized_sources.values()):
                raise PermissionError("source authorization changed")
            data = self._read()
            snapshots = []
            chosen = []
            for binding_id in binding_ids:
                matches = [row for batch in data["batches"] for row in batch["bindings"] if row["binding_id"] == binding_id]
                if len(matches) != 1:
                    raise WorkRecordBindingConflict("copy binding is missing")
                row = matches[0]
                source = authorized_sources.get(row["project_id"])
                if source is None or source.authorization_id != row["authorization_id"] or source.store.session_id != row["source_session_id"]:
                    raise PermissionError("exact source authorization required")
                if (type(row.get("start_receipt")) is not dict or type(row.get("lifecycle")) is not dict
                        or row["start_receipt"]["turn_epoch"] != row["lifecycle"]["sequence"]
                        or type(row.get("source_item_digest")) is not str):
                    raise WorkRecordBindingConflict("copy lacks started lifecycle or confirmed source snapshot")
                items = [item for item in source.store._read_items_locked() if item.get("item_id") == row["item_id"]]
                if (len(items) != 1 or _digest(items[0]) != row["source_item_digest"]
                        or items[0].get("scope") != {"kind": "project", "project_id": row["project_id"]}):
                    raise WorkRecordBindingConflict("source item snapshot changed")
                reqs = [req for req in items[0]["requirements"] if req.get("requirement_id") == row["requirement_id"]]
                if len(reqs) != 1 or reqs[0].get("text") != row["requirement_text"]:
                    raise WorkRecordBindingConflict("source requirement changed")
                snap = {key: row[key] for key in ("binding_id", "project_id", "source_session_id", "item_id", "requirement_id", "requirement_text", "source_item_digest", "worker_id")}
                snap.update({"item_title": items[0]["title"], "item_status": items[0]["status"], "requirement_status": reqs[0]["status"],
                             "worker_run_id": row["run_id"], "host_start": row["start_receipt"],
                             "host_lifecycle": row["lifecycle"], "operation_result": "unknown", "verification": "not_verified"})
                if (type(snap["item_title"]) is not str or not snap["item_title"].strip()
                        or len(snap["item_title"].encode("utf-8")) > 4096
                        or len(snap["requirement_text"].encode("utf-8")) > 4096):
                    raise WorkRecordBindingConflict("copy field exceeds safe bounds")
                snapshots.append(json.loads(json.dumps(snap, ensure_ascii=False)))
                chosen.append(row)
            if set(authorized_sources) != {row["project_id"] for row in chosen}:
                raise PermissionError("unnecessary source authorization")
            if proposed is None:
                return snapshots
            if snapshots != proposed or is_current() is not True:
                raise WorkRecordBindingConflict("confirmed copy snapshot or parent binding changed")
            saved = []
            changed = False
            for row, snap in zip(chosen, snapshots):
                copy = {"snapshot": snap, "intent": {"copy_id": _digest(snap), "state": "pending"}}
                if "card_copy" in row:
                    previous = row["card_copy"]
                    if previous.get("snapshot") != snap or previous.get("intent") != copy["intent"]:
                        raise WorkRecordBindingConflict("immutable linked copy already exists")
                    copy = previous
                else:
                    copy["confirmed_at"] = datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
                    row["card_copy"] = copy
                    changed = True
                saved.append(copy)
            if changed:
                data["revision"] += 1
                self._validate_payload(data)
                if is_current() is not True:
                    raise WorkRecordBindingConflict("parent execution binding changed")
                self._publish(data)
            return saved

    def project_authorized_worker_status(
        self, *, binding_id: str, source: AuthorizedProjectRecordSource,
    ) -> dict[str, Any] | None:
        """Read a conservative host lifecycle fact, not a card or source contents.

        The capability must still be live. Lock the source and dialogue in the
        same sorted order as binding creation; no source mutation or execution.
        A snapshot-bearing binding verifies the complete bound item only; legacy
        bindings have no such evidence. Neither authorizes publication or asserts
        operation completion.
        """
        if not _valid_id(binding_id):
            raise ValueError("invalid binding identity")
        if (type(source) is not AuthorizedProjectRecordSource
                or not _is_issued_authorized_project_record_source(source)
                or source.conversation_id != self._conversation_id):
            raise PermissionError("a live host-authorized source is required")
        reader = source.store
        reader.validate_scope()
        if reader.agent_root != self._agent_root or reader.project_id != source.project_id:
            raise PermissionError("source scope mismatch")
        locks = {str(p.resolve(strict=False)).casefold(): p for p in (self._dialog_lock_path, reader.lock_path)}
        with ExitStack() as stack:
            for path in sorted(locks.values(), key=lambda p: str(p).casefold()):
                stack.enter_context(_file_lock(path, self._lock_timeout))
            self._validate_scope()
            if not _is_issued_authorized_project_record_source(source):
                raise PermissionError("source authorization changed")
            reader.validate_scope()
            data = self._read()
            rows = [row for batch in data["batches"] for row in batch["bindings"]
                    if row["binding_id"] == binding_id]
            if len(rows) != 1:
                return None
            row = rows[0]
            if (row["authorization_id"] != source.authorization_id
                    or row["project_id"] != source.project_id
                    or row["source_session_id"] != reader.session_id):
                raise PermissionError("source does not match binding")
            start, lifecycle = row.get("start_receipt"), row.get("lifecycle")
            if (type(start) is not dict or type(lifecycle) is not dict
                    or start["turn_epoch"] != lifecycle["sequence"]):
                return None
            items = [item for item in reader._read_items_locked() if item.get("item_id") == row["item_id"]]
            if len(items) != 1 or items[0].get("scope") != {"kind": "project", "project_id": row["project_id"]}:
                return None
            requirements = [req for req in items[0]["requirements"] if req.get("requirement_id") == row["requirement_id"]]
            if len(requirements) != 1 or requirements[0].get("text") != row["requirement_text"]:
                return None
            snapshot_digest = row.get("source_item_digest")
            if snapshot_digest is not None and _digest(items[0]) != snapshot_digest:
                return None
            return {"project_id": row["project_id"], "binding_id": row["binding_id"],
                    "host_status": lifecycle["status"], "finish_reason": lifecycle["finish_reason"],
                    "operation_result": "unknown", "verification": "not_verified",
                    "source_snapshot_verified": snapshot_digest is not None}

    def record_worker_start(
        self, *, binding_id: str, worker_id: str, run_id: str,
        reservation_id: str, parent_run_id: str, turn_epoch: int,
        project_id: str, task_id: str,
    ) -> dict[str, Any]:
        """Atomically record a slot-authorized host start against its exact binding."""
        if (not all(_valid_id(value) for value in
                    (binding_id, worker_id, run_id, reservation_id, parent_run_id, project_id, task_id))
                or type(turn_epoch) is not int or turn_epoch < 1):
            raise ValueError("invalid worker start receipt")
        receipt = {"reservation_id": reservation_id, "parent_run_id": parent_run_id, "turn_epoch": turn_epoch}
        self._validate_scope()
        with _file_lock(self._dialog_lock_path, self._lock_timeout):
            data = self._read()
            rows = [row for batch in data["batches"] for row in batch["bindings"]
                    if row["binding_id"] == binding_id]
            if len(rows) != 1:
                raise KeyError("worker binding is missing or ambiguous")
            row = rows[0]
            if (row["worker_id"], row["run_id"], row["project_id"], row["task_id"]) != (worker_id, run_id, project_id, task_id):
                raise WorkRecordBindingConflict("worker start identity does not match binding")
            prior = row.get("start_receipt")
            if prior is not None:
                if prior != receipt:
                    raise WorkRecordBindingConflict("worker start conflicts with prior receipt")
                return json.loads(json.dumps(row))
            if row["lifecycle"] is not None:
                raise WorkRecordBindingConflict("cannot add start to an existing terminal lifecycle")
            if any(
                other["binding_id"] != binding_id
                and (other["project_id"], other["task_id"]) == (project_id, task_id)
                and _has_unresolved_started_binding(other)
                for batch in data["batches"] for other in batch["bindings"]
            ):
                raise WorkRecordBindingConflict(
                    "prior linked worker start is unresolved; automatic recovery and re-execution are blocked; "
                    "inspect the project and settle this binding explicitly"
                )
            row["start_receipt"] = receipt
            data["revision"] += 1
            self._publish(data)
            return json.loads(json.dumps(row))

    def record_worker_lifecycle(
        self,
        *,
        binding_id: str,
        worker_id: str,
        run_id: str,
        sequence: int,
        status: str,
        finish_reason: str,
        read_attempts_available: bool | None = None,
        read_attempts: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Durably record a bound lifecycle and optional separate read-attempt evidence."""
        if (not _valid_id(binding_id) or not _valid_id(worker_id) or not _valid_id(run_id)
                or type(sequence) is not int or sequence < 1
                or type(status) is not str or status not in _LIFECYCLE_STATUSES
                or type(finish_reason) is not str or finish_reason not in _FINISH_REASONS):
            raise ValueError("invalid host lifecycle receipt")
        if (read_attempts_available is None) != (read_attempts is None):
            raise ValueError("read-attempt availability and rows must be supplied together")
        if read_attempts_available is not None:
            from project_worker_read_evidence import validate_project_worker_read_attempts
            if not validate_project_worker_read_attempts(read_attempts_available, read_attempts):
                raise ValueError("invalid content-free project worker read-attempt evidence")
        self._validate_scope()
        with _file_lock(self._dialog_lock_path, self._lock_timeout):
            data = self._read()
            matches = [(batch, row) for batch in data["batches"] for row in batch["bindings"]
                       if row["binding_id"] == binding_id]
            if len(matches) != 1:
                raise KeyError("worker binding is missing or ambiguous")
            _batch, row = matches[0]
            if (row["worker_id"], row["run_id"]) != (worker_id, run_id):
                raise WorkRecordBindingConflict("worker lifecycle identity does not match binding")
            start = row.get("start_receipt")
            if status == "unknown" and (start is None or finish_reason != "unknown"
                                        or start["turn_epoch"] != sequence):
                raise WorkRecordBindingConflict("unknown lifecycle requires matching start receipt")
            if start is not None and start["turn_epoch"] != sequence:
                raise WorkRecordBindingConflict("lifecycle sequence does not match worker start")
            receipt = {"sequence": sequence, "status": status, "finish_reason": finish_reason}
            incoming_evidence = None if read_attempts_available is None else {
                "read_attempts_available": read_attempts_available,
                "read_attempts": read_attempts,
            }
            if row["lifecycle"] is not None:
                if row["lifecycle"] != receipt:
                    raise WorkRecordBindingConflict("worker lifecycle receipt conflicts with prior settlement")
                has_prior_evidence = "read_attempts_available" in row or "read_attempts" in row
                if has_prior_evidence:
                    if ("read_attempts_available" not in row or "read_attempts" not in row):
                        raise WorkRecordBindingConflict("worker read-attempt receipt is incomplete")
                    if incoming_evidence is None or incoming_evidence["read_attempts_available"] is False:
                        return json.loads(json.dumps(row))
                    if any(row[key] != incoming_evidence[key] for key in incoming_evidence):
                        raise WorkRecordBindingConflict("worker read-attempt receipt conflicts with prior evidence")
                    return json.loads(json.dumps(row))
                if incoming_evidence is None:
                    return json.loads(json.dumps(row))
                row.update(incoming_evidence)
                data["revision"] += 1
                self._publish(data)
                return json.loads(json.dumps(row))
            row["lifecycle"] = receipt
            if incoming_evidence is not None:
                row.update(incoming_evidence)
            data["revision"] += 1
            self._publish(data)
            return json.loads(json.dumps(row))


