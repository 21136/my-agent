"""Bottom-pinned terminal layout (Claude Code viewport)."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from terminal_app import bottom_layout_enabled
from terminal_ui import WelcomeContent, format_welcome_plain


class BottomLayoutFlagTests(unittest.TestCase):
    def test_bottom_layout_default_on_when_prompt_toolkit_ok(self) -> None:
        with mock.patch("terminal_app.prompt_toolkit_enabled", return_value=True):
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("MY_AGENT_TERMINAL_LAYOUT", None)
                self.assertTrue(bottom_layout_enabled())

    def test_bottom_layout_off_when_scroll(self) -> None:
        with mock.patch("terminal_app.prompt_toolkit_enabled", return_value=True):
            with mock.patch.dict(
                os.environ,
                {"MY_AGENT_TERMINAL_LAYOUT": "scroll"},
                clear=False,
            ):
                self.assertFalse(bottom_layout_enabled())

    def test_mouse_support_default_off_on_windows(self) -> None:
        from terminal_app import mouse_support_enabled

        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MY_AGENT_TERMINAL_MOUSE", None)
            with mock.patch("sys.platform", "win32"):
                self.assertFalse(mouse_support_enabled())
            with mock.patch.dict(
                os.environ,
                {"MY_AGENT_TERMINAL_MOUSE": "1"},
                clear=False,
            ):
                self.assertTrue(mouse_support_enabled())


class BottomPickerLogicTests(unittest.TestCase):
    def test_finish_picker_puts_result(self) -> None:
        from terminal_app import BottomPinnedTerminal, _PICKER_CANCEL

        term = object.__new__(BottomPinnedTerminal)
        term._picker_active = True
        term._picker_queue = __import__("queue").Queue(maxsize=1)
        term._app = mock.Mock(is_running=False)
        term._finish_picker("deepseek-v4-pro")
        self.assertFalse(term._picker_active)
        self.assertEqual(term._picker_queue.get_nowait(), "deepseek-v4-pro")

        term._picker_active = True
        term._finish_picker(_PICKER_CANCEL)
        self.assertIs(term._picker_queue.get_nowait(), _PICKER_CANCEL)


class ClipboardHelperTests(unittest.TestCase):
    def test_copy_to_system_clipboard_rejects_empty(self) -> None:
        from terminal_app import _copy_to_system_clipboard

        self.assertFalse(_copy_to_system_clipboard(""))
        self.assertFalse(_copy_to_system_clipboard("   "))


class BottomTerminalInitTests(unittest.TestCase):
    def test_bottom_terminal_builds_without_console(self) -> None:
        from terminal_app import BottomPinnedTerminal
        from terminal_ui import TerminalConsole

        console = TerminalConsole.create(write=None)
        with mock.patch("prompt_toolkit.Application"):
            term = BottomPinnedTerminal(console)
        self.assertIsNotNone(term._transcript)
        self.assertTrue(term._transcript.window.content.focusable())
        self.assertIsNotNone(term._input_window)


class TranscriptWriteTests(unittest.TestCase):
    def test_write_unlocks_readonly_textarea(self) -> None:
        import threading

        from prompt_toolkit.widgets import TextArea

        from terminal_app import BottomPinnedTerminal

        term = object.__new__(BottomPinnedTerminal)
        term._lock = threading.Lock()
        term._transcript = TextArea(text="", read_only=True, scrollbar=True)
        term._welcome_compact = False
        term._transcript_follow_tail = True
        term._pending_raw = ""
        term._raw_flush_scheduled = False
        term._app = mock.Mock(is_running=False)
        term.write("hello")
        self.assertIn("hello", term._transcript.buffer.text)
        term.append_transcript_raw("world")
        self.assertIn("world", term._transcript.buffer.text)

    def test_schedule_ui_uses_call_soon_threadsafe_when_running(self) -> None:
        from terminal_app import BottomPinnedTerminal

        term = object.__new__(BottomPinnedTerminal)
        calls: list[str] = []

        def _cb() -> None:
            calls.append("ran")

        loop = mock.Mock()
        loop.is_closed.return_value = False

        def _soon(fn: object) -> None:
            if callable(fn):
                fn()

        loop.call_soon_threadsafe.side_effect = _soon
        app = mock.Mock()
        app.is_running = True
        app.loop = loop
        term._app = app
        term._schedule_ui(_cb)
        self.assertEqual(calls, ["ran"])
        loop.call_soon_threadsafe.assert_called_once()

    def test_scroll_transcript_moves_cursor_not_just_vertical_scroll(self) -> None:
        import threading

        from prompt_toolkit.document import Document
        from prompt_toolkit.widgets import TextArea

        from terminal_app import BottomPinnedTerminal

        term = object.__new__(BottomPinnedTerminal)
        term._lock = threading.Lock()
        term._transcript = TextArea(
            text="a\nb\nc\nd\ne\nf\ng\n",
            read_only=True,
            scrollbar=True,
        )
        term._transcript_follow_tail = True
        term._app = mock.Mock(is_running=False)
        with term._transcript_edit() as buf:
            buf.document = Document(
                text=buf.text,
                cursor_position=len(buf.text),
            )
        before = term._transcript.buffer.document.cursor_position_row
        term._scroll_transcript(-3)
        self.assertFalse(term._transcript_follow_tail)
        after = term._transcript.buffer.document.cursor_position_row
        self.assertEqual(after, before - 3)


class WelcomeVisibilityTests(unittest.TestCase):
    def test_welcome_visible_tracks_mount_and_dismiss(self) -> None:
        from terminal_app import BottomPinnedTerminal

        term = object.__new__(BottomPinnedTerminal)
        term._welcome_visible = False
        term._welcome_panel = None
        term._app = mock.Mock(is_running=False)
        term._transcript_follow_tail = True
        term._transcript = None
        self.assertFalse(term.welcome_visible)

        term.mount_welcome(mock.Mock())
        self.assertTrue(term.welcome_visible)

        term.dismiss_welcome()
        self.assertFalse(term.welcome_visible)


class WelcomePlainTests(unittest.TestCase):
    def test_format_welcome_plain_includes_workspace_and_root(self) -> None:
        panel = WelcomeContent(
            effective_root="D:/my-agent",
            llm_model="flash",
            terminal_scope_kind="agent",
            harness="terminal",
            terminal_cwd=".",
            session_id="sess-1",
            resume=True,
            workspace_name="my-agent",
            left_lines=("欢迎回来", "my-agent", "D:/my-agent"),
            right_lines=(),
        )
        text = format_welcome_plain(panel)
        self.assertIn("my-agent", text)
        self.assertIn("v0.2.1", text)
        self.assertIn("忆梦", text)
        self.assertIn("打工仔", text)
        self.assertIn("D:/my-agent", text)


if __name__ == "__main__":
    unittest.main()
