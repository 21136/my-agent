"""Interactive arrow-key prompts for terminal harness (T-5710 · /model picker)."""

from __future__ import annotations

import os
import sys
from typing import Any, Callable, Protocol, Sequence

ReadKeyFn = Callable[[], str | None]

# ANSI (Windows 10+ cmd with VT enabled)
_STYLE_SEL = "\033[36m\033[1m"
_STYLE_DIM = "\033[2m"
_STYLE_OFF = "\033[0m"


class ModelChoiceEntry(Protocol):
    id: str
    name: str
    tier: str


def interactive_choice_available() -> bool:
    if os.environ.get("MY_AGENT_TERMINAL_INTERACTIVE", "1").strip().casefold() in {
        "0",
        "false",
        "no",
    }:
        return False
    try:
        return sys.stdin.isatty()
    except Exception:
        return False


def _menu_stream() -> Any:
    return sys.stdout


def _enable_windows_vt() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        for handle_id in (-11, -12):
            handle = kernel32.GetStdHandle(handle_id)
            mode = ctypes.c_ulong()
            if kernel32.GetConsoleMode(handle, ctypes.byref(mode)) == 0:
                continue
            mode.value |= 0x0004
            kernel32.SetConsoleMode(handle, mode)
    except Exception:
        return


def _read_key_windows() -> str | None:
    import msvcrt

    ch = msvcrt.getch()
    if ch in {b"\x00", b"\xe0"}:
        ch2 = msvcrt.getch()
        mapping = {b"H": "up", b"P": "down"}
        return mapping.get(ch2)
    if ch in {b"\r", b"\n"}:
        return "enter"
    if ch == b"\x1b":
        return "esc"
    if ch == b"\x03":
        raise KeyboardInterrupt
    return None


def _read_key_unix() -> str | None:
    import select
    import termios
    import tty

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
        if ch == "\x03":
            raise KeyboardInterrupt
        if ch in "\r\n":
            return "enter"
        if ch == "\x1b":
            if not select.select([sys.stdin], [], [], 0.05)[0]:
                return "esc"
            ch2 = sys.stdin.read(1)
            if ch2 == "[":
                ch3 = sys.stdin.read(1)
                if ch3 == "A":
                    return "up"
                if ch3 == "B":
                    return "down"
            return "esc"
        return None
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def default_read_key() -> str | None:
    if sys.platform == "win32":
        return _read_key_windows()
    return _read_key_unix()


def _format_model_menu_lines(
    models: Sequence[ModelChoiceEntry],
    index: int,
    *,
    current_id: str,
    use_color: bool = True,
) -> list[str]:
    from llm_client import format_context_tokens_short
    from llm_models import get_registry

    registry = get_registry()
    header = "选择模型"
    if current_id:
        header += f"  ·  当前 {current_id}"
    if use_color:
        header = f"{_STYLE_DIM}{header}{_STYLE_OFF}"
    lines = [
        header,
        f"{_STYLE_DIM}  ↑↓ 移动 · Enter 确认 · Esc 取消{_STYLE_OFF}" if use_color else "  ↑↓ 移动 · Enter 确认 · Esc 取消",
    ]
    for i, entry in enumerate(models):
        selected = i == index
        prefix = "› " if selected else "  "
        tier = str(entry.tier).upper()
        resolved = registry.get(entry.id)
        ctx = (
            format_context_tokens_short(resolved.max_input_tokens)
            if resolved is not None
            else ""
        )
        ctx_suffix = f" · {ctx} ctx" if ctx else ""
        body = f"{prefix}{entry.name}  ({entry.id} · {tier}{ctx_suffix})"
        if use_color and selected:
            body = f"{_STYLE_SEL}{body}{_STYLE_OFF}"
        lines.append(body)
    return lines


def _print_menu(lines: list[str], *, stream: Any | None = None) -> None:
    target = stream if stream is not None else _menu_stream()
    for line in lines:
        target.write(line + "\n")
    target.flush()


def _redraw_menu(lines: list[str], *, stream: Any | None = None) -> None:
    """Move cursor up and overwrite the menu block in-place."""
    if not lines:
        return
    target = stream if stream is not None else _menu_stream()
    height = len(lines)
    target.write(f"\033[{height}A")
    for line in lines:
        target.write("\033[2K\r")
        target.write(line + "\n")
    target.flush()


def _erase_menu_lines(lines: list[str], *, stream: Any | None = None) -> None:
    """Remove the menu block and collapse the gap (no blank lines left behind)."""
    if not lines:
        return
    target = stream if stream is not None else _menu_stream()
    height = len(lines)
    target.write(f"\033[{height}A\r")
    target.write(f"\033[{height}M")
    target.flush()


def prompt_model_choice(
    models: Sequence[ModelChoiceEntry],
    *,
    current_id: str,
    read_key: ReadKeyFn | None = None,
    console: Any | None = None,
) -> str | None:
    """Return chosen model id, or ``None`` when cancelled. Menu clears on exit."""
    del console  # always draw to sys.stdout — Rich Live/Console breaks on Windows cmd
    if not models:
        return None

    read = read_key or default_read_key
    start = 0
    for i, entry in enumerate(models):
        if entry.id == current_id:
            start = i
            break

    _enable_windows_vt()

    index = start
    menu_lines = _format_model_menu_lines(models, index, current_id=current_id)
    _print_menu(menu_lines)
    try:
        while True:
            key = read()
            if key == "up":
                index = max(0, index - 1)
                menu_lines = _format_model_menu_lines(models, index, current_id=current_id)
                _redraw_menu(menu_lines)
            elif key == "down":
                index = min(len(models) - 1, index + 1)
                menu_lines = _format_model_menu_lines(models, index, current_id=current_id)
                _redraw_menu(menu_lines)
            elif key == "enter":
                return models[index].id
            elif key == "esc":
                return None
    finally:
        _erase_menu_lines(menu_lines)
