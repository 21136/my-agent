"""Cross-process lease primitives for long-running project execution."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from paths import AgentPaths
from project_mode import project_dir

LEASE_FILENAME = "runaway-lease.json"
DEFAULT_LEASE_TTL_SECONDS = 120.0
MIN_HEARTBEAT_SECONDS = 1.0


def lease_path(paths: AgentPaths, project_id: str) -> Path:
    return project_dir(paths, project_id.strip()) / ".plan-agent" / LEASE_FILENAME


def _now() -> float:
    return time.time()


def _record(*, project_id: str, token: str, ttl_seconds: float, timestamp: float) -> dict[str, Any]:
    return {
        "project_id": project_id,
        "pid": os.getpid(),
        "token": token,
        "acquired_at": timestamp,
        "heartbeat_at": timestamp,
        "heartbeat_unix": timestamp,
        "ttl_seconds": ttl_seconds,
    }


def _read(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


@contextmanager
def _operation_lock(path: Path):
    lock_path = path.with_name(f".{path.name}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as handle:
        if handle.seek(0, os.SEEK_END) == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _write_new(path: Path, record: dict[str, Any]) -> bool:
    payload = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return False
    except OSError:
        return False
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        return True
    except OSError:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        return False


def _write_existing(path: Path, record: dict[str, Any]) -> bool:
    temporary: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.stem}-",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(record, handle, ensure_ascii=False, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
        return True
    except OSError:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        return False


def _process_alive(pid: Any) -> bool:
    try:
        value = int(pid)
    except (TypeError, ValueError):
        return False
    if value <= 0:
        return False
    try:
        os.kill(value, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _record_stale(record: dict[str, Any], *, now: float, ttl_seconds: float) -> bool:
    heartbeat = record.get("heartbeat_unix")
    try:
        age = now - float(heartbeat)
    except (TypeError, ValueError):
        return False
    try:
        pid = int(record.get("pid"))
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if not _process_alive(pid):
        return True
    return age > max(MIN_HEARTBEAT_SECONDS, ttl_seconds)


@dataclass
class RunawayLease:
    project_id: str
    path: Path
    token: str
    ttl_seconds: float
    heartbeat_seconds: float
    _stop: threading.Event = field(default_factory=threading.Event, repr=False)
    _thread: threading.Thread | None = field(default=None, repr=False)

    def start(self) -> RunawayLease:
        self._thread = threading.Thread(
            target=self._heartbeat_loop,
            name=f"runaway-lease-{self.project_id}",
            daemon=True,
        )
        self._thread.start()
        return self

    def _heartbeat_loop(self) -> None:
        while not self._stop.wait(self.heartbeat_seconds):
            if not self.heartbeat():
                return

    def heartbeat(self) -> bool:
        with _operation_lock(self.path):
            current = _read(self.path)
            if not current or current.get("token") != self.token:
                self._stop.set()
                return False
            timestamp = _now()
            current["heartbeat_at"] = timestamp
            current["heartbeat_unix"] = timestamp
            current["pid"] = os.getpid()
            current["ttl_seconds"] = self.ttl_seconds
            return _write_existing(self.path, current)

    def release(self) -> bool:
        self._stop.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=max(1.0, self.heartbeat_seconds * 2))
        with _operation_lock(self.path):
            current = _read(self.path)
            if not current or current.get("token") != self.token:
                return False
            try:
                self.path.unlink()
            except FileNotFoundError:
                return False
            except OSError:
                return False
            return True


def reclaim_local_idle_lease(paths: AgentPaths, project_id: str) -> bool:
    """Drop a lease file owned by this process when no turn holds it (orphan cleanup)."""
    pid = project_id.strip()
    if not pid:
        return False
    path = lease_path(paths, pid)
    with _operation_lock(path):
        current = _read(path)
        if not current:
            return False
        try:
            owner = int(current.get("pid"))
        except (TypeError, ValueError):
            return False
        if owner != os.getpid():
            return False
        try:
            path.unlink()
            return True
        except OSError:
            return False


def acquire_runaway_lease_with_reclaim(
    paths: AgentPaths,
    project_id: str,
    *,
    ttl_seconds: float = DEFAULT_LEASE_TTL_SECONDS,
    reclaim_local_orphan: bool = False,
) -> RunawayLease | None:
    lease = acquire_runaway_lease(paths, project_id, ttl_seconds=ttl_seconds)
    if lease is not None:
        return lease
    if reclaim_local_orphan and reclaim_local_idle_lease(paths, project_id):
        return acquire_runaway_lease(paths, project_id, ttl_seconds=ttl_seconds)
    return None


def acquire_runaway_lease(
    paths: AgentPaths,
    project_id: str,
    *,
    ttl_seconds: float = DEFAULT_LEASE_TTL_SECONDS,
) -> RunawayLease | None:
    pid = project_id.strip()
    if not pid:
        return None
    ttl = max(MIN_HEARTBEAT_SECONDS * 2, float(ttl_seconds))
    path = lease_path(paths, pid)
    path.parent.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    record = _record(project_id=pid, token=token, ttl_seconds=ttl, timestamp=_now())
    with _operation_lock(path):
        if not _write_new(path, record):
            current = _read(path)
            if current is None or not _record_stale(current, now=_now(), ttl_seconds=ttl):
                return None
            stale_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.stale")
            try:
                path.rename(stale_path)
            except OSError:
                return None
            try:
                if not _write_new(path, record):
                    return None
            finally:
                try:
                    stale_path.unlink(missing_ok=True)
                except OSError:
                    pass
    heartbeat_seconds = max(MIN_HEARTBEAT_SECONDS, min(ttl / 3, 30.0))
    return RunawayLease(pid, path, token, ttl, heartbeat_seconds).start()
