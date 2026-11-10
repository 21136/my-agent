"""Versioned current-session work records and append-only item lifecycle controls."""
from __future__ import annotations

import contextlib
import datetime as _datetime
import hashlib
import json
import math
import os
import re
import stat
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Iterator

from session import Session
from file_guard import _prune_backups
from guard_snapshots import STAMP, safe_container

_FILENAME = "work-records.v1.json"
_SCHEMA_VERSION = 1
_WORK_RECORD_PROCESS_INSTANCE_ID = str(uuid.uuid4())
_MAX_BYTES = 4 * 1024 * 1024
_MAX_EVENTS = 10_000
MAX_WORK_RECORD_RESULT_EXCERPT_BYTES = 4096
MAX_WORK_RECORD_RESULT_SOURCES = 16
MAX_ASSISTANT_REPLY_EXCERPT_BYTES = 4096
MAX_ASSISTANT_REPLY_EXCERPT_SOURCES = 16
_RESULT_SOURCE_AVAILABILITY = {"available", "unavailable", "not_applicable"}
_SOURCE_KINDS = {"project", "work_record", "requirement", "result"}
_SOURCE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_PUBLICATION_KEY = "publication_intents"
_INDEPENDENT_PUBLICATION_KEY = "independent_publication_intents"
LEGACY_OUTBOX_DIGEST_KEY = "legacy_outbox_v1_sha256"
LEGACY_OUTBOX_MIGRATIONS_KEY = "legacy_outbox_v1_migrations"
_PUBLICATION_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_PUBLICATION_INTENT_KEYS = frozenset({
    "intent_id", "session_id", "project_id", "first_run_id", "latest_item_id",
    "latest_requirement_id", "latest_run_id", "result_id", "generation",
    "request_id", "card_id", "state", "window_owner_id", "legacy_source_digest",
    "window_started_monotonic", "deadline_monotonic", "window_seconds",
})
_PUBLICATION_CARD_ID = re.compile(r"wc-[0-9a-f]{64}\Z")
_INDEPENDENT_PUBLICATION_INTENT_KEYS = frozenset({
    "intent_id", "session_id", "first_run_id", "latest_item_id", "latest_requirement_id",
    "latest_run_id", "result_id", "generation", "request_id", "card_id", "state",
    "window_owner_id", "window_started_monotonic", "deadline_monotonic", "window_seconds",
})
_PROJECT_SCOPED_PUBLICATION_KEY = "project_scoped_card_intents"
_PROJECT_SCOPED_RESULT_ID = re.compile(r"worker-result-[0-9a-f]{64}\Z")
_PROJECT_SCOPED_CARD_FOCUS_KEYS = frozenset({
    "latest_item_id", "latest_requirement_id", "latest_run_id", "latest_result_id",
})
_PROJECT_SCOPED_CARD_INTENT_KEYS = frozenset({
    "session_id", "project_id", "request_id", "generation", "state", "card_id",
    "window_seconds", "window_started_monotonic", "deadline_monotonic", "boot_marker",
    "source_result_ids",
})
_PROJECT_SCOPED_CARD_INTENT_KEYS_WITH_FOCUS = _PROJECT_SCOPED_CARD_INTENT_KEYS | _PROJECT_SCOPED_CARD_FOCUS_KEYS
_PROJECT_SCOPED_CARD_INTENT_LEGACY_KEYS = _PROJECT_SCOPED_CARD_INTENT_KEYS - {"source_result_ids"}
_PROJECT_SCOPED_CARD_INTENT_LEGACY_KEYS_WITH_FOCUS = _PROJECT_SCOPED_CARD_INTENT_LEGACY_KEYS | _PROJECT_SCOPED_CARD_FOCUS_KEYS
_PROJECT_SCOPED_CARD_ID = re.compile(r"wc-[0-9a-f]{64}\Z")
_PUBLICATION_WINDOW_SECONDS = 30.0
_PUBLICATION_PROCESS_INSTANCE_ID = str(uuid.uuid4())


def _monotonic() -> float:
    return time.monotonic()


def _publication_uuid_hex(value: Any) -> str | None:
    if type(value) is not str:
        return None
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError):
        return None
    return parsed.hex if parsed.hex == value.replace("-", "").lower() else None


def publication_card_request_id(session_id: str, project_id: str, intent_id: str, generation: int) -> str:
    return str(uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"my-agent-work-card-publication:{session_id}:{project_id}:{intent_id}:{generation}",
    ))


def independent_publication_card_request_id(session_id: str, item_id: str, intent_id: str, generation: int) -> str:
    return str(uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"my-agent-independent-work-card-publication:{session_id}:{item_id}:{intent_id}:{generation}",
    ))


def _server_settlement_result_id(session_id: str, item_id: str, requirement_id: str, run_id: str) -> str:
    raw = "\0".join((session_id, item_id, requirement_id, run_id))
    return "settlement-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()

def _valid_server_publication_result(
    session_id: str, item_id: str, requirement_id: str, run: dict[str, Any],
    project_id: str | None = None,
) -> bool:
    if type(run) is not dict or type(run.get("run_id")) is not str or not run["run_id"]:
        return False
    if any(type(value) is not str or not value for value in (session_id, item_id, requirement_id)):
        return False
    verification = run.get("verification")
    host = verification.get("host_execution") if type(verification) is dict else None
    if (type(host) is not dict or type(host.get("sequence")) is not int
            or isinstance(host.get("sequence"), bool) or host["sequence"] < 1):
        return False
    if run.get("status") == "unknown":
        if run.get("result_id") == _server_settlement_result_id(
                session_id, item_id, requirement_id, run["run_id"]):
            return True
        if _publication_uuid_hex(run["run_id"]) is None:
            return False
        report = verification.get("project_test") if type(verification) is dict else None
        expected_host_keys = {
            "sequence", "session_id", "project_id", "item_id", "requirement_id",
            "parent_run_id", "worker_run_id", "worker_id", "task_id", "reservation_id",
        }
        reservation_id = host.get("reservation_id")
        reservation_parts = reservation_id.split(":", 1) if type(reservation_id) is str else []
        reservation_id_valid = (
            len(reservation_parts) == 2
            and _publication_uuid_hex(reservation_parts[0]) is not None
            and re.fullmatch(r"[1-9][0-9]*", reservation_parts[1]) is not None
        )
        if (verification.get("status") != "not_verified"
                or verification.get("evidence_refs") != []
                or type(report) is not dict
                or set(report) != {"status", "source", "pytest_target", "exit_code"}
                or report.get("status") != "passed"
                or report.get("source") != "user_authorized_project_self_report"
                or type(report.get("pytest_target")) is not str
                or re.fullmatch(r"[A-Za-z0-9_./-]+\.py::test_[A-Za-z0-9_]+", report["pytest_target"]) is None
                or any(part in ("", ".", "..") for part in report["pytest_target"].split("::")[0].split("/"))
                or type(report.get("exit_code")) is not int or report["exit_code"] != 0
                or set(host) != expected_host_keys
                or host.get("session_id") != session_id
                or host.get("project_id") != project_id
                or host.get("item_id") != item_id
                or host.get("requirement_id") != requirement_id
                or host.get("parent_run_id") != run.get("run_id")
                or _publication_uuid_hex(host.get("worker_run_id")) is None
                or not reservation_id_valid
                or _publication_uuid_hex(host.get("worker_id")) is None
                or type(host.get("task_id")) is not str or not host["task_id"]
                or len(host["task_id"]) > 8000 or any(ord(char) < 32 for char in host["task_id"])):
            return False
        raw = "\0".join((session_id, item_id, requirement_id, run.get("run_id"), report["pytest_target"]))
        expected_result_id = "worker-self-report-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()
        return run.get("result_id") == expected_result_id
    if run.get("status") == "blocked":
        if verification.get("status") != "not_verified" or host.get("host_status") != "blocked":
            return False
        raw = "\0".join((session_id, item_id, requirement_id, run["run_id"], "blocked", str(host["sequence"])))
        return run.get("result_id") == "worker-blocked-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()
    if run.get("status") != "completed" or verification.get("status") != "passed":
        return False
    from project_worker_work_record_settlement import _VERIFIER_ID_RE, _EVIDENCE_REF_RE
    verifier, refs = verification.get("host_verifier"), verification.get("evidence_refs")
    if (type(verifier) is not str or _VERIFIER_ID_RE.fullmatch(verifier) is None
            or type(refs) is not list or not 1 <= len(refs) <= 16
            or any(type(ref) is not str or _EVIDENCE_REF_RE.fullmatch(ref) is None
                   or ".." in ref.split("/") or ref.startswith("/") for ref in refs)
            or len(set(refs)) != len(refs)):
        return False
    raw = "\0".join((session_id, item_id, requirement_id, run["run_id"], verifier, *refs))
    if run.get("result_id") != "worker-verified-" + hashlib.sha256(raw.encode("utf-8")).hexdigest():
        return False
    return True


_thread_locks: dict[str, threading.RLock] = {}
_thread_locks_guard = threading.Lock()


class WorkRecordError(RuntimeError):
    """Base error for work-record operations."""


class WorkRecordScopeError(WorkRecordError):
    """The supplied session is not a canonical session under its own AgentPaths."""


class WorkRecordBindingChanged(WorkRecordScopeError):
    """The retained Session was rebound after store authorization."""


class WorkRecordCorrupt(WorkRecordError):
    """The sidecar is corrupt, mismatched, or uses an unsupported schema."""


class WorkRecordConflict(WorkRecordError):
    """An idempotency key was reused with different content or target."""


class WorkRecordStaleRun(WorkRecordError):
    """A result does not belong to the requirement's durably current run."""


class WorkRecordIdempotencyRequired(WorkRecordError):
    """A write requires a stable caller-supplied request key; random fallback is forbidden."""


class WorkRecordLockUnavailable(WorkRecordError):
    """A verifiable cross-process lock could not be obtained."""


class WorkRecordWriteError(WorkRecordError):
    """The new snapshot was not published; prior snapshot remains authoritative."""


def _file_identity(info: os.stat_result) -> tuple[int, int]:
    return info.st_dev, info.st_ino


def _require_private_regular_file(info: os.stat_result, *, error: type[WorkRecordError], label: str) -> None:
    if (not stat.S_ISREG(info.st_mode)
            or getattr(info, "st_file_attributes", 0) & 0x400
            or getattr(info, "st_nlink", 1) != 1):
        raise error(f"{label} is not a private regular file (link, reparse point, or hardlink alias)")


def _canonical_session(session: Session) -> tuple[Path, Path, str]:
    if not isinstance(session, Session):
        raise TypeError("WorkRecordStore requires the current Session object")
    conversation_id = session.conversation_id
    if not isinstance(conversation_id, str) or not conversation_id or any(c in conversation_id for c in "\\/:"):
        raise WorkRecordScopeError("invalid conversation identity")
    expected = session.paths.data / "sessions" / conversation_id
    supplied = Path(session.session_dir)
    # No authenticated intermediate trust root is available. Do not trust a
    # process-selected TEMP path: check to the filesystem anchor and reject
    # even legitimate redirected ancestors until a safe root is established.
    candidates = (session.paths.agent_root, session.paths.data,
                  session.paths.data / "sessions", expected, supplied)
    seen: set[Path] = set()
    for candidate in candidates:
        absolute = Path(os.path.abspath(candidate))
        anchor = Path(absolute.anchor)
        for component in (absolute, *absolute.parents):
            if component not in seen:
                try:
                    info = component.lstat()
                except OSError as exc:
                    raise WorkRecordScopeError("session storage path cannot be checked") from exc
                if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                    raise WorkRecordScopeError("session storage path contains a link or reparse point")
                seen.add(component)
            if component == anchor:
                break
        else:
            raise WorkRecordScopeError("session storage path has no trusted anchor")
    try:
        if supplied.resolve(strict=True) != expected.resolve(strict=True) or not supplied.is_dir():
            raise WorkRecordScopeError("session directory does not match current Session identity")
        root = session.paths.agent_root.resolve(strict=True)
    except OSError as exc:
        raise WorkRecordScopeError("session storage root is unavailable") from exc
    if not expected.resolve(strict=False).is_relative_to(root):
        raise WorkRecordScopeError("session directory escapes its agent root")
    return expected, root, conversation_id


@contextlib.contextmanager
def _file_lock(path: Path, timeout: float) -> Iterator[None]:
    """OS-backed byte-range lock; fail closed on unsupported or timed-out locks."""
    key = str(path.resolve(strict=False)).casefold()
    with _thread_locks_guard:
        local_lock = _thread_locks.setdefault(key, threading.RLock())
    if not local_lock.acquire(timeout=max(0.0, timeout)):
        raise WorkRecordLockUnavailable("work-record write lock is busy")
    handle = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            before = path.lstat()
        except FileNotFoundError:
            before = None
        if before is not None:
            _require_private_regular_file(before, error=WorkRecordLockUnavailable, label="lock path")
        handle = path.open("a+b")
        opened = os.fstat(handle.fileno())
        _require_private_regular_file(opened, error=WorkRecordLockUnavailable, label="opened lock")
        try:
            current = path.lstat()
        except OSError as exc:
            raise WorkRecordLockUnavailable("lock path changed while opening") from exc
        if _file_identity(opened) != _file_identity(current) or (before is not None and _file_identity(before) != _file_identity(opened)):
            raise WorkRecordLockUnavailable("lock path changed while opening")
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                elif os.name == "posix":
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                else:
                    raise WorkRecordLockUnavailable("OS locking is unsupported on this platform")
                break
            except WorkRecordLockUnavailable:
                raise
            except (OSError, BlockingIOError) as exc:
                if time.monotonic() >= deadline:
                    raise WorkRecordLockUnavailable("work-record write lock is busy or unavailable") from exc
                time.sleep(0.025)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        if handle is not None:
            handle.close()
        local_lock.release()


class WorkRecordStore:
    """Store/query records scoped to exactly one already-authorized Session.

    No API accepts a filesystem path or conversation id as a lookup target.
    This module is intentionally not connected to the agent loop.
    """

    def __init__(self, session: Session, *, lock_timeout: float = 1.0) -> None:
        self._session = session
        self._session_dir, self._agent_root, self._conversation_id = _canonical_session(session)
        self._bound_project_id = getattr(session.meta, "project_id", "")
        if lock_timeout < 0:
            raise ValueError("lock_timeout must be nonnegative")
        self._lock_timeout = lock_timeout
        self._path = self._session_dir / _FILENAME
        self._lock_path = self._session_dir / ".work-records.lock"

    def _validate_scope(self) -> None:
        session_dir, agent_root, conversation_id = _canonical_session(self._session)
        if (session_dir, agent_root, conversation_id) != (self._session_dir, self._agent_root, self._conversation_id):
            raise WorkRecordScopeError("current Session binding changed")
        if getattr(self._session.meta, "project_id", "") != self._bound_project_id:
            raise WorkRecordBindingChanged("current Session project binding changed")

    def _read(self) -> dict[str, Any]:
        self._validate_scope()
        if not self._path.exists() and not self._path.is_symlink():
            return {"schema_version": _SCHEMA_VERSION, "conversation_id": self._conversation_id, "revision": 0, "items": []}
        try:
            before = self._path.lstat()
            _require_private_regular_file(before, error=WorkRecordCorrupt, label="sidecar")
            if before.st_size > _MAX_BYTES:
                raise WorkRecordCorrupt("sidecar exceeds size limit")
            flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(self._path, flags)
            try:
                opened = os.fstat(fd)
                _require_private_regular_file(opened, error=WorkRecordCorrupt, label="opened sidecar")
                current = self._path.lstat()
                if _file_identity(before) != _file_identity(opened) or _file_identity(opened) != _file_identity(current):
                    raise WorkRecordCorrupt("sidecar path changed while opening")
                if opened.st_size > _MAX_BYTES:
                    raise WorkRecordCorrupt("sidecar exceeds size limit")
                with os.fdopen(fd, "r", encoding="utf-8") as stream:
                    fd = -1
                    data = json.load(stream)
            finally:
                if fd >= 0:
                    os.close(fd)
        except WorkRecordCorrupt:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise WorkRecordCorrupt("sidecar cannot be read; refusing overwrite") from exc
        self._validate_payload(data)
        return data

    @staticmethod
    def _validate_source_ref(source: Any, *, corrupt: bool = False) -> None:
        error = WorkRecordCorrupt if corrupt else ValueError
        if (not isinstance(source, dict) or set(source) != {"kind", "id"}
                or not isinstance(source.get("kind"), str) or source["kind"] not in _SOURCE_KINDS
                or not isinstance(source.get("id"), str) or not _SOURCE_ID.fullmatch(source["id"])):
            raise error("source reference must contain only a supported kind and identifier")

    @classmethod
    def _validate_source_refs(cls, sources: Any, *, corrupt: bool = False) -> None:
        error = WorkRecordCorrupt if corrupt else ValueError
        if not isinstance(sources, list):
            raise error("source references must be a list")
        for source in sources:
            cls._validate_source_ref(source, corrupt=corrupt)

    @classmethod
    def _validate_assistant_reply_excerpt(cls, value: Any, *, corrupt: bool = False) -> None:
        error = WorkRecordCorrupt if corrupt else ValueError
        if (type(value) is not dict
                or set(value) != {"excerpt_id", "text", "source_availability", "sources"}
                or type(value.get("excerpt_id")) is not str
                or type(value.get("text")) is not str or not value["text"].strip()
                or type(value.get("source_availability")) is not str
                or value["source_availability"] not in {"available", "unavailable"}
                or type(value.get("sources")) is not list
                or len(value["sources"]) > MAX_ASSISTANT_REPLY_EXCERPT_SOURCES):
            raise error("assistant reply excerpt fields are invalid")
        try:
            if str(uuid.UUID(value["excerpt_id"])) != value["excerpt_id"]:
                raise ValueError("excerpt id is not canonical")
            if len(value["text"].encode("utf-8")) > MAX_ASSISTANT_REPLY_EXCERPT_BYTES:
                raise ValueError("assistant reply excerpt exceeds the size limit")
        except (ValueError, UnicodeError) as exc:
            raise error("assistant reply excerpt identity or size is invalid") from exc
        cls._validate_source_refs(value["sources"], corrupt=corrupt)
        if ((value["source_availability"] == "available" and not value["sources"])
                or (value["source_availability"] == "unavailable" and value["sources"])):
            raise error("assistant reply source availability does not match its references")

    def _validate_payload(self, data: Any) -> None:
        """Validate the complete nested snapshot before exposing or mutating it."""
        if (not isinstance(data, dict) or data.get("schema_version") != _SCHEMA_VERSION
                or data.get("conversation_id") != self._conversation_id
                or type(data.get("revision")) is not int or data["revision"] < 0
                or not isinstance(data.get("items"), list)):
            raise WorkRecordCorrupt("sidecar schema or conversation identity is invalid")
        item_statuses = {"active", "queued", "waiting_confirmation", "completed", "in_progress", "unknown", "blocked", "stopped"}
        requirement_statuses = {"pending", "queued", "waiting_confirmation", "completed", "in_progress", "unknown", "blocked", "stopped"}
        result_statuses = {"completed", "blocked", "stopped", "unknown", "in_progress"}
        item_ids: set[str] = set()
        for item in data["items"]:
            if not isinstance(item, dict):
                raise WorkRecordCorrupt("sidecar item is not an object")
            if (not isinstance(item.get("item_id"), str) or not item["item_id"]
                    or not isinstance(item.get("title"), str)
                    or not isinstance(item.get("status"), str) or item["status"] not in item_statuses
                    or not isinstance(item.get("requirements"), list)
                    or not isinstance(item.get("runs"), list)
                    or not isinstance(item.get("history"), list)):
                raise WorkRecordCorrupt("sidecar item fields are invalid")
            if item["item_id"] in item_ids:
                raise WorkRecordCorrupt("sidecar has duplicate item identity")
            item_ids.add(item["item_id"])
            if "source_refs" in item:
                self._validate_source_refs(item["source_refs"], corrupt=True)
            scope = item.get("scope")
            if not isinstance(scope, dict):
                raise WorkRecordCorrupt("sidecar item scope is invalid")
            if not isinstance(scope.get("kind"), str):
                raise WorkRecordCorrupt("sidecar item scope is invalid")
            if scope.get("kind") == "project":
                valid_scope = isinstance(scope.get("project_id"), str) and bool(scope["project_id"])
                if valid_scope and scope["project_id"] != self._bound_project_id:
                    raise WorkRecordScopeError("sidecar contains a project record outside the current session binding")
            else:
                valid_scope = scope.get("kind") == "independent" and scope.get("object_ref") is None
            if not valid_scope:
                raise WorkRecordCorrupt("sidecar item scope is invalid")
            requirement_ids: set[str] = set()
            for req in item["requirements"]:
                if (not isinstance(req, dict) or not isinstance(req.get("requirement_id"), str)
                        or not req["requirement_id"] or not isinstance(req.get("text"), str)
                        or not isinstance(req.get("status"), str) or req["status"] not in requirement_statuses
                        ):
                    raise WorkRecordCorrupt("sidecar requirement is invalid")
                if req["requirement_id"] in requirement_ids:
                    raise WorkRecordCorrupt("sidecar has duplicate requirement identity")
                requirement_ids.add(req["requirement_id"])
                if "current_run_id" in req and (
                        not isinstance(req["current_run_id"], str) or not req["current_run_id"]):
                    raise WorkRecordCorrupt("sidecar requirement current run identity is invalid")
                if "execution_owner_id" in req and (
                        type(req["execution_owner_id"]) is not str or not req["execution_owner_id"]):
                    raise WorkRecordCorrupt("sidecar requirement execution owner identity is invalid")
                if "source_ref" in req:
                    self._validate_source_ref(req["source_ref"], corrupt=True)
                if "schedule_claim" in req:
                    claim = req["schedule_claim"]
                    claim_fields = {"source_session_id", "project_id", "item_id", "requirement_id",
                                    "revision", "requirement_digest", "owner_id", "reservation_id",
                                    "parent_run_id", "state"}
                    if (type(claim) is not dict or set(claim) != claim_fields
                            or claim.get("source_session_id") != self._conversation_id
                            or claim.get("project_id") != self._bound_project_id
                            or claim.get("item_id") != item["item_id"]
                            or claim.get("requirement_id") != req["requirement_id"]
                            or type(claim.get("revision")) is not int or claim["revision"] < 0
                            or type(claim.get("requirement_digest")) is not str
                            or re.fullmatch(r"[0-9a-f]{64}", claim["requirement_digest"]) is None
                            or any(type(claim.get(key)) is not str or not claim[key].strip()
                                   for key in ("owner_id", "reservation_id", "parent_run_id"))
                            or claim.get("state") not in {"claimed", "unknown"}):
                        raise WorkRecordCorrupt("persistent schedule claim is invalid")
                dependencies = req.get("depends_on", [])
                dependency_refs = req.get("depends_on_refs", [])
                if (type(dependency_refs) is not list or any(
                        type(ref) is not dict or set(ref) != {"item_id", "requirement_id"}
                        or type(ref.get("item_id")) is not str or not ref["item_id"]
                        or type(ref.get("requirement_id")) is not str or not ref["requirement_id"]
                        for ref in dependency_refs)
                        or len({(ref["item_id"], ref["requirement_id"]) for ref in dependency_refs}) != len(dependency_refs)):
                    raise WorkRecordCorrupt("cross-item requirement dependencies are invalid")
                if (type(dependencies) is not list
                        or any(type(dependency) is not str or not dependency
                               or dependency == req["requirement_id"] for dependency in dependencies)
                        or len(set(dependencies)) != len(dependencies)):
                    raise WorkRecordCorrupt("requirement dependencies are invalid")
            for req in item["requirements"]:
                if any(dependency not in requirement_ids for dependency in req.get("depends_on", [])):
                    raise WorkRecordCorrupt("requirement dependency references a missing requirement")
            if "priority" in item and (type(item["priority"]) is not int or not 0 <= item["priority"] <= 5):
                raise WorkRecordCorrupt("work-record priority is invalid")
            result_ids: set[str] = set()
            for run in item["runs"]:
                if (not isinstance(run, dict) or not all(isinstance(run.get(key), str) and run[key]
                        for key in ("run_id", "requirement_id", "result_id", "result_summary"))
                        or not isinstance(run.get("status"), str) or run["status"] not in result_statuses
                        or not isinstance(run.get("verification"), dict)
                        or not isinstance(run.get("sources"), list)):
                    raise WorkRecordCorrupt("sidecar result is invalid")
                if run["result_id"] in result_ids:
                    raise WorkRecordCorrupt("sidecar has duplicate result identity")
                result_ids.add(run["result_id"])
                if run["requirement_id"] not in requirement_ids:
                    raise WorkRecordCorrupt("sidecar result references a missing requirement")
                self._validate_source_refs(run["sources"], corrupt=True)
                if "assistant_reply_excerpt" in run:
                    self._validate_assistant_reply_excerpt(run["assistant_reply_excerpt"], corrupt=True)
                    if (run.get("status") != "unknown"
                            or run.get("verification", {}).get("status") != "not_verified"):
                        raise WorkRecordCorrupt("assistant reply excerpt cannot upgrade an unverified run")
            event_ids: set[str] = set()
            for event in item["history"]:
                if (not isinstance(event, dict) or not isinstance(event.get("event_id"), str)
                        or not event["event_id"] or not isinstance(event.get("kind"), str)
                        or not isinstance(event.get("payload"), dict)
                        or type(event.get("at")) not in (int, float)
                        or ("digest" in event and not isinstance(event["digest"], str))
                        or ("idempotency_key" in event and not isinstance(event["idempotency_key"], str))
                        or ("idempotency_digest" in event and not isinstance(event["idempotency_digest"], str))):
                    raise WorkRecordCorrupt("sidecar history entry is invalid")
                if event["event_id"] in event_ids:
                    raise WorkRecordCorrupt("sidecar has duplicate event identity within an item")
                event_ids.add(event["event_id"])
            for run in item["runs"]:
                excerpt = run.get("assistant_reply_excerpt") if type(run) is dict else None
                if excerpt is None:
                    continue
                matches = [event for event in item["history"]
                           if event.get("event_id") == excerpt["excerpt_id"]
                           and event.get("kind") == "assistant_reply_excerpt_added"
                           and event.get("payload") == {
                               "requirement_id": run["requirement_id"],
                               "run_id": run["run_id"],
                               "source_availability": excerpt["source_availability"],
                           }
                           and event.get("digest") == _digest(excerpt)]
                if len(matches) != 1:
                    raise WorkRecordCorrupt("assistant reply excerpt audit event is missing or inconsistent")
        # New references are session-local and scope-exact. Build one graph across all
        # items so legacy local edges participate in cycle detection too.
        item_by_id = {item["item_id"]: item for item in data["items"]}
        requirement_by_key = {}
        graph = {}
        for item in data["items"]:
            for req in item["requirements"]:
                key = (item["item_id"], req["requirement_id"])
                requirement_by_key[key] = (item, req)
                graph[key] = [(item["item_id"], dep) for dep in req.get("depends_on", [])]
        for item in data["items"]:
            for req in item["requirements"]:
                key = (item["item_id"], req["requirement_id"])
                for ref in req.get("depends_on_refs", []):
                    target = item_by_id.get(ref["item_id"])
                    target_req = requirement_by_key.get((ref["item_id"], ref["requirement_id"]))
                    if target is None or target_req is None:
                        raise WorkRecordCorrupt("cross-item dependency references a missing requirement")
                    if target.get("scope") != item.get("scope"):
                        raise WorkRecordScopeError("cross-item dependencies must have exactly matching scope")
                    graph[key].append((ref["item_id"], ref["requirement_id"]))
        visiting, visited = set(), set()
        def visit_dependency(key):
            if key in visiting:
                raise WorkRecordCorrupt("work-record dependency graph contains a cycle")
            if key in visited:
                return
            visiting.add(key)
            for target in graph.get(key, []):
                visit_dependency(target)
            visiting.remove(key)
            visited.add(key)
        for key in graph:
            visit_dependency(key)

        legacy_digest = data.get(LEGACY_OUTBOX_DIGEST_KEY)
        if (legacy_digest is not None
                and (type(legacy_digest) is not str or _PUBLICATION_SHA256.fullmatch(legacy_digest) is None)):
            raise WorkRecordCorrupt("legacy outbox migration digest is invalid")
        migrations = data.get(LEGACY_OUTBOX_MIGRATIONS_KEY, [])
        if type(migrations) is not list or len(migrations) > 2:
            raise WorkRecordCorrupt("legacy outbox migration receipts are invalid")
        seen_migrations: set[str] = set()
        for migration in migrations:
            if (type(migration) is not dict
                    or set(migration) != {"intent_id", "source_digest", "state"}
                    or _publication_uuid_hex(migration.get("intent_id")) is None
                    or migration["intent_id"] in seen_migrations
                    or type(migration.get("source_digest")) is not str
                    or _PUBLICATION_SHA256.fullmatch(migration["source_digest"]) is None
                    or migration.get("state") not in {"pending", "published"}):
                raise WorkRecordCorrupt("legacy outbox migration receipt shape is invalid")
            seen_migrations.add(migration["intent_id"])
        self._validate_publication_intents(data)
        self._validate_independent_publication_intents(data)
        self._validate_project_scoped_card_intents(data)
        from work_card_reference_outbox import validate_reference_intents
        validate_reference_intents(data, self._conversation_id, self._bound_project_id)

    def _validate_independent_publication_intents(self, data: dict[str, Any]) -> None:
        intents = data.get(_INDEPENDENT_PUBLICATION_KEY, [])
        if type(intents) is not list or len(intents) > 4096:
            raise WorkRecordCorrupt("independent publication intent collection is invalid")
        item_by_id = {item["item_id"]: item for item in data["items"]}
        intent_ids: set[str] = set()
        generations: set[tuple[str, int]] = set()
        pending_items: set[str] = set()
        highest_generation: dict[str, int] = {}
        for intent in intents:
            if type(intent) is not dict or set(intent) != _INDEPENDENT_PUBLICATION_INTENT_KEYS:
                raise WorkRecordCorrupt("independent publication intent shape is invalid")
            state = intent.get("state")
            session_id = intent.get("session_id")
            item_id = _publication_uuid_hex(intent.get("latest_item_id"))
            requirement_id = _publication_uuid_hex(intent.get("latest_requirement_id"))
            intent_id = _publication_uuid_hex(intent.get("intent_id"))
            first_run_id = _publication_uuid_hex(intent.get("first_run_id"))
            latest_run_id = _publication_uuid_hex(intent.get("latest_run_id"))
            owner_id = _publication_uuid_hex(intent.get("window_owner_id"))
            generation = intent.get("generation")
            request_id = intent.get("request_id")
            card_id = intent.get("card_id")
            started, deadline, window = (intent.get("window_started_monotonic"),
                                         intent.get("deadline_monotonic"), intent.get("window_seconds"))
            if (state not in {"pending", "card_saved", "reference_queued"}
                    or session_id != self._conversation_id
                    or not all((item_id, requirement_id, intent_id, first_run_id, latest_run_id, owner_id))
                    or type(generation) is not int or generation < 1
                    or type(request_id) is not str or request_id != independent_publication_card_request_id(
                        self._conversation_id, intent["latest_item_id"], intent["intent_id"], generation,
                    )
                    or type(started) not in (int, float) or isinstance(started, bool)
                    or not math.isfinite(started) or started < 0
                    or type(deadline) not in (int, float) or isinstance(deadline, bool)
                    or not math.isfinite(deadline)
                    or type(window) not in (int, float) or isinstance(window, bool)
                    or not math.isfinite(window) or window <= 0 or window > _PUBLICATION_WINDOW_SECONDS
                    or abs((deadline - started) - window) > 0.001
                    or (state == "pending" and card_id is not None)
                    or (state != "pending" and
                        (type(card_id) is not str or _PUBLICATION_CARD_ID.fullmatch(card_id) is None))
                    or intent["intent_id"] in intent_ids):
                raise WorkRecordCorrupt("independent publication intent identity/window is invalid")
            item = item_by_id.get(intent["latest_item_id"])
            if (type(item) is not dict or item.get("scope") != {"kind": "independent", "object_ref": None}):
                raise WorkRecordCorrupt("independent publication intent does not reference an independent item")
            req = next((row for row in item["requirements"]
                        if row.get("requirement_id") == intent["latest_requirement_id"]), None)
            run = next((row for row in item["runs"]
                        if row.get("run_id") == intent["latest_run_id"]
                        and row.get("requirement_id") == intent["latest_requirement_id"]
                        and row.get("result_id") == intent["result_id"]), None)
            if (req is None or run is None or run.get("status") != "unknown"
                    or intent["result_id"] != _server_settlement_result_id(
                        self._conversation_id, item["item_id"], req["requirement_id"], run["run_id"],
                    )
                    or type(run.get("verification")) is not dict
                    or type(run["verification"].get("host_execution")) is not dict
                    or type(run["verification"]["host_execution"].get("sequence")) is not int
                    or run["verification"]["host_execution"]["sequence"] < 1):
                raise WorkRecordCorrupt("independent publication intent lacks its exact host settlement")
            identity = (item["item_id"], generation)
            if identity in generations or (state == "pending" and item["item_id"] in pending_items):
                raise WorkRecordCorrupt("independent publication generation is duplicated")
            if generation <= highest_generation.get(item["item_id"], 0):
                raise WorkRecordCorrupt("independent publication generation is out of order")
            generations.add(identity)
            highest_generation[item["item_id"]] = generation
            if state == "pending":
                pending_items.add(item["item_id"])
            intent_ids.add(intent["intent_id"])

    def _validate_project_scoped_card_intents(self, data: dict[str, Any]) -> None:
        rows = data.get(_PROJECT_SCOPED_PUBLICATION_KEY, [])
        if type(rows) is not list or len(rows) > 4096:
            raise WorkRecordCorrupt("project-scoped publication intent collection is invalid")
        seen: set[tuple[str, int]] = set()
        latest: dict[str, int] = {}
        from project_mode import ProjectModeError, normalize_project_id
        linked_projects = {
            req.get("source_ref", {}).get("id")
            for item in data["items"] if type(item.get("scope")) is dict
            and item["scope"].get("kind") == "independent"
            for req in item.get("requirements", []) if type(req) is dict
            and type(req.get("source_ref")) is dict
            and req["source_ref"].get("kind") == "project"
        }
        for row in rows:
            if type(row) is not dict or frozenset(row) not in (
                    _PROJECT_SCOPED_CARD_INTENT_KEYS, _PROJECT_SCOPED_CARD_INTENT_LEGACY_KEYS,
                    _PROJECT_SCOPED_CARD_INTENT_KEYS_WITH_FOCUS,
                    _PROJECT_SCOPED_CARD_INTENT_LEGACY_KEYS_WITH_FOCUS,
            ):
                raise WorkRecordCorrupt("project-scoped publication intent shape is invalid")
            project_id = row.get("project_id")
            try:
                canonical = normalize_project_id(project_id)
            except (ProjectModeError, TypeError, ValueError) as exc:
                raise WorkRecordCorrupt("project-scoped publication project identity is invalid") from exc
            request_id = _publication_uuid_hex(row.get("request_id"))
            generation = row.get("generation")
            state = row.get("state")
            started, deadline = row.get("window_started_monotonic"), row.get("deadline_monotonic")
            window, marker = row.get("window_seconds"), row.get("boot_marker")
            card_id = row.get("card_id")
            source_ids = row.get("source_result_ids", [])
            has_focus = _PROJECT_SCOPED_CARD_FOCUS_KEYS.issubset(row)
            latest_item_id = row.get("latest_item_id")
            latest_requirement_id = row.get("latest_requirement_id")
            latest_run_id = row.get("latest_run_id")
            latest_result_id = row.get("latest_result_id")
            if (row.get("session_id") != self._conversation_id or project_id != canonical
                    or project_id not in linked_projects
                    or request_id is None or type(generation) is not int or generation < 1
                    or generation <= latest.get(project_id, 0)
                    or state not in {"pending", "card_saved", "reference_queued"}
                    or type(window) not in (int, float) or isinstance(window, bool)
                    or not math.isfinite(window) or window <= 0 or window > _PUBLICATION_WINDOW_SECONDS
                    or type(started) not in (int, float) or isinstance(started, bool)
                    or not math.isfinite(started) or started < 0
                    or type(deadline) not in (int, float) or isinstance(deadline, bool)
                    or not math.isfinite(deadline) or abs((deadline - started) - window) > 0.001
                    or type(marker) not in (int, float) or isinstance(marker, bool) or not math.isfinite(marker)
                    or (state == "pending" and card_id is not None)
                    or (state != "pending" and
                        (type(card_id) is not str or _PROJECT_SCOPED_CARD_ID.fullmatch(card_id) is None))
                    or type(source_ids) is not list or len(source_ids) > _MAX_EVENTS
                    or any(type(value) is not str or _PROJECT_SCOPED_RESULT_ID.fullmatch(value) is None
                           for value in source_ids)
                    or len(set(source_ids)) != len(source_ids)
                    or (has_focus and (
                        _publication_uuid_hex(latest_item_id) is None
                        or _publication_uuid_hex(latest_requirement_id) is None
                        or type(latest_run_id) is not str or not latest_run_id
                        or type(latest_result_id) is not str
                        or _PROJECT_SCOPED_RESULT_ID.fullmatch(latest_result_id) is None
                        or latest_result_id not in source_ids
                    ))):
                raise WorkRecordCorrupt("project-scoped publication intent identity/window is invalid")
            if has_focus:
                item = next((value for value in data["items"] if value.get("item_id") == latest_item_id), None)
                requirement = next((value for value in item.get("requirements", [])
                                    if value.get("requirement_id") == latest_requirement_id), None) if item else None
                run = next((value for value in item.get("runs", [])
                            if value.get("run_id") == latest_run_id
                            and value.get("requirement_id") == latest_requirement_id
                            and value.get("result_id") == latest_result_id), None) if item else None
                from work_card_project_scope import _valid_receipt
                if (type(item) is not dict or item.get("scope") != {"kind": "independent", "object_ref": None}
                        or type(requirement) is not dict
                        or requirement.get("source_ref") != {"kind": "project", "id": project_id}
                        or type(run) is not dict
                        or not _valid_receipt(
                            requirement, run, session_id=self._conversation_id,
                            item_id=latest_item_id, project_id=project_id,
                            require_current=False,
                        )):
                    raise WorkRecordCorrupt("project-scoped publication focus source identity or host receipt is invalid")
            identity = (project_id, generation)
            if identity in seen:
                raise WorkRecordCorrupt("duplicate project-scoped publication generation")
            seen.add(identity)
            latest[project_id] = generation

    def _validate_publication_intents(self, data: dict[str, Any]) -> None:
        intents = data.get(_PUBLICATION_KEY, [])
        if type(intents) is not list or len(intents) > 2:
            raise WorkRecordCorrupt("publication intent collection is invalid")
        states: set[str] = set()
        intent_ids: set[str] = set()
        item_by_id = {item["item_id"]: item for item in data["items"]}
        for intent in intents:
            if type(intent) is not dict or set(intent) != _PUBLICATION_INTENT_KEYS:
                raise WorkRecordCorrupt("publication intent shape is invalid")
            state = intent.get("state")
            if state not in {"pending", "published"} or state in states:
                raise WorkRecordCorrupt("publication intent state is invalid")
            project_id = intent.get("project_id")
            if (type(project_id) is not str or not project_id
                    or project_id != self._bound_project_id):
                raise WorkRecordScopeError("publication intent project scope does not match Session")
            session_id = intent.get("session_id")
            if session_id != self._conversation_id:
                raise WorkRecordScopeError("publication intent Session identity mismatch")
            intent_id = _publication_uuid_hex(intent.get("intent_id"))
            first_run_id = _publication_uuid_hex(intent.get("first_run_id"))
            latest_run_id = _publication_uuid_hex(intent.get("latest_run_id"))
            owner_id = _publication_uuid_hex(intent.get("window_owner_id"))
            item_id = _publication_uuid_hex(intent.get("latest_item_id"))
            requirement_id = _publication_uuid_hex(intent.get("latest_requirement_id"))
            generation = intent.get("generation")
            request_id = intent.get("request_id")
            card_id = intent.get("card_id")
            started = intent.get("window_started_monotonic")
            deadline = intent.get("deadline_monotonic")
            window = intent.get("window_seconds")
            legacy_source_digest = intent.get("legacy_source_digest")
            if (not all((intent_id, first_run_id, latest_run_id, owner_id, item_id, requirement_id))
                    or (legacy_source_digest is not None
                        and (type(legacy_source_digest) is not str
                             or _PUBLICATION_SHA256.fullmatch(legacy_source_digest) is None))
                    or type(generation) is not int or generation < 1
                    or type(request_id) is not str
                    or request_id != publication_card_request_id(
                        self._conversation_id, project_id, intent["intent_id"], generation,
                    )
                    or type(started) not in (int, float) or not math.isfinite(started) or started < 0
                    or type(deadline) not in (int, float) or not math.isfinite(deadline)
                    or type(window) not in (int, float) or not math.isfinite(window)
                    or window <= 0 or window > _PUBLICATION_WINDOW_SECONDS
                    or abs((deadline - started) - window) > 0.001
                    or (card_id is not None and
                        (type(card_id) is not str or _PUBLICATION_CARD_ID.fullmatch(card_id) is None))
                    or (state == "published" and card_id is None)
                    or intent_id in intent_ids):
                raise WorkRecordCorrupt("publication intent identity/window is invalid")
            item = item_by_id.get(intent["latest_item_id"])
            if (type(item) is not dict or type(item.get("scope")) is not dict
                    or item["scope"].get("kind") != "project"
                    or item["scope"].get("project_id") != project_id):
                raise WorkRecordCorrupt("publication intent does not reference a project item")
            req = next((row for row in item["requirements"]
                        if row["requirement_id"] == intent["latest_requirement_id"]), None)
            if req is None:
                raise WorkRecordCorrupt("publication intent requirement is missing")
            run = next((row for row in item["runs"]
                        if row["run_id"] == intent["latest_run_id"]
                        and row["requirement_id"] == intent["latest_requirement_id"]
                        and row["result_id"] == intent["result_id"]), None)
            if (run is None or not _valid_server_publication_result(
                    self._conversation_id, item["item_id"], req["requirement_id"], run, project_id)):
                raise WorkRecordCorrupt("publication intent lacks its bound server settlement")
            states.add(state)
            intent_ids.add(intent_id)

    def _backup_previous(self, expected: dict[str, Any]) -> None:
        """Copy validated prior bytes before publishing; filesystem trust is not implied."""
        try:
            before = self._path.lstat()
            _require_private_regular_file(before, error=WorkRecordCorrupt, label="sidecar")
            if before.st_size > _MAX_BYTES:
                raise WorkRecordCorrupt("sidecar exceeds size limit")
            fd = os.open(self._path, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0))
            try:
                opened = os.fstat(fd)
                _require_private_regular_file(opened, error=WorkRecordCorrupt, label="opened sidecar")
                current = self._path.lstat()
                if _file_identity(before) != _file_identity(opened) or _file_identity(opened) != _file_identity(current):
                    raise WorkRecordCorrupt("sidecar changed while preparing backup")
                chunks: list[bytes] = []
                remaining = _MAX_BYTES + 1
                while remaining:
                    chunk = os.read(fd, min(64 * 1024, remaining))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    remaining -= len(chunk)
                raw = b"".join(chunks)
            finally:
                os.close(fd)
            if len(raw) > _MAX_BYTES:
                raise WorkRecordCorrupt("sidecar exceeds size limit")
            current_data = json.loads(raw.decode("utf-8"))
            self._validate_payload(current_data)
            if current_data != expected:
                raise WorkRecordCorrupt("sidecar changed before backup")

            key = hashlib.sha256(self._path.resolve(strict=True).as_posix().encode("utf-8")).hexdigest()[:16]
            directory = self._agent_root / "data" / ".file-guard" / key
            directory.mkdir(parents=True, exist_ok=True)
            if not safe_container(directory):
                raise WorkRecordScopeError("work-record backup directory is unsafe")
            # Retention sorts fixed-width stamps by name. Choose a logical time
            # later than every existing stamp, including unsafe entries: reusing a
            # pruned hole would cause the new backup itself to be pruned.
            stamp_time = _datetime.datetime.now(_datetime.UTC).replace(microsecond=0)
            stamp = stamp_time.strftime("%Y%m%dT%H%M%SZ")
            latest = max((entry.name for entry in directory.iterdir()
                          if STAMP.fullmatch(entry.name)),
                         default="")
            if latest >= stamp:
                try:
                    stamp_time = (_datetime.datetime.strptime(latest, "%Y%m%dT%H%M%SZ")
                                  .replace(tzinfo=_datetime.UTC) + _datetime.timedelta(seconds=1))
                except (ValueError, OverflowError) as exc:
                    raise WorkRecordWriteError("cannot allocate a later work-record backup timestamp") from exc
                stamp = stamp_time.strftime("%Y%m%dT%H%M%SZ")
            destination = directory / stamp
            fd, name = tempfile.mkstemp(prefix=".work-records-backup.", suffix=".tmp", dir=directory)
            temporary = Path(name)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, destination)
            finally:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
            _prune_backups(directory, keep=20)
        except WorkRecordError:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise WorkRecordWriteError("work-record backup failed; current sidecar was not published") from exc

    def _publish(self, data: dict[str, Any], *, previous: dict[str, Any] | None = None) -> None:
        # Never atomically replace a sidecar with a payload that its next reader rejects.
        # This also validates migrated legacy rows using the same compatibility rules as _read.
        self._validate_payload(data)
        raw = (json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n").encode("utf-8")
        if len(raw) > _MAX_BYTES or sum(len(item.get("history", [])) for item in data["items"]) > _MAX_EVENTS:
            raise WorkRecordWriteError("work-record size/history limit reached; no data was truncated")
        self._validate_scope()
        try:
            info = self._path.lstat()
        except FileNotFoundError:
            info = None
        if info is not None:
            _require_private_regular_file(info, error=WorkRecordScopeError, label="sidecar")
            if previous is None:
                raise WorkRecordWriteError("cannot publish over an existing sidecar without a verified backup source")
            self._backup_previous(previous)
        fd, name = tempfile.mkstemp(prefix=".work-records.", suffix=".tmp", dir=self._session_dir)
        tmp = Path(name)
        try:
            # Revalidate after creating the temporary file: the directory path may
            # have changed since the earlier scope check. This narrows, but cannot
            # eliminate, path-based TOCTOU races on Windows.
            with os.fdopen(fd, "wb") as stream:
                self._validate_scope()
                stream.write(raw)
                stream.flush()
            self._validate_scope()
            os.replace(tmp, self._path)
        except OSError as exc:
            raise WorkRecordWriteError("work-record snapshot was not published") from exc
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass

    def _mutate(self, change, *, return_commit_status=False):
        try:
            self._validate_scope()
            with _file_lock(self._lock_path, self._lock_timeout):
                data = self._read()
                previous = json.loads(json.dumps(data))
                result, changed = change(data)
                if changed:
                    data["revision"] += 1
                    self._publish(data, previous=previous)
                return (result, changed) if return_commit_status else result
        except WorkRecordError:
            raise
        except OSError as exc:
            raise WorkRecordWriteError("work-record storage operation failed") from exc

    def list_publication_intents(self) -> list[dict[str, Any]]:
        """Read only explicit current-session pending intents from the atomic source file."""
        self._validate_scope()
        with _file_lock(self._lock_path, self._lock_timeout):
            data = self._read()
            intents = data.get(_PUBLICATION_KEY, [])
            return json.loads(json.dumps([row for row in intents if row["state"] == "pending"]))

    def validate_publication_intent(self, intent: dict[str, Any]) -> bool:
        if type(intent) is not dict:
            return False
        self._validate_scope()
        with _file_lock(self._lock_path, self._lock_timeout):
            data = self._read()
            current = next((row for row in data.get(_PUBLICATION_KEY, [])
                            if row.get("intent_id") == intent.get("intent_id")
                            and row.get("state") == "pending"), None)
            return current == intent

    def publication_intent_remaining_seconds(self, intent: dict[str, Any]) -> float:
        if not self.validate_publication_intent(intent):
            raise WorkRecordStaleRun("publication intent changed before deadline check")
        if intent.get("window_owner_id") != _PUBLICATION_PROCESS_INSTANCE_ID:
            return 0.0
        now = _monotonic()
        started = intent["window_started_monotonic"]
        if now < started:
            return 0.0
        return max(0.0, min(float(intent["window_seconds"]), intent["deadline_monotonic"] - now))

    def bind_publication_intent_card(self, *, intent_id: str, generation: int, card_id: str) -> str:
        if type(card_id) is not str or _PUBLICATION_CARD_ID.fullmatch(card_id) is None:
            return "rejected"
        outcome = {"status": "missing"}
        def change(data):
            row = next((entry for entry in data.get(_PUBLICATION_KEY, [])
                        if entry["intent_id"] == intent_id and entry["state"] == "pending"), None)
            if row is None:
                return None, False
            if row["generation"] != generation:
                outcome["status"] = "stale"
                return row, False
            if row["card_id"] not in (None, card_id):
                outcome["status"] = "conflict"
                return row, False
            if row["card_id"] == card_id:
                outcome["status"] = "already_bound"
                return row, False
            row["card_id"] = card_id
            outcome["status"] = "bound"
            return row, True
        try:
            self._mutate(change)
        except Exception:
            return "uncertain"
        return outcome["status"]

    def mark_publication_intent_enqueued(self, *, intent_id: str, generation: int, card_id: str) -> str:
        outcome = {"status": "missing"}
        def change(data):
            intents = data.get(_PUBLICATION_KEY, [])
            row = next((entry for entry in intents
                        if entry["intent_id"] == intent_id and entry["state"] == "pending"), None)
            if row is None:
                return None, False
            if row["generation"] != generation or row["card_id"] != card_id:
                outcome["status"] = "stale"
                return row, False
            data[_PUBLICATION_KEY] = [
                entry for entry in intents if entry is row or entry["state"] != "published"
            ]
            row["state"] = "published"
            legacy_digest = row.get("legacy_source_digest")
            if legacy_digest is not None:
                receipt = next((entry for entry in data.get(LEGACY_OUTBOX_MIGRATIONS_KEY, [])
                                if entry["intent_id"] == row["intent_id"]
                                and entry["source_digest"] == legacy_digest), None)
                if receipt is None:
                    raise WorkRecordCorrupt("migrated intent has no stable source receipt")
                receipt["state"] = "published"
            outcome["status"] = "published"
            return row, True
        try:
            self._mutate(change)
        except Exception:
            return "uncertain"
        return outcome["status"]

    def list_independent_publication_intents(self, *, include_queued: bool = False) -> list[dict[str, Any]]:
        self._validate_scope()
        with _file_lock(self._lock_path, self._lock_timeout):
            data = self._read()
            states = {"pending", "card_saved", "reference_queued"} if include_queued else {"pending", "card_saved"}
            return json.loads(json.dumps([row for row in data.get(_INDEPENDENT_PUBLICATION_KEY, [])
                                         if row["state"] in states]))

    def validate_independent_publication_intent(self, intent: dict[str, Any]) -> bool:
        if type(intent) is not dict:
            return False
        self._validate_scope()
        with _file_lock(self._lock_path, self._lock_timeout):
            data = self._read()
            current = next((row for row in data.get(_INDEPENDENT_PUBLICATION_KEY, [])
                            if row.get("intent_id") == intent.get("intent_id")
                            and row.get("state") in {"pending", "card_saved"}), None)
            return current == intent

    def independent_publication_intent_remaining_seconds(self, intent: dict[str, Any]) -> float:
        if not self.validate_independent_publication_intent(intent):
            raise WorkRecordStaleRun("independent publication intent changed before deadline check")
        if intent.get("window_owner_id") != _PUBLICATION_PROCESS_INSTANCE_ID:
            return 0.0
        now = _monotonic()
        started = intent["window_started_monotonic"]
        if now < started:
            return 0.0
        return max(0.0, min(float(intent["window_seconds"]), intent["deadline_monotonic"] - now))

    def bind_independent_publication_card(
        self, *, intent_id: str, generation: int, card_id: str,
    ) -> str:
        if type(card_id) is not str or _PUBLICATION_CARD_ID.fullmatch(card_id) is None:
            return "rejected"
        outcome = {"status": "missing"}
        def change(data):
            row = next((entry for entry in data.get(_INDEPENDENT_PUBLICATION_KEY, [])
                        if entry["intent_id"] == intent_id and entry["state"] == "pending"), None)
            if row is None:
                return None, False
            if row["generation"] != generation:
                outcome["status"] = "stale"
                return row, False
            if row["card_id"] not in (None, card_id):
                outcome["status"] = "conflict"
                return row, False
            if row["card_id"] == card_id:
                outcome["status"] = "already_saved"
                return row, False
            row["card_id"] = card_id
            row["state"] = "card_saved"
            outcome["status"] = "saved"
            return row, True
        try:
            self._mutate(change)
        except Exception:
            return "uncertain"
        return outcome["status"]

    def mark_independent_publication_queued(
        self, *, intent_id: str, generation: int, card_id: str,
    ) -> str:
        outcome = {"status": "missing"}
        def change(data):
            row = next((entry for entry in data.get(_INDEPENDENT_PUBLICATION_KEY, [])
                        if entry["intent_id"] == intent_id and entry["state"] == "card_saved"), None)
            if row is None:
                queued = next((entry for entry in data.get(_INDEPENDENT_PUBLICATION_KEY, [])
                               if entry["intent_id"] == intent_id and entry["state"] == "reference_queued"), None)
                if queued and queued.get("generation") == generation and queued.get("card_id") == card_id:
                    outcome["status"] = "already_queued"
                    return queued, False
                return None, False
            if row["generation"] != generation or row["card_id"] != card_id:
                outcome["status"] = "stale"
                return row, False
            row["state"] = "reference_queued"
            outcome["status"] = "queued"
            return row, True
        try:
            self._mutate(change)
        except Exception:
            return "uncertain"
        return outcome["status"]

    def with_locked_items(self, callback):
        """Run a local callback on the latest items while holding the OS lock.

        Lock order for summary publication is work-record lock then journal lock.
        The callback must not call this store again, wait for user/model input, or
        perform outbound I/O; it may only validate and perform the journal commit.
        Exceptions propagate and the lock is released by the context manager.
        """
        if not callable(callback):
            raise TypeError("callback must be callable")
        self._validate_scope()
        with _file_lock(self._lock_path, self._lock_timeout):
            items = json.loads(json.dumps(self._read()["items"]))
            return callback(items)

    def with_locked_data(self, callback):
        """Run a read-only local callback over the validated sidecar under its OS lock."""
        if not callable(callback):
            raise TypeError("callback must be callable")
        self._validate_scope()
        with _file_lock(self._lock_path, self._lock_timeout):
            data = json.loads(json.dumps(self._read()))
            return callback(data)

    def list_items(self) -> list[dict[str, Any]]:
        self._validate_scope()
        with _file_lock(self._lock_path, self._lock_timeout):
            return json.loads(json.dumps(self._read()["items"]))

    def get_item(self, item_id: str) -> dict[str, Any] | None:
        return next((item for item in self.list_items() if item["item_id"] == item_id), None)

    def search(self, query: str) -> list[dict[str, Any]]:
        needle = query.casefold().strip()
        return [item for item in self.list_items() if not needle or needle in json.dumps(item, ensure_ascii=False).casefold()]

    def create_item(self, *, title: str, scope: dict[str, Any], source_refs: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        if not isinstance(title, str) or not title.strip():
            raise ValueError("title must be non-empty")
        if scope == {"kind": "independent"}:
            normalized_scope = {"kind": "independent", "object_ref": None}
        elif isinstance(scope, dict) and scope.get("kind") == "project" and isinstance(scope.get("project_id"), str) and scope["project_id"].strip():
            if scope["project_id"].strip() != self._bound_project_id:
                raise WorkRecordScopeError("project scope must match the current session binding")
            normalized_scope = {"kind": "project", "project_id": scope["project_id"].strip()}
        else:
            raise ValueError("scope must be an independent item or a project with project_id")
        item = {"item_id": str(uuid.uuid4()), "scope": normalized_scope, "title": title.strip(), "status": "active", "requirements": [], "runs": [], "history": []}
        event = {"event_id": str(uuid.uuid4()), "kind": "created", "payload": {"title": item["title"], "scope": normalized_scope}, "at": time.time()}
        if source_refs is not None:
            self._validate_source_refs(source_refs)
            if source_refs:
                item["source_refs"] = source_refs
        item["history"].append(event)
        def change(data):
            data["items"].append(item)
            return item, True
        return self._mutate(change)

    @staticmethod
    def _require_idempotency_key(key: str | None, event_id: str | None = None) -> str:
        if key is None and isinstance(event_id, str) and event_id.strip():
            # Backward compatibility: the original event_id contract was a
            # caller-stable event identity. Preserve non-random legacy IDs, but
            # reject UUIDv4 values because those were used as fresh per-call IDs.
            try:
                legacy_id = uuid.UUID(event_id.strip())
            except (ValueError, AttributeError):
                legacy_id = None
            if legacy_id is None or legacy_id.version != 4:
                return event_id.strip()
        if not isinstance(key, str) or not key.strip() or len(key) > 256:
            raise WorkRecordIdempotencyRequired("a stable caller-supplied idempotency_key is required")
        return key.strip()

    @staticmethod
    def _find_idempotent_event(data: dict[str, Any], key: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
        matches = [(item, event) for item in data["items"] for event in item["history"]
                   if event.get("idempotency_key") == key]
        if len(matches) > 1:
            raise WorkRecordCorrupt("idempotency key is duplicated in the sidecar")
        return matches[0] if matches else None

    def find_capture_replay(self, *, event_id: str, idempotency_key: str) -> tuple[str, str] | None:
        """Under the store lock, resolve only a matching, previously saved capture.

        A reused event ID with another key is a conflict; no raw message is stored.
        This read-only optimization must not replace the atomic commit check.
        """
        key = self._require_idempotency_key(idempotency_key, event_id)
        self._validate_scope()
        with _file_lock(self._lock_path, self._lock_timeout):
            data = self._read()
            matches = [(item, event) for item in data["items"] for event in item["history"]
                       if event.get("event_id") == event_id]
            if not matches:
                if self._find_idempotent_event(data, key) is not None:
                    raise WorkRecordConflict("capture key is bound to another event")
                return None
            if len(matches) != 1:
                raise WorkRecordCorrupt("capture event id is duplicated")
            item, event = matches[0]
            if event.get("idempotency_key") != key:
                raise WorkRecordConflict("event_id is already bound to another request")
            if self._find_idempotent_event(data, key) != (item, event):
                raise WorkRecordCorrupt("capture idempotency identity is inconsistent")
            req_id = event.get("payload", {}).get("requirement_id")
            rows = [row for row in item["requirements"] if row.get("requirement_id") == req_id]
            if event.get("kind") != "requirement_added" or not isinstance(req_id, str) or len(rows) != 1:
                raise WorkRecordCorrupt("capture requirement identity is invalid")
            return item["item_id"], req_id

    def create_item_with_requirement(
        self,
        *,
        title: str,
        scope: dict[str, Any],
        requirement: str,
        event_id: str,
        expected_snapshot: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        return_commit_status: bool = False,
        terminal_run: dict[str, Any] | None = None,
        result_evidence: dict[str, Any] | None = None,
    ) -> dict[str, Any] | tuple[dict[str, Any], bool]:
        """Atomically publish a new item and its first requirement.

        ``terminal_run`` is an optional host-proven terminal lifecycle receipt.
        When supplied, the new requirement, its unknown/not-verified run result,
        and their history events are published in the same snapshot update.
        Legacy callers that omit it retain the original item/requirement shape.
        """
        key = self._require_idempotency_key(idempotency_key, event_id)
        if not isinstance(title, str) or not title.strip():
            raise ValueError("title must be non-empty")
        if not isinstance(requirement, str) or not requirement.strip() or not isinstance(event_id, str) or not event_id:
            raise ValueError("requirement and event_id are required")
        if scope == {"kind": "independent"}:
            normalized_scope = {"kind": "independent", "object_ref": None}
        elif isinstance(scope, dict) and scope.get("kind") == "project" and isinstance(scope.get("project_id"), str) and scope["project_id"].strip():
            if scope["project_id"].strip() != self._bound_project_id:
                raise WorkRecordScopeError("project scope must match the current session binding")
            normalized_scope = {"kind": "project", "project_id": scope["project_id"].strip()}
        else:
            raise ValueError("scope must be an independent item or a project with project_id")

        item_id = str(uuid.uuid4())
        requirement_id = str(uuid.uuid4())
        now = time.time()
        clean_title = title.strip()
        clean_requirement = requirement.strip()
        requirement_row = {"requirement_id": requirement_id, "text": clean_requirement, "status": "pending"}
        item = {
            "item_id": item_id,
            "scope": normalized_scope,
            "title": clean_title,
            "status": "active",
            "requirements": [requirement_row],
            "runs": [],
            "history": [
                {"event_id": str(uuid.uuid4()), "kind": "created", "payload": {"title": clean_title, "scope": normalized_scope}, "at": now},
                {
                    "event_id": event_id,
                    "kind": "requirement_added",
                    "payload": {"requirement_id": requirement_id, "text": clean_requirement},
                    "digest": _digest({"kind": "requirement_added", "text": clean_requirement, "source_ref": None}),
                    "at": now,
                },
            ],
        }
        normalized_terminal_run = None
        if terminal_run is not None:
            if (type(terminal_run) is not dict
                    or set(terminal_run) != {"run_id", "sequence", "terminal_state", "finish_reason"}
                    or type(terminal_run.get("run_id")) is not str or not terminal_run["run_id"].strip()
                    or type(terminal_run.get("sequence")) is not int or terminal_run["sequence"] < 1
                    or type(terminal_run.get("terminal_state")) is not str
                    or terminal_run.get("terminal_state") not in {"settled", "paused", "failed"}
                    or (terminal_run.get("finish_reason") is not None and type(terminal_run.get("finish_reason")) is not str)
                    or (terminal_run.get("finish_reason") is not None and terminal_run.get("finish_reason") not in {
                        "completed", "stop", "cancelled", "timeout", "error", "paused", "unknown", "stopped",
                        "runaway_paused", "task_paused", "segment_cap_pause", "total_cap_exceeded",
                    })):
                raise ValueError("terminal run receipt is invalid")
            from evolution.context import normalize_host_run_id
            canonical_run_id = normalize_host_run_id(terminal_run["run_id"])
            if not canonical_run_id:
                raise ValueError("terminal run identity is invalid")
            normalized_terminal_run = {**terminal_run, "run_id": canonical_run_id}
        normalized_result_evidence = None
        if result_evidence is not None:
            if (normalized_terminal_run is None or type(result_evidence) is not dict
                    or set(result_evidence) != {"summary_excerpt", "source_availability", "sources"}
                    or type(result_evidence.get("summary_excerpt")) is not str
                    or not result_evidence["summary_excerpt"].strip()
                    or len(result_evidence["summary_excerpt"].encode("utf-8")) > MAX_WORK_RECORD_RESULT_EXCERPT_BYTES
                    or type(result_evidence.get("source_availability")) is not str
                    or result_evidence["source_availability"] not in _RESULT_SOURCE_AVAILABILITY
                    or type(result_evidence.get("sources")) is not list
                    or len(result_evidence["sources"]) > MAX_WORK_RECORD_RESULT_SOURCES):
                raise ValueError("host result evidence is invalid or exceeds its storage bound")
            self._validate_source_refs(result_evidence["sources"])
            if (result_evidence["source_availability"] == "available" and not result_evidence["sources"]
                    or result_evidence["source_availability"] == "not_applicable" and result_evidence["sources"]):
                raise ValueError("source availability does not match the supplied source references")
            normalized_result_evidence = {
                "summary_excerpt": result_evidence["summary_excerpt"],
                "source_availability": result_evidence["source_availability"],
                "sources": [dict(source) for source in result_evidence["sources"]],
            }
        expected = None if expected_snapshot is None else json.loads(json.dumps(expected_snapshot))
        operation = {
            "kind": "create_item_with_requirement", "title": clean_title,
            "scope": normalized_scope, "requirement": clean_requirement,
        }
        if normalized_terminal_run is not None:
            operation["terminal_run"] = normalized_terminal_run
        if normalized_result_evidence is not None:
            operation["result_evidence"] = normalized_result_evidence
        operation_digest = _digest(operation)

        def change(data):
            prior = self._find_idempotent_event(data, key)
            if prior is not None:
                prior_item, prior_event = prior
                if (prior_event.get("kind") != "requirement_added"
                        or prior_event.get("idempotency_digest") != operation_digest):
                    raise WorkRecordConflict("idempotency key reused with different create payload")
                return prior_item, False
            if any(event.get("event_id") == event_id for row in data["items"] for event in row["history"]):
                raise WorkRecordConflict("event_id is already bound to another operation")
            if expected is not None and data != expected:
                raise WorkRecordConflict("work-record snapshot changed after authorization preview")
            item["history"][-1]["idempotency_key"] = key
            item["history"][-1]["idempotency_digest"] = operation_digest
            if normalized_terminal_run is not None:
                run_id = normalized_terminal_run["run_id"]
                if any(run.get("run_id") == run_id for row in data["items"] for run in row["runs"]):
                    raise WorkRecordConflict("terminal run is already bound to another work record")
                result_id = str(uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    "work-record-capture-terminal:" + self._conversation_id + ":" + event_id + ":" + run_id,
                ))
                if any(result_id == event.get("event_id") for row in data["items"] for event in row["history"]):
                    raise WorkRecordConflict("terminal capture result identity is already in use")
                requirement_row["current_run_id"] = run_id
                requirement_row["status"] = "unknown"
                host_execution = {"sequence": normalized_terminal_run["sequence"]}
                if normalized_terminal_run["finish_reason"] is not None:
                    host_execution["finish_reason"] = normalized_terminal_run["finish_reason"]
                if normalized_result_evidence is not None:
                    host_execution["source_availability"] = normalized_result_evidence["source_availability"]
                run = {
                    "run_id": run_id, "requirement_id": requirement_id, "status": "unknown",
                    "result_summary": (
                        normalized_result_evidence["summary_excerpt"] if normalized_result_evidence is not None
                        else "Host terminal receipt recorded; requirement outcome remains unknown."
                    ),
                    "verification": {
                        "status": "not_verified", "evidence_refs": [], "host_execution": host_execution,
                    },
                    "sources": normalized_result_evidence["sources"] if normalized_result_evidence is not None else [],
                    "result_id": result_id,
                }
                item["runs"].append(run)
                item["history"].append({
                    "event_id": result_id, "kind": "result_recorded",
                    "payload": {"run_id": run_id, "requirement_id": requirement_id},
                    "digest": _digest(run), "at": time.time(),
                })
                item["status"] = self._derive_item_status(item["requirements"])
            data["items"].append(item)
            return item, True

        return self._mutate(change, return_commit_status=return_commit_status)

    def add_requirement(
        self, item_id: str, text: str, *, event_id: str,
        source_ref: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        expected_snapshot: dict[str, Any] | None = None,
        return_commit_status: bool = False,
        terminal_run: dict[str, Any] | None = None,
        expected_scope: dict[str, Any] | None = None,
    ) -> dict[str, Any] | tuple[dict[str, Any], bool]:
        """Append once, optionally atomically associating a trusted host terminal run."""
        key = self._require_idempotency_key(idempotency_key, event_id)
        if not isinstance(text, str) or not text.strip() or not isinstance(event_id, str) or not event_id.strip():
            raise ValueError("requirement text and event_id are required")
        requirement = {"requirement_id": str(uuid.uuid4()), "text": text.strip(), "status": "pending"}
        if source_ref is not None:
            self._validate_source_ref(source_ref)
            requirement["source_ref"] = source_ref
        expected = None if expected_snapshot is None else json.loads(json.dumps(expected_snapshot))
        normalized_terminal = None
        if terminal_run is not None:
            from evolution.context import normalize_host_run_id
            if (type(expected) is not dict or type(expected_scope) is not dict or source_ref is not None
                    or type(terminal_run) is not dict
                    or set(terminal_run) != {"run_id", "sequence", "terminal_state", "finish_reason"}
                    or type(terminal_run['run_id']) is not str
                    or type(terminal_run['sequence']) is not int or terminal_run['sequence'] < 1
                    or type(terminal_run['terminal_state']) is not str
                    or terminal_run['terminal_state'] not in {"settled", "paused", "failed"}
                    or (terminal_run['finish_reason'] is not None and (type(terminal_run['finish_reason']) is not str
                        or terminal_run['finish_reason'] not in {
                        "completed", "stop", "cancelled", "timeout", "error", "paused", "unknown", "stopped",
                        "runaway_paused", "task_paused", "segment_cap_pause", "total_cap_exceeded"}))):
                raise ValueError("terminal append requires exact snapshot, scope and valid host receipt")
            run_id = normalize_host_run_id(terminal_run['run_id'])
            if not run_id:
                raise ValueError("invalid host run identity")
            normalized_terminal = {**terminal_run, 'run_id': run_id}
            expected_scope = json.loads(json.dumps(expected_scope))
        elif expected_scope is not None:
            raise ValueError("expected_scope requires terminal_run")
        operation = {
            "kind": "append_requirement", "item_id": item_id,
            "text": requirement["text"], "source_ref": requirement.get("source_ref"),
        }
        if normalized_terminal is not None:
            operation['terminal_run'] = normalized_terminal
            operation['expected_scope'] = expected_scope
        operation_digest = _digest(operation)
        def change(data):
            prior_global = self._find_idempotent_event(data, key)
            if prior_global is not None:
                prior_item, prior_event = prior_global
                if (prior_item.get("item_id") != item_id
                        or prior_event.get("kind") != "requirement_added"
                        or prior_event.get("idempotency_digest") != operation_digest
                        or (normalized_terminal is not None and prior_event.get('event_id') != event_id)):
                    raise WorkRecordConflict("idempotency key reused with different append target or payload")
                prior_req_id = prior_event.get("payload", {}).get("requirement_id")
                prior_reqs = [row for row in prior_item['requirements'] if row.get('requirement_id') == prior_req_id]
                if len(prior_reqs) != 1:
                    raise WorkRecordCorrupt("idempotent append requirement identity is inconsistent")
                if normalized_terminal is not None:
                    result_id = str(uuid.uuid5(uuid.NAMESPACE_URL,
                        "work-record-capture-terminal:" + self._conversation_id + ":" + event_id + ":" + normalized_terminal['run_id']))
                    prior_runs = [row for row in prior_item['runs']
                                  if row.get('result_id') == result_id and row.get('run_id') == normalized_terminal['run_id']
                                  and row.get('requirement_id') == prior_req_id]
                    prior_results = [row for row in prior_item['history']
                                     if row.get('event_id') == result_id and row.get('kind') == 'result_recorded']
                    if len(prior_runs) != 1 or len(prior_results) != 1:
                        raise WorkRecordCorrupt("idempotent terminal append association is incomplete")
                return prior_reqs[0], False
            item = self._find(data, item_id)
            digest = _digest({"kind": "requirement_added", "text": requirement["text"], "source_ref": requirement.get("source_ref")})
            prior = any(event.get("event_id") == event_id for row in data["items"] for event in row["history"])
            if prior:
                # A prior event ID without the same stable request key cannot be
                # treated as an authorized retry, even if the payload happens to match.
                raise WorkRecordConflict("event_id is already bound to another request")
            if expected is not None and data != expected:
                raise WorkRecordConflict("work-record snapshot changed after authorization preview")
            if normalized_terminal is not None and item.get('scope') != expected_scope:
                raise WorkRecordConflict("append target scope changed after authorization")
            event = {
                "event_id": event_id, "kind": "requirement_added",
                "payload": {"requirement_id": requirement["requirement_id"], "text": requirement["text"]},
                "digest": digest, "idempotency_key": key,
                "idempotency_digest": operation_digest, "at": time.time(),
            }
            if normalized_terminal is not None:
                run_id = normalized_terminal['run_id']
                if any(run.get('run_id') == run_id for row in data['items'] for run in row['runs']):
                    raise WorkRecordConflict("terminal run is already bound to another work record")
                result_id = str(uuid.uuid5(uuid.NAMESPACE_URL,
                    "work-record-capture-terminal:" + self._conversation_id + ":" + event_id + ":" + run_id))
                if any(result_id == old.get('event_id') for row in data['items'] for old in row['history']):
                    raise WorkRecordConflict("terminal capture result identity is already in use")
                requirement['current_run_id'] = run_id
                requirement['status'] = 'unknown'
                host_execution = {'sequence': normalized_terminal['sequence']}
                if normalized_terminal['finish_reason'] is not None:
                    host_execution['finish_reason'] = normalized_terminal['finish_reason']
                run = {'run_id': run_id, 'requirement_id': requirement['requirement_id'],
                       'status': 'unknown',
                       'result_summary': 'Host terminal receipt recorded; requirement outcome remains unknown.',
                       'verification': {'status': 'not_verified', 'evidence_refs': [], 'host_execution': host_execution},
                       'sources': [], 'result_id': result_id}
                item['runs'].append(run)
            item["requirements"].append(requirement); item["history"].append(event)
            if normalized_terminal is not None:
                item['history'].append({'event_id': result_id, 'kind': 'result_recorded',
                    'payload': {'run_id': run_id, 'requirement_id': requirement['requirement_id']},
                    'digest': _digest(run), 'at': time.time()})
            item["status"] = self._derive_item_status(item["requirements"])
            return requirement, True
        return self._mutate(change, return_commit_status=return_commit_status)

    def copy_requirement_to_new_item(
        self, source_item_id: str, source_requirement_id: str, *,
        expected_revision: int, event_id: str, idempotency_key: str,
    ) -> dict[str, Any]:
        """Copy requirement text into a fresh pending independent item.

        The source item and its evidence are read-only. Revision comparison,
        source resolution and destination creation happen in one store mutation
        under the sidecar lock. Provenance is recorded on the new item's history.
        """
        if (type(source_item_id) is not str or not source_item_id.strip()
                or type(source_requirement_id) is not str or not source_requirement_id.strip()
                or type(expected_revision) is not int or expected_revision < 0
                or type(event_id) is not str or not event_id.strip()
                or type(idempotency_key) is not str or not idempotency_key.strip()):
            raise ValueError("source identities, revision and stable audit identities are required")
        key = self._require_idempotency_key(idempotency_key, event_id)
        operation = {"kind": "copy_requirement_to_new_item",
                     "source_item_id": source_item_id,
                     "source_requirement_id": source_requirement_id}
        digest = _digest(operation)
        def change(data):
            prior = self._find_idempotent_event(data, key)
            if prior is not None:
                prior_item, event = prior
                payload = event.get("payload", {})
                if (prior_item.get("item_id") != payload.get("new_item_id")
                        or event.get("kind") != "requirement_copied_to_new_item"
                        or event.get("idempotency_digest") != digest
                        or event.get("event_id") != event_id
                        or payload.get("source_session_id") != self._conversation_id
                        or payload.get("source_item_id") != source_item_id
                        or payload.get("source_requirement_id") != source_requirement_id):
                    raise WorkRecordConflict("copy idempotency key reused for another source or operation")
                copied_requirements = [row for row in prior_item.get("requirements", [])
                                       if row.get("requirement_id") == payload.get("new_requirement_id")]
                if (len(copied_requirements) != 1
                        or copied_requirements[0].get("status") != "pending"
                        or copied_requirements[0].get("source_ref") is not None
                        or copied_requirements[0].get("depends_on", [])
                        or prior_item.get("runs")):
                    raise WorkRecordCorrupt("copied requirement identity or state is inconsistent")
                return prior_item, False
            if any(event.get("event_id") == event_id
                   for item in data["items"] for event in item["history"]):
                raise WorkRecordConflict("event_id is already bound to another operation")
            if data["revision"] != expected_revision:
                raise WorkRecordConflict("work-record revision changed after copy preview")
            source_item = self._find(data, source_item_id)
            if source_item.get("scope") != {"kind": "independent", "object_ref": None}:
                raise WorkRecordConflict("only current-session independent items can be copied")
            matches = [req for req in source_item["requirements"]
                       if req.get("requirement_id") == source_requirement_id]
            if len(matches) != 1:
                raise KeyError(source_requirement_id)
            source_req = matches[0]
            text = source_req.get("text")
            if type(text) is not str or not text.strip():
                raise WorkRecordCorrupt("source requirement text is invalid")
            new_item_id, new_requirement_id = str(uuid.uuid4()), str(uuid.uuid4())
            source_text_digest = _digest({"text": text})
            payload = {
                "source_session_id": self._conversation_id,
                "source_item_id": source_item_id,
                "source_requirement_id": source_requirement_id,
                "source_text_digest": source_text_digest,
                "new_item_id": new_item_id,
                "new_requirement_id": new_requirement_id,
            }
            item = {
                "item_id": new_item_id,
                "scope": {"kind": "independent", "object_ref": None},
                "title": source_item["title"], "status": "active",
                "requirements": [{"requirement_id": new_requirement_id,
                                  "text": text, "status": "pending"}],
                "runs": [],
                "history": [
                    {"event_id": str(uuid.uuid4()), "kind": "created",
                     "payload": {"title": source_item["title"], "scope": {"kind": "independent", "object_ref": None}},
                     "at": time.time()},
                    {"event_id": event_id, "kind": "requirement_copied_to_new_item",
                     "payload": payload, "digest": digest,
                     "idempotency_key": key, "idempotency_digest": digest,
                     "at": time.time()},
                ],
            }
            data["items"].append(item)
            return item, True

        item = self._mutate(change)
        return json.loads(json.dumps(item))

    _MERGEABLE_REQUIREMENT_STATUSES = frozenset({
        "pending", "queued", "stopped", "blocked", "waiting_confirmation",
    })

    def merge_independent_item_into(
        self, target_item_id: str, source_item_id: str, *,
        expected_revision: int, event_id: str, idempotency_key: str,
    ) -> dict[str, Any]:
        """Move mergeable requirements from one independent item into another.

        Source evidence and non-mergeable requirements stay on the source item.
        """
        if (type(target_item_id) is not str or not target_item_id.strip()
                or type(source_item_id) is not str or not source_item_id.strip()
                or target_item_id == source_item_id
                or type(expected_revision) is not int or expected_revision < 0
                or type(event_id) is not str or not event_id.strip()
                or type(idempotency_key) is not str or not idempotency_key.strip()):
            raise ValueError("target, source, revision and stable audit identities are required")
        key = self._require_idempotency_key(idempotency_key, event_id)
        operation = {"kind": "merge_independent_item_into",
                     "target_item_id": target_item_id, "source_item_id": source_item_id}
        digest = _digest(operation)

        def change(data):
            prior = self._find_idempotent_event(data, key)
            if prior is not None:
                prior_item, event = prior
                payload = event.get("payload", {})
                if (prior_item.get("item_id") != target_item_id
                        or event.get("kind") != "requirements_merged_from_item"
                        or event.get("idempotency_digest") != digest
                        or event.get("event_id") != event_id
                        or payload.get("source_item_id") != source_item_id):
                    raise WorkRecordConflict("merge idempotency key reused for another operation")
                return prior_item, False
            if any(event.get("event_id") == event_id for row in data["items"] for event in row["history"]):
                raise WorkRecordConflict("event_id is already bound to another operation")
            if data["revision"] != expected_revision:
                raise WorkRecordConflict("work-record revision changed after merge preview")
            target = self._find(data, target_item_id)
            source = self._find(data, source_item_id)
            independent = {"kind": "independent", "object_ref": None}
            if target.get("scope") != independent or source.get("scope") != independent:
                raise WorkRecordConflict("only current-session independent items can be merged")
            moving, remaining = [], []
            for req in source["requirements"]:
                if req.get("status") in self._MERGEABLE_REQUIREMENT_STATUSES:
                    moving.append(req)
                else:
                    remaining.append(req)
            if not moving:
                raise WorkRecordConflict("source item has no mergeable requirements")
            target_ids = {row.get("requirement_id") for row in target["requirements"]}
            for req in moving:
                if req.get("requirement_id") in target_ids:
                    raise WorkRecordConflict("duplicate requirement identity across merge targets")
            payload = {
                "target_item_id": target_item_id,
                "source_item_id": source_item_id,
                "moved_requirement_ids": [req["requirement_id"] for req in moving],
            }
            for req in moving:
                moved = {key: value for key, value in req.items() if key != "current_run_id"}
                target["requirements"].append(moved)
            source["requirements"] = remaining
            target["history"].append({
                "event_id": event_id,
                "kind": "requirements_merged_from_item",
                "payload": payload,
                "digest": digest,
                "idempotency_key": key,
                "idempotency_digest": digest,
                "at": time.time(),
            })
            source["history"].append({
                "event_id": str(uuid.uuid4()),
                "kind": "requirements_merged_into_item",
                "payload": payload,
                "at": time.time(),
            })
            target["status"] = self._derive_item_status(target["requirements"])
            source["status"] = self._derive_item_status(source["requirements"]) if remaining else "completed"
            return target, True

        item = self._mutate(change)
        return json.loads(json.dumps(item))

    def set_requirement_control(
        self, item_id: str, requirement_id: str, *, action: str,
        expected_status: str, event_id: str, idempotency_key: str,
        expected_snapshot: dict[str, Any] | None = None,
        depends_on: list[str] | None = None,
        depends_on_refs: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        """Persist a safe queue/confirmation/stop/continue control without running tools.

        This method never claims to cancel a live worker. An in-progress requirement
        must be stopped through the host cancellation path and settled from its
        actual terminal receipt; a user-visible stop request alone is insufficient.
        """
        allowed_actions = {"set_dependencies", "queue", "wait_confirmation", "release_confirmation", "stop", "continue"}
        if (action not in allowed_actions or type(expected_status) is not str
                or type(event_id) is not str or not event_id.strip()
                or type(idempotency_key) is not str or not idempotency_key.strip()):
            raise ValueError("invalid work-record control request")
        key = self._require_idempotency_key(idempotency_key, event_id)
        normalized_refs = None
        if depends_on_refs is not None:
            if (type(depends_on_refs) is not list or any(
                    type(ref) is not dict or set(ref) != {"item_id", "requirement_id"}
                    or type(ref.get("item_id")) is not str or not ref["item_id"]
                    or type(ref.get("requirement_id")) is not str or not ref["requirement_id"]
                    for ref in depends_on_refs)
                    or len({(ref["item_id"], ref["requirement_id"]) for ref in depends_on_refs}) != len(depends_on_refs)):
                raise ValueError("cross-item dependency references must be unique stable identities")
            normalized_refs = [dict(ref) for ref in depends_on_refs]
        normalized_dependencies = None
        if depends_on is not None:
            if (type(depends_on) is not list or any(type(value) is not str or not value for value in depends_on)
                    or len(set(depends_on)) != len(depends_on)):
                raise ValueError("dependency list must contain unique requirement identities")
            normalized_dependencies = list(depends_on)
        expected = None if expected_snapshot is None else json.loads(json.dumps(expected_snapshot))
        digest = _digest({"kind": "requirement_control", "item_id": item_id,
                          "requirement_id": requirement_id, "action": action,
                          "expected_status": expected_status, "depends_on": normalized_dependencies,
                          "depends_on_refs": normalized_refs})

        def change(data):
            prior = self._find_idempotent_event(data, key)
            if prior is not None:
                prior_item, prior_event = prior
                if (prior_item.get("item_id") != item_id or prior_event.get("kind") != "requirement_control"
                        or prior_event.get("idempotency_digest") != digest):
                    raise WorkRecordConflict("control idempotency key reused with a different operation")
                return dict(prior_event["payload"]), False
            if any(event.get("event_id") == event_id for row in data["items"] for event in row["history"]):
                raise WorkRecordConflict("event_id is already bound to another operation")
            if expected is not None and data != expected:
                raise WorkRecordConflict("work-record snapshot changed after control preview")
            item = self._find(data, item_id)
            reqs = item["requirements"]
            req = next((row for row in reqs if row["requirement_id"] == requirement_id), None)
            if req is None:
                raise KeyError(requirement_id)
            current = req["status"]
            if current != expected_status:
                raise WorkRecordConflict("requirement status changed after control preview")
            if current == "in_progress":
                raise WorkRecordConflict("live execution requires host cancellation and terminal receipt")
            if action == "set_dependencies":
                if current in {"in_progress", "unknown", "completed"}:
                    raise WorkRecordConflict("dependencies cannot change for running, uncertain or completed work")
                next_status = current
            elif action == "queue":
                if current not in {"pending", "stopped", "blocked"}:
                    raise WorkRecordConflict("only pending, stopped or blocked work can be queued")
                if current in {"stopped", "blocked"}:
                    raise WorkRecordConflict("stopped/blocked work must be explicitly continued before queueing")
                next_status = "queued"
            elif action == "wait_confirmation":
                if current not in {"pending", "queued"}:
                    raise WorkRecordConflict("only not-started work can wait for confirmation")
                next_status = "waiting_confirmation"
            elif action == "release_confirmation":
                if current != "waiting_confirmation":
                    raise WorkRecordConflict("requirement is not waiting for confirmation")
                next_status = "pending"
            elif action == "stop":
                if current not in {"pending", "queued", "waiting_confirmation", "blocked"}:
                    raise WorkRecordConflict("only non-running work can be stopped directly")
                next_status = "stopped"
            else:
                if current not in {"stopped", "blocked", "unknown"}:
                    raise WorkRecordConflict("only stopped, blocked or unknown work can be continued")
                if current == "unknown":
                    raise WorkRecordConflict("unknown side effects must be inspected before continuation")
                next_status = "pending"
            if normalized_dependencies is not None or normalized_refs is not None:
                next_local = req.get("depends_on", []) if normalized_dependencies is None else normalized_dependencies
                next_refs = req.get("depends_on_refs", []) if normalized_refs is None else normalized_refs
                if requirement_id in next_local or any(
                        ref["item_id"] == item_id and ref["requirement_id"] == requirement_id for ref in next_refs):
                    raise WorkRecordConflict("a requirement cannot depend on itself")
                if any(dep not in {row["requirement_id"] for row in reqs} for dep in next_local):
                    raise WorkRecordConflict("legacy dependency must reference another requirement in this item")
                by_item = {row["item_id"]: row for row in data["items"]}
                for ref in next_refs:
                    target_item = by_item.get(ref["item_id"])
                    target = next((r for r in target_item["requirements"]
                                   if r["requirement_id"] == ref["requirement_id"]), None) if target_item else None
                    if target is None or target_item["scope"] != item["scope"]:
                        raise WorkRecordConflict("dependency target must exist in this session with exactly matching scope")
                graph = {}
                for row_item in data["items"]:
                    for row_req in row_item["requirements"]:
                        graph_key = (row_item["item_id"], row_req["requirement_id"])
                        graph[graph_key] = [(row_item["item_id"], dep) for dep in row_req.get("depends_on", [])]
                        graph[graph_key].extend((ref["item_id"], ref["requirement_id"])
                                          for ref in row_req.get("depends_on_refs", []))
                graph[(item_id, requirement_id)] = [(item_id, dep) for dep in next_local]
                graph[(item_id, requirement_id)].extend((ref["item_id"], ref["requirement_id"]) for ref in next_refs)
                visiting, visited = set(), set()
                def check_cycle(key):
                    if key in visiting:
                        raise WorkRecordConflict("dependency cycle is not allowed")
                    if key in visited:
                        return
                    visiting.add(key)
                    for target_key in graph.get(key, []):
                        check_cycle(target_key)
                    visiting.remove(key)
                    visited.add(key)
                for graph_node in graph:
                    check_cycle(graph_node)
                req["depends_on"] = list(next_local)
                req["depends_on_refs"] = [dict(ref) for ref in next_refs]
            req["status"] = next_status
            item["status"] = self._derive_item_status(reqs)
            payload = {"requirement_id": requirement_id, "action": action,
                       "old_status": current, "new_status": next_status,
                       "depends_on": req.get("depends_on", []),
                       "depends_on_refs": req.get("depends_on_refs", [])}
            item["history"].append({"event_id": event_id, "kind": "requirement_control",
                "payload": payload, "digest": digest, "idempotency_key": key,
                "idempotency_digest": digest, "at": time.time()})
            return payload, True

        return self._mutate(change)

    def set_item_priority(
        self, item_id: str, priority: int, *, event_id: str, idempotency_key: str,
        expected_snapshot: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Set a 0–5 attention priority; this changes display ordering only."""
        if type(priority) is not int or not 0 <= priority <= 5:
            raise ValueError("priority must be an integer from 0 through 5")
        key = self._require_idempotency_key(idempotency_key, event_id)
        expected = None if expected_snapshot is None else json.loads(json.dumps(expected_snapshot))
        digest = _digest({"kind": "item_priority_changed", "item_id": item_id, "priority": priority})
        def change(data):
            prior = self._find_idempotent_event(data, key)
            if prior is not None:
                prior_item, event = prior
                if (prior_item.get("item_id") != item_id or event.get("kind") != "item_priority_changed"
                        or event.get("idempotency_digest") != digest):
                    raise WorkRecordConflict("priority idempotency key reused")
                return dict(event["payload"]), False
            if expected is not None and data != expected:
                raise WorkRecordConflict("work-record snapshot changed after priority preview")
            if any(event.get("event_id") == event_id for row in data["items"] for event in row["history"]):
                raise WorkRecordConflict("event_id is already bound to another operation")
            item = self._find(data, item_id)
            previous = item.get("priority", 3)
            item["priority"] = priority
            payload = {"old_priority": previous, "new_priority": priority}
            item["history"].append({"event_id": event_id, "kind": "item_priority_changed",
                "payload": payload, "digest": digest, "idempotency_key": key,
                "idempotency_digest": digest, "at": time.time()})
            return payload, True
        return self._mutate(change)

    def mark_interrupted_runs_unknown(
        self, *, event_id_prefix: str, current_owner_id: str = _WORK_RECORD_PROCESS_INSTANCE_ID,
    ) -> int:
        """Conservatively recover persisted in-progress rows after a process restart.

        Call only from an explicit startup recovery pass after confirming there is no
        matching live host execution. This records uncertainty; it never resumes work.
        """
        if (type(event_id_prefix) is not str or not event_id_prefix.strip()
                or type(current_owner_id) is not str or not current_owner_id.strip()):
            raise ValueError("recovery identity is required")
        def change(data):
            changed = 0
            for item in data["items"]:
                for req in item["requirements"]:
                    if (req.get("status") != "in_progress"
                            or req.get("execution_owner_id") == current_owner_id):
                        continue
                    run_id = req.get("current_run_id")
                    req["status"] = "unknown"
                    req.pop("execution_owner_id", None)
                    # Keep any schedule claim as a durable exclusion marker. A
                    # caller may only recover it after independently confirming
                    # that no host execution is still live.
                    event_id = f"{event_id_prefix}:{item['item_id']}:{req['requirement_id']}"
                    existing = next((event for event in item["history"] if event.get("event_id") == event_id), None)
                    if existing is None:
                        payload = {"requirement_id": req["requirement_id"], "run_id": run_id,
                                   "reason": "application_interrupted", "outcome": "unknown"}
                        item["history"].append({"event_id": event_id, "kind": "requirement_interrupted",
                            "payload": payload, "digest": _digest(payload), "at": time.time()})
                    changed += 1
                item["status"] = self._derive_item_status(item["requirements"])
            return changed, changed > 0
        return self._mutate(change)

    def record_schedule_claim_inspection(
        self, item_id: str, requirement_id: str, *, evidence: str, event_id: str,
        idempotency_key: str, expected_snapshot: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Record a human check while preserving an unresolved exclusion claim."""
        if (type(evidence) is not str or not evidence.strip() or len(evidence.strip()) > 4000
                or type(item_id) is not str or not item_id.strip()
                or type(requirement_id) is not str or not requirement_id.strip()
                or type(event_id) is not str or not event_id.strip()):
            raise ValueError("inspection evidence and identities are required")
        key = self._require_idempotency_key(idempotency_key, event_id)
        evidence = evidence.strip()
        digest = _digest({"kind": "schedule_claim_inspected", "item_id": item_id,
                          "requirement_id": requirement_id, "evidence": evidence,
                          "disposition": "unresolved_claim_retained"})
        expected = None if expected_snapshot is None else json.loads(json.dumps(expected_snapshot))
        def change(data):
            prior = self._find_idempotent_event(data, key)
            if prior is not None:
                prior_item, event = prior
                if (prior_item.get("item_id") != item_id or event.get("kind") != "schedule_claim_inspected"
                        or event.get("idempotency_digest") != digest):
                    raise WorkRecordConflict("inspection idempotency key reused")
                return dict(event["payload"]), False
            if expected is not None and data != expected:
                raise WorkRecordConflict("work-record snapshot changed before claim inspection")
            if any(event.get("event_id") == event_id for row in data["items"] for event in row["history"]):
                raise WorkRecordConflict("event_id is already bound to another operation")
            item = self._find(data, item_id)
            req = next((row for row in item["requirements"] if row.get("requirement_id") == requirement_id), None)
            claim = req.get("schedule_claim") if type(req) is dict else None
            if (type(claim) is not dict or claim.get("state") != "unknown"
                    or req.get("status") != "unknown"):
                raise WorkRecordConflict("only an unknown persisted claim may be inspected")
            payload = {"requirement_id": requirement_id, "evidence": evidence,
                       "claim_state": "unknown", "disposition": "unresolved_claim_retained",
                       "worker_stop_proven": False, "claim_released": False}
            item["history"].append({"event_id": event_id, "kind": "schedule_claim_inspected",
                "payload": payload, "digest": digest, "idempotency_key": key,
                "idempotency_digest": digest, "at": time.time()})
            return payload, True
        return self._mutate(change)

    def revise_requirement(
        self, item_id: str, requirement_id: str, *, expected_text: str, new_text: str,
        event_id: str, idempotency_key: str | None = None,
        expected_snapshot: dict[str, Any] | None = None,
        return_commit_status: bool = False,
        allow_project_scope: bool = False,
    ) -> dict[str, Any] | tuple[dict[str, Any], bool]:
        """CAS-revise one exact requirement; project scope needs trusted coordination.

        Callers must separately prevent any reserved/unstarted dispatch and obtain
        authorization. In-progress or uncertain runs are never revised here.
        """
        key = self._require_idempotency_key(idempotency_key, event_id)
        if type(allow_project_scope) is not bool:
            raise ValueError("allow_project_scope must be an explicit boolean")
        if (type(item_id) is not str or not item_id.strip()
                or type(requirement_id) is not str or not requirement_id.strip()
                or type(expected_text) is not str or not expected_text.strip()
                or type(new_text) is not str or not new_text.strip()
                or type(event_id) is not str or not event_id.strip()):
            raise ValueError("explicit identities, expected text and new text are required")
        replacement = new_text.strip()
        if replacement == expected_text:
            raise ValueError("requirement revision must change the text")
        expected = None if expected_snapshot is None else json.loads(json.dumps(expected_snapshot))
        payload = {"requirement_id": requirement_id, "old_text": expected_text, "new_text": replacement}
        digest = _digest({"kind": "requirement_revised", "item_id": item_id, **payload})

        def change(data):
            prior = self._find_idempotent_event(data, key)
            if prior is not None:
                prior_item, event = prior
                if (prior_item.get("item_id") != item_id or event.get("kind") != "requirement_revised"
                        or event.get("idempotency_digest") != digest):
                    raise WorkRecordConflict("idempotency key reused with a different revision")
                return dict(event["payload"]), False
            if any(event.get("event_id") == event_id for item in data["items"] for event in item["history"]):
                raise WorkRecordConflict("event_id is already bound to another operation")
            if expected is not None and data != expected:
                raise WorkRecordConflict("work-record snapshot changed after authorization preview")
            item = self._find(data, item_id)
            matches = [row for row in item["requirements"] if row["requirement_id"] == requirement_id]
            if len(matches) != 1:
                raise KeyError(requirement_id)
            req = matches[0]
            if "schedule_claim" in req:
                raise WorkRecordConflict("persistent schedule claim must be inspected before revision")
            if item["scope"].get("kind") == "project" and not allow_project_scope:
                raise WorkRecordConflict("project requirement requires cross-session dispatch coordination")
            if req["text"] != expected_text:
                raise WorkRecordConflict("requirement text changed since revision expectation")
            if any(type(event.get("idempotency_key")) is str
                   and event["idempotency_key"].startswith("worker-batch-task:")
                   and event.get("payload", {}).get("requirement_id") == requirement_id
                   for event in item["history"]):
                raise WorkRecordConflict("worker batch requirement requires dispatch coordination")
            current = req.get("current_run_id")
            if req["status"] in {"in_progress", "unknown"}:
                raise WorkRecordConflict("running or uncertain requirement requires dispatch coordination")
            if current is not None:
                matches = [run for run in item["runs"] if run["requirement_id"] == requirement_id
                           and run["run_id"] == current and run["status"] == req["status"]]
                if req["status"] not in {"completed", "blocked", "stopped"} or len(matches) != 1:
                    raise WorkRecordConflict("current run requires dispatch coordination")
            scope = item["scope"]
            if ((scope.get("kind") == "project" and any(row.get("state") == "pending"
                    for row in data.get(_PUBLICATION_KEY, [])))
                    or any(row.get("state") in {"pending", "card_saved"} and row.get("latest_item_id") == item_id
                           for row in data.get(_INDEPENDENT_PUBLICATION_KEY, []))
                    or any(row.get("state") == "pending" for row in data.get(_PROJECT_SCOPED_PUBLICATION_KEY, []))):
                raise WorkRecordConflict("pending card publication requires coordination")
            req["text"] = replacement
            req["status"] = "pending"
            req.pop("current_run_id", None)
            req.pop("execution_owner_id", None)
            item["status"] = self._derive_item_status(item["requirements"])
            item["history"].append({"event_id": event_id, "kind": "requirement_revised",
                "payload": payload, "digest": digest, "idempotency_key": key,
                "idempotency_digest": digest, "at": time.time()})
            return dict(payload), True

        return self._mutate(change, return_commit_status=return_commit_status)

    def claim_scheduled_requirement(
        self, project_id: str, item_id: str, requirement_id: str, *,
        expected_revision: int, owner_id: str, reservation_id: str, parent_run_id: str,
    ) -> dict[str, Any]:
        """Persist one exclusive source-record claim; never reclaim an existing claim.

        The source WorkRecord lock serializes contenders across processes. A lost
        response is safely replayable only by the identical owner/reservation/run;
        any previously claimed execution remains unknown until explicit recovery.
        """
        values = (project_id, item_id, requirement_id, owner_id, reservation_id, parent_run_id)
        if any(type(value) is not str or not value.strip() for value in values):
            raise ValueError("complete schedule claim identities are required")
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("expected source revision is required")
        if project_id != self._bound_project_id:
            raise WorkRecordScopeError("schedule claim project does not match source Session")

        def change(data):
            item = self._find(data, item_id)
            if item.get("scope") != {"kind": "project", "project_id": project_id}:
                raise WorkRecordScopeError("schedule claim source item is not in the exact project scope")
            matches = [row for row in item["requirements"] if row.get("requirement_id") == requirement_id]
            if len(matches) != 1:
                raise KeyError(requirement_id)
            req = matches[0]
            digest = _digest({"text": req["text"]})
            claim = {
                "source_session_id": self._conversation_id, "project_id": project_id,
                "item_id": item_id, "requirement_id": requirement_id,
                "revision": expected_revision, "requirement_digest": digest,
                "owner_id": owner_id, "reservation_id": reservation_id,
                "parent_run_id": parent_run_id, "state": "claimed",
            }
            existing = req.get("schedule_claim")
            if existing is not None:
                if existing == claim:
                    return existing, False
                raise WorkRecordConflict("requirement already has a persistent claim")
            if req.get("status") not in {"queued", "pending"}:
                raise WorkRecordConflict("only untouched queued or pending requirements may be claimed")
            if data["revision"] != expected_revision:
                raise WorkRecordConflict("source WorkRecord revision changed before claim")
            req["schedule_claim"] = claim
            return claim, True

        claim = self._mutate(change)
        return json.loads(json.dumps(claim))

    def mark_schedule_claim_unknown(
        self, item_id: str, requirement_id: str, *, owner_id: str,
        reservation_id: str, parent_run_id: str,
    ) -> dict[str, Any]:
        """Mark an exact persisted claim uncertain without releasing or retrying it."""
        if any(type(value) is not str or not value.strip()
               for value in (item_id, requirement_id, owner_id, reservation_id, parent_run_id)):
            raise ValueError("complete schedule claim identities are required")

        def change(data):
            item = self._find(data, item_id)
            req = next((row for row in item["requirements"]
                        if row.get("requirement_id") == requirement_id), None)
            if req is None:
                raise KeyError(requirement_id)
            claim = req.get("schedule_claim")
            if type(claim) is not dict or any(claim.get(key) != value for key, value in (
                    ("owner_id", owner_id), ("reservation_id", reservation_id),
                    ("parent_run_id", parent_run_id))):
                raise WorkRecordConflict("schedule claim identity changed before recovery")
            if claim["state"] == "unknown":
                return claim, False
            claim["state"] = "unknown"
            return claim, True

        return json.loads(json.dumps(self._mutate(change)))

    def begin_claimed_schedule_run(
        self, project_id: str, item_id: str, requirement_id: str, *,
        run_id: str, owner_id: str, reservation_id: str, parent_run_id: str,
        expected_requirement_text: str,
    ) -> dict[str, Any]:
        """Atomically consume the exact persistent claim at worker start."""
        if any(type(value) is not str or not value.strip() for value in (
                project_id, item_id, requirement_id, run_id, owner_id,
                reservation_id, parent_run_id, expected_requirement_text)):
            raise ValueError("complete claimed schedule start identities are required")
        if project_id != self._bound_project_id:
            raise WorkRecordScopeError("schedule start project does not match source Session")
        start_id = "schedule-start-" + hashlib.sha256(
            f"{item_id}\\0{requirement_id}\\0{run_id}\\0{reservation_id}".encode("utf-8")
        ).hexdigest()

        def change(data):
            item = self._find(data, item_id)
            req = next((row for row in item["requirements"] if row["requirement_id"] == requirement_id), None)
            expected_claim = req.get("schedule_claim") if req is not None else None
            if (item.get("scope") != {"kind": "project", "project_id": project_id}
                    or type(req) is not dict or req.get("text") != expected_requirement_text
                    or type(expected_claim) is not dict
                    or any(expected_claim.get(key) != value for key, value in (
                        ("owner_id", owner_id), ("reservation_id", reservation_id),
                        ("parent_run_id", parent_run_id), ("project_id", project_id),
                        ("item_id", item_id), ("requirement_id", requirement_id),
                    ))):
                raise WorkRecordConflict("exact persistent schedule claim changed before worker start")
            if req.get("current_run_id") == run_id and req.get("status") == "in_progress":
                return req, False
            if (expected_claim.get("state") != "claimed"
                    or req.get("status") not in {"queued", "pending"}
                    or req.get("current_run_id") is not None):
                raise WorkRecordConflict("schedule requirement is no longer safely startable")
            for dependency in req.get("depends_on", []):
                target = next((row for row in item["requirements"]
                               if row.get("requirement_id") == dependency), None)
                if target is None or target.get("status") != "completed":
                    raise WorkRecordConflict("requirement dependencies are not all completed")
            by_item = {row["item_id"]: row for row in data["items"]}
            for ref in req.get("depends_on_refs", []):
                target_item = by_item.get(ref["item_id"])
                target_req = next((row for row in target_item["requirements"]
                                   if row.get("requirement_id") == ref["requirement_id"]), None) if target_item else None
                if (target_req is None or target_item.get("scope") != item.get("scope")
                        or target_req.get("status") != "completed"):
                    raise WorkRecordConflict("referenced requirement dependencies are not all completed")
            req["current_run_id"] = run_id
            req["execution_owner_id"] = _WORK_RECORD_PROCESS_INSTANCE_ID
            req["status"] = "in_progress"
            item["history"].append({
                "event_id": start_id, "kind": "requirement_run_started",
                "payload": {"run_id": run_id, "requirement_id": requirement_id,
                            "reservation_id": reservation_id},
                "digest": _digest({"run_id": run_id, "requirement_id": requirement_id,
                                   "reservation_id": reservation_id}),
                "at": time.time(),
            })
            item["status"] = self._derive_item_status(item["requirements"])
            return req, True

        return self._mutate(change)

    def begin_requirement_run(
        self, item_id: str, requirement_id: str, *, run_id: str,
        expected_current_run_id: str | None,
        expected_requirement_text: str | None = None,
    ) -> dict[str, Any]:
        """CAS-bind a run under the durable sidecar lock.

        Callers pass the current run they observed (explicit ``None`` initially).
        Same-run retries are idempotent; stale expectations cannot replace newer state.
        """
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("run_id must be non-empty")
        if expected_current_run_id is not None and (
                not isinstance(expected_current_run_id, str) or not expected_current_run_id.strip()):
            raise ValueError("expected_current_run_id must be non-empty or None")
        if expected_requirement_text is not None and (
                type(expected_requirement_text) is not str or not expected_requirement_text.strip()):
            raise ValueError("expected_requirement_text must be non-empty or None")
        run_id = run_id.strip()
        if expected_current_run_id is not None:
            expected_current_run_id = expected_current_run_id.strip()
        start_id = "run-start-" + hashlib.sha256(
            f"{item_id}\0{requirement_id}\0{run_id}".encode("utf-8")
        ).hexdigest()

        def change(data):
            item = self._find(data, item_id)
            req = next((row for row in item["requirements"] if row["requirement_id"] == requirement_id), None)
            if req is None:
                raise KeyError(requirement_id)
            if "schedule_claim" in req:
                raise WorkRecordConflict("persistent schedule claim must be inspected before run start")
            revised = any(event.get("kind") == "requirement_revised"
                          and event.get("payload", {}).get("requirement_id") == requirement_id
                          for event in item["history"])
            if ((expected_requirement_text is None and revised)
                    or (expected_requirement_text is not None and req["text"] != expected_requirement_text)):
                raise WorkRecordConflict("requirement text changed or dispatch expectation is missing")
            current = req.get("current_run_id")
            if current == run_id:
                return req, False
            local_dependencies_incomplete = any(
                next(row for row in item["requirements"] if row["requirement_id"] == dependency)["status"] != "completed"
                for dependency in req.get("depends_on", [])
            )
            by_item = {row["item_id"]: row for row in data["items"]}
            referenced_dependencies_incomplete = False
            for ref in req.get("depends_on_refs", []):
                target_item = by_item.get(ref["item_id"])
                target_req = next((row for row in target_item["requirements"]
                                   if row["requirement_id"] == ref["requirement_id"]), None) if target_item else None
                if target_req is None or target_item["scope"] != item["scope"]:
                    raise WorkRecordConflict("dependency target is missing or outside the exact scope")
                if target_req["status"] != "completed":
                    referenced_dependencies_incomplete = True
            if local_dependencies_incomplete or referenced_dependencies_incomplete:
                raise WorkRecordConflict("requirement dependencies are not all completed")
            if req.get("status") == "waiting_confirmation":
                raise WorkRecordConflict("requirement is waiting for user confirmation")
            if current != expected_current_run_id:
                raise WorkRecordStaleRun("current run changed since begin expectation")
            if any(event.get("event_id") == start_id for event in item["history"]):
                raise WorkRecordStaleRun("a superseded run cannot be started again")
            req["current_run_id"] = run_id
            req["execution_owner_id"] = _WORK_RECORD_PROCESS_INSTANCE_ID
            req["status"] = "in_progress"
            item["history"].append({
                "event_id": start_id,
                "kind": "requirement_run_started",
                "payload": {"run_id": run_id, "requirement_id": requirement_id},
                "digest": _digest({"run_id": run_id, "requirement_id": requirement_id}),
                "at": time.time(),
            })
            item["status"] = self._derive_item_status(item["requirements"])
            return req, True

        return self._mutate(change)

    def record_project_scoped_result_if_current_run(
        self, item_id: str, requirement_id: str, *, run_id: str, result_id: str,
        status: str, summary: str, verification: dict[str, Any],
        sources: list[dict[str, Any]] | None, project_id: str,
        publication_window_seconds: float = _PUBLICATION_WINDOW_SECONDS,
    ) -> dict[str, Any]:
        """Atomically commit one trusted current-dialogue worker receipt and its project-card intent.

        The work-card intent is not an execution authorization. Its project binding,
        current run, strict host receipt, source identity, and immutable projection are
        rechecked under the same WorkRecordStore lock as the result write. If the
        result is not card-projectable, the result still commits as unknown/not_verified
        but no publication intent is created.
        """
        if (type(project_id) is not str or not project_id or status != "unknown"
                or type(publication_window_seconds) not in (int, float)
                or isinstance(publication_window_seconds, bool)
                or not math.isfinite(publication_window_seconds)
                or publication_window_seconds <= 0
                or publication_window_seconds > _PUBLICATION_WINDOW_SECONDS):
            raise ValueError("project-scoped publication identity/window is invalid")
        return self._record_result_if_current_run(
            item_id, requirement_id, run_id=run_id, result_id=result_id,
            status=status, summary=summary, verification=verification, sources=sources,
            server_publication_window=None, independent_publication_window=None,
            project_scoped_project_id=project_id,
            project_scoped_window_seconds=float(publication_window_seconds),
        )

    def record_result_if_current_run(
        self, item_id: str, requirement_id: str, *, run_id: str, result_id: str,
        status: str, summary: str, verification: dict[str, Any],
        sources: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Persist a normal run result atomically, without creating a card intent."""
        return self._record_result_if_current_run(
            item_id, requirement_id, run_id=run_id, result_id=result_id,
            status=status, summary=summary, verification=verification, sources=sources,
            server_publication_window=None, independent_publication_window=None,
            project_scoped_project_id=None, project_scoped_window_seconds=None,
        )

    def record_server_settlement_if_current_run(
        self, item_id: str, requirement_id: str, *, run_id: str, result_id: str,
        status: str, summary: str, verification: dict[str, Any],
        sources: list[dict[str, Any]] | None = None,
        publication_window_seconds: float = _PUBLICATION_WINDOW_SECONDS,
        _commit_feedback: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Atomically persist a verified host settlement and its project-card intent.

        This entry point is used only by WorkRecordSettlementAdapter after the
        server has matched Session/item/requirement/run and a terminal lifecycle receipt.
        """
        if (type(publication_window_seconds) not in (int, float)
                or not math.isfinite(publication_window_seconds)
                or publication_window_seconds <= 0
                or publication_window_seconds > _PUBLICATION_WINDOW_SECONDS):
            raise ValueError("publication merge window must be finite and at most 30 seconds")
        if status != "unknown":
            raise ValueError("server settlement publication requires unknown completion status")
        return self._record_result_if_current_run(
            item_id, requirement_id, run_id=run_id, result_id=result_id,
            status=status, summary=summary, verification=verification, sources=sources,
            server_publication_window=float(publication_window_seconds),
            independent_publication_window=None, project_scoped_project_id=None,
            project_scoped_window_seconds=None, _commit_feedback=_commit_feedback,
        )

    def record_verified_worker_settlement_if_current_run(
        self, item_id: str, requirement_id: str, *, run_id: str, result_id: str,
        summary: str, verification: dict[str, Any], sources: list[dict[str, Any]] | None = None,
        publication_window_seconds: float = _PUBLICATION_WINDOW_SECONDS,
        publish_project_update: bool = True, expected_project_id: str | None = None,
        expected_requirement_text: str | None = None,
    ) -> dict[str, Any]:
        """CAS the verified worker result and optional publication intent together."""
        if (type(publication_window_seconds) not in (int, float)
                or not math.isfinite(publication_window_seconds)
                or publication_window_seconds <= 0 or publication_window_seconds > _PUBLICATION_WINDOW_SECONDS):
            raise ValueError("server publication window is invalid")
        return self._record_result_if_current_run(
            item_id, requirement_id, run_id=run_id, result_id=result_id,
            status="completed", summary=summary, verification=verification, sources=sources,
            server_publication_window=float(publication_window_seconds) if publish_project_update else None,
            independent_publication_window=None, project_scoped_project_id=None,
            project_scoped_window_seconds=None, verified_expected=(expected_project_id, expected_requirement_text),
        )

    def record_project_self_reported_worker_settlement_if_current_run(
        self, item_id: str, requirement_id: str, *, run_id: str, result_id: str,
        verification: dict[str, Any], expected_project_id: str, expected_requirement_text: str,
        publish_project_update: bool = False,
        publication_window_seconds: float = _PUBLICATION_WINDOW_SECONDS,
    ) -> dict[str, Any]:
        """Atomically record an unverified self-report and optional project-card intent."""
        if (type(publication_window_seconds) not in (int, float)
                or isinstance(publication_window_seconds, bool)
                or not math.isfinite(publication_window_seconds)
                or publication_window_seconds <= 0
                or publication_window_seconds > _PUBLICATION_WINDOW_SECONDS):
            raise ValueError("self-report publication merge window must be finite and at most 30 seconds")
        if type(publish_project_update) is not bool:
            raise ValueError("self-report publication choice must be explicit")
        return self._record_result_if_current_run(
            item_id, requirement_id, run_id=run_id, result_id=result_id, status="unknown",
            summary="User-authorized project self-reports that one pytest target passed; requirement remains unverified.",
            verification=verification, sources=[],
            server_publication_window=float(publication_window_seconds) if publish_project_update else None,
            independent_publication_window=None, project_scoped_project_id=None,
            project_scoped_window_seconds=None, verified_expected=(expected_project_id, expected_requirement_text),
        )
    def record_blocked_worker_settlement_if_current_run(
        self, item_id: str, requirement_id: str, *, run_id: str, result_id: str,
        summary: str, verification: dict[str, Any], sources: list[dict[str, Any]] | None = None,
        publication_window_seconds: float = _PUBLICATION_WINDOW_SECONDS,
    ) -> dict[str, Any]:
        """CAS a host-bound blocked result and its project publication intent."""
        if (type(publication_window_seconds) not in (int, float)
                or not math.isfinite(publication_window_seconds)
                or publication_window_seconds <= 0 or publication_window_seconds > _PUBLICATION_WINDOW_SECONDS):
            raise ValueError("server publication window is invalid")
        return self._record_result_if_current_run(
            item_id, requirement_id, run_id=run_id, result_id=result_id,
            status="blocked", summary=summary, verification=verification, sources=sources,
            server_publication_window=float(publication_window_seconds),
            independent_publication_window=None, project_scoped_project_id=None,
            project_scoped_window_seconds=None,
        )

    def record_independent_server_settlement_if_current_run(
        self, item_id: str, requirement_id: str, *, run_id: str, result_id: str,
        status: str, summary: str, verification: dict[str, Any],
        sources: list[dict[str, Any]] | None = None,
        publication_window_seconds: float = _PUBLICATION_WINDOW_SECONDS,
        _commit_feedback: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Atomically persist one exact independent-item settlement and card intent."""
        if (type(publication_window_seconds) not in (int, float)
                or isinstance(publication_window_seconds, bool)
                or not math.isfinite(publication_window_seconds)
                or publication_window_seconds <= 0
                or publication_window_seconds > _PUBLICATION_WINDOW_SECONDS):
            raise ValueError("independent publication merge window must be finite and at most 30 seconds")
        if status != "unknown":
            raise ValueError("independent server settlement requires unknown completion status")
        return self._record_result_if_current_run(
            item_id, requirement_id, run_id=run_id, result_id=result_id,
            status=status, summary=summary, verification=verification, sources=sources,
            server_publication_window=None,
            independent_publication_window=float(publication_window_seconds),
            project_scoped_project_id=None, project_scoped_window_seconds=None,
            _commit_feedback=_commit_feedback,
        )

    def _record_result_if_current_run(
        self, item_id: str, requirement_id: str, *, run_id: str, result_id: str,
        status: str, summary: str, verification: dict[str, Any],
        sources: list[dict[str, Any]] | None,
        server_publication_window: float | None,
        independent_publication_window: float | None,
        project_scoped_project_id: str | None,
        project_scoped_window_seconds: float | None,
        _commit_feedback: dict[str, Any] | None = None,
        verified_expected: tuple[str | None, str | None] | None = None,
    ) -> dict[str, Any]:
        if status not in {"completed", "blocked", "stopped", "unknown", "in_progress"} or not all((run_id, result_id)):
            raise ValueError("invalid result identity or status")
        if not isinstance(verification, dict) or not isinstance(summary, str):
            raise ValueError("invalid result payload")
        if sources is not None:
            self._validate_source_refs(sources)
        run = {"run_id": run_id, "requirement_id": requirement_id, "status": status,
               "result_summary": summary, "verification": verification,
               "sources": sources or [], "result_id": result_id}
        digest = _digest(run)

        def change(data):
            item = self._find(data, item_id)
            req = next((row for row in item["requirements"] if row["requirement_id"] == requirement_id), None)
            if req is None:
                raise KeyError(requirement_id)
            if req.get("current_run_id") != run_id:
                raise WorkRecordStaleRun("result run is not the current requirement run")
            if verified_expected is not None:
                project_id, requirement_text = verified_expected
                if (not project_id or not requirement_text or req.get("text") != requirement_text
                        or item.get("scope") != {"kind": "project", "project_id": project_id}
                        or req.get("source_ref") not in (None, {"kind": "project", "id": project_id})
                        or any(row.get("run_id") == run_id and row.get("requirement_id") == requirement_id
                               for row in item["runs"])):
                    raise WorkRecordStaleRun("verified worker binding is changed or already settled")
            prior = self._event_by_id(item, result_id)
            result_changed = False
            if prior:
                if prior.get("digest") != digest:
                    raise WorkRecordConflict("result_id reused with different content")
                prior_run = next((row for row in item["runs"] if row.get("result_id") == result_id), None)
                if prior_run is None:
                    raise WorkRecordCorrupt("result history has no matching run record")
                stored_run = prior_run
            else:
                if req.get("status") != "in_progress":
                    raise WorkRecordStaleRun("result cannot settle a requirement that is no longer in progress")
                item["runs"].append(run)
                req["status"] = status
                claim = req.get("schedule_claim")
                host_execution = verification.get("host_execution") if type(verification) is dict else None
                if (type(claim) is dict and claim.get("state") in {"claimed", "unknown"}
                        and type(host_execution) is dict
                        and host_execution.get("reservation_id") == claim.get("reservation_id")
                        and host_execution.get("parent_run_id") == claim.get("parent_run_id")):
                    # The terminal current-run CAS also consumes the scheduling
                    # marker; settled requirements must not stay permanently claimed.
                    req.pop("schedule_claim", None)
                item["history"].append({
                    "event_id": result_id, "kind": "result_recorded",
                    "payload": {"run_id": run_id, "requirement_id": requirement_id},
                    "digest": digest, "at": time.time(),
                })
                item["status"] = self._derive_item_status(item["requirements"])
                stored_run = run
                result_changed = True
            intent_changed = False
            # A result and its publication intent are committed in this same CAS.
            # Replaying an already-recorded result must therefore be a no-op for
            # publication state; otherwise an older merged run can rewind latest_*.
            if result_changed and server_publication_window is not None:
                intent_changed = self._upsert_server_publication_intent(
                    data, item, req, stored_run,
                    window_seconds=server_publication_window,
                )
            if result_changed and independent_publication_window is not None:
                intent_changed = self._upsert_independent_server_publication_intent(
                    data, item, req, stored_run, window_seconds=independent_publication_window,
                ) or intent_changed
            if result_changed and project_scoped_project_id is not None:
                intent_changed = self._upsert_project_scoped_card_intent(
                    data, item, req, stored_run, project_id=project_scoped_project_id,
                    window_seconds=project_scoped_window_seconds or _PUBLICATION_WINDOW_SECONDS,
                ) or intent_changed
            if type(_commit_feedback) is dict:
                _commit_feedback.clear()
                _commit_feedback.update({
                    "result_changed": result_changed,
                    "intent_changed": intent_changed,
                })
            return stored_run, result_changed or intent_changed

        return self._mutate(change)

    def _upsert_independent_server_publication_intent(
        self, data: dict[str, Any], item: dict[str, Any], requirement: dict[str, Any],
        run: dict[str, Any], *, window_seconds: float,
    ) -> bool:
        if item.get("scope") != {"kind": "independent", "object_ref": None}:
            raise WorkRecordScopeError("independent server settlement scope changed")
        host_execution = run.get("verification", {}).get("host_execution")
        if (run.get("status") != "unknown" or type(host_execution) is not dict
                or type(host_execution.get("sequence")) is not int or host_execution["sequence"] < 1
                or run.get("result_id") != _server_settlement_result_id(
                    self._conversation_id, item["item_id"], requirement["requirement_id"], run["run_id"],
                )):
            raise WorkRecordConflict("independent server settlement facts do not match the host-bound result")
        intents = data.setdefault(_INDEPENDENT_PUBLICATION_KEY, [])
        if type(intents) is not list:
            raise WorkRecordCorrupt("independent publication intent collection is invalid")
        item_id = item["item_id"]
        requirement_id = requirement["requirement_id"]
        run_id = run["run_id"]
        result_id = run["result_id"]
        for intent in intents:
            if (intent.get("latest_item_id") == item_id and intent.get("latest_run_id") == run_id):
                if (intent.get("latest_requirement_id") != requirement_id
                        or intent.get("result_id") != result_id):
                    raise WorkRecordConflict("independent settlement run conflicts with its publication intent")
                return False
        pending = next((row for row in reversed(intents)
                        if row["latest_item_id"] == item_id and row["state"] == "pending"), None)
        if pending is None and len(intents) >= 4096:
            raise WorkRecordWriteError("independent publication intent capacity reached; result not committed")
        now = _monotonic()
        if pending is None:
            intent_id = str(uuid.uuid4())
            generation = max((row["generation"] for row in intents
                              if row["latest_item_id"] == item_id), default=0) + 1
            intents.append({
                "intent_id": intent_id,
                "session_id": self._conversation_id,
                "first_run_id": run_id,
                "latest_item_id": item_id,
                "latest_requirement_id": requirement_id,
                "latest_run_id": run_id,
                "result_id": result_id,
                "generation": generation,
                "request_id": independent_publication_card_request_id(
                    self._conversation_id, item_id, intent_id, generation,
                ),
                "card_id": None,
                "state": "pending",
                "window_owner_id": _PUBLICATION_PROCESS_INSTANCE_ID,
                "window_started_monotonic": now,
                "deadline_monotonic": now + window_seconds,
                "window_seconds": window_seconds,
            })
            return True
        generation = pending["generation"] + 1
        pending.update({
            "latest_requirement_id": requirement_id,
            "latest_run_id": run_id,
            "result_id": result_id,
            "generation": generation,
            "request_id": independent_publication_card_request_id(
                self._conversation_id, item_id, pending["intent_id"], generation,
            ),
            "card_id": None,
        })
        return True

    def _upsert_project_scoped_card_intent(
        self, data: dict[str, Any], item: dict[str, Any], requirement: dict[str, Any],
        run: dict[str, Any], *, project_id: str, window_seconds: float,
    ) -> bool:
        scope = item.get("scope")
        source = requirement.get("source_ref")
        if (type(scope) is not dict or scope.get("kind") != "independent"
                or type(source) is not dict or source.get("kind") != "project"):
            return False
        source_project_id = source.get("id")
        from project_mode import ProjectModeError, normalize_project_id
        try:
            canonical_project_id = normalize_project_id(project_id)
        except (ProjectModeError, TypeError, ValueError) as exc:
            raise WorkRecordScopeError("worker result project identity is invalid") from exc
        if source_project_id != canonical_project_id:
            raise WorkRecordScopeError("worker result project does not match its requirement source")
        from work_card_project_scope import (
            _build_project_snapshot, _valid_receipt, project_scoped_snapshot_request_id,
        )
        if not _valid_receipt(
            requirement, run, session_id=self._conversation_id,
            item_id=item["item_id"], project_id=project_id,
        ):
            # Preserve the ordinary unknown/not_verified source result, but do not
            # manufacture a projection intent without the exact host receipt.
            return False
        source_result_id = run.get("result_id")
        if type(source_result_id) is not str or _PROJECT_SCOPED_RESULT_ID.fullmatch(source_result_id) is None:
            return False

        snapshot = _build_project_snapshot(self, self._conversation_id, project_id, data["items"])
        from work_record_cards import bind_work_card_change_focus
        snapshot = bind_work_card_change_focus(snapshot, {
            "state": "pending", "session_id": self._conversation_id, "project_id": project_id,
            "latest_item_id": item["item_id"],
            "latest_requirement_id": requirement["requirement_id"],
            "latest_run_id": run["run_id"], "latest_result_id": source_result_id,
        })
        if snapshot.completeness == "complete" and snapshot.cards:
            request_id = project_scoped_snapshot_request_id(snapshot)
        else:
            request_id = str(uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"my-agent-project-scoped-pending:{self._conversation_id}:{project_id}:{source_result_id}",
            ))

        intents = data.setdefault(_PROJECT_SCOPED_PUBLICATION_KEY, [])
        self._validate_project_scoped_card_intents(data)
        for row in intents:
            if row["project_id"] == project_id and source_result_id in row.get("source_result_ids", []):
                return False
        source_result_ids = [source_result_id]
        pending = next((row for row in reversed(intents)
                        if row["project_id"] == project_id and row["state"] == "pending"), None)
        if pending is not None:
            if "source_result_ids" not in pending:
                pending["source_result_ids"] = []
            if source_result_id not in pending["source_result_ids"]:
                if len(pending["source_result_ids"]) >= _MAX_EVENTS:
                    raise WorkRecordWriteError("project-scoped source receipt limit reached")
                pending["source_result_ids"].append(source_result_id)
            pending.update({
                "latest_item_id": item["item_id"],
                "latest_requirement_id": requirement["requirement_id"],
                "latest_run_id": run["run_id"],
                "latest_result_id": source_result_id,
            })
            if pending["request_id"] != request_id:
                pending["request_id"] = request_id
            return True
        project_rows = [row for row in intents if row["project_id"] == project_id]
        generation = max((row["generation"] for row in project_rows), default=0) + 1
        if len(intents) >= 4096:
            raise WorkRecordWriteError("project-scoped publication intent capacity reached; result not committed")
        now = _monotonic()
        fresh = {
            "session_id": self._conversation_id, "project_id": project_id,
            "request_id": request_id, "generation": generation,
            "state": "pending", "card_id": None, "window_seconds": window_seconds,
            "window_started_monotonic": now, "deadline_monotonic": now + window_seconds,
            "boot_marker": time.time() - now, "source_result_ids": source_result_ids,
            "latest_item_id": item["item_id"],
            "latest_requirement_id": requirement["requirement_id"],
            "latest_run_id": run["run_id"],
            "latest_result_id": source_result_id,
        }
        intents.append(fresh)
        return True

    def _upsert_server_publication_intent(
        self, data: dict[str, Any], item: dict[str, Any], requirement: dict[str, Any],
        run: dict[str, Any], *, window_seconds: float,
    ) -> bool:
        scope = item.get("scope")
        if type(scope) is not dict or scope.get("kind") != "project":
            if run.get("status") in {"completed", "blocked"}:
                raise WorkRecordScopeError("worker publication requires project scope")
            return False
        project_id = scope.get("project_id")
        if (type(project_id) is not str or not project_id
                or project_id != self._bound_project_id):
            raise WorkRecordScopeError("server settlement project scope changed")
        from project_mode import ProjectModeError, normalize_project_id
        try:
            if normalize_project_id(project_id) != project_id:
                raise WorkRecordScopeError("server settlement project id is not canonical")
        except ProjectModeError as exc:
            raise WorkRecordScopeError("server settlement project id is invalid") from exc
        if not _valid_server_publication_result(
                self._conversation_id, item["item_id"], requirement["requirement_id"], run, project_id):
            raise WorkRecordConflict("server settlement facts do not match the host-bound result")
        intents = data.setdefault(_PUBLICATION_KEY, [])
        if type(intents) is not list:
            raise WorkRecordCorrupt("publication intent collection is invalid")
        item_id = item["item_id"]
        requirement_id = requirement["requirement_id"]
        run_id = run["run_id"]
        for intent in intents:
            if (intent.get("state") == "published" and intent.get("latest_item_id") == item_id
                    and intent.get("latest_requirement_id") == requirement_id
                    and intent.get("latest_run_id") == run_id):
                return False
        pending = next((intent for intent in intents if intent.get("state") == "pending"), None)
        if pending is not None and pending.get("latest_run_id") == run_id:
            if (pending.get("latest_item_id") != item_id
                    or pending.get("latest_requirement_id") != requirement_id
                    or pending.get("result_id") != run["result_id"]):
                raise WorkRecordConflict("server run conflicts with the pending project-card intent")
            return False
        now = _monotonic()
        if pending is None:
            intent_id = str(uuid.uuid4())
            generation = 1
            intent = {
                "intent_id": intent_id,
                "session_id": self._conversation_id,
                "project_id": project_id,
                "first_run_id": run_id,
                "latest_item_id": item_id,
                "latest_requirement_id": requirement_id,
                "latest_run_id": run_id,
                "result_id": run["result_id"],
                "generation": generation,
                "request_id": publication_card_request_id(
                    self._conversation_id, project_id, intent_id, generation,
                ),
                "card_id": None,
                "state": "pending",
                "legacy_source_digest": None,
                "window_owner_id": _PUBLICATION_PROCESS_INSTANCE_ID,
                "window_started_monotonic": now,
                "deadline_monotonic": now + window_seconds,
                "window_seconds": window_seconds,
            }
            intents.append(intent)
        else:
            generation = pending["generation"] + 1
            pending.update({
                "latest_item_id": item_id,
                "latest_requirement_id": requirement_id,
                "latest_run_id": run_id,
                "result_id": run["result_id"],
                "generation": generation,
                "request_id": publication_card_request_id(
                    self._conversation_id, project_id, pending["intent_id"], generation,
                ),
                "card_id": None,
            })
        # A matched host-blocked terminal is immediately due. Persist the
        # existing deadline fields (not an ephemeral timer flag) in the same CAS,
        # so a pre-existing coalescing timer and crash recovery both see it.
        if run.get("status") == "blocked":
            target = intent if pending is None else pending
            due = min(now, target["deadline_monotonic"])
            if due <= 0:
                raise WorkRecordWriteError("immediate publication clock unavailable")
            start = max(0.0, due - min(float(window_seconds), due / 2.0))
            target.update({"window_started_monotonic": start,
                           "deadline_monotonic": due,
                           "window_seconds": due - start})
        return True

    def append_assistant_reply_excerpt_if_current_run(
        self, item_id: str, requirement_id: str, *, run_id: str, sequence: int,
        finish_reason: str | None, text: str, source_availability: str,
        sources: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], bool]:
        """Append one bounded, source-labelled assistant excerpt to an exact unknown run.

        This never changes the requirement/run outcome or verification status. The
        deterministic excerpt identity makes retries idempotent; a changed excerpt
        for the same bound run is a conflict rather than an overwrite.
        """
        from evolution.context import normalize_host_run_id

        normalized_run_id = normalize_host_run_id(run_id) if type(run_id) is str else ""
        if (not normalized_run_id or type(item_id) is not str or not item_id
                or type(requirement_id) is not str or not requirement_id
                or type(sequence) is not int or sequence < 1
                or type(finish_reason) not in (str, type(None))
                or (finish_reason is not None and finish_reason not in {
                    "completed", "stop", "cancelled", "timeout", "error", "paused", "unknown", "stopped",
                    "runaway_paused", "task_paused", "segment_cap_pause", "total_cap_exceeded",
                })
                or type(text) is not str or not text.strip()
                or type(source_availability) is not str
                or source_availability not in {"available", "unavailable"}
                or type(sources) is not list or len(sources) > MAX_ASSISTANT_REPLY_EXCERPT_SOURCES):
            raise ValueError("assistant reply excerpt binding or fields are invalid")
        if len(text.encode("utf-8")) > MAX_ASSISTANT_REPLY_EXCERPT_BYTES:
            raise ValueError("assistant reply excerpt exceeds the size limit")
        self._validate_source_refs(sources)
        if ((source_availability == "available" and not sources)
                or (source_availability == "unavailable" and sources)):
            raise ValueError("source availability does not match source references")
        safe_sources = [dict(source) for source in sources]
        excerpt_id = str(uuid.uuid5(
            uuid.NAMESPACE_URL,
            "work-record-assistant-reply-excerpt:" + self._conversation_id + ":"
            + item_id + ":" + requirement_id + ":" + normalized_run_id,
        ))
        excerpt = {
            "excerpt_id": excerpt_id, "text": text,
            "source_availability": source_availability, "sources": safe_sources,
        }

        def change(data):
            item = self._find(data, item_id)
            requirement = next((row for row in item["requirements"]
                                if row.get("requirement_id") == requirement_id), None)
            current_run_id = normalize_host_run_id(requirement.get("current_run_id")) if requirement is not None else ""
            if requirement is None or current_run_id != normalized_run_id:
                raise WorkRecordStaleRun("assistant excerpt run is no longer current")
            matching_runs = [row for row in item["runs"]
                             if normalize_host_run_id(row.get("run_id")) == normalized_run_id
                             and row.get("requirement_id") == requirement_id]
            if len(matching_runs) != 1:
                raise WorkRecordStaleRun("assistant excerpt has no unique current run result")
            run = matching_runs[0]
            verification = run.get("verification")
            host_execution = verification.get("host_execution") if type(verification) is dict else None
            if (run.get("status") != "unknown" or type(verification) is not dict
                    or verification.get("status") != "not_verified"
                    or type(host_execution) is not dict
                    or host_execution.get("sequence") != sequence
                    or host_execution.get("finish_reason") != finish_reason):
                raise WorkRecordStaleRun("assistant excerpt receipt does not match an unverified current run")
            previous_excerpt = run.get("assistant_reply_excerpt")
            if previous_excerpt is not None:
                if previous_excerpt == excerpt:
                    return previous_excerpt, False
                raise WorkRecordConflict("assistant excerpt already exists with different content or sources")
            if any(event.get("event_id") == excerpt_id
                   for row in data["items"] for event in row["history"]):
                raise WorkRecordConflict("assistant excerpt identity is already in use")
            run["assistant_reply_excerpt"] = excerpt
            item["history"].append({
                "event_id": excerpt_id, "kind": "assistant_reply_excerpt_added",
                "payload": {
                    "requirement_id": requirement_id, "run_id": run["run_id"],
                    "source_availability": source_availability,
                },
                "digest": _digest(excerpt), "at": time.time(),
            })
            return excerpt, True

        return self._mutate(change, return_commit_status=True)

    def record_result(self, item_id: str, requirement_id: str, *, run_id: str, result_id: str, status: str, summary: str, verification: dict[str, Any], sources: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        """Legacy write for requirements not yet enrolled in run-CAS tracking.

        Once a requirement has ``current_run_id``, all writes must go through
        ``record_result_if_current_run``; this guard is rechecked under the file lock.
        """
        if status not in {"completed", "blocked", "stopped", "unknown", "in_progress"} or not all((run_id, result_id)):
            raise ValueError("invalid result identity or status")
        if sources is not None:
            self._validate_source_refs(sources)
        run = {"run_id": run_id, "requirement_id": requirement_id, "status": status, "result_summary": summary, "verification": verification, "sources": sources or [], "result_id": result_id}
        digest = _digest(run)
        def change(data):
            item = self._find(data, item_id)
            req = next((row for row in item["requirements"] if row["requirement_id"] == requirement_id), None)
            if req is None:
                raise KeyError(requirement_id)
            if "current_run_id" in req:
                raise WorkRecordStaleRun("requirement is CAS-managed; use record_result_if_current_run")
            prior = self._event_by_id(item, result_id)
            if prior:
                if prior["digest"] != digest: raise WorkRecordConflict("result_id reused with different content")
                return next(r for r in item["runs"] if r["result_id"] == result_id), False
            item["runs"].append(run)
            req = next(r for r in item["requirements"] if r["requirement_id"] == requirement_id)
            req["status"] = status
            item["history"].append({"event_id": result_id, "kind": "result_recorded", "payload": {"run_id": run_id, "requirement_id": requirement_id}, "digest": digest, "at": time.time()})
            item["status"] = self._derive_item_status(item["requirements"])
            return run, True
        return self._mutate(change)

    @staticmethod
    def _derive_item_status(requirements: list[dict[str, Any]]) -> str:
        if not requirements:
            return "active"
        statuses = {requirement["status"] for requirement in requirements}
        if "in_progress" in statuses:
            return "in_progress"
        if "unknown" in statuses:
            return "unknown"
        if "blocked" in statuses:
            return "blocked"
        if "waiting_confirmation" in statuses:
            return "waiting_confirmation"
        if "stopped" in statuses:
            return "stopped"
        if "queued" in statuses:
            return "queued"
        if statuses == {"completed"}:
            return "completed"
        return "active"

    @staticmethod
    def _find(data, item_id):
        item = next((candidate for candidate in data["items"] if candidate.get("item_id") == item_id), None)
        if item is None: raise KeyError(item_id)
        return item

    @staticmethod
    def _event_by_id(item, event_id):
        return next((event for event in item["history"] if event.get("event_id") == event_id), None)




    def associate_posthoc_run_if_authorized(
        self, item_id: str, requirement_id: str, *, session_id: str,
        request_id: str, run_id: str, sequence: int,
        finish_reason: str | None = None,
    ) -> dict[str, Any]:
        """CAS-associate caller-supplied host identifiers with an authorized requirement.

        This API cannot authenticate ``run_id``/``sequence`` or verify that the host
        run actually reached a terminal state. Only a trusted host that has checked
        the actual terminal lifecycle and matched it to this Session and target may
        call it; stored lifecycle fields remain caller-supplied, not API-verified.
        This is not a run-start API: it never writes in-progress or a
        ``requirement_run_started`` event. Requirement success and side effects remain
        unknown and unverified.
        """
        if type(session_id) is not str or session_id != self._conversation_id:
            raise WorkRecordScopeError("posthoc host run belongs to another Session")
        request_id = self._require_idempotency_key(request_id)
        if (type(run_id) is not str or not run_id or run_id != run_id.strip()
                or type(sequence) is not int or sequence < 1
                or (finish_reason is not None and (type(finish_reason) is not str or finish_reason not in {
                    "completed", "cancelled", "timeout", "error", "paused", "unknown",
                }))):
            raise ValueError("posthoc host run identity or lifecycle facts are invalid")

        result_id = _server_settlement_result_id(
            self._conversation_id, item_id, requirement_id, run_id,
        )
        host_execution: dict[str, Any] = {"sequence": sequence}
        if finish_reason is not None:
            host_execution["finish_reason"] = finish_reason
        run = {
            "run_id": run_id,
            "requirement_id": requirement_id,
            "status": "unknown",
            "result_summary": "Posthoc host run associated; requirement outcome remains unknown and unverified.",
            "verification": {
                "status": "not_verified",
                "evidence_refs": [],
                "host_execution": host_execution,
            },
            "sources": [],
            "result_id": result_id,
        }
        digest = _digest(run)

        def change(data):
            item = self._find(data, item_id)
            req = next((row for row in item["requirements"]
                        if row["requirement_id"] == requirement_id), None)
            if req is None:
                raise KeyError(requirement_id)
            keyed_events = [(row, event) for row in data["items"] for event in row["history"]
                            if event.get("idempotency_key") == request_id]
            authorizations = [event for row, event in keyed_events
                              if row is item and event.get("kind") == "requirement_added"
                              and event.get("payload", {}).get("requirement_id") == requirement_id]
            if len(keyed_events) > 1:
                raise WorkRecordCorrupt("posthoc request identity is duplicated in the sidecar")
            if len(authorizations) != 1:
                raise WorkRecordConflict("request_id does not authorize this exact requirement")
            authorization = authorizations[0]
            expected_authorization_digest = _digest({
                "kind": "create_item_with_requirement", "title": item["title"],
                "scope": item["scope"], "requirement": req["text"],
            })
            if authorization.get("idempotency_digest") != expected_authorization_digest:
                raise WorkRecordCorrupt("posthoc requirement authorization digest is invalid")

            current = req.get("current_run_id")
            if current == run_id:
                prior = self._event_by_id(item, result_id)
                stored = next((row for row in item["runs"] if row.get("result_id") == result_id), None)
                if prior is None and stored is None:
                    raise WorkRecordConflict("host run was started through a different lifecycle")
                if prior is None or stored is None:
                    raise WorkRecordCorrupt("posthoc run association is incomplete")
                if prior.get("digest") != digest or stored != run:
                    raise WorkRecordConflict("posthoc host run facts changed on replay")
                return stored, False
            if current is not None:
                raise WorkRecordStaleRun("requirement already has a different current run")
            if req.get("status") != "pending":
                raise WorkRecordConflict("posthoc association requires an untouched pending requirement")
            if any(candidate.get("run_id") == run_id
                   for row in data["items"] for candidate in row["runs"]):
                raise WorkRecordConflict("host run is already associated with another result")

            req["current_run_id"] = run_id
            req["status"] = "unknown"
            item["runs"].append(run)
            item["history"].append({
                "event_id": result_id,
                "kind": "result_recorded",
                "payload": {"run_id": run_id, "requirement_id": requirement_id},
                "digest": digest,
                "at": time.time(),
            })
            item["status"] = self._derive_item_status(item["requirements"])
            return run, True

        return self._mutate(change)


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()






