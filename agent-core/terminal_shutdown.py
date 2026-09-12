"""Terminal console shutdown hooks (Windows close button · Ink child exit)."""

from __future__ import annotations

import atexit
import os
import sys
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from cli_terminal import TerminalRepl
    from interface_lock import InterfaceLockGuard


_WIN_HANDLER_REF: object | None = None


def cleanup_terminal_session(
    lock_guard: InterfaceLockGuard,
    repl: TerminalRepl | None,
) -> None:
    """Release the interface lock and stop Ink / REPL loops."""
    if repl is not None:
        repl._stop = True
        bridge = repl._ink_bridge
        if bridge is not None:
            bridge.signal_shutdown()
            try:
                bridge.close()
            except OSError:
                pass
    try:
        lock_guard.release()
    except Exception:
        pass


def _install_windows_console_handler(callback: Callable[[], None]) -> object | None:
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:
        return None

    CTRL_CLOSE_EVENT = 2
    CTRL_LOGOFF_EVENT = 5
    CTRL_SHUTDOWN_EVENT = 6
    HandlerRoutine = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)

    @HandlerRoutine
    def handler(ctrl_type: int) -> bool:
        if ctrl_type not in (CTRL_CLOSE_EVENT, CTRL_LOGOFF_EVENT, CTRL_SHUTDOWN_EVENT):
            return False
        try:
            callback()
        except Exception:
            pass
        os._exit(0)

    kernel32 = ctypes.windll.kernel32
    if kernel32.SetConsoleCtrlHandler(handler, True) == 0:
        return None
    return handler


@dataclass
class TerminalShutdownHooks:
    """Register cleanup for normal exit, atexit, and Windows console close."""

    lock_guard: InterfaceLockGuard
    repl_holder: list[TerminalRepl | None] = field(default_factory=lambda: [None])
    _installed: bool = field(default=False, repr=False)
    _ran: bool = field(default=False, repr=False)

    def install(self) -> None:
        if self._installed:
            return
        global _WIN_HANDLER_REF
        atexit.register(self.run)
        _WIN_HANDLER_REF = _install_windows_console_handler(self.run)
        self._installed = True

    def run(self) -> None:
        if self._ran:
            return
        self._ran = True
        cleanup_terminal_session(self.lock_guard, self.repl_holder[0])
