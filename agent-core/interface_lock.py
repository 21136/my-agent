"""Electron ↔ CLI session interface lock (DESKTOP.md §4.5, TASKS T-904i)."""

from __future__ import annotations

import atexit
import json
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Literal

_AGENT_CORE = Path(__file__).resolve().parent
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from paths import AgentPaths
from session import sessions_root, utc_now_iso

InterfaceUi = Literal["electron", "cli", "terminal"]
_LOCK_FILENAME = ".interface.lock"


class InterfaceLockError(Exception):
    """Lock could not be acquired."""


@dataclass(frozen=True, slots=True)
class InterfaceLock:
    ui: InterfaceUi
    pid: int
    since: str

    def to_dict(self) -> dict[str, Any]:
        return {"ui": self.ui, "pid": self.pid, "since": self.since}


class AcquireStatus(str, Enum):
    ACQUIRED = "acquired"
    CONFLICT = "conflict"


@dataclass(frozen=True, slots=True)
class AcquireResult:
    status: AcquireStatus
    lock: InterfaceLock | None = None
    holder: InterfaceLock | None = None


def lock_path(paths: AgentPaths) -> Path:
    return sessions_root(paths) / _LOCK_FILENAME


def read_lock(paths: AgentPaths) -> InterfaceLock | None:
    path = lock_path(paths)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    ui = payload.get("ui")
    pid = payload.get("pid")
    since = payload.get("since")
    if ui not in {"electron", "cli", "terminal"}:
        return None
    try:
        pid_int = int(pid)
    except (TypeError, ValueError):
        return None
    if not isinstance(since, str) or not since.strip():
        since = ""
    return InterfaceLock(ui=ui, pid=pid_int, since=since.strip())


def is_pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if handle:
            kernel32.CloseHandle(handle)
            return True
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    else:
        return True


def is_stale(lock: InterfaceLock) -> bool:
    return not is_pid_alive(lock.pid)


def write_lock(paths: AgentPaths, ui: InterfaceUi) -> InterfaceLock:
    record = InterfaceLock(ui=ui, pid=os.getpid(), since=utc_now_iso())
    path = lock_path(paths)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return record


def release_lock(paths: AgentPaths, *, ui: InterfaceUi | None = None) -> bool:
    """Remove lock file when held by this process (and optional *ui*)."""
    current = read_lock(paths)
    if current is None:
        return False
    if current.pid != os.getpid():
        return False
    if ui is not None and current.ui != ui:
        return False
    try:
        lock_path(paths).unlink()
    except OSError:
        return False
    return True


def acquire_lock(
    paths: AgentPaths,
    ui: InterfaceUi,
    *,
    takeover: bool = False,
) -> AcquireResult:
    """Acquire or refuse the interface lock.

    TM-9 / TM-19: *takeover* is ignored — live locks are never stolen.
    """
    _ = takeover
    existing = read_lock(paths)
    if existing is not None and not is_stale(existing):
        if existing.pid == os.getpid() and existing.ui == ui:
            return AcquireResult(status=AcquireStatus.ACQUIRED, lock=existing)
        return AcquireResult(status=AcquireStatus.CONFLICT, holder=existing)
    record = write_lock(paths, ui)
    return AcquireResult(status=AcquireStatus.ACQUIRED, lock=record)


def _ui_label(ui: InterfaceUi) -> str:
    return {
        "electron": "Electron 桌面",
        "cli": "CLI REPL",
        "terminal": "Terminal 狂野模式",
    }[ui]


def format_holder_message(holder: InterfaceLock, *, requesting_ui: InterfaceUi) -> str:
    ui_label = _ui_label(holder.ui)
    req_label = _ui_label(requesting_ui)
    since = holder.since or "(unknown)"
    return (
        f"{req_label} 无法启动：{ui_label} 正在占用会话 "
        f"(pid {holder.pid}, since {since})。"
    )


def format_takeover_hint(requesting_ui: InterfaceUi) -> str:
    if requesting_ui == "terminal":
        return "请关闭占用中的界面后重试（Terminal 不支持接管）。"
    if requesting_ui == "cli":
        return "请先 exit 终端 REPL / Terminal，再启动本入口（不支持接管会话 · TM-9）。"
    return "请先 exit 终端 REPL / Terminal，再启动桌面（不支持接管会话 · TM-9）。"


def prompt_takeover(holder: InterfaceLock, *, requesting_ui: InterfaceUi) -> bool:
    """Interactive y/n when *takeover* was requested."""
    message = format_holder_message(holder, requesting_ui=requesting_ui)
    try:
        answer = input(f"{message}\n是否接管会话？(y/n) ").strip().casefold()
    except (EOFError, KeyboardInterrupt):
        return False
    return answer in {"y", "yes", "是", "好"}


def ensure_interface_lock(
    paths: AgentPaths,
    ui: InterfaceUi,
    *,
    takeover: bool = False,
    interactive_takeover: bool = True,
) -> InterfaceLock:
    """Acquire lock or raise :class:`InterfaceLockError`."""
    if takeover:
        raise InterfaceLockError(
            "interface lock takeover is disabled (TM-9); exit the other UI first"
        )

    result = acquire_lock(paths, ui, takeover=False)
    if result.status == AcquireStatus.ACQUIRED and result.lock is not None:
        return result.lock

    holder = result.holder
    if holder is None:
        raise InterfaceLockError("interface lock conflict (unknown holder)")

    message = format_holder_message(holder, requesting_ui=ui)
    hint = format_takeover_hint(ui)
    raise InterfaceLockError(f"{message} {hint}")


def lock_conflict_payload(holder: InterfaceLock) -> dict[str, Any]:
    return {
        "ready": False,
        "error": "lock_conflict",
        "lock": holder.to_dict(),
    }


@dataclass
class InterfaceLockGuard:
    """Hold interface lock for process lifetime."""

    paths: AgentPaths
    ui: InterfaceUi
    _held: bool = False

    def acquire(self, *, takeover: bool = False, interactive_takeover: bool = True) -> None:
        ensure_interface_lock(
            self.paths,
            self.ui,
            takeover=takeover,
            interactive_takeover=interactive_takeover,
        )
        self._held = True
        atexit.register(self.release)

    def release(self) -> None:
        if not self._held:
            return
        release_lock(self.paths, ui=self.ui)
        self._held = False


def _demo() -> int:
    import shutil
    import tempfile

    from session import create_new

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "evolve").mkdir()
        (root / "evolve" / "_index.core.toml").write_text("", encoding="utf-8")
        (root / "data").mkdir()
        paths = AgentPaths.from_root(root)

        first = acquire_lock(paths, "cli")
        assert first.status == AcquireStatus.ACQUIRED
        assert first.lock is not None
        print("[PASS] T-904i: acquire cli lock")

        second = acquire_lock(paths, "electron")
        assert second.status == AcquireStatus.CONFLICT
        assert second.holder is not None and second.holder.ui == "cli"
        print("[PASS] T-904i: electron blocked by live cli lock")

        taken = acquire_lock(paths, "electron", takeover=True)
        assert taken.status == AcquireStatus.CONFLICT
        print("[PASS] T-5704: takeover cannot steal live lock (TM-9)")

        assert release_lock(paths, ui="cli")
        assert read_lock(paths) is None
        print("[PASS] T-904i: release lock")

        session = create_new(paths, conversation_id="_repl_lock_demo")
        assert lock_path(paths).parent.is_dir()
        session_dir = paths.data / "sessions" / "_repl_lock_demo"
        if session_dir.is_dir():
            shutil.rmtree(session_dir)

    stale = InterfaceLock(ui="cli", pid=9_999_999, since=datetime.now(UTC).isoformat())
    assert is_stale(stale)
    print("[PASS] T-904i: dead pid treated as stale")

    return 0


if __name__ == "__main__":
    raise SystemExit(_demo())
