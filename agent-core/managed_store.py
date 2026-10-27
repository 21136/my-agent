"""Host-only empty-store lease fence for a verified managed production context.

This module is a low-level mechanism. It does not authorize application launch,
register a runtime, or claim that existing application writers use this fence.
"""

from __future__ import annotations

import contextlib
import ctypes
import hashlib
import json
import os
import re
import secrets
import sqlite3
import stat
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

FORMAT = 1
STORE_MODE = "managed_host_store_v1"
BINDING_MODE = "managed_production_binding_v1"
NAMESPACE_MODE = "managed_project_local_v1"
SCHEMA = "managed_empty_store_v1"
CONTROL_NAME = "managed-store-control.sqlite3"
LOCK_NAME = "managed-store.lock"
DATA_DB_NAME = "managed-data.sqlite3"
MAX_BINDING_BYTES = 128 * 1024
MAX_MARKER_BYTES = 16 * 1024
MAX_RELATIVE_PATH_BYTES = 512
MAX_WRITE_BYTES = 4 * 1024 * 1024
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$", re.IGNORECASE)
GIT_ID_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$", re.IGNORECASE)


class ManagedStoreError(RuntimeError):
    """A store identity, operation, or filesystem invariant was rejected."""


class StoreBusyError(ManagedStoreError):
    """Another process currently holds the store lifetime lock."""


class MaintenanceRequired(ManagedStoreError):
    """An uncertain or partial state requires explicit maintenance."""


def _fail(message: str = "managed store invariant rejected") -> None:
    raise ManagedStoreError(message)


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ManagedStoreError("managed store record is not canonical JSON data") from exc


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("managed store record contains duplicate fields")
        result[key] = value
    return result


def _parse_record(path: Path, limit: int) -> dict[str, Any]:
    info = _lstat_regular(path, limit)
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1 or opened.st_size != info.st_size:
            _fail("managed store identity record changed while opening")
        chunks = bytearray()
        while len(chunks) <= limit:
            block = os.read(descriptor, min(4096, limit + 1 - len(chunks)))
            if not block:
                break
            chunks.extend(block)
        raw = bytes(chunks)
    finally:
        os.close(descriptor)
    if len(raw) > limit or not raw.endswith(b"\n") or b"\n" in raw[:-1] or b"\r" in raw:
        _fail("managed store identity record exceeds its canonical frame")
    try:
        value = json.loads(
            raw[:-1].decode("utf-8", "strict"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda _: _fail("managed store record contains a non-finite number"),
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ManagedStoreError("managed store identity record is invalid JSON") from exc
    if type(value) is not dict or _canonical(value) + b"\n" != raw:
        _fail("managed store identity record is not canonical")
    return value


def _lstat_regular(path: Path, limit: int | None = None) -> os.stat_result:
    try:
        info = path.lstat()
    except FileNotFoundError:
        raise
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        _fail("managed store file must be an ordinary single-link file")
    if limit is not None and info.st_size > limit:
        _fail("managed store identity file exceeds its size bound")
    return info


def _ordinary_directory(path: Path) -> None:
    try:
        info = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise ManagedStoreError("managed store directory is unavailable") from exc
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_nlink < 1:
        _fail("managed store path must be an ordinary directory")
    if os.path.normcase(str(resolved)) != os.path.normcase(str(path)):
        _fail("managed store path alias rejected")


def _absolute_canonical(value: Any, label: str) -> Path:
    if type(value) is not str or not value or not Path(value).is_absolute():
        _fail(f"{label} must be an absolute path")
    path = Path(value)
    if str(path) != value or any(part in (".", "..") for part in path.parts):
        _fail(f"{label} is not a normalized absolute path")
    return path


def _uuid(value: Any, label: str) -> str:
    if type(value) is not str or not UUID_RE.fullmatch(value):
        _fail(f"{label} must be a canonical UUID")
    return str(uuid.UUID(value))


def _hash(value: Any, label: str) -> str:
    if type(value) is not str or not HASH_RE.fullmatch(value):
        _fail(f"{label} must be a lowercase SHA-256 digest")
    return value


def _exact(value: Any, keys: set[str], label: str) -> None:
    if type(value) is not dict or set(value) != keys:
        _fail(f"{label} fields do not match the production schema")


def _fixed_paths(root: Path, release_id: str) -> dict[str, str]:
    return {
        "launcher": str(root / "launcher"),
        "state": str(root / "state"),
        "hostState": str(root / "host" / "state"),
        "hostProfile": str(root / "host" / "profile"),
        "hostWorkspace": str(root / "host" / "workspace"),
        "data": str(root / "data" / "store"),
        "code": str(root / "releases" / release_id / "app"),
    }


def _validate_source_roots(roots: Any, marker_roots: dict[str, Any]) -> None:
    sides = {"stable", "sandbox"}
    if type(roots) is not dict or set(roots) != sides:
        _fail("production source root sides are invalid")
    common_dirs: list[Path] = []
    git_dirs: list[Path] = []
    for side in ("stable", "sandbox"):
        record = roots[side]
        _exact(record, {"worktreeRoot", "gitDir", "commonDir", "branch"}, f"{side} source root")
        worktree = _absolute_canonical(record["worktreeRoot"], f"{side} worktreeRoot")
        git_dir = _absolute_canonical(record["gitDir"], f"{side} gitDir")
        common_dir = _absolute_canonical(record["commonDir"], f"{side} commonDir")
        if worktree != _absolute_canonical(marker_roots[side], f"{side} namespace source root"):
            _fail("production source worktree differs from the namespace marker")
        branch = record["branch"]
        if type(branch) is not str or not branch or len(branch) > 255 or any(ord(char) < 32 for char in branch):
            _fail("production source branch identity is invalid")
        try:
            if os.path.normcase(str(worktree.resolve(strict=True))) != os.path.normcase(str(worktree)):
                _fail("production source worktree alias rejected")
            if os.path.normcase(str(git_dir.resolve(strict=True))) != os.path.normcase(str(git_dir)):
                _fail("production source gitDir alias rejected")
            if os.path.normcase(str(common_dir.resolve(strict=True))) != os.path.normcase(str(common_dir)):
                _fail("production source commonDir alias rejected")
        except OSError as exc:
            raise ManagedStoreError("production source Git identity path is unavailable") from exc
        if not git_dir.is_dir() or not common_dir.is_dir():
            _fail("production source Git identity directories are invalid")
        try:
            git_dir.relative_to(common_dir)
        except ValueError as exc:
            raise ManagedStoreError("production gitDir is outside the common Git directory") from exc
        common_dirs.append(common_dir)
        git_dirs.append(git_dir)
    if os.path.normcase(str(common_dirs[0])) != os.path.normcase(str(common_dirs[1])):
        _fail("production source roots do not share their verified Git common directory")
    if os.path.normcase(str(git_dirs[0])) == os.path.normcase(str(git_dirs[1])):
        _fail("production source roots do not identify distinct Git worktrees")


def _validate_identity(root: Path, installation_id: str, data_store_id: str, release_id: str, binding_digest: str) -> dict[str, Any]:
    installation_id = _uuid(installation_id, "installation_id")
    data_store_id = _uuid(data_store_id, "data_store_id")
    release_id = _hash(release_id, "release_id")
    binding_digest = _hash(binding_digest, "binding_digest")
    _ordinary_directory(root)
    marker_path = root / "state" / "managed-namespace.json"
    binding_path = root / "state" / "production-binding.json"
    marker = _parse_record(marker_path, MAX_MARKER_BYTES)
    marker_keys = {"format", "mode", "stage", "data_policy", "security_policy", "credentials_policy", "installation_id", "root", "source_roots", "paths"}
    _exact(marker, marker_keys, "managed namespace marker")
    if marker["format"] != 1 or marker["mode"] != NAMESPACE_MODE or marker["stage"] != "prepared_empty":
        _fail("fixture or unsupported namespace mode rejected")
    if marker["data_policy"] != "empty_no_migration" or marker["security_policy"] != "logical_separation_only" or marker["credentials_policy"] != "forbidden":
        _fail("managed namespace policy mismatch")
    if _uuid(marker["installation_id"], "namespace installation_id") != installation_id:
        _fail("managed namespace installation mismatch")
    marker_root = _absolute_canonical(marker["root"], "namespace root")
    sandbox_root = _absolute_canonical(marker.get("source_roots", {}).get("sandbox"), "sandbox source root") if type(marker.get("source_roots")) is dict else None
    if marker_root != root or sandbox_root is None or root != sandbox_root / ".managed-runtime" / installation_id:
        _fail("managed namespace root does not match the registered production location")
    source_roots = marker["source_roots"]
    _exact(source_roots, {"stable", "sandbox"}, "namespace source roots")
    for key, path_value in source_roots.items():
        _absolute_canonical(path_value, f"namespace {key} source root")
    expected_marker_paths = {
        "launcher": str(root / "launcher"), "state": str(root / "state"), "releases": str(root / "releases"),
        "host_state": str(root / "host" / "state"), "host_profile": str(root / "host" / "profile"),
        "host_workspace": str(root / "host" / "workspace"), "data": str(root / "data" / "store"),
    }
    if marker["paths"] != expected_marker_paths:
        _fail("managed namespace paths are not the fixed production layout")
    binding = _parse_record(binding_path, MAX_BINDING_BYTES)
    binding_keys = {"format", "mode", "installationId", "namespace", "source", "release", "paths", "data", "access", "runtimeRegistration", "writerLease", "readyForLaunch", "bindingDigest"}
    _exact(binding, binding_keys, "production binding")
    if binding["format"] != 1 or binding["mode"] != BINDING_MODE:
        _fail("fixture or unsupported production binding rejected")
    body = {key: value for key, value in binding.items() if key != "bindingDigest"}
    if _hash(binding["bindingDigest"], "binding digest") != _digest(body) or binding["bindingDigest"] != binding_digest:
        _fail("production binding digest mismatch")
    if _uuid(binding["installationId"], "binding installationId") != installation_id:
        _fail("production binding installation mismatch")
    namespace = binding["namespace"]
    _exact(namespace, {"mode", "root", "digest", "securityPolicy", "credentialsPolicy"}, "binding namespace")
    if namespace != {
        "mode": NAMESPACE_MODE, "root": str(root), "digest": _digest(marker),
        "securityPolicy": "logical_separation_only", "credentialsPolicy": "forbidden",
    }:
        _fail("production binding namespace identity mismatch")
    source = binding["source"]
    _exact(source, {"operationId", "planDigest", "receiptDigest", "snapshotDigest", "mergedHead", "mergedTree", "roots"}, "binding source")
    _uuid(source["operationId"], "source operationId")
    for key in ("planDigest", "receiptDigest", "snapshotDigest"):
        _hash(source[key], f"source {key}")
    for key in ("mergedHead", "mergedTree"):
        if type(source[key]) is not str or not GIT_ID_RE.fullmatch(source[key]):
            _fail(f"source {key} is invalid")
    _validate_source_roots(source["roots"], source_roots)
    release = binding["release"]
    _exact(release, {"releaseId", "manifestDigest", "root", "buildReceiptDigest", "toolchainDigest", "buildPolicy"}, "binding release")
    if _hash(release["releaseId"], "releaseId") != release_id or release["manifestDigest"] != release_id:
        _fail("production binding release mismatch")
    if release["root"] != str(root / "releases" / release_id):
        _fail("production binding release root mismatch")
    for key in ("manifestDigest", "buildReceiptDigest", "toolchainDigest"):
        _hash(release[key], f"release {key}")
    if release["buildPolicy"] != "configless_snapshot_v2":
        _fail("production binding build policy mismatch")
    if binding["paths"] != _fixed_paths(root, release_id):
        _fail("production binding host paths mismatch")
    data = binding["data"]
    _exact(data, {"storeId", "schema", "revision", "policy"}, "binding data")
    if _uuid(data["storeId"], "data store id") != data_store_id or data != {
        "storeId": data_store_id, "schema": SCHEMA, "revision": 0, "policy": "empty_no_migration",
    }:
        _fail("production binding data identity mismatch")
    if binding["access"] != "read_only" or binding["runtimeRegistration"] is not None or binding["writerLease"] is not None or binding["readyForLaunch"] is not False:
        _fail("production binding access or readiness fields rejected")
    for relative in ("launcher", "state", "releases", "host", "host/state", "host/profile", "host/workspace", "data", "data/store", f"releases/{release_id}", f"releases/{release_id}/app"):
        _ordinary_directory(root / relative)
    return binding


def _safe_relative(value: Any) -> tuple[str, ...]:
    if type(value) is not str or not value or len(value.encode("utf-8", "strict")) > MAX_RELATIVE_PATH_BYTES:
        _fail("managed store relative path is invalid")
    if "\\" in value or value.startswith("/") or value.endswith("/") or "\x00" in value or ":" in value:
        _fail("managed store path must be canonical POSIX relative form")
    parts = value.split("/")
    if any(part in ("", ".", "..") for part in parts) or str(PurePosixPath(value)) != value:
        _fail("managed store traversal path rejected")
    reserved = {".env", "credentials", "credential", "secrets", "secret", "keys", "api_keys", "apikeys", "managed-data.sqlite3", CONTROL_NAME.casefold(), LOCK_NAME.casefold()}
    if any(part.casefold() in reserved or part.casefold().startswith(".managed-") or any(term in part.casefold() for term in ("secret", "credential", "private_key", "api_key")) for part in parts):
        _fail("managed store internal or secret path rejected")
    return tuple(parts)


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        # Windows does not support FlushFileBuffers on directory handles.
        # SQLite uses FULL synchronous commits and payloads are published with
        # MOVEFILE_WRITE_THROUGH after flushing the staged file itself.
        return
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class _ProcessLock:
    def __init__(self, path: Path):
        self.path = path
        self.fd: int | None = None

    def acquire(self) -> None:
        flags = os.O_RDWR | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(self.path, flags | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                os.write(descriptor, b"\x00")
                os.fsync(descriptor)
            except BaseException:
                os.close(descriptor)
                raise
            _fsync_directory(self.path.parent)
        except FileExistsError:
            descriptor = os.open(self.path, flags)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size != 1:
            os.close(descriptor)
            _fail("managed store process lock identity rejected")
        try:
            if os.name == "nt":
                import msvcrt

                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError) as exc:
            os.close(descriptor)
            raise StoreBusyError("managed store writer is already active") from exc
        self.fd = descriptor

    def release(self) -> None:
        if self.fd is None:
            return
        descriptor, self.fd = self.fd, None
        try:
            if os.name == "nt":
                import msvcrt

                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


@dataclass(repr=False)
class Lease:
    generation: int
    operation_id: str
    installation_id: str
    data_store_id: str
    release_id: str
    binding_digest: str
    _token: str = field(repr=False)
    _owner: object = field(repr=False)
    _process_lock: _ProcessLock = field(repr=False)
    _closed: bool = field(default=False, repr=False)

    def __repr__(self) -> str:
        return f"Lease(generation={self.generation}, operation_id={self.operation_id!r}, closed={self._closed})"

    def _close_process_lock(self) -> None:
        self._process_lock.release()
        self._closed = True

    def __del__(self) -> None:
        with contextlib.suppress(BaseException):
            self._close_process_lock()


class ManagedStore:
    """Store mechanism anchored to a canonical project-local production binding."""

    def __init__(self, root: str, *, installation_id: str, data_store_id: str, release_id: str, binding_digest: str):
        self.root = _absolute_canonical(root, "root")
        self.installation_id = _uuid(installation_id, "installation_id")
        self.data_store_id = _uuid(data_store_id, "data_store_id")
        self.release_id = _hash(release_id, "release_id")
        self.binding_digest = _hash(binding_digest, "binding_digest")
        self.binding = _validate_identity(self.root, self.installation_id, self.data_store_id, self.release_id, self.binding_digest)
        self.state_dir = self.root / "state"
        self.data_dir = self.root / "data" / "store"
        self.control_path = self.state_dir / CONTROL_NAME
        self.data_db_path = self.data_dir / DATA_DB_NAME
        self._owner_id = secrets.token_hex(16)
        self._mutex = threading.RLock()

    def _revalidate_binding(self, lease: Lease | None = None) -> None:
        try:
            current = _validate_identity(self.root, self.installation_id, self.data_store_id, self.release_id, self.binding_digest)
            if _canonical(current) != _canonical(self.binding):
                _fail("managed production binding changed after store construction")
        except BaseException:
            if isinstance(lease, Lease) and lease._owner is self._owner_id:
                lease._close_process_lock()
            raise

    @staticmethod
    def _validate_operation(operation_id: str) -> str:
        return _uuid(operation_id, "operation_id")

    def _connect_control(self, *, create: bool) -> sqlite3.Connection:
        created = False
        if not self.control_path.exists():
            if not create:
                raise FileNotFoundError(self.control_path)
            flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
            try:
                descriptor = os.open(self.control_path, flags, 0o600)
                os.fsync(descriptor)
                os.close(descriptor)
                _fsync_directory(self.state_dir)
                created = True
            except FileExistsError:
                pass
        info = _lstat_regular(self.control_path)
        if info.st_size == 0 and not created:
            raise MaintenanceRequired("managed store control journal is incomplete")
        connection = sqlite3.connect(self.control_path, timeout=0.1, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA journal_mode=DELETE")
        if created:
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                connection.execute("CREATE TABLE lease (singleton INTEGER PRIMARY KEY CHECK(singleton=1), generation INTEGER NOT NULL, active INTEGER NOT NULL, token_hash TEXT, owner_operation_id TEXT, installation_id TEXT NOT NULL, data_store_id TEXT NOT NULL, release_id TEXT NOT NULL, binding_digest TEXT NOT NULL)")
                connection.execute("CREATE TABLE operations (operation_id TEXT PRIMARY KEY, kind TEXT NOT NULL, fingerprint TEXT NOT NULL, status TEXT NOT NULL, receipt TEXT NOT NULL)")
                values = {
                    "format": str(FORMAT), "installation_id": self.installation_id, "data_store_id": self.data_store_id,
                    "release_id": self.release_id, "binding_digest": self.binding_digest, "revision": "0", "initialized": "0",
                }
                connection.executemany("INSERT INTO meta(key,value) VALUES(?,?)", values.items())
                connection.execute("INSERT INTO lease VALUES(1,0,0,NULL,NULL,?,?,?,?)", (self.installation_id, self.data_store_id, self.release_id, self.binding_digest))
                connection.execute("COMMIT")
            except BaseException:
                with contextlib.suppress(sqlite3.Error):
                    connection.execute("ROLLBACK")
                connection.close()
                raise
        return connection

    def _validate_control(self, connection: sqlite3.Connection) -> tuple[dict[str, str], sqlite3.Row]:
        try:
            objects = connection.execute("SELECT type,name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name").fetchall()
            expected_objects = [("table", name) for name in ("lease", "meta", "operations")]
            if [(row["type"], row["name"]) for row in objects] != expected_objects:
                raise MaintenanceRequired("managed store control schema is incomplete or unknown")
            meta_rows = connection.execute("SELECT key,value FROM meta ORDER BY key").fetchall()
            meta = {row["key"]: row["value"] for row in meta_rows}
            expected_keys = {"format", "installation_id", "data_store_id", "release_id", "binding_digest", "revision", "initialized"}
            if set(meta) != expected_keys or meta.get("format") != str(FORMAT):
                raise MaintenanceRequired("managed store control metadata is incomplete")
            identities = (meta["installation_id"], meta["data_store_id"], meta["release_id"], meta["binding_digest"])
            expected_identity = (self.installation_id, self.data_store_id, self.release_id, self.binding_digest)
            if identities != expected_identity or not meta["revision"].isdigit() or meta["initialized"] not in ("0", "1"):
                raise MaintenanceRequired("managed store control identity mismatch")
            lease = connection.execute("SELECT * FROM lease WHERE singleton=1").fetchone()
            if lease is None or connection.execute("SELECT COUNT(*) FROM lease").fetchone()[0] != 1:
                raise MaintenanceRequired("managed store lease record is incomplete")
            if (lease["installation_id"], lease["data_store_id"], lease["release_id"], lease["binding_digest"]) != expected_identity:
                raise MaintenanceRequired("managed store lease identity mismatch")
            if lease["active"] not in (0, 1) or type(lease["generation"]) is not int or lease["generation"] < 0:
                raise MaintenanceRequired("managed store lease state is invalid")
            if lease["active"]:
                if not HASH_RE.fullmatch(lease["token_hash"] or "") or not UUID_RE.fullmatch(lease["owner_operation_id"] or ""):
                    raise MaintenanceRequired("managed store active lease identity is incomplete")
            elif lease["token_hash"] is not None or lease["owner_operation_id"] is not None:
                raise MaintenanceRequired("managed store released lease retained an owner")
            for operation in connection.execute("SELECT operation_id,kind,fingerprint,status,receipt FROM operations"):
                if not UUID_RE.fullmatch(operation["operation_id"] or "") or operation["kind"] not in ("acquire", "initialize", "write_bytes", "release"):
                    raise MaintenanceRequired("managed store operation record is invalid")
                if not HASH_RE.fullmatch(operation["fingerprint"] or "") or operation["status"] not in ("pending", "complete"):
                    raise MaintenanceRequired("managed store operation status is invalid")
                try:
                    operation_receipt = json.loads(operation["receipt"])
                except (TypeError, json.JSONDecodeError) as exc:
                    raise MaintenanceRequired("managed store operation evidence is invalid") from exc
                if _canonical(operation_receipt).decode("utf-8") != operation["receipt"]:
                    raise MaintenanceRequired("managed store operation evidence is not canonical")
            check = connection.execute("PRAGMA quick_check").fetchone()[0]
            if check != "ok":
                raise MaintenanceRequired("managed store control database integrity failed")
            return meta, lease
        except sqlite3.Error as exc:
            raise MaintenanceRequired("managed store control journal is unreadable") from exc

    @staticmethod
    def _pending(connection: sqlite3.Connection) -> bool:
        return connection.execute("SELECT 1 FROM operations WHERE status='pending' LIMIT 1").fetchone() is not None

    def _make_receipt(self, mode: str, operation_id: str, generation: int, revision: int, **extra: Any) -> dict[str, Any]:
        result = {
            "format": FORMAT, "mode": mode, "operationId": operation_id, "generation": generation,
            "installationId": self.installation_id, "dataStoreId": self.data_store_id,
            "releaseId": self.release_id, "bindingDigest": self.binding_digest, "revision": revision,
        }
        result.update(extra)
        return result

    def _validate_receipt(self, receipt: dict[str, Any], mode: str, operation_id: str, generation: int, extra: dict[str, Any] | None = None, revision: int | None = None) -> None:
        extra = extra or {}
        keys = {"format", "mode", "operationId", "generation", "installationId", "dataStoreId", "releaseId", "bindingDigest", "revision", *extra.keys()}
        _exact(receipt, keys, "managed store receipt")
        if (type(receipt["generation"]) is not int or not 0 < receipt["generation"] <= 2 ** 63 - 1
                or type(generation) is not int or not 0 < generation <= 2 ** 63 - 1):
            raise MaintenanceRequired("managed store receipt generation rejected")
        if receipt["format"] != FORMAT or receipt["mode"] != mode or receipt["operationId"] != operation_id or receipt["generation"] != generation:
            raise MaintenanceRequired("managed store receipt identity mismatch")
        if (receipt["installationId"], receipt["dataStoreId"], receipt["releaseId"], receipt["bindingDigest"]) != (self.installation_id, self.data_store_id, self.release_id, self.binding_digest):
            raise MaintenanceRequired("managed store receipt provenance mismatch")
        if type(receipt["revision"]) is not int or receipt["revision"] < 0 or (revision is not None and receipt["revision"] != revision):
            raise MaintenanceRequired("managed store receipt revision mismatch")
        if any(receipt.get(key) != value for key, value in extra.items()):
            raise MaintenanceRequired("managed store receipt operation data mismatch")

    def acquire(self, operation_id: str) -> Lease:
        operation_id = self._validate_operation(operation_id)
        self._revalidate_binding()
        lock = _ProcessLock(self.state_dir / LOCK_NAME)
        lock.acquire()
        connection: sqlite3.Connection | None = None
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
        try:
            self._revalidate_binding()
            connection = self._connect_control(create=True)
            meta, current = self._validate_control(connection)
            if self._pending(connection):
                raise MaintenanceRequired("managed store has an uncertain operation")
            if current["active"]:
                raise MaintenanceRequired("managed store has a durable owner without its process lock")
            if connection.execute("SELECT 1 FROM operations WHERE operation_id=?", (operation_id,)).fetchone():
                _fail("managed store operation ID was already used")
            generation = int(current["generation"]) + 1
            receipt = self._make_receipt("managed_store_lease_acquired_v1", operation_id, generation, int(meta["revision"]))
            fingerprint = _digest({"kind": "acquire", "installationId": self.installation_id, "dataStoreId": self.data_store_id, "releaseId": self.release_id, "bindingDigest": self.binding_digest})
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("INSERT INTO operations VALUES(?,?,?,'complete',?)", (operation_id, "acquire", fingerprint, _canonical(receipt).decode("utf-8")))
            connection.execute("UPDATE lease SET generation=?,active=1,token_hash=?,owner_operation_id=? WHERE singleton=1", (generation, token_hash, operation_id))
            connection.execute("COMMIT")
            lease = Lease(generation, operation_id, self.installation_id, self.data_store_id, self.release_id, self.binding_digest, token, self._owner_id, lock)
            return lease
        except BaseException:
            if connection is not None:
                with contextlib.suppress(sqlite3.Error):
                    connection.execute("ROLLBACK")
                connection.close()
            lock.release()
            raise
        finally:
            if connection is not None and connection:
                with contextlib.suppress(sqlite3.Error):
                    connection.close()

    def _validate_lease(self, connection: sqlite3.Connection, lease: Lease) -> tuple[dict[str, str], sqlite3.Row]:
        if not isinstance(lease, Lease) or lease._owner is not self._owner_id or lease._closed:
            _fail("managed store lease capability is stale or foreign")
        if (lease.installation_id, lease.data_store_id, lease.release_id, lease.binding_digest) != (self.installation_id, self.data_store_id, self.release_id, self.binding_digest):
            _fail("managed store lease identity mismatch")
        meta, current = self._validate_control(connection)
        if self._pending(connection):
            raise MaintenanceRequired("managed store has an uncertain operation")
        if not current["active"] or current["generation"] != lease.generation or current["owner_operation_id"] != lease.operation_id:
            _fail("managed store lease generation is stale")
        token_hash = hashlib.sha256(lease._token.encode("ascii")).hexdigest()
        if not secrets.compare_digest(current["token_hash"] or "", token_hash):
            _fail("managed store lease token mismatch")
        return meta, current

    def _abandon_if_uncertain(self, connection: sqlite3.Connection, lease: Lease) -> None:
        with contextlib.suppress(BaseException):
            if self._pending(connection):
                lease._close_process_lock()

    def _operation(self, connection: sqlite3.Connection, operation_id: str, kind: str, fingerprint: str) -> dict[str, Any] | None:
        row = connection.execute("SELECT kind,fingerprint,status,receipt FROM operations WHERE operation_id=?", (operation_id,)).fetchone()
        if row is None:
            return None
        if row["kind"] != kind or row["fingerprint"] != fingerprint:
            _fail("managed store operation ID was reused with different input")
        if row["status"] == "pending":
            raise MaintenanceRequired("managed store operation is uncertain")
        if row["status"] != "complete":
            raise MaintenanceRequired("managed store operation status is unknown")
        try:
            receipt = json.loads(row["receipt"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise MaintenanceRequired("managed store receipt is unreadable") from exc
        if _canonical(receipt).decode("utf-8") != row["receipt"]:
            raise MaintenanceRequired("managed store receipt is not canonical")
        return receipt

    def _begin_intent(self, connection: sqlite3.Connection, operation_id: str, kind: str, fingerprint: str, intent: dict[str, Any]) -> None:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("INSERT INTO operations VALUES(?,?,?,'pending',?)", (operation_id, kind, fingerprint, _canonical(intent).decode("utf-8")))
        connection.execute("COMMIT")

    def _complete_intent(self, connection: sqlite3.Connection, operation_id: str, receipt: dict[str, Any], *, revision: int | None = None, initialized: bool | None = None) -> None:
        connection.execute("BEGIN IMMEDIATE")
        cursor = connection.execute("UPDATE operations SET status='complete',receipt=? WHERE operation_id=? AND status='pending'", (_canonical(receipt).decode("utf-8"), operation_id))
        if cursor.rowcount != 1:
            raise MaintenanceRequired("managed store intent disappeared before completion")
        if revision is not None:
            connection.execute("UPDATE meta SET value=? WHERE key='revision'", (str(revision),))
        if initialized is not None:
            connection.execute("UPDATE meta SET value=? WHERE key='initialized'", ("1" if initialized else "0",))
        connection.execute("COMMIT")

    def _data_connection(self, *, readonly: bool = False) -> sqlite3.Connection:
        info = _lstat_regular(self.data_db_path)
        if info.st_size == 0:
            raise MaintenanceRequired("managed data schema file is incomplete")
        if readonly:
            connection = sqlite3.connect(self.data_db_path.as_uri() + "?mode=ro", uri=True, timeout=0.1, isolation_level=None)
        else:
            connection = sqlite3.connect(self.data_db_path, timeout=0.1, isolation_level=None)
        connection.row_factory = sqlite3.Row
        if not readonly:
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("PRAGMA journal_mode=DELETE")
        try:
            self._verify_data_identity(connection)
            return connection
        except BaseException:
            connection.close()
            raise

    def _verify_data_identity(self, connection: sqlite3.Connection) -> dict[str, Any]:
        try:
            objects = connection.execute("SELECT type,name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name").fetchall()
            if [(row["type"], row["name"]) for row in objects] != [("table", "_managed_store_identity")]:
                raise MaintenanceRequired("managed data schema contains unknown objects")
            row = connection.execute("SELECT record FROM _managed_store_identity WHERE singleton=1").fetchone()
            if row is None or connection.execute("SELECT COUNT(*) FROM _managed_store_identity").fetchone()[0] != 1:
                raise MaintenanceRequired("managed data schema identity is missing")
            record_text = row["record"]
            if type(record_text) is not str:
                raise MaintenanceRequired("managed data schema identity encoding is invalid")
            record = json.loads(
                record_text,
                object_pairs_hook=_reject_duplicate_keys,
                parse_constant=lambda _: _fail("managed data schema identity contains a non-finite number"),
            )
            expected_keys = {"format", "mode", "installationId", "dataStoreId", "releaseId", "bindingDigest", "schema", "revision"}
            if type(record) is not dict or set(record) != expected_keys or _canonical(record).decode("utf-8") != record_text:
                raise MaintenanceRequired("managed data schema identity is not exact canonical JSON")
            expected = {
                "format": FORMAT, "mode": STORE_MODE, "installationId": self.installation_id,
                "dataStoreId": self.data_store_id, "releaseId": self.release_id,
                "bindingDigest": self.binding_digest, "schema": SCHEMA,
            }
            if any(record[key] != value for key, value in expected.items()) or type(record["revision"]) is not int or record["revision"] < 1:
                raise MaintenanceRequired("managed data schema identity mismatch")
            if connection.execute("PRAGMA user_version").fetchone()[0] != 1:
                raise MaintenanceRequired("managed data schema version mismatch")
            if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise MaintenanceRequired("managed data schema integrity failed")
            return record
        except (sqlite3.Error, json.JSONDecodeError, TypeError, ManagedStoreError) as exc:
            if isinstance(exc, MaintenanceRequired):
                raise
            raise MaintenanceRequired("managed data schema is unreadable") from exc

    def initialize(self, lease: Lease, operation_id: str) -> dict[str, Any]:
        operation_id = self._validate_operation(operation_id)
        self._revalidate_binding(lease)
        with self._mutex:
            connection = self._connect_control(create=False)
            try:
                meta, _ = self._validate_lease(connection, lease)
                fingerprint = _digest({"kind": "initialize", "identity": self.binding_digest, "generation": lease.generation})
                previous = self._operation(connection, operation_id, "initialize", fingerprint)
                if previous is not None:
                    if meta["initialized"] != "1":
                        raise MaintenanceRequired("completed initialization no longer reconciles")
                    initial_record = {"format": FORMAT, "mode": STORE_MODE, "installationId": self.installation_id, "dataStoreId": self.data_store_id, "releaseId": self.release_id, "bindingDigest": self.binding_digest, "schema": SCHEMA, "revision": 1}
                    self._validate_receipt(previous, "managed_store_initialized_v1", operation_id, lease.generation, {"schema": SCHEMA, "schemaDigest": _digest(initial_record)}, 1)
                    data_connection = self._data_connection(readonly=True)
                    try:
                        record = self._verify_data_identity(data_connection)
                        if record["revision"] != int(meta["revision"]):
                            raise MaintenanceRequired("completed initialization no longer matches the current revision")
                    finally:
                        data_connection.close()
                    return previous
                if meta["initialized"] != "0" or self.data_db_path.exists():
                    raise MaintenanceRequired("store initialization is partial or already completed")
                if any(self.data_dir.iterdir()):
                    raise MaintenanceRequired("managed data store is not empty")
                intent = {"mode": "managed_store_intent_v1", "kind": "initialize", "operationId": operation_id, "generation": lease.generation, "bindingDigest": self.binding_digest, "revisionBefore": 0}
                self._begin_intent(connection, operation_id, "initialize", fingerprint, intent)
                flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
                descriptor = os.open(self.data_db_path, flags, 0o600)
                os.fsync(descriptor)
                os.close(descriptor)
                _fsync_directory(self.data_dir)
                data = sqlite3.connect(self.data_db_path, timeout=0.1, isolation_level=None)
                data.execute("PRAGMA synchronous=FULL")
                data.execute("PRAGMA journal_mode=DELETE")
                record = {"format": FORMAT, "mode": STORE_MODE, "installationId": self.installation_id, "dataStoreId": self.data_store_id, "releaseId": self.release_id, "bindingDigest": self.binding_digest, "schema": SCHEMA, "revision": 1}
                receipt = self._make_receipt("managed_store_initialized_v1", operation_id, lease.generation, 1, schema=SCHEMA, schemaDigest=_digest(record))
                try:
                    data.execute("BEGIN IMMEDIATE")
                    data.execute("CREATE TABLE _managed_store_identity (singleton INTEGER PRIMARY KEY CHECK(singleton=1), record TEXT NOT NULL)")
                    data.execute("INSERT INTO _managed_store_identity VALUES(1,?)", (_canonical(record).decode("utf-8"),))
                    data.execute("PRAGMA user_version=1")
                    data.execute("COMMIT")
                except BaseException:
                    with contextlib.suppress(sqlite3.Error):
                        data.execute("ROLLBACK")
                    raise
                finally:
                    data.close()
                descriptor = os.open(self.data_db_path, os.O_RDWR | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0))
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
                _fsync_directory(self.data_dir)
                self._complete_intent(connection, operation_id, receipt, revision=1, initialized=True)
                return receipt
            except BaseException:
                if connection.in_transaction:
                    with contextlib.suppress(sqlite3.Error):
                        connection.execute("ROLLBACK")
                self._abandon_if_uncertain(connection, lease)
                raise
            finally:
                connection.close()

    def _write_exclusive(self, target: Path, data: bytes) -> None:
        staged = target.with_name(target.name + ".managed-pending-" + secrets.token_hex(8))
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(staged, flags, 0o600)
        try:
            view = memoryview(data)
            offset = 0
            while offset < len(view):
                written = os.write(descriptor, view[offset:])
                if written <= 0:
                    raise OSError("short managed store write")
                offset += written
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        if os.name == "nt":
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            move = kernel32.MoveFileExW
            move.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
            move.restype = ctypes.c_int
            # No REPLACE_EXISTING flag: an existing destination is never overwritten.
            if not move(str(staged), str(target), 0x00000008):
                error = ctypes.get_last_error()
                raise FileExistsError(error, "exclusive managed store publication failed", str(target))
        else:
            os.link(staged, target, follow_symlinks=False)
            staged.unlink()

    def _prepare_parent_directories(self, parent: Path) -> None:
        relative = parent.relative_to(self.data_dir)
        current = self.data_dir
        for part in relative.parts:
            current = current / part
            try:
                current.mkdir()
                _fsync_directory(current.parent)
            except FileExistsError:
                pass
            _ordinary_directory(current)

    def _verify_written_file(self, target: Path, expected_hash: str, expected_length: int) -> None:
        info = _lstat_regular(target, MAX_WRITE_BYTES)
        descriptor = os.open(target, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0))
        digest = hashlib.sha256()
        total = 0
        try:
            opened = os.fstat(descriptor)
            if opened.st_nlink != 1 or opened.st_size != info.st_size:
                raise MaintenanceRequired("managed output changed during verification")
            while True:
                block = os.read(descriptor, 64 * 1024)
                if not block:
                    break
                total += len(block)
                digest.update(block)
        finally:
            os.close(descriptor)
        if total != expected_length or digest.hexdigest() != expected_hash:
            raise MaintenanceRequired("managed output did not verify after write")

    def write_bytes(self, lease: Lease, operation_id: str, relative_path: str, data: bytes) -> dict[str, Any]:
        operation_id = self._validate_operation(operation_id)
        parts = _safe_relative(relative_path)
        if type(data) is not bytes or len(data) > MAX_WRITE_BYTES:
            _fail("managed store write payload is invalid or exceeds its bound")
        self._revalidate_binding(lease)
        content_hash = hashlib.sha256(data).hexdigest()
        target = self.data_dir.joinpath(*parts)
        with self._mutex:
            connection = self._connect_control(create=False)
            try:
                meta, _ = self._validate_lease(connection, lease)
                if meta["initialized"] != "1":
                    _fail("managed store must be initialized before application writes")
                fingerprint = _digest({"kind": "write_bytes", "identity": self.binding_digest, "generation": lease.generation, "path": relative_path, "sha256": content_hash, "byteLength": len(data)})
                previous = self._operation(connection, operation_id, "write_bytes", fingerprint)
                if previous is not None:
                    self._validate_receipt(previous, "managed_store_bytes_written_v1", operation_id, lease.generation,
                                           {"path": relative_path, "sha256": content_hash, "byteLength": len(data)})
                    self._verify_written_file(target, content_hash, len(data))
                    data_connection = self._data_connection(readonly=True)
                    try:
                        record = self._verify_data_identity(data_connection)
                        if record["revision"] != int(meta["revision"]) or record["revision"] < previous["revision"]:
                            raise MaintenanceRequired("managed write receipt and data revision do not reconcile")
                    finally:
                        data_connection.close()
                    return previous
                before_revision = int(meta["revision"])
                intent = {"mode": "managed_store_intent_v1", "kind": "write_bytes", "operationId": operation_id, "generation": lease.generation, "bindingDigest": self.binding_digest, "path": relative_path, "sha256": content_hash, "byteLength": len(data), "revisionBefore": before_revision}
                self._begin_intent(connection, operation_id, "write_bytes", fingerprint, intent)
                self._prepare_parent_directories(target.parent)
                self._write_exclusive(target, data)
                self._verify_written_file(target, content_hash, len(data))
                _fsync_directory(target.parent)
                data_connection = self._data_connection()
                try:
                    record = self._verify_data_identity(data_connection)
                    if record["revision"] != before_revision:
                        raise MaintenanceRequired("managed data revision drifted before write commit")
                    next_revision = before_revision + 1
                    record["revision"] = next_revision
                    data_connection.execute("BEGIN IMMEDIATE")
                    data_connection.execute("UPDATE _managed_store_identity SET record=? WHERE singleton=1", (_canonical(record).decode("utf-8"),))
                    data_connection.execute("COMMIT")
                except BaseException:
                    if data_connection.in_transaction:
                        with contextlib.suppress(sqlite3.Error):
                            data_connection.execute("ROLLBACK")
                    raise
                finally:
                    data_connection.close()
                receipt = self._make_receipt("managed_store_bytes_written_v1", operation_id, lease.generation, next_revision, path=relative_path, sha256=content_hash, byteLength=len(data))
                self._complete_intent(connection, operation_id, receipt, revision=next_revision)
                return receipt
            except BaseException:
                if connection.in_transaction:
                    with contextlib.suppress(sqlite3.Error):
                        connection.execute("ROLLBACK")
                self._abandon_if_uncertain(connection, lease)
                raise
            finally:
                connection.close()

    def release(self, lease: Lease, operation_id: str) -> dict[str, Any]:
        operation_id = self._validate_operation(operation_id)
        self._revalidate_binding(lease)
        connection = self._connect_control(create=False)
        try:
            if not isinstance(lease, Lease) or lease._owner is not self._owner_id:
                _fail("managed store lease capability is foreign")
            fingerprint = _digest({"kind": "release", "identity": self.binding_digest, "generation": lease.generation, "leaseOperationId": lease.operation_id})
            if lease._closed:
                meta, current = self._validate_control(connection)
                if self._pending(connection):
                    raise MaintenanceRequired("managed store has an uncertain operation")
                previous = self._operation(connection, operation_id, "release", fingerprint)
                if previous is not None and not current["active"] and current["generation"] == lease.generation:
                    self._validate_receipt(previous, "managed_store_lease_released_v1", operation_id, lease.generation, revision=int(meta["revision"]))
                    return previous
                _fail("managed store release lease is stale")
            meta, current = self._validate_lease(connection, lease)
            if operation_id == lease.operation_id or connection.execute("SELECT 1 FROM operations WHERE operation_id=?", (operation_id,)).fetchone():
                _fail("managed store release operation ID must be new")
            receipt = self._make_receipt("managed_store_lease_released_v1", operation_id, lease.generation, int(meta["revision"]))
            intent = {"mode": "managed_store_intent_v1", "kind": "release", "operationId": operation_id, "generation": lease.generation, "bindingDigest": self.binding_digest, "revisionBefore": int(meta["revision"])}
            self._begin_intent(connection, operation_id, "release", fingerprint, intent)
            connection.execute("BEGIN IMMEDIATE")
            lease_update = connection.execute("UPDATE lease SET active=0,token_hash=NULL,owner_operation_id=NULL WHERE singleton=1 AND generation=? AND active=1", (lease.generation,))
            operation_update = connection.execute("UPDATE operations SET status='complete',receipt=? WHERE operation_id=? AND status='pending'", (_canonical(receipt).decode("utf-8"), operation_id))
            if lease_update.rowcount != 1 or operation_update.rowcount != 1:
                raise MaintenanceRequired("managed store release state changed before commit")
            connection.execute("COMMIT")
            lease._close_process_lock()
            return receipt
        except BaseException:
            if connection.in_transaction:
                with contextlib.suppress(sqlite3.Error):
                    connection.execute("ROLLBACK")
            self._abandon_if_uncertain(connection, lease)
            raise
        finally:
            connection.close()

    def application_snapshot(self, lease: Lease | None = None) -> dict[str, Any]:
        """Read and reconcile the bounded immutable application log, never acquire a lease.

        A live caller must present its lease. A cold readonly caller may inspect
        only an unleased journal. Every returned byte is bound to a completed
        write receipt and the data/control revision.
        """
        self._revalidate_binding(lease)
        if not self.control_path.exists():
            if lease is not None or any(self.data_dir.iterdir()):
                raise MaintenanceRequired("application store is incomplete")
            return {"revision": 0, "initialized": False, "generation": 0, "events": [], "releases": []}
        _lstat_regular(self.control_path)
        with self._mutex:
            connection = sqlite3.connect(self.control_path.as_uri() + "?mode=ro", uri=True, timeout=0.1, isolation_level=None)
            connection.row_factory = sqlite3.Row
            try:
                if connection.execute("SELECT COUNT(*) FROM operations").fetchone()[0] > 32768:
                    raise MaintenanceRequired("application operation bound exceeded")
                if lease is None:
                    meta, current = self._validate_control(connection)
                    if current["active"] or self._pending(connection):
                        raise MaintenanceRequired("application store has an active or uncertain owner")
                else:
                    meta, current = self._validate_lease(connection, lease)
                if meta["initialized"] != "1":
                    raise MaintenanceRequired("application control exists without completed initialization")
                data = self._data_connection(readonly=True)
                try:
                    if self._verify_data_identity(data)["revision"] != int(meta["revision"]):
                        raise MaintenanceRequired("application data revision mismatch")
                finally:
                    data.close()
                writes, releases = [], []
                total = 0
                for row in connection.execute("SELECT operation_id,kind,status,receipt FROM operations"):
                    if row["status"] != "complete":
                        raise MaintenanceRequired("application operation is pending")
                    receipt = json.loads(row["receipt"])
                    if row["kind"] == "write_bytes":
                        if len(writes) >= 8192:
                            raise MaintenanceRequired("application event count exceeded")
                        length = receipt.get("byteLength")
                        if type(length) is not int or not 0 <= length <= 64 * 1024:
                            raise MaintenanceRequired("application event length rejected")
                        total += length
                        if total > 32 * 1024 * 1024:
                            raise MaintenanceRequired("application log byte limit exceeded")
                        self._validate_receipt(receipt, "managed_store_bytes_written_v1", row["operation_id"], receipt.get("generation"),
                                               {key: receipt.get(key) for key in ("path", "sha256", "byteLength")})
                        writes.append(receipt)
                    elif row["kind"] == "release":
                        self._validate_receipt(receipt, "managed_store_lease_released_v1", row["operation_id"], receipt.get("generation"))
                        releases.append(receipt)
                writes.sort(key=lambda value: value["revision"])
                if int(meta["revision"]) != len(writes) + 1:
                    raise MaintenanceRequired("application write count and revision mismatch")
                expected = {DATA_DB_NAME}
                if writes:
                    expected.add("application")
                def bounded_names(directory, cap):
                    names = set()
                    with os.scandir(directory) as scan:
                        for child in scan:
                            if len(names) >= cap:
                                raise MaintenanceRequired("application directory entry bound exceeded")
                            names.add(child.name)
                    return names

                if bounded_names(self.data_dir, 2) != expected:
                    raise MaintenanceRequired("application data contains unknown records")
                events = []
                if writes:
                    app_root = self.data_dir / "application"
                    events_root = app_root / "events"
                    _ordinary_directory(app_root)
                    _ordinary_directory(events_root)
                    if bounded_names(app_root, 1) != {"events"}:
                        raise MaintenanceRequired("application data layout rejected")
                    names = set()
                    with os.scandir(events_root) as scan:
                        for child in scan:
                            if len(names) >= 8192:
                                raise MaintenanceRequired("application event count exceeded")
                            names.add(child.name)
                    if names != {f"{index:08d}.json" for index in range(1, len(writes) + 1)}:
                        raise MaintenanceRequired("application log inventory mismatch")
                for sequence, receipt in enumerate(writes, 1):
                    relative = f"application/events/{sequence:08d}.json"
                    if receipt["path"] != relative or receipt["revision"] != sequence + 1 or type(receipt["generation"]) is not int or receipt["generation"] < 1:
                        raise MaintenanceRequired("application receipt sequence mismatch")
                    target = self.data_dir / relative
                    self._verify_written_file(target, receipt["sha256"], receipt["byteLength"])
                    with target.open("rb") as source:
                        raw = source.read(64 * 1024 + 1)
                    if len(raw) != receipt["byteLength"] or hashlib.sha256(raw).hexdigest() != receipt["sha256"]:
                        raise MaintenanceRequired("application event changed while reading")
                    events.append({"raw": raw, "receipt": receipt})
                return {"revision": int(meta["revision"]), "initialized": True, "generation": current["generation"], "events": events, "releases": releases}
            except (sqlite3.Error, KeyError, TypeError, ValueError) as exc:
                raise MaintenanceRequired("application journal cannot be reconciled") from exc
            finally:
                connection.close()

    def inspect(self) -> dict[str, Any]:
        try:
            self._revalidate_binding()
        except ManagedStoreError:
            return {"format": FORMAT, "mode": "managed_store_inspection_v1", "state": "maintenance_required", "initialized": False, "revision": 0, "activeLease": False, "pendingOperations": 0}
        if not self.control_path.exists():
            if any(self.data_dir.iterdir()):
                return {"format": FORMAT, "mode": "managed_store_inspection_v1", "state": "maintenance_required", "initialized": False, "revision": 0, "activeLease": False, "pendingOperations": 0}
            return {"format": FORMAT, "mode": "managed_store_inspection_v1", "state": "empty_uninitialized", "initialized": False, "revision": 0, "activeLease": False, "pendingOperations": 0}
        try:
            _lstat_regular(self.control_path)
            connection = sqlite3.connect(self.control_path.as_uri() + "?mode=ro", uri=True, timeout=0.1, isolation_level=None)
            connection.row_factory = sqlite3.Row
            try:
                meta, lease = self._validate_control(connection)
                pending = connection.execute("SELECT COUNT(*) FROM operations WHERE status='pending'").fetchone()[0]
                if meta["initialized"] == "1":
                    data_connection = self._data_connection(readonly=True)
                    try:
                        record = self._verify_data_identity(data_connection)
                        if record["revision"] != int(meta["revision"]):
                            raise MaintenanceRequired("managed store control and schema revisions differ")
                    finally:
                        data_connection.close()
                elif self.data_db_path.exists() or any(self.data_dir.iterdir()):
                    raise MaintenanceRequired("uninitialized managed store contains partial data")
                state = "maintenance_required" if pending or lease["active"] else ("initialized" if meta["initialized"] == "1" else "empty_uninitialized")
                return {
                    "format": FORMAT, "mode": "managed_store_inspection_v1", "state": state,
                    "initialized": meta["initialized"] == "1", "revision": int(meta["revision"]),
                    "schema": SCHEMA if meta["initialized"] == "1" else None,
                    "activeLease": bool(lease["active"]), "pendingOperations": int(pending),
                    "installationId": self.installation_id, "dataStoreId": self.data_store_id,
                    "releaseId": self.release_id, "bindingDigest": self.binding_digest,
                }
            finally:
                connection.close()
        except (sqlite3.Error, ManagedStoreError):
            return {"format": FORMAT, "mode": "managed_store_inspection_v1", "state": "maintenance_required", "initialized": False, "revision": 0, "activeLease": False, "pendingOperations": 0}


__all__ = ["ManagedStore", "Lease", "ManagedStoreError", "StoreBusyError", "MaintenanceRequired"]
