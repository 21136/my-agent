from __future__ import annotations

import json
import os
import stat
import threading
import time
import uuid
import weakref
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

JOURNAL_DIRNAME = ".stream-journal"
MAX_JOURNAL_BYTES = 16 * 1024 * 1024
MAX_RECORD_BYTES = 1024 * 1024
MAX_RECORD_COUNT = 65_536
_local_lease_guard = threading.Lock()
_local_lease_locks: weakref.WeakValueDictionary[str, threading.Semaphore] = weakref.WeakValueDictionary()


def lease_path_for(journal_path: Path) -> Path:
    return journal_path.parent / ".leases" / f"{journal_path.stem}.lock"


@contextmanager
def journal_lease(path: Path, *, nonblocking: bool = False):
    """Cross-process lease plus transferable local guard for one journal."""
    key = str(path.resolve(strict=False)).casefold()
    with _local_lease_guard:
        local = _local_lease_locks.setdefault(key, threading.Semaphore(1))
    acquired = local.acquire(False) if nonblocking else local.acquire(timeout=5.0)
    if not acquired:
        yield False
        return
    handle = None
    os_locked = False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if _is_reparse_or_symlink(path):
            raise OSError("stream journal lease path is a link")
        try:
            before = path.lstat()
        except FileNotFoundError:
            before = None
        handle = path.open("a+b")
        opened = os.fstat(handle.fileno())
        current = path.lstat()
        if (not stat.S_ISREG(opened.st_mode) or _is_reparse_or_symlink(path)
                or (before is not None and (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino))
                or (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino)):
            raise OSError("stream journal lease path changed while opening")
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        deadline = time.monotonic() + (0 if nonblocking else 5.0)
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
                    raise OSError("OS locking is unsupported")
                os_locked = True
                break
            except (OSError, BlockingIOError) as exc:
                if nonblocking:
                    yield False
                    return
                if time.monotonic() >= deadline:
                    raise OSError("stream journal lease is busy or unavailable") from exc
                time.sleep(0.025)
        yield True
    finally:
        try:
            if handle is not None:
                try:
                    if os_locked:
                        handle.seek(0)
                        if os.name == "nt":
                            import msvcrt
                            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                        else:
                            import fcntl
                            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                finally:
                    handle.close()
        finally:
            local.release()


def _valid_request_id(value: Any) -> bool:
    if type(value) is not str or len(value) != 32 or value != value.lower():
        return False
    try:
        parsed = uuid.UUID(hex=value)
    except (ValueError, AttributeError):
        return False
    return parsed.version == 4 and parsed.hex == value


def _is_reparse_or_symlink(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(info.st_mode):
        return True
    attributes = getattr(info, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse_flag)


def _assert_regular_inside(path: Path, root: Path, *, expected_parent: Path | None = None) -> os.stat_result:
    if _is_reparse_or_symlink(path):
        raise ValueError("stream journal path cannot be a symlink or reparse point")
    root_real = root.resolve(strict=True)
    path_real = path.resolve(strict=True)
    try:
        path_real.relative_to(root_real)
    except ValueError as exc:
        raise ValueError("stream journal escapes its bound session") from exc
    if expected_parent is not None and path_real.parent != expected_parent.resolve(strict=True):
        raise ValueError("stream journal is not in its bound journal directory")
    info = path.stat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("stream journal is not a regular file")
    return info


def _encode_line(record: dict[str, Any]) -> bytes:
    payload = (json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    if len(payload) > MAX_RECORD_BYTES:
        raise ValueError("stream journal record exceeds size limit")
    return payload


def _sync_line(handle, record: dict[str, Any]) -> int:
    payload = _encode_line(record)
    handle.write(payload.decode("utf-8"))
    handle.flush()
    os.fsync(handle.fileno())
    return len(payload)


@dataclass
class StreamJournal:
    """Bounded, request-local append-only journal; each visible delta is fsynced first."""
    session_dir: Path
    session_id: str
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    run_id: str | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _bytes_written: int = field(default=0, init=False, repr=False)
    _record_count: int = field(default=0, init=False, repr=False)

    def __post_init__(self) -> None:
        if not _valid_request_id(self.request_id):
            raise ValueError("stream request id must be a canonical UUIDv4 hex value")
        if type(self.session_id) is not str or not self.session_id:
            raise ValueError("stream session identity is invalid")
        if self.run_id is not None and (type(self.run_id) is not str or not self.run_id):
            raise ValueError("stream run identity is invalid")
        session = self.session_dir.resolve(strict=True)
        if _is_reparse_or_symlink(self.session_dir) or not session.is_dir():
            raise ValueError("stream journal session root is not a regular directory")
        directory = session / JOURNAL_DIRNAME
        if os.path.lexists(directory) and _is_reparse_or_symlink(directory):
            raise ValueError("stream journal directory cannot be a link")
        directory.mkdir(exist_ok=True)
        directory_real = directory.resolve(strict=True)
        if directory_real.parent != session or not directory_real.is_dir():
            raise ValueError("stream journal directory escapes its session")
        leases_dir = directory_real / ".leases"
        if os.path.lexists(leases_dir) and _is_reparse_or_symlink(leases_dir):
            raise ValueError("stream journal lease directory cannot be a link")
        leases_dir.mkdir(exist_ok=True)
        if leases_dir.resolve(strict=True).parent != directory_real:
            raise ValueError("stream journal lease directory escapes its session")
        self.session_dir = session
        self.path = directory_real / f"{self.request_id}.jsonl"
        self._lease_cm = journal_lease(lease_path_for(self.path))
        if not self._lease_cm.__enter__():
            raise OSError("stream journal lease is busy")
        try:
            record = {"kind": "start", "session_id": self.session_id,
                      "request_id": self.request_id, "run_id": self.run_id}
            payload_size = len(_encode_line(record))
            with self.path.open("x", encoding="utf-8", newline="\n") as handle:
                _sync_line(handle, record)
            self._bytes_written = payload_size
            self._record_count = 1
        except BaseException:
            self.release_lease()
            raise

    def release_lease(self) -> None:
        manager = getattr(self, "_lease_cm", None)
        if manager is not None:
            self._lease_cm = None
            manager.__exit__(None, None, None)

    def append(self, text: str) -> None:
        if not isinstance(text, str) or not text:
            return
        record = {"kind": "delta", "session_id": self.session_id,
                  "request_id": self.request_id, "run_id": self.run_id, "text": text}
        payload_size = len(_encode_line(record))
        with self._lock:
            if self._record_count >= MAX_RECORD_COUNT or self._bytes_written + payload_size > MAX_JOURNAL_BYTES:
                raise ValueError("stream journal limit exceeded")
            before = _assert_regular_inside(self.path, self.session_dir, expected_parent=self.session_dir / JOURNAL_DIRNAME)
            with self.path.open("a", encoding="utf-8", newline="\n") as handle:
                opened = os.fstat(handle.fileno())
                current = self.path.lstat()
                if ((opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
                        or (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino)
                        or opened.st_size != self._bytes_written):
                    raise ValueError("stream journal changed concurrently or escaped its session")
                _sync_line(handle, record)
            self._bytes_written += payload_size
            self._record_count += 1

    def content(self) -> str:
        rows = read_journal(
            self.path, expected_session_dir=self.session_dir,
            expected_session_id=self.session_id, expected_request_id=self.request_id,
        )
        return "".join(row["text"] for row in rows if row["kind"] == "delta")

    def settle(self) -> None:
        with self._lock:
            if not os.path.lexists(self.path):
                self.release_lease()
                return
            rows = read_journal(
                self.path, expected_session_dir=self.session_dir,
                expected_session_id=self.session_id, expected_request_id=self.request_id,
            )
            if any(row["kind"] == "settled" for row in rows):
                raise ValueError("stream journal is already settled")
            record = {"kind": "settled", "session_id": self.session_id,
                      "request_id": self.request_id, "run_id": self.run_id}
            payload_size = len(_encode_line(record))
            if self._record_count >= MAX_RECORD_COUNT or self._bytes_written + payload_size > MAX_JOURNAL_BYTES:
                raise ValueError("stream journal limit exceeded")
            before = _assert_regular_inside(self.path, self.session_dir, expected_parent=self.session_dir / JOURNAL_DIRNAME)
            with self.path.open("a", encoding="utf-8", newline="\n") as handle:
                opened = os.fstat(handle.fileno())
                current = self.path.lstat()
                if ((opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
                        or (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino)
                        or opened.st_size != self._bytes_written):
                    raise ValueError("stream journal changed concurrently or escaped its session")
                _sync_line(handle, record)
            # Cleanup only a validated journal. A crash before unlink is idempotently recoverable.
            try:
                _assert_regular_inside(self.path, self.session_dir, expected_parent=self.session_dir / JOURNAL_DIRNAME)
                self.path.unlink()
            finally:
                self.release_lease()


def read_journal(
    path: Path,
    *,
    expected_session_dir: Path,
    expected_session_id: str,
    expected_request_id: str,
) -> list[dict[str, Any]]:
    if not _valid_request_id(expected_request_id) or path.name != f"{expected_request_id}.jsonl":
        raise ValueError("stream journal filename/request identity mismatch")
    info = _assert_regular_inside(
        path, expected_session_dir, expected_parent=expected_session_dir / JOURNAL_DIRNAME,
    )
    if info.st_size > MAX_JOURNAL_BYTES:
        raise ValueError("stream journal exceeds total size limit")
    rows: list[dict[str, Any]] = []
    total_bytes = 0
    settled = False
    with path.open("rb") as handle:
        opened = os.fstat(handle.fileno())
        if (opened.st_dev, opened.st_ino, opened.st_size) != (info.st_dev, info.st_ino, info.st_size):
            raise ValueError("stream journal changed while opening")
        while True:
            raw = handle.readline(MAX_RECORD_BYTES + 1)
            if not raw:
                break
            total_bytes += len(raw)
            if len(raw) > MAX_RECORD_BYTES or total_bytes > MAX_JOURNAL_BYTES:
                raise ValueError("stream journal record or total size limit exceeded")
            if len(rows) >= MAX_RECORD_COUNT:
                raise ValueError("stream journal record count limit exceeded")
            if not raw.endswith(b"\n"):
                raise ValueError("incomplete stream journal tail")
            row = json.loads(raw.decode("utf-8", errors="strict"))
            if type(row) is not dict:
                raise ValueError("invalid stream journal record")
            if settled:
                raise ValueError("stream journal contains records after settlement")
            if not rows:
                if set(row) != {"kind", "session_id", "request_id", "run_id"} or row.get("kind") != "start":
                    raise ValueError("stream journal lacks a valid start record")
                if (row.get("session_id") != expected_session_id
                        or row.get("request_id") != expected_request_id):
                    raise ValueError("stream journal is bound to another session or request")
                run_id = row.get("run_id")
                if run_id is not None and (type(run_id) is not str or not run_id):
                    raise ValueError("stream journal run identity is invalid")
            else:
                common = {"kind", "session_id", "request_id", "run_id"}
                if (row.get("session_id") != expected_session_id
                        or row.get("request_id") != expected_request_id
                        or row.get("run_id") != rows[0].get("run_id")):
                    raise ValueError("stream journal identity changed")
                if row.get("kind") == "delta":
                    if set(row) != common | {"text"} or type(row.get("text")) is not str or not row["text"]:
                        raise ValueError("stream journal delta is invalid")
                elif row.get("kind") == "settled":
                    if set(row) != common:
                        raise ValueError("stream journal settlement is invalid")
                    settled = True
                else:
                    raise ValueError("unknown stream journal record")
            rows.append(row)
    if not rows:
        raise ValueError("empty stream journal")
    return rows


def pending_journals(session_dir: Path, session_id: str) -> list[Path]:
    if type(session_id) is not str or not session_id:
        raise ValueError("invalid bound session identity")
    session = session_dir.resolve(strict=True)
    if _is_reparse_or_symlink(session_dir) or not session.is_dir():
        raise ValueError("session root is not a regular directory")
    directory = session / JOURNAL_DIRNAME
    if not os.path.lexists(directory):
        return []
    if _is_reparse_or_symlink(directory):
        raise ValueError("stream journal directory cannot be a link")
    directory_real = directory.resolve(strict=True)
    if directory_real.parent != session or not directory_real.is_dir():
        raise ValueError("stream journal directory escapes its session")
    leases_dir = directory_real / ".leases"
    if os.path.lexists(leases_dir):
        if _is_reparse_or_symlink(leases_dir) or not leases_dir.is_dir():
            raise ValueError("invalid stream journal lease directory")
        if leases_dir.resolve(strict=True).parent != directory_real:
            raise ValueError("stream journal lease directory escapes its session")
        for lease in leases_dir.iterdir():
            if lease.suffix != ".lock" or not _valid_request_id(lease.stem):
                raise ValueError("unexpected stream journal lease entry")
            _assert_regular_inside(lease, session, expected_parent=leases_dir)
    paths: list[Path] = []
    for entry in directory_real.iterdir():
        if entry.name == ".leases":
            continue
        if entry.suffix != ".jsonl" or not _valid_request_id(entry.stem):
            raise ValueError("unexpected entry in stream journal directory")
        _assert_regular_inside(entry, session, expected_parent=directory_real)
        paths.append(entry)
    return sorted(paths)
