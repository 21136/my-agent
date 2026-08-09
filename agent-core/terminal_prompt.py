"""Claude-style terminal input — thin wrapper over vendored clawcodex prompt setup."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from terminal_ui import TerminalConsole

from vendor.clawcodex_prompt import create_claude_prompt_session, register_shift_enter_sequences


def prompt_toolkit_enabled() -> bool:
    raw = os.environ.get("MY_AGENT_TERMINAL_PROMPT", "auto").strip().casefold()
    if raw in {"0", "false", "off", "plain"}:
        return False
    if os.environ.get("NO_COLOR"):
        return False
    try:
        if not sys.stdin.isatty():
            return False
    except Exception:
        return False
    try:
        import prompt_toolkit  # noqa: F401

        return True
    except ImportError:
        return False


def _enable_windows_vt() -> None:
    from terminal_picker import _enable_windows_vt

    _enable_windows_vt()


def _history_path(console: TerminalConsole) -> Path | None:
    paths = console.paths
    if paths is None:
        return None
    target = paths.data / "terminal_history"
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def _draw_input_frame() -> None:
    """Visible border when terminal bg-fill is weak (CMD / some WT themes)."""
    try:
        width = shutil.get_terminal_size(fallback=(100, 24)).columns
    except OSError:
        width = 100
    inner = max(20, width - 2)
    top = f"\033[38;5;240m╭{'─' * inner}╮\033[0m"
    sys.stdout.write(top + "\n")
    sys.stdout.flush()


class TerminalPromptSession:
    """Blocking Claude-style prompt (vendored clawcodex prompt_toolkit contract)."""

    def __init__(self, console: TerminalConsole) -> None:
        from prompt_toolkit.history import FileHistory, InMemoryHistory

        _enable_windows_vt()
        register_shift_enter_sequences()
        self._console = console

        history_file = _history_path(console)
        if history_file is not None:
            history: Any = FileHistory(str(history_file))
        else:
            history = InMemoryHistory()

        self._session = create_claude_prompt_session(
            history=history,
            bottom_toolbar=self._toolbar,
        )

    def _toolbar(self) -> Any:
        from prompt_toolkit.formatted_text import FormattedText

        from terminal_ui import (
            build_status_bar,
            format_activity_status,
            prompt_model_short,
            welcome_status_dot,
        )

        if self._console.session is None or self._console.paths is None:
            return FormattedText("")
        bar = build_status_bar(
            session=self._console.session,
            paths=self._console.paths,
            status=self._console.status,
            active_tool=self._console.active_tool,
            activity_detail=self._console.activity_detail,
        )
        model = prompt_model_short(bar.llm_model)
        dot = welcome_status_dot(bar.status)
        status = format_activity_status(
            bar.status,
            active_tool=bar.active_tool,
            activity_detail=bar.activity_detail,
        )
        dot_class = {
            "idle": "class:bottom-toolbar.dot-green",
            "working": "class:bottom-toolbar.dot-yellow",
            "cancelled": "class:bottom-toolbar.dot-red",
        }.get(bar.status, "class:bottom-toolbar.dot-green")
        return FormattedText(
            [
                ("class:bottom-toolbar.model", model),
                ("class:bottom-toolbar", " · "),
                ("class:bottom-toolbar.root", bar.root_short),
                ("class:bottom-toolbar", " · agent · "),
                (dot_class, dot),
                ("class:bottom-toolbar.status", f" {status}"),
            ]
        )

    def read_line(self) -> str:
        if self._console.sink.backend.kind == "rich":
            self._console.sink.backend.render_line("")
        _draw_input_frame()
        return self._session.prompt("❯ ")
