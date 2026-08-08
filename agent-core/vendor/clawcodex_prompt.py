"""Vendored Claude Code-style prompt_toolkit setup (MIT · clawcodex).

Source: https://github.com/chennanli/clawcodex/blob/main/src/repl/core.py
"""

from __future__ import annotations

from typing import Any, Callable

_SHIFT_ENTER_REGISTERED = False

ToolbarFn = Callable[[], Any]


def register_shift_enter_sequences() -> None:
    global _SHIFT_ENTER_REGISTERED
    if _SHIFT_ENTER_REGISTERED:
        return
    try:
        from prompt_toolkit.input import ansi_escape_sequences as ansi_seq
        from prompt_toolkit.keys import Keys
    except ImportError:
        return
    ansi_seq.ANSI_SEQUENCES["\x1b[13;2u"] = (Keys.Escape, Keys.ControlM)
    ansi_seq.ANSI_SEQUENCES["\x1b[27;2;13~"] = (Keys.Escape, Keys.ControlM)
    _SHIFT_ENTER_REGISTERED = True


def build_claude_key_bindings() -> Any:
    """Enter submits; Shift/Meta+Enter and ``\\``+Enter insert newline."""
    from prompt_toolkit.key_binding import KeyBindings

    bindings = KeyBindings()

    @bindings.add("c-m")
    def _enter_submits_or_backslash_newline(event: Any) -> None:
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
        buf.validate_and_handle()

    @bindings.add("escape", "c-m")
    def _meta_or_shift_enter_newline(event: Any) -> None:
        event.current_buffer.insert_text("\n")

    return bindings


def claude_prompt_style() -> Any:
    from prompt_toolkit.styles import Style

    return Style.from_dict(
        {
            "": "bg:#262626 fg:#f2f2f2",
            "prompt": "bold fg:#f8f8f8 bg:#262626",
            "bottom-toolbar": "fg:#888888",
            "bottom-toolbar.model": "#b8b8b8",
            "bottom-toolbar.root": "#888888",
            "bottom-toolbar.status": "#888888",
            "bottom-toolbar.dot-green": "#7ec87e",
            "bottom-toolbar.dot-yellow": "#d4a84b",
            "bottom-toolbar.dot-red": "#d47474",
        }
    )


def prompt_continuation(width: int, line_number: int, is_soft_wrap: bool) -> str:
    if is_soft_wrap:
        return " " * width
    marker = "… "
    if width <= len(marker):
        return marker[:width]
    return marker.rjust(width)


def create_claude_prompt_session(
    *,
    history: Any,
    bottom_toolbar: ToolbarFn,
) -> Any:
    """Build a ``PromptSession`` configured like clawcodex inline REPL."""
    from prompt_toolkit import PromptSession
    from prompt_toolkit.output import ColorDepth

    register_shift_enter_sequences()
    return PromptSession(
        history=history,
        style=claude_prompt_style(),
        key_bindings=build_claude_key_bindings(),
        multiline=True,
        prompt_continuation=prompt_continuation,
        bottom_toolbar=bottom_toolbar,
        color_depth=ColorDepth.TRUE_COLOR,
    )
