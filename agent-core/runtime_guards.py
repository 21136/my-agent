"""Phase 16 runtime guards — turn wall, stall watchdog, env helpers (RUNTIME-GUARDS v0.2.0)."""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

DEFAULT_TURN_WALL_SEC = 900.0
DEFAULT_STALL_WATCHDOG_SEC = 0.0
DEFAULT_WRITE_INLINE_MAX_CHARS = 8192
DEFAULT_AUTO_DEMO_ON_WRITE_EVOLVE = True
DEFAULT_CHECKER_AUTO_ON_SCAFFOLD = False
SUBPROCESS_KILL_WAIT_SEC = 3.0
SCAFFOLD_DEMO_PREVIEW_CHARS = 2000

PROGRESS_EVENT_TYPES = frozenset(
    {
        "assistant.delta",
        "tool.start",
        "tool.end",
        "confirm.request",
        "confirm.done",
    }
)


def turn_wall_sec() -> float:
    raw = os.environ.get("TURN_WALL_SEC", str(int(DEFAULT_TURN_WALL_SEC)))
    try:
        value = float(raw)
    except ValueError:
        value = DEFAULT_TURN_WALL_SEC
    return max(0.0, value)


def write_inline_max_chars() -> int:
    raw = os.environ.get("WRITE_INLINE_MAX_CHARS", str(DEFAULT_WRITE_INLINE_MAX_CHARS))
    try:
        value = int(raw)
    except ValueError:
        value = DEFAULT_WRITE_INLINE_MAX_CHARS
    return max(1, value)


def auto_demo_on_write_evolve() -> bool:
    raw = os.environ.get("AUTO_DEMO_ON_WRITE_EVOLVE", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def checker_auto_on_scaffold() -> bool:
    """M1 T-1620: auto spawn checker after grow scaffold tool.toml + auto demo."""
    raw = os.environ.get(
        "CHECKER_AUTO_ON_SCAFFOLD",
        "1" if DEFAULT_CHECKER_AUTO_ON_SCAFFOLD else "0",
    ).strip().lower()
    return raw not in {"0", "false", "no", "off"}


def stall_watchdog_sec() -> float:
    raw = os.environ.get("STALL_WATCHDOG_SEC", str(int(DEFAULT_STALL_WATCHDOG_SEC)))
    try:
        value = float(raw)
    except ValueError:
        value = DEFAULT_STALL_WATCHDOG_SEC
    return max(0.0, value)


@dataclass
class TurnWatchdog:
    """Wall-clock + optional stall watchdog for one user turn (segment does not reset wall)."""

    cancel_event: threading.Event
    on_auto_timeout: Callable[[str], None]
    wall_sec: float = field(default_factory=turn_wall_sec)
    stall_sec: float = field(default_factory=stall_watchdog_sec)
    _started_at: float = field(default=0.0, init=False)
    _last_progress_at: float = field(default=0.0, init=False)
    _cancel_reason: str | None = field(default=None, init=False)
    _stop: threading.Event = field(default_factory=threading.Event, init=False)
    _thread: threading.Thread | None = field(default=None, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)

    def begin(self) -> None:
        now = time.monotonic()
        self._started_at = now
        self._last_progress_at = now
        with self._lock:
            self._cancel_reason = None
        self._stop.clear()
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, name="turn-watchdog", daemon=True)
        self._thread.start()

    def end(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)
        self._thread = None

    def touch_progress(self) -> None:
        self._last_progress_at = time.monotonic()

    def note_progress_event(self, event_type: str) -> None:
        if event_type in PROGRESS_EVENT_TYPES:
            self.touch_progress()

    def request_user_cancel(self) -> None:
        with self._lock:
            if self.cancel_event.is_set():
                return
            self._cancel_reason = "cancelled"

    def resolve_finish_reason(self, agent_finish_reason: str | None = None) -> str:
        with self._lock:
            if self._cancel_reason == "timeout":
                return "timeout"
        if agent_finish_reason == "timeout":
            return "timeout"
        with self._lock:
            if self._cancel_reason == "cancelled" or self.cancel_event.is_set():
                return "cancelled"
        if agent_finish_reason == "cancelled":
            return "cancelled"
        if agent_finish_reason:
            return agent_finish_reason
        return "completed"

    def resolve_interrupt_reason(self) -> str:
        with self._lock:
            if self._cancel_reason:
                return self._cancel_reason
        return "cancelled"

    def _loop(self) -> None:
        while not self._stop.is_set():
            if self.cancel_event.is_set():
                return
            now = time.monotonic()
            if self.wall_sec > 0 and now - self._started_at >= self.wall_sec:
                self._trigger_auto_timeout("回合已超过墙钟限制，已自动停止")
                return
            if self.stall_sec > 0 and now - self._last_progress_at >= self.stall_sec:
                self._trigger_auto_timeout("回合超时无响应，已自动停止")
                return
            time.sleep(0.05)

    def _trigger_auto_timeout(self, message: str) -> None:
        with self._lock:
            if self.cancel_event.is_set():
                return
            self._cancel_reason = "timeout"
        self.cancel_event.set()
        self.on_auto_timeout(message)


def _demo() -> int:
    cancel = threading.Event()
    watchdog = TurnWatchdog(
        cancel_event=cancel,
        on_auto_timeout=lambda _m: None,
        wall_sec=0.05,
        stall_sec=0,
    )
    watchdog.begin()
    time.sleep(0.08)
    watchdog.end()
    assert cancel.is_set()
    assert watchdog.resolve_finish_reason() == "timeout"
    assert turn_wall_sec() == 900.0
    assert stall_watchdog_sec() == 0.0
    assert write_inline_max_chars() == 8192
    assert auto_demo_on_write_evolve() is True
    assert checker_auto_on_scaffold() is False
    print("[PASS] T-1513/T-1510: runtime_guards defaults + wall timeout")
    print("[PASS] T-1511/T-1520: write_inline_max + auto_demo env defaults")
    print("[PASS] T-1620: checker_auto_on_scaffold default off")
    return 0


if __name__ == "__main__":
    raise SystemExit(_demo())
