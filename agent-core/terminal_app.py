"""Claude Code layout: transcript scrolls above, thin-rule input pinned at bottom.

Layout mirrors Claude Code / prompt_toolkit HSplit demos — NOT a Frame box::

    [transcript fills viewport]
    [optional /model picker — sits just above the input rule]
    ────────────────────────────
    > input (1 line, grows when multiline)
    ────────────────────────────
    model · root · agent · ● status
"""

from __future__ import annotations

import os
import queue
import re
import threading
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, Callable, Iterator, Sequence

if TYPE_CHECKING:
    from terminal_ui import TerminalConsole

from terminal_prompt import _enable_windows_vt, _history_path, prompt_toolkit_enabled
from vendor.clawcodex_prompt import register_shift_enter_sequences

_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_PICKER_CANCEL = object()


def _copy_to_system_clipboard(text: str) -> bool:
    """Copy transcript selection to the OS clipboard when possible."""
    payload = (text or "").strip("\r\n")
    if not payload.strip():
        return False
    try:
        import subprocess
        import sys

        if sys.platform == "win32":
            subprocess.run(
                ["clip"],
                input=payload,
                text=True,
                check=False,
                shell=True,
            )
            return True
    except Exception:
        pass
    try:
        import pyperclip

        pyperclip.copy(payload)
        return True
    except Exception:
        return False


def bottom_layout_enabled() -> bool:
    """Fullscreen bottom TUI (welcome + pinned input) — default on.

    Disable with ``MY_AGENT_TERMINAL_LAYOUT=scroll`` / ``plain`` / ``0`` / ``off``
    for native terminal scrollback instead.
    """
    if not prompt_toolkit_enabled():
        return False
    raw = os.environ.get("MY_AGENT_TERMINAL_LAYOUT", "bottom").strip().casefold()
    return raw not in {"scroll", "plain", "0", "off"}


def mouse_support_enabled() -> bool:
    """Whether Bottom TUI should capture the mouse.

    On Windows, ``mouse_support=True`` commonly breaks native WT select/copy, and
    the wheel arrives as ScrollUp/ScrollDown keys on the *focused* input — so the
    transcript never scrolls until the user types. Default off on win32.
    """
    raw = os.environ.get("MY_AGENT_TERMINAL_MOUSE", "").strip().casefold()
    if raw in {"1", "on", "true", "yes"}:
        return True
    if raw in {"0", "off", "false", "no"}:
        return False
    import sys

    return sys.platform != "win32"


def _rule_window() -> Any:
    from prompt_toolkit.layout import Window

    return Window(height=1, char="─", style="class:separator")


def make_bottom_confirm_input_fn(bottom: BottomPinnedTerminal) -> Callable[[str], str]:
    """Route tool confirm prompts to the bottom TUI instead of blocking stdin."""

    def confirm_input(prompt: str) -> str:
        allow_all = "a]llow" in prompt.casefold()
        return bottom.prompt_confirm(allow_approve_all=allow_all)

    return confirm_input


class BottomPinnedTerminal:
    """Full-screen UI — Claude-style thin rules + bottom-pinned input (no Frame)."""

    def __init__(self, console: TerminalConsole) -> None:
        from prompt_toolkit import Application
        from prompt_toolkit.buffer import Buffer
        from prompt_toolkit.filters import Condition
        from prompt_toolkit.history import FileHistory, InMemoryHistory
        from prompt_toolkit.layout import BufferControl, Dimension, HSplit, Layout, Window
        from prompt_toolkit.layout.containers import ConditionalContainer
        from prompt_toolkit.layout.controls import FormattedTextControl
        from prompt_toolkit.styles import Style
        from prompt_toolkit.widgets import TextArea

        _enable_windows_vt()
        register_shift_enter_sequences()
        self._console = console
        self._lock = threading.Lock()
        self._stop = False
        self._transcript = TextArea(
            text="",
            read_only=True,
            focus_on_click=True,
            scrollbar=True,
            wrap_lines=True,
            focusable=True,
            height=Dimension(weight=1),
        )
        # Keep cursor visible when focused so click-drag selection works
        # (calculator.py pattern; always_hide_cursor breaks select UX).
        self._attach_transcript_scroll_guard()
        self._input = Buffer()
        self._picker_active = False
        self._picker_models: list[Any] = []
        self._picker_index = 0
        self._picker_current = ""
        self._picker_queue: queue.Queue[Any] = queue.Queue(maxsize=1)
        self._confirm_active = False
        self._confirm_allow_all = False
        self._confirm_queue: queue.Queue[str] = queue.Queue(maxsize=1)
        self._welcome_visible = False
        self._welcome_panel: Any | None = None
        self._welcome_compact = False
        self._transcript_follow_tail = True
        # While a turn is streaming, never pause auto-tail — otherwise new text
        # lands below the viewport and looks like a "half answer" until the next
        # keypress/submit (which re-enables follow).
        self._force_follow = False
        # User browse position (vertical_scroll) — restored across transcript appends.
        self._transcript_pinned_scroll: int | None = None
        self._pending_raw = ""
        self._raw_flush_scheduled = False
        self._assistant_replace_start: int | None = None
        self._deferred_ui: list[Callable[[], None]] = []
        self._on_cancel_callback: Callable[[], bool] | None = None
        self._mouse_support = mouse_support_enabled()

        history_file = _history_path(console)
        if history_file is not None:
            self._input.history = FileHistory(str(history_file))
        else:
            self._input.history = InMemoryHistory()

        def _submit(text: str) -> None:
            stripped = text.strip()
            if not stripped:
                return
            if self._confirm_active:
                choice = self._normalize_confirm_choice(stripped, self._confirm_allow_all)
                if choice is None:
                    self.write(
                        "请输入 "
                        + ("y / n / a" if self._confirm_allow_all else "y / n")
                    )
                    return
                self._input.reset()
                self._finish_confirm(choice)
                return
            self._transcript_follow_tail = True
            self._transcript_pinned_scroll = None
            self._input.reset()
            self._focus_input()
            threading.Thread(target=self._on_submit, args=(stripped,), daemon=True).start()

        picker_active = Condition(lambda: self._picker_active)
        confirm_active = Condition(lambda: self._confirm_active)
        picker_idle = Condition(lambda: not self._picker_active and not self._confirm_active)
        welcome_full_active = Condition(
            lambda: self._welcome_visible and not self._welcome_compact
        )
        welcome_compact_active = Condition(
            lambda: self._welcome_visible and self._welcome_compact
        )

        def _line_prefix(line_number: int, wrap_count: int) -> Any:
            from prompt_toolkit.formatted_text import FormattedText

            if self._confirm_active and line_number == 0 and wrap_count == 0:
                return FormattedText([("class:prompt", "? ")])
            if line_number == 0 and wrap_count == 0:
                return FormattedText([("class:prompt", "> ")])
            return FormattedText([("class:prompt", "  ")])

        input_window = Window(
            content=BufferControl(buffer=self._input, focusable=True),
            height=Dimension(min=1, preferred=1, max=6),
            wrap_lines=True,
            dont_extend_height=True,
            get_line_prefix=_line_prefix,
        )
        self._input_window = input_window
        bindings = self._build_submit_bindings(
            _submit,
            picker_active,
            confirm_active,
            picker_idle,
        )
        welcome_full_window = Window(
            content=FormattedTextControl(self._welcome_formatted),
            height=Dimension(min=16, preferred=18, max=20),
            dont_extend_height=True,
            wrap_lines=False,
        )
        welcome_compact_window = Window(
            content=FormattedTextControl(self._welcome_compact_formatted),
            height=Dimension(min=3, preferred=3, max=4),
            dont_extend_height=True,
            wrap_lines=False,
        )
        picker_window = Window(
            content=FormattedTextControl(self._picker_formatted),
            height=Dimension(min=3, preferred=8, max=14),
            dont_extend_height=True,
            wrap_lines=False,
            style="class:picker",
        )
        confirm_window = Window(
            content=FormattedTextControl(self._confirm_formatted),
            height=Dimension(min=2, preferred=3, max=4),
            dont_extend_height=True,
            wrap_lines=False,
            style="class:picker",
        )
        status_window = Window(
            content=FormattedTextControl(self._toolbar),
            height=1,
            dont_extend_height=True,
        )
        root = HSplit(
            [
                ConditionalContainer(welcome_full_window, filter=welcome_full_active),
                ConditionalContainer(welcome_compact_window, filter=welcome_compact_active),
                self._transcript,
                ConditionalContainer(picker_window, filter=picker_active),
                ConditionalContainer(confirm_window, filter=confirm_active),
                _rule_window(),
                input_window,
                _rule_window(),
                status_window,
            ]
        )
        style = Style.from_dict(
            {
                # Inherit terminal bg — no gray slab on the footer row.
                "": "bg:default fg:#e8e8e8",
                "prompt": "bold fg:#f5f5f5 bg:default",
                "separator": "fg:#3a3a3a bg:default",
                "welcome.border": "fg:#b8956a bg:default",
                "welcome.greet": "bold fg:#f0ebe3 bg:default",
                "welcome.sub": "fg:#d8d0c4 bg:default",
                "welcome.meta-model": "fg:#c9a96e bg:default",
                "welcome.meta-sep": "fg:#5a5a5a bg:default",
                "welcome.meta-root": "fg:#7a9eb5 bg:default",
                "welcome.pixel-fill": "fg:#e8a54b bg:default",
                "welcome.pixel-shade": "fg:#a67c3a bg:default",
                "picker": "fg:#c8c8c8 bg:default",
                "picker.hint": "fg:#6a6a6a italic bg:default",
                "picker.selected": "bold fg:#5fd7ff bg:default",
                "picker.item": "fg:#b8b8b8 bg:default",
                "status.model": "fg:#d8d8d8 bg:default",
                "status.sep": "fg:#454545 bg:default",
                "status.root": "fg:#6a9fb5 bg:default",
                "status.label": "fg:#5a5a5a bg:default",
                "status.dot-green": "fg:#5cb85c bg:default",
                "status.dot-yellow": "fg:#d4a84b bg:default",
                "status.dot-red": "fg:#d47474 bg:default",
                "status.state-idle": "fg:#6a6a6a bg:default",
                "status.state-working": "fg:#d4a84b bg:default",
                "status.state-cancelled": "fg:#d47474 bg:default",
            }
        )

        self._app: Application[Any] = Application(
            layout=Layout(root, focused_element=input_window),
            full_screen=True,
            style=style,
            key_bindings=bindings,
            refresh_interval=0.1,
            mouse_support=self._mouse_support,
            enable_page_navigation_bindings=False,
        )
        self._on_submit_callback: Callable[[str], None] | None = None

    def bind_console(self, console: TerminalConsole) -> None:
        self._console = console

    def _focus_input(self) -> None:
        """Keep the prompt pinned at the bottom — transcript is scroll-only."""
        try:
            self._app.layout.focus(self._input_window)
        except Exception:
            pass

    def _schedule_ui(self, callback: Callable[[], None]) -> None:
        """Run UI mutations on the prompt_toolkit event loop thread.

        prompt_toolkit 3.x has no ``call_from_executor`` — use
        ``loop.call_soon_threadsafe`` (see ``shortcuts/dialogs.py`` log_text).

        Never mutate the Buffer from the agent worker thread: if the loop is
        briefly unavailable while the app is running, defer until the next
        successful schedule / invalidate.
        """
        app = getattr(self, "_app", None)
        running = bool(app is not None and getattr(app, "is_running", False) is True)
        if running:
            loop = getattr(app, "loop", None)
            if loop is not None and not loop.is_closed():
                try:
                    loop.call_soon_threadsafe(callback)
                    return
                except Exception:
                    pass
            with self._lock:
                self._deferred_ui.append(callback)
            return
        # App not started yet (welcome mount) — safe to run inline.
        callback()

    def _drain_deferred_ui(self) -> None:
        lock = getattr(self, "_lock", None)
        if lock is None:
            pending = list(getattr(self, "_deferred_ui", []) or [])
            self._deferred_ui = []
        else:
            with lock:
                pending = list(getattr(self, "_deferred_ui", []) or [])
                self._deferred_ui = []
        for callback in pending:
            try:
                callback()
            except Exception:
                pass

    def begin_turn_output(self) -> None:
        """Call when a turn starts streaming — resume auto-tail + shrink welcome.

        The user may still scroll up mid-stream (that pauses follow); appends
        keep flowing into the buffer either way.
        """

        def _do() -> None:
            self._drain_deferred_ui()
            self._flush_pending_raw_locked()
            self._assistant_replace_start = None
            self._transcript_follow_tail = True
            self._transcript_pinned_scroll = None
            if self._welcome_visible and not self._welcome_compact:
                self._welcome_compact = True
            self._invalidate_now()

        self._schedule_ui(_do)

    def end_turn_output(self) -> None:
        """Call when a turn goes idle — flush stream; snap to end only if following."""

        def _do() -> None:
            self._drain_deferred_ui()
            self._flush_pending_raw_locked()
            if self._transcript_follow_tail:
                from prompt_toolkit.document import Document

                with self._transcript_edit() as buf:
                    buf.document = Document(
                        text=buf.text, cursor_position=len(buf.text)
                    )
                self._transcript_pinned_scroll = None
            self._sync_welcome_compact()
            self._invalidate_now()

        self._schedule_ui(_do)

    def _following_tail(self) -> bool:
        return bool(getattr(self, "_transcript_follow_tail", True))

    def _restore_browse_scroll(self) -> None:
        """Re-apply pinned scroll after layout (mutate/invalidate resets viewport)."""
        if self._following_tail():
            return
        pinned = getattr(self, "_transcript_pinned_scroll", None)
        if pinned is None:
            return
        win = self._transcript.window
        info = getattr(win, "render_info", None)
        if info is None:
            return
        max_scroll = max(0, int(info.content_height) - int(info.window_height))
        win.vertical_scroll = max(0, min(max_scroll, int(pinned)))

    def _scroll_transcript(self, delta_lines: int) -> None:
        """Scroll the transcript viewport (input can stay focused).

        With ``wrap_lines=True``, moving only the buffer cursor does not always
        change ``Window.vertical_scroll`` while another control is focused.
        Adjust ``vertical_scroll`` directly and pin it while follow-tail is off.
        """
        if delta_lines == 0:
            return

        win = self._transcript.window
        buf = self._transcript.buffer
        from prompt_toolkit.document import Document

        doc = buf.document
        if doc.line_count <= 0:
            return

        info = getattr(win, "render_info", None)
        if info is not None:
            content_h = int(info.content_height)
            view_h = max(1, int(info.window_height))
            max_scroll = max(0, content_h - view_h)
            current = int(win.vertical_scroll)
            new_scroll = max(0, min(max_scroll, current + delta_lines))
            win.vertical_scroll = new_scroll

            if new_scroll >= max_scroll:
                self._transcript_follow_tail = True
                self._transcript_pinned_scroll = None
                cursor_idx = len(doc.text)
            else:
                self._transcript_follow_tail = False
                self._transcript_pinned_scroll = new_scroll
                anchor_row = max(0, min(doc.line_count - 1, new_scroll))
                cursor_idx = doc.translate_row_col_to_index(anchor_row, 0)

            with self._transcript_edit():
                buf.document = Document(text=doc.text, cursor_position=cursor_idx)
        else:
            # Before the first layout pass (unit tests) — cursor-only fallback.
            self._transcript_follow_tail = False
            new_row = max(
                0, min(doc.line_count - 1, doc.cursor_position_row + delta_lines)
            )
            if new_row >= doc.line_count - 1:
                self._transcript_follow_tail = True
                self._transcript_pinned_scroll = None
            else:
                self._transcript_pinned_scroll = None
            new_index = doc.translate_row_col_to_index(new_row, 0)
            with self._transcript_edit():
                buf.document = Document(text=doc.text, cursor_position=new_index)

        self._invalidate_now()

    def _page_transcript(self, direction: int) -> None:
        """Page transcript up (-1) or down (+1) by roughly one viewport of lines."""
        win = self._transcript.window
        info = getattr(win, "render_info", None)
        page = int(info.window_height) - 1 if info is not None else 10
        if page < 1:
            page = 1
        self._scroll_transcript(direction * page)

    def _wheel_delta_lines(self) -> int:
        """Lines to move per mouse wheel notch / ScrollUp/ScrollDown key."""
        raw = os.environ.get("MY_AGENT_TERMINAL_WHEEL_LINES", "").strip()
        if raw.isdigit():
            return max(1, int(raw))

        win = self._transcript.window
        info = getattr(win, "render_info", None)
        if info is not None:
            view_h = max(1, int(info.window_height))
            # ~1/3 viewport per notch — feels closer to native terminal scroll.
            return max(6, view_h // 3)
        return 6

    def _attach_transcript_scroll_guard(self) -> None:
        """Pause auto-tail when the user scrolls the transcript via mouse."""
        from prompt_toolkit.mouse_events import MouseEvent, MouseEventType

        win = self._transcript.window
        previous = win._mouse_handler

        def _mouse_handler(mouse_event: MouseEvent) -> Any:
            if mouse_event.event_type == MouseEventType.SCROLL_UP:
                self._scroll_transcript(-self._wheel_delta_lines())
                return None
            if mouse_event.event_type == MouseEventType.SCROLL_DOWN:
                self._scroll_transcript(self._wheel_delta_lines())
                return None
            if mouse_event.event_type == MouseEventType.MOUSE_DOWN:
                self._transcript_follow_tail = False
            return previous(mouse_event)

        win._mouse_handler = _mouse_handler  # type: ignore[method-assign]

    def _welcome_formatted(self) -> Any:
        if not self._welcome_visible or self._welcome_panel is None:
            from prompt_toolkit.formatted_text import FormattedText

            return FormattedText("")
        from terminal_ui import build_welcome_formatted

        return build_welcome_formatted(self._welcome_panel)

    def _welcome_compact_formatted(self) -> Any:
        if not self._welcome_visible or self._welcome_panel is None:
            from prompt_toolkit.formatted_text import FormattedText

            return FormattedText("")
        from terminal_ui import build_welcome_compact_formatted

        return build_welcome_compact_formatted(self._welcome_panel)

    def mount_welcome(self, panel: Any) -> None:
        def _do() -> None:
            self._welcome_panel = panel
            self._welcome_visible = True
            transcript = getattr(self, "_transcript", None)
            if transcript is not None:
                self._welcome_compact = bool(transcript.buffer.text.strip())
            else:
                self._welcome_compact = False
            self._invalidate()

        self._schedule_ui(_do)

    def dismiss_welcome(self) -> None:
        """Hide welcome card (/clear remounts; 新会话 remounts)."""

        def _do() -> None:
            if self._welcome_visible:
                self._welcome_visible = False
                self._invalidate()

        self._schedule_ui(_do)

    @property
    def welcome_visible(self) -> bool:
        return self._welcome_visible

    def _build_submit_bindings(
        self,
        on_submit: Callable[[str], None],
        picker_active: Any,
        confirm_active: Any,
        picker_idle: Any,
    ) -> Any:
        from prompt_toolkit.filters import Condition, has_selection
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.keys import Keys

        bindings = KeyBindings()
        transcript_window = self._transcript.window
        input_window = self._input_window

        def _transcript_focused() -> bool:
            try:
                return self._app.layout.current_window == transcript_window
            except Exception:
                return False

        def _input_focused() -> bool:
            try:
                return self._app.layout.current_window == input_window
            except Exception:
                return False

        transcript_focused = Condition(_transcript_focused)
        input_focused = Condition(_input_focused)

        def _pause_transcript_tail(_event: Any) -> None:
            self._transcript_follow_tail = False

        def _copy_transcript_selection(event: Any) -> bool:
            buf = self._transcript.buffer
            data = None
            if buf.selection_state is not None:
                data = buf.copy_selection()
            if data is None or not data.text:
                data = event.current_buffer.copy_selection()
            if not data.text:
                return False
            if not _copy_to_system_clipboard(data.text):
                event.app.clipboard.set_data(data)
            return True

        @bindings.add("up", filter=picker_active, eager=True)
        def _picker_up(event: Any) -> None:
            self._picker_index = max(0, self._picker_index - 1)
            self._invalidate()

        @bindings.add("down", filter=picker_active, eager=True)
        def _picker_down(event: Any) -> None:
            if not self._picker_models:
                return
            self._picker_index = min(len(self._picker_models) - 1, self._picker_index + 1)
            self._invalidate()

        @bindings.add("c-m", filter=picker_active, eager=True)
        def _picker_enter(event: Any) -> None:
            if not self._picker_models:
                self._finish_picker(_PICKER_CANCEL)
                return
            self._finish_picker(self._picker_models[self._picker_index].id)

        @bindings.add("escape", filter=picker_active, eager=True)
        def _picker_esc(event: Any) -> None:
            self._finish_picker(_PICKER_CANCEL)

        @bindings.add("y", filter=confirm_active, eager=True)
        def _confirm_y(event: Any) -> None:
            self._finish_confirm("y")

        @bindings.add("n", filter=confirm_active, eager=True)
        def _confirm_n(event: Any) -> None:
            self._finish_confirm("n")

        @bindings.add("a", filter=confirm_active, eager=True)
        def _confirm_a(event: Any) -> None:
            if self._confirm_allow_all:
                self._finish_confirm("a")

        @bindings.add("c-m", filter=confirm_active & input_focused, eager=True)
        def _confirm_enter(event: Any) -> None:
            on_submit(event.current_buffer.text)

        @bindings.add("c-m", filter=picker_idle & input_focused)
        def _enter(event: Any) -> None:
            buf = event.current_buffer
            if buf.complete_state:
                buf.complete_state = None
                return
            text = buf.text
            pos = buf.cursor_position
            if pos > 0 and text[pos - 1] == "\\":
                buf.delete_before_cursor(count=1)
                buf.insert_text("\n")
                return
            on_submit(text)

        @bindings.add("escape", "c-m", filter=picker_idle & input_focused)
        def _newline(event: Any) -> None:
            event.current_buffer.insert_text("\n")

        @bindings.add("c-c")
        def _interrupt(event: Any) -> None:
            if self._picker_active:
                self._finish_picker(_PICKER_CANCEL)
                return
            if self._confirm_active:
                self._finish_confirm("n")
                return
            # Prefer copy when transcript has a selection (Windows muscle memory).
            buf = self._transcript.buffer
            if buf.selection_state is not None:
                data = buf.copy_selection()
                if data.text:
                    if not _copy_to_system_clipboard(data.text):
                        event.app.clipboard.set_data(data)
                    return
            if self._on_cancel_callback is not None and self._on_cancel_callback():
                return
            event.app.exit(exception=KeyboardInterrupt)

        @bindings.add("c-d", filter=picker_idle & input_focused)
        def _eof(event: Any) -> None:
            if event.current_buffer.text:
                return
            event.app.exit()

        @bindings.add("escape", filter=transcript_focused)
        def _transcript_escape_to_input(event: Any) -> None:
            event.current_buffer.exit_selection()
            self._focus_input()

        @bindings.add(
            "c-insert",
            "c-s-insert",
            filter=has_selection,
            eager=True,
        )
        def _transcript_copy(event: Any) -> None:
            _copy_transcript_selection(event)

        @bindings.add("escape", "w", filter=transcript_focused & has_selection, eager=True)
        def _transcript_copy_emacs(event: Any) -> None:
            _copy_transcript_selection(event)

        @bindings.add(Keys.Any, filter=transcript_focused & picker_idle, eager=True)
        def _transcript_type_to_input(event: Any) -> Any:
            data = event.data
            if not data or len(data) != 1 or not data.isprintable():
                return NotImplemented
            self._focus_input()
            self._input.insert_text(data)
            return None

        # Windows often delivers the wheel as ScrollUp/ScrollDown *keys* to the
        # focused input — bind them globally so transcript still scrolls.
        @bindings.add(Keys.ScrollUp, filter=picker_idle, eager=True)
        def _wheel_up(event: Any) -> None:
            self._scroll_transcript(-self._wheel_delta_lines())

        @bindings.add(Keys.ScrollDown, filter=picker_idle, eager=True)
        def _wheel_down(event: Any) -> None:
            self._scroll_transcript(self._wheel_delta_lines())

        @bindings.add("up", filter=transcript_focused, eager=True)
        def _transcript_up(event: Any) -> None:
            self._scroll_transcript(-1)

        @bindings.add("down", filter=transcript_focused, eager=True)
        def _transcript_down(event: Any) -> None:
            self._scroll_transcript(1)

        @bindings.add("c-up", filter=picker_idle, eager=True)
        def _transcript_c_up(event: Any) -> None:
            self._scroll_transcript(-self._wheel_delta_lines())

        @bindings.add("c-down", filter=picker_idle, eager=True)
        def _transcript_c_down(event: Any) -> None:
            self._scroll_transcript(self._wheel_delta_lines())

        @bindings.add("pageup", filter=picker_idle, eager=True)
        def _transcript_pageup(event: Any) -> None:
            self._page_transcript(-1)

        @bindings.add("pagedown", filter=picker_idle, eager=True)
        def _transcript_pagedown(event: Any) -> None:
            self._page_transcript(1)

        # Empty input + ↑/↓ = scroll history (Claude-ish); non-empty keeps caret.
        @bindings.add("up", filter=input_focused & picker_idle, eager=True)
        def _input_up_or_scroll(event: Any) -> Any:
            if self._input.text.strip():
                return NotImplemented
            self._scroll_transcript(-1)
            return None

        @bindings.add("down", filter=input_focused & picker_idle, eager=True)
        def _input_down_or_scroll(event: Any) -> Any:
            if self._input.text.strip():
                return NotImplemented
            self._scroll_transcript(1)
            return None

        @bindings.add("c-o", filter=picker_idle, eager=True)
        def _copy_all_transcript(event: Any) -> None:
            """Escape hatch: copy full transcript (Claude Ctrl+O spirit)."""
            text = self._transcript.buffer.text
            if text.strip() and _copy_to_system_clipboard(text):
                return
            if text.strip():
                from prompt_toolkit.clipboard import ClipboardData

                event.app.clipboard.set_data(ClipboardData(text))

        return bindings

    def set_submit_handler(self, handler: Callable[[str], None]) -> None:
        self._on_submit_callback = handler

    def set_cancel_handler(self, handler: Callable[[], bool]) -> None:
        """Return True from handler when an in-flight turn was cancelled."""
        self._on_cancel_callback = handler

    def _on_submit(self, text: str) -> None:
        if self._on_submit_callback is not None:
            self._on_submit_callback(text)

    def _confirm_formatted(self) -> Any:
        from prompt_toolkit.formatted_text import FormattedText

        lines = [
            ("class:picker.hint", "工具确认  ·  底栏输入 y / n 后 Enter，或直接按 Y / N"),
        ]
        if self._confirm_allow_all:
            lines.append(("", "\n"))
            lines.append(("class:picker.hint", "a = 本会话允许 workspace evolved"))
        return FormattedText(lines + [("", "\n")])

    @staticmethod
    def _normalize_confirm_choice(raw: str, allow_approve_all: bool) -> str | None:
        choice = raw.strip().casefold()
        if choice in {"y", "yes"}:
            return "y"
        if choice in {"n", "no"}:
            return "n"
        if allow_approve_all and choice in {"a", "all"}:
            return "a"
        return None

    def _finish_confirm(self, choice: str) -> None:
        if not self._confirm_active:
            return
        self._confirm_active = False
        try:
            self._confirm_queue.put_nowait(choice)
        except queue.Full:
            try:
                self._confirm_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self._confirm_queue.put_nowait(choice)
            except queue.Full:
                pass
        self._invalidate()

    def prompt_confirm(self, *, allow_approve_all: bool) -> str:
        """Block caller thread until the user confirms a tool (bottom TUI)."""
        while True:
            try:
                self._confirm_queue.get_nowait()
            except queue.Empty:
                break
        self._confirm_allow_all = allow_approve_all
        self._confirm_active = True
        previous_status = getattr(self._console, "status", "working")

        def _arm() -> None:
            self._input.reset()
            self._focus_input()
            if hasattr(self._console, "set_activity"):
                self._console.set_activity("待确认", "y / n")
            elif hasattr(self._console, "set_status"):
                self._console.set_status("idle")
            self._invalidate_now()

        self._schedule_ui(_arm)
        choice = self._confirm_queue.get()
        self._confirm_active = False

        def _restore() -> None:
            if hasattr(self._console, "clear_activity"):
                self._console.clear_activity()
            if hasattr(self._console, "set_status"):
                self._console.set_status(previous_status)
            self._invalidate_now()

        self._schedule_ui(_restore)
        return choice

    def _picker_formatted(self) -> Any:
        from prompt_toolkit.formatted_text import FormattedText

        fragments: list[tuple[str, str]] = [
            ("class:picker.hint", "选择模型  ·  ↑↓ 移动 · Enter 确认 · Esc 取消"),
            ("", "\n"),
        ]
        if self._picker_current:
            fragments.append(("class:picker.hint", f"当前 {self._picker_current}"))
            fragments.append(("", "\n"))
        for i, entry in enumerate(self._picker_models):
            selected = i == self._picker_index
            prefix = "› " if selected else "  "
            tier = str(getattr(entry, "tier", "")).upper()
            name = str(getattr(entry, "name", entry.id))
            body = f"{prefix}{name}  ({entry.id} · {tier})"
            style = "class:picker.selected" if selected else "class:picker.item"
            fragments.append((style, body))
            fragments.append(("", "\n"))
        return FormattedText(fragments)

    def _finish_picker(self, result: Any) -> None:
        if not self._picker_active:
            return
        self._picker_active = False
        try:
            self._picker_queue.put_nowait(result)
        except queue.Full:
            try:
                self._picker_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self._picker_queue.put_nowait(result)
            except queue.Full:
                pass
        self._invalidate()

    def pick_model(self, models: Sequence[Any], *, current_id: str) -> str | None:
        """Show bottom-anchored ↑↓ picker. Blocks caller thread until confirmed/cancelled."""
        if not models:
            return None
        # Drain any stale result from a previous cancelled wait.
        while True:
            try:
                self._picker_queue.get_nowait()
            except queue.Empty:
                break
        start = 0
        for i, entry in enumerate(models):
            if entry.id == current_id:
                start = i
                break
        self._picker_models = list(models)
        self._picker_index = start
        self._picker_current = current_id or ""
        self._picker_active = True
        self._invalidate()
        result = self._picker_queue.get()
        if result is _PICKER_CANCEL:
            return None
        return str(result) if result is not None else None

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
            "idle": "class:status.dot-green",
            "working": "class:status.dot-yellow",
            "cancelled": "class:status.dot-red",
        }.get(bar.status, "class:status.dot-green")
        state_class = {
            "idle": "class:status.state-idle",
            "working": "class:status.state-working",
            "cancelled": "class:status.state-cancelled",
        }.get(bar.status, "class:status.state-idle")
        return FormattedText(
            [
                ("class:status.model", model),
                ("class:status.sep", " · "),
                ("class:status.root", bar.root_short),
                ("class:status.sep", " · "),
                ("class:status.label", "agent"),
                ("class:status.sep", " · "),
                (dot_class, dot),
                (state_class, f" {status}"),
            ]
        )

    @staticmethod
    def _plain_text(text: str) -> str:
        return _ANSI_ESCAPE.sub("", text)

    def _transcript_text(self) -> str:
        return self._transcript.buffer.text

    def _sync_welcome_compact(self) -> None:
        compact = bool(self._transcript.buffer.text.strip())
        if compact != self._welcome_compact:
            self._welcome_compact = compact

    @contextmanager
    def _transcript_edit(self) -> Iterator[Any]:
        """Briefly unlock read-only transcript for programmatic appends."""
        from prompt_toolkit.filters import Condition

        buf = self._transcript.buffer
        buf.read_only = Condition(lambda: False)
        try:
            yield buf
        finally:
            buf.read_only = Condition(lambda: True)

    def _mutate_transcript(self, mutator: Callable[[Any], None]) -> None:
        # Align with prompt_toolkit calculator.py: set Document so the
        # renderer sees a consistent text+cursor snapshot (avoids half frames).
        from prompt_toolkit.document import Document

        cursor = self._transcript.buffer.cursor_position
        with self._transcript_edit() as buf:
            mutator(buf)
            text = buf.text
            if self._following_tail():
                buf.document = Document(text=text, cursor_position=len(text))
            else:
                # Keep the user's browse cursor — Window scroll follows it.
                buf.document = Document(
                    text=text,
                    cursor_position=min(cursor, len(text)),
                )

    def write(self, text: str) -> None:
        payload = self._plain_text(text).rstrip("\n")
        if not payload:
            return

        def _do() -> None:
            self._drain_deferred_ui()

            def _append(buf: Any) -> None:
                current = buf.text
                if current and not current.endswith("\n"):
                    current += "\n"
                buf.text = current + payload + "\n"

            self._mutate_transcript(_append)
            self._sync_welcome_compact()
            self._invalidate_now()

        self._schedule_ui(_do)

    def _invalidate_now(self) -> None:
        self._drain_deferred_ui()
        app = getattr(self, "_app", None)
        try:
            if app is None:
                return
            app.invalidate()
            pinned = getattr(self, "_transcript_pinned_scroll", None)
            if pinned is not None and not self._following_tail():
                loop = getattr(app, "loop", None)
                if loop is not None and not loop.is_closed():
                    loop.call_soon(self._restore_browse_scroll)
                    return
        except Exception:
            pass
        self._restore_browse_scroll()

    def _invalidate(self) -> None:
        self._schedule_ui(self._invalidate_now)

    def request_redraw(self) -> None:
        """Public invalidate for status / meta updates from the worker thread."""
        self._invalidate()

    def set_transcript(self, text: str) -> None:
        def _do() -> None:
            self._drain_deferred_ui()
            self._mutate_transcript(lambda buf: setattr(buf, "text", text) or None)
            self._sync_welcome_compact()
            self._invalidate_now()

        self._schedule_ui(_do)

    def begin_assistant_stream(self, header: str) -> None:
        """Record stream start offset and write a coarse header before raw deltas."""

        def _do() -> None:
            self._drain_deferred_ui()
            self._flush_pending_raw_locked()
            self._assistant_replace_start = len(self._transcript.buffer.text)
            plain = self._plain_text(header)
            if plain:
                self._mutate_transcript(lambda buf: setattr(buf, "text", buf.text + plain))
            self._sync_welcome_compact()
            self._invalidate_now()

        self._schedule_ui(_do)

    def finalize_assistant_stream(self, formatted_block: str) -> None:
        """Replace the coarse stream span with a formatted assistant block."""

        def _do() -> None:
            self._drain_deferred_ui()
            self._flush_pending_raw_locked()
            start = self._assistant_replace_start
            self._assistant_replace_start = None
            block = self._plain_text(formatted_block).rstrip("\n")
            if not block:
                self._invalidate_now()
                return
            if start is None:
                self._mutate_transcript(
                    lambda buf: setattr(buf, "text", buf.text + block + "\n")
                )
            else:
                end = len(self._transcript.buffer.text)
                self._replace_transcript_span_locked(start, end, block + "\n")
            self._sync_welcome_compact()
            self._invalidate_now()

        self._schedule_ui(_do)

    def replace_transcript_span(self, start: int, end: int, new_text: str) -> None:
        """UI-thread safe. Replace ``[start:end)``; preserve browse scroll when not following."""

        def _do() -> None:
            self._drain_deferred_ui()
            self._flush_pending_raw_locked()
            self._replace_transcript_span_locked(start, end, new_text)
            self._sync_welcome_compact()
            self._invalidate_now()

        self._schedule_ui(_do)

    def _replace_transcript_span_locked(self, start: int, end: int, new_text: str) -> None:
        text = self._transcript.buffer.text
        start_clamped = max(0, min(start, len(text)))
        end_clamped = max(start_clamped, min(end, len(text)))
        if start_clamped >= end_clamped and not new_text:
            return
        replacement = self._plain_text(new_text)
        new_full = text[:start_clamped] + replacement + text[end_clamped:]

        def _replace(buf: Any) -> None:
            buf.text = new_full

        self._mutate_transcript(_replace)

    def append_transcript_block(self, block: str) -> None:
        block = self._plain_text(block).strip()
        if not block:
            return

        def _do() -> None:
            self._drain_deferred_ui()

            def _append(buf: Any) -> None:
                current = buf.text
                if current:
                    current = current.rstrip("\n") + "\n\n"
                buf.text = current + block + "\n"

            self._mutate_transcript(_append)
            self._sync_welcome_compact()
            self._invalidate_now()

        self._schedule_ui(_do)

    def append_transcript_raw(self, text: str) -> None:
        """Append inline stream chunks (reasoning / assistant deltas) without forcing newlines.

        Chunks are coalesced: while a flush is pending, later chunks merge into
        ``_pending_raw`` so a fast stream costs one Document rebuild per UI tick
        instead of one per token (that per-token rebuild was the stutter).
        """
        if not text:
            return
        plain = self._plain_text(text)
        with self._lock:
            self._pending_raw += plain
            if self._raw_flush_scheduled:
                return
            self._raw_flush_scheduled = True
        self._schedule_raw_flush()

    def _schedule_raw_flush(self) -> None:
        """Flush after a short delay so bursts of tokens land in one repaint."""
        app = getattr(self, "_app", None)
        if app is not None and getattr(app, "is_running", False) is True:
            loop = getattr(app, "loop", None)
            if loop is not None and not loop.is_closed():
                try:
                    loop.call_soon_threadsafe(
                        lambda: loop.call_later(0.05, self._flush_pending_raw)
                    )
                    return
                except Exception:
                    pass
        self._flush_pending_raw()

    def _flush_pending_raw_locked(self) -> None:
        with self._lock:
            chunk = self._pending_raw
            self._pending_raw = ""
            self._raw_flush_scheduled = False
        if not chunk:
            return
        self._mutate_transcript(lambda buf: setattr(buf, "text", buf.text + chunk))
        self._sync_welcome_compact()

    def _flush_pending_raw(self) -> None:
        self._drain_deferred_ui()
        self._flush_pending_raw_locked()
        self._invalidate_now()

    def flush_pending(self) -> None:
        """Force any coalesced stream chunks onto the UI thread (e.g. turn end)."""
        self._schedule_ui(self._flush_pending_raw)

    def notify_cancelled(self) -> None:
        self.write("(cancelled)")

    def run(self) -> None:
        """Block until the user exits the terminal UI."""
        try:
            self._app.run()
        except KeyboardInterrupt:
            self._stop = True

    def request_stop(self) -> None:
        self._stop = True
        if self._picker_active:
            self._finish_picker(_PICKER_CANCEL)
        if self._confirm_active:
            self._finish_confirm("n")

        def _exit() -> None:
            try:
                self._app.exit()
            except Exception:
                pass

        self._schedule_ui(_exit)

    @property
    def should_stop(self) -> bool:
        return self._stop
