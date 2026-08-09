"""Terminal TUI rendering (TERMINAL-MODE §6.4 · T-5710a–c)."""

from __future__ import annotations

import io
import os
import re
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from llm_client import StreamHandlers, resolve_session_model
from terminal_scope import TerminalScopeError, TerminalScopeFields, resolve_terminal_effective_root

OutputFn = Callable[[str], None]

_TOOL_OK_MARK = "✓"
_TOOL_FAIL_MARK = "✗"
_TURN_SEP = "─" * 40
_TERMINAL_UI_VERSION = "0.2.1"
_DEFAULT_TERMINAL_TIPS = (
    "快捷键: Ctrl+C 停止回合",
    "命令: 新会话 · exit · /model · /clear · /compact",
    "提示: effective root 内写/跑默认免 confirm",
)
TERMINAL_COMMANDS_LINE = "Commands: 新会话 | exit | /model | /clear | /compact"


def _env_ui_mode() -> str:
    raw = os.environ.get("MY_AGENT_TERMINAL_UI", "auto").strip().casefold()
    if raw in {"auto", "rich", "plain"}:
        return raw
    return "auto"


def _rich_import_ok() -> bool:
    try:
        import rich  # noqa: F401

        return True
    except ImportError:
        return False


def welcome_enabled() -> bool:
    return os.environ.get("MY_AGENT_TERMINAL_WELCOME", "1").strip() not in {
        "0",
        "false",
        "no",
        "off",
    }


def reasoning_enabled() -> bool:
    return os.environ.get("MY_AGENT_TERMINAL_REASONING", "1").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def tool_panels_enabled() -> bool:
    """Tool invocation boxes in transcript — off by default for terminal CLI."""
    return os.environ.get("MY_AGENT_TERMINAL_TOOL_PANELS", "0").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def turn_separator_enabled() -> bool:
    return os.environ.get("MY_AGENT_TERMINAL_TURN_SEP", "0").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def terminal_transcript_format_enabled() -> bool:
    """Pretty-print assistant markdown in bottom-layout transcript (default on)."""
    return os.environ.get("MY_AGENT_TERMINAL_MARKDOWN", "1").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


_INLINE_STRONG_RE = re.compile(r"\*\*(.+?)\*\*")
_INLINE_EMPH_RE = re.compile(r"(?<!\*)\*([^*]+?)\*(?!\*)")
_INLINE_CODE_RE = re.compile(r"`([^`]+)`")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")
_HRULE_RE = re.compile(r"^-{3,}\s*$")
_BULLET_RE = re.compile(r"^[-*]\s+")
_ORDERED_RE = re.compile(r"^(\d+)\.\s+")
_BLOCKQUOTE_RE = re.compile(r"^>\s?(.*)$")
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def _terminal_content_width(*, fallback: int = 80, padding: int = 4) -> int:
    import shutil

    try:
        cols = shutil.get_terminal_size(fallback=(fallback, 24)).columns
    except OSError:
        cols = fallback
    return max(48, min(cols - padding, 96))


def _normalize_terminal_lines(text: str) -> str:
    lines = [line.rstrip() for line in text.splitlines()]
    return "\n".join(lines).strip()


def _strip_inline_markdown(text: str) -> str:
    cleaned = _LINK_RE.sub(r"\1 (\2)", text)
    cleaned = _INLINE_STRONG_RE.sub(r"\1", cleaned)
    cleaned = _INLINE_EMPH_RE.sub(r"\1", cleaned)
    return _INLINE_CODE_RE.sub(r"\1", cleaned)


def _format_code_block_box(lang: str, lines: list[str], *, width: int) -> list[str]:
    label = (lang or "code").strip() or "code"
    inner_w = max(24, width - 6)
    title = f" {label} "
    top_inner = title + "─" * max(4, inner_w - len(title))
    top = f"  ╭{top_inner}╮"
    body: list[str] = []
    for raw in lines or [""]:
        line = raw.rstrip() or " "
        if len(line) > inner_w:
            line = line[: inner_w - 1] + "…"
        body.append(f"  │ {line.ljust(inner_w)} │")
    bottom = f"  ╰{'─' * inner_w}╯"
    return [top, *body, bottom]


def _format_assistant_with_rich(text: str, *, width: int | None = None) -> str:
    from rich.console import Console
    from rich.markdown import Markdown

    render_width = width or _terminal_content_width()
    buffer = io.StringIO()
    console = Console(
        file=buffer,
        width=render_width,
        no_color=True,
        force_terminal=False,
        highlight=False,
        soft_wrap=True,
    )
    console.print(Markdown(text, justify="left"))
    return _normalize_terminal_lines(buffer.getvalue())


def format_terminal_assistant_text(text: str) -> str:
    """Markdown → readable terminal transcript (Rich when available)."""
    stripped = (text or "").strip()
    if not stripped:
        return ""
    if _rich_import_ok() and terminal_transcript_format_enabled():
        try:
            return _format_assistant_with_rich(stripped)
        except Exception:
            pass
    return _format_assistant_plain(stripped)


def _format_assistant_plain(text: str) -> str:
    """Regex fallback when Rich is unavailable."""
    width = _terminal_content_width()
    lines_out: list[str] = []
    in_code = False
    code_lang = ""
    code_lines: list[str] = []
    for raw in text.splitlines():
        line = raw.rstrip()
        fence = line.strip()
        if fence.startswith("```"):
            if in_code:
                lines_out.extend(_format_code_block_box(code_lang, code_lines, width=width))
                code_lines = []
                code_lang = ""
                in_code = False
            else:
                in_code = True
                code_lang = fence[3:].strip()
            continue
        if in_code:
            code_lines.append(line)
            continue
        if not line.strip():
            lines_out.append("")
            continue
        if _HRULE_RE.match(line.strip()):
            lines_out.append("  " + "─" * min(48, width - 2))
            continue
        quote = _BLOCKQUOTE_RE.match(line.strip())
        if quote:
            lines_out.append("  ▏ " + _strip_inline_markdown(quote.group(1).strip()))
            continue
        heading = _HEADING_RE.match(line.strip())
        if heading:
            level = len(heading.group(1))
            title = _strip_inline_markdown(heading.group(2).strip())
            if level <= 2:
                lines_out.append("")
                lines_out.append(title)
                lines_out.append("  " + "─" * min(len(title), width - 4))
            else:
                lines_out.append(f"  · {title}")
            continue
        cleaned = _strip_inline_markdown(line.strip())
        ordered = _ORDERED_RE.match(cleaned)
        if ordered:
            lines_out.append(f"  {ordered.group(1)}. " + cleaned[ordered.end() :].strip())
            continue
        if _BULLET_RE.match(cleaned):
            cleaned = "  • " + _BULLET_RE.sub("", cleaned, count=1).strip()
        else:
            cleaned = "  " + cleaned
        lines_out.append(cleaned)
    if in_code:
        lines_out.extend(_format_code_block_box(code_lang, code_lines, width=width))
    return _normalize_terminal_lines("\n".join(lines_out))


def format_terminal_assistant_block(text: str) -> str:
    body = format_terminal_assistant_text(text)
    if not body:
        return ""
    name = terminal_assistant_name()
    header = f"◆ {name}"
    parts = [header, ""]
    for line in body.splitlines():
        parts.append(f"  {line}" if line else "")
    return "\n".join(parts).rstrip()


def format_terminal_reasoning_block(text: str) -> str:
    body = _normalize_terminal_lines(text)
    if not body:
        return ""
    width = _terminal_content_width()
    inner_w = min(40, max(16, width - 8))
    top = f"  ╭─ 思考 {'─' * inner_w}"
    lines = [top]
    for raw in body.splitlines():
        line = _strip_inline_markdown(raw.strip())
        if not line:
            lines.append("  │")
            continue
        if len(line) > width - 8:
            line = line[: width - 9] + "…"
        lines.append(f"  │ {line}")
    lines.append(f"  ╰{'─' * (inner_w + 3)}")
    return "\n".join(lines)


def format_terminal_user_line(text: str) -> str:
    stripped = text.strip()
    if not stripped:
        return ""
    return f"❯ {stripped}"


def parse_terminal_slash_command(line: str) -> str | None:
    """Return ``clear`` | ``compact`` for terminal slash commands, else ``None``."""
    stripped = line.strip()
    if not stripped.startswith("/"):
        return None
    token = stripped.split(maxsplit=1)[0].casefold()
    if token == "/clear":
        return "clear"
    if token in {"/compact", "/compress", "/summarize"}:
        return "compact"
    return None


def parse_terminal_model_command(line: str) -> str | None:
    """Return model id/alias for ``/model <id>``, empty string for ``/model`` list, else ``None``."""
    stripped = line.strip()
    if not stripped.casefold().startswith("/model"):
        return None
    parts = stripped.split(maxsplit=1)
    if len(parts) == 1:
        return ""
    return parts[1].strip()


def format_terminal_models_list(paths: Any, *, current_model: str) -> list[str]:
    from llm_models import get_registry

    registry = get_registry(paths)
    current = (current_model or "").strip()
    lines = ["可用模型："]
    for entry in registry.models:
        marker = "→" if entry.id == current else " "
        tier = entry.tier.upper()
        lines.append(f"  {marker} {entry.id}  ({entry.name} · {tier})")
    lines.append("用法: /model（↑↓ 选择）| /model flash | /model pro")
    return lines


def shorten_middle_path(path: str, max_len: int = 52) -> str:
    """Abbreviate long absolute paths (``D:/…/huiyi``); never return bare ``.``."""
    normalized = path.replace("\\", "/").strip()
    if not normalized or normalized == ".":
        return normalized
    if len(normalized) <= max_len:
        return normalized
    if max_len < 8:
        return normalized[:max_len]
    parts = [part for part in normalized.split("/") if part]
    if len(parts) >= 2 and parts[0].endswith(":"):
        tail = parts[-1]
        candidate = f"{parts[0]}/…/{tail}"
        if len(candidate) <= max_len:
            return candidate
    keep = (max_len - 1) // 2
    return normalized[:keep] + "…" + normalized[-(max_len - keep - 1) :]


def resolve_effective_root_abs(meta: Any, paths: Any) -> str:
    try:
        return resolve_terminal_effective_root(meta, paths).resolve().as_posix()
    except TerminalScopeError:
        return "(unknown root)"


_TIP_MAX_LEN = 72
_TIP_MAX_BULLETS = 5


def format_welcome_root_label(effective_root: str, *, max_len: int = 56) -> str:
    """Display root in Welcome; always absolute-based, never bare ``.``."""
    root = effective_root.strip()
    if not root or root == "(unknown root)":
        return root or "(unknown root)"
    return shorten_middle_path(root.replace("\\", "/"), max_len=max_len)


def format_welcome_cwd_label(*, terminal_cwd: str, effective_root: str) -> str:
    """Human cwd label for Welcome; never bare ``.``."""
    raw = terminal_cwd.strip().replace("\\", "/")
    if raw in {"", ".", "./"}:
        return format_welcome_root_label(effective_root)
    return raw


def _compact_tip_line(text: str, *, max_len: int = _TIP_MAX_LEN) -> str:
    cleaned = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    cleaned = re.sub(r"`([^`]+)`", r"\1", cleaned)
    cleaned = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if len(cleaned) <= max_len:
        return cleaned
    cut = cleaned[: max_len - 1].rsplit(" ", 1)[0]
    return (cut or cleaned[: max_len - 1]).rstrip() + "…"


def load_whats_new_lines(changelog_path: Path, *, max_bullets: int = _TIP_MAX_BULLETS) -> list[str]:
    if not changelog_path.is_file():
        return list(_DEFAULT_TERMINAL_TIPS)
    text = changelog_path.read_text(encoding="utf-8")
    section_match = re.search(r"^## \[(?P<title>[^\]]+)\][^\n]*\n", text, re.MULTILINE)
    if section_match is None:
        return list(_DEFAULT_TERMINAL_TIPS)
    start = section_match.end()
    next_heading = re.search(r"^## \[", text[start:], re.MULTILINE)
    section_body = text[start : start + next_heading.start()] if next_heading else text[start:]
    bullets: list[str] = []
    title = section_match.group("title").strip()
    if title:
        bullets.append(f"What's new · {title}")
    for line in section_body.splitlines():
        stripped = line.strip()
        if not stripped.startswith("- "):
            continue
        if stripped.startswith("- **") and "：" in stripped[:40]:
            # Section headers like "- **文档**" — skip category rows
            continue
        tip = _compact_tip_line(stripped[2:].strip())
        if tip:
            bullets.append(tip)
        if len(bullets) >= max_bullets + 1:
            break
    if len(bullets) <= 1:
        return list(_DEFAULT_TERMINAL_TIPS)
    return bullets[: max_bullets + 1]


@dataclass(frozen=True)
class WelcomeContent:
    effective_root: str
    llm_model: str
    terminal_scope_kind: str
    harness: str
    terminal_cwd: str
    session_id: str
    resume: bool
    workspace_name: str
    left_lines: tuple[str, ...]
    right_lines: tuple[str, ...]
    title: str = f"my-agent Terminal v{_TERMINAL_UI_VERSION}"


@dataclass(frozen=True)
class StatusBarContent:
    llm_model: str
    turn_mode: str
    root_short: str
    session_suffix: str
    status: str
    active_tool: str = ""
    activity_detail: str = ""


def welcome_session_suffix(session_id: str) -> str:
    sid = session_id.strip()
    if len(sid) <= 10:
        return sid
    return f"…{sid[-8:]}"


def welcome_workspace_name(effective_root: str) -> str:
    name = Path(effective_root.replace("\\", "/")).name.strip()
    return name or effective_root


def welcome_status_dot(status: str) -> str:
    return {
        "idle": "●",
        "working": "◐",
        "cancelled": "○",
    }.get(status, "●")


def welcome_status_label(status: str) -> str:
    return {
        "idle": "就绪",
        "working": "思考中",
        "cancelled": "已停止",
    }.get(status, status)


def format_activity_status(
    status: str,
    *,
    active_tool: str = "",
    activity_detail: str = "",
) -> str:
    """Status line text: prefer running tool name over generic '思考中'."""
    tool = active_tool.strip()
    detail = activity_detail.strip()
    if tool:
        label = tool if len(tool) <= 36 else f"{tool[:34]}…"
        if detail:
            short = detail if len(detail) <= 24 else f"{detail[:22]}…"
            return f"{label} · {short}"
        return label
    return welcome_status_label(status)


def _activity_tool_label(tool: str, summary: str) -> str:
    summary_text = summary.strip()
    if summary_text:
        return summary_text
    return tool.strip() or "tool"


def terminal_user_name() -> str:
    raw = os.environ.get("MY_AGENT_TERMINAL_USER_NAME", "忆梦").strip()
    return raw or "忆梦"


def terminal_assistant_name() -> str:
    raw = os.environ.get("MY_AGENT_TERMINAL_ASSISTANT_NAME", "打工仔").strip()
    return raw or "打工仔"


def build_time_greeting_lines(
    *,
    resume: bool,
    workspace: str,
    user: str | None = None,
    assistant: str | None = None,
    hour: int | None = None,
) -> tuple[str, str]:
    """Two-line warm greeting (line1 · line2) for amber welcome card."""
    import datetime

    who = user if user is not None else terminal_user_name()
    aide = assistant if assistant is not None else terminal_assistant_name()
    ws = (workspace or "项目").strip() or "项目"
    now_hour = hour if hour is not None else datetime.datetime.now().hour

    if not resume:
        return (f"你好，{who}。", f"{aide}已就位，我们开始。")
    if 5 <= now_hour < 12:
        return (f"早上好，{who}。", f"{aide}在这，今天继续搞 {ws}。")
    if 12 <= now_hour < 18:
        return (f"下午好，{who}，又见面了。", f"{aide}在这，今天继续搞 {ws}。")
    if 18 <= now_hour < 23:
        return (f"晚上好，{who}，又见面了。", f"{aide}在这，今天继续搞 {ws}。")
    return (f"夜深了，{who}。", f"需要{aide}陪你熬一会吗。")


def _welcome_card_width(*, fallback: int = 80) -> int:
    import shutil

    try:
        cols = shutil.get_terminal_size(fallback=(fallback, 24)).columns
    except OSError:
        cols = fallback
    return max(48, cols - 2)


def _display_width(text: str) -> int:
    import unicodedata

    width = 0
    for ch in text:
        east = unicodedata.east_asian_width(ch)
        if east in ("W", "F"):
            width += 2
        elif east == "A" and ord(ch) > 0x2E7F:
            width += 2
        else:
            width += 1
    return width


def _pad_display(text: str, target: int) -> str:
    gap = target - _display_width(text)
    if gap <= 0:
        return text
    return text + (" " * gap)


def _truncate_display(text: str, target: int) -> str:
    if _display_width(text) <= target:
        return text
    out: list[str] = []
    used = 0
    for ch in text:
        w = _display_width(ch)
        if used + w > target - 1:
            out.append("…")
            break
        out.append(ch)
        used += w
    return "".join(out)


def build_welcome_formatted(panel: WelcomeContent) -> Any:
    """Amber reception card — left greeting, right 打工仔 truecolor mascot."""
    from prompt_toolkit.formatted_text import ANSI, FormattedText, to_formatted_text

    from welcome_mascot import SPRITE_LABEL, sprite_display_width, sprite_lines

    greet1, greet2 = build_time_greeting_lines(
        resume=panel.resume,
        workspace=panel.workspace_name,
    )
    model = prompt_model_short(panel.llm_model) or panel.llm_model or "default"
    root = format_welcome_root_label(panel.effective_root)
    version = f"v{_TERMINAL_UI_VERSION}"

    width = _welcome_card_width()
    inner = width - 2
    sprite_w = sprite_display_width()
    sprite_col = sprite_w + 2
    left_w = max(28, inner - sprite_col - 1)

    title_left = " my-agent "
    title_right = f" {version} "
    title_fill = max(1, inner - len(title_left) - len(title_right))

    border = "class:welcome.border"
    greet_style = "class:welcome.greet"
    sub_style = "class:welcome.sub"
    meta_model = "class:welcome.meta-model"
    meta_sep = "class:welcome.meta-sep"
    meta_root = "class:welcome.meta-root"
    label_style = "class:welcome.sub"

    fragments: list[tuple[str, str]] = []

    def _sprite_cell(line: str | None, *, kind: str = "none") -> None:
        if kind == "ansi" and line:
            pad = max(0, (sprite_col - sprite_w) // 2)
            if pad:
                fragments.append(("", " " * pad))
            fragments.extend(to_formatted_text(ANSI(line)))
            rest = sprite_col - pad - sprite_w
            if rest > 0:
                fragments.append(("", " " * rest))
            return
        if kind == "label" and line:
            fragments.append((label_style, _pad_display(line.center(sprite_col), sprite_col)))
            return
        fragments.append(("", " " * sprite_col))

    def _left_cell(text: str, style: str) -> None:
        cell = _pad_display(_truncate_display(text, left_w), left_w)
        if style:
            fragments.append((style, cell))
        else:
            fragments.append(("", cell))

    def _row(
        left_text: str = "",
        *,
        left_style: str = "",
        sprite_line: str | None = None,
        sprite_kind: str = "none",
    ) -> None:
        fragments.append((border, "│"))
        _left_cell(left_text, left_style)
        fragments.append((border, "│"))
        _sprite_cell(sprite_line, kind=sprite_kind)
        fragments.append((border, "│\n"))

    def _meta_row(sprite_line: str | None, *, sprite_kind: str) -> None:
        fragments.append((border, "│"))
        prefix = "  "
        mid = "  ·  "
        model_text = prefix + model
        used = _display_width(model_text) + _display_width(mid) + _display_width(root)
        fragments.append((meta_model, model_text))
        fragments.append((meta_sep, mid))
        fragments.append((meta_root, root))
        if used < left_w:
            fragments.append(("", " " * (left_w - used)))
        fragments.append((border, "│"))
        _sprite_cell(sprite_line, kind=sprite_kind)
        fragments.append((border, "│\n"))

    fragments.append((border, "╭" + title_left))
    fragments.append((border, "─" * title_fill))
    fragments.append((border, title_right + "╮\n"))

    sprite = list(sprite_lines())
    for index, sprite_line in enumerate(sprite):
        if index == 0:
            _row(f"  {greet1}", left_style=greet_style, sprite_line=sprite_line, sprite_kind="ansi")
        elif index == 1:
            _row(f"  {greet2}", left_style=sub_style, sprite_line=sprite_line, sprite_kind="ansi")
        elif index == 2:
            _meta_row(sprite_line, sprite_kind="ansi")
        else:
            _row("", sprite_line=sprite_line, sprite_kind="ansi")
    _row("", sprite_line=SPRITE_LABEL, sprite_kind="label")

    fragments.append((border, "╰" + "─" * inner + "╯\n"))
    return FormattedText(fragments)


def build_welcome_compact_formatted(panel: WelcomeContent) -> Any:
    """One-line amber strip — keeps branding without eating the transcript viewport."""
    from prompt_toolkit.formatted_text import FormattedText

    greet1, _greet2 = build_time_greeting_lines(
        resume=panel.resume,
        workspace=panel.workspace_name,
    )
    model = prompt_model_short(panel.llm_model) or panel.llm_model or "default"
    root = format_welcome_root_label(panel.effective_root)
    version = f"v{_TERMINAL_UI_VERSION}"

    width = _welcome_card_width()
    inner = width - 2
    border = "class:welcome.border"
    title_left = " my-agent "
    title_right = f" {version} "
    title_fill = max(1, inner - len(title_left) - len(title_right))
    body = f"  {greet1}  ·  {model}  ·  {root}  "

    return FormattedText(
        [
            (border, "╭" + title_left),
            (border, "─" * title_fill),
            (border, title_right + "╮\n"),
            (border, "│"),
            ("class:welcome.greet", _pad_display(_truncate_display(body, inner), inner)),
            (border, "│\n"),
            (border, "╰" + "─" * inner + "╯\n"),
        ]
    )


def format_welcome_plain(panel: WelcomeContent) -> str:
    """Plain-text fallback when styled welcome pane is unavailable."""
    greet1, greet2 = build_time_greeting_lines(
        resume=panel.resume,
        workspace=panel.workspace_name,
    )
    model = prompt_model_short(panel.llm_model) or panel.llm_model or "default"
    root = format_welcome_root_label(panel.effective_root)
    return "\n".join(
        (
            f"my-agent  v{_TERMINAL_UI_VERSION}",
            greet1,
            greet2,
            f"{model}  ·  {root}",
        )
    )


def build_welcome(
    *,
    session: Any,
    paths: Any,
    scope_fields: TerminalScopeFields | None = None,
    resume: bool = False,
    changelog_path: Path | None = None,
) -> WelcomeContent:
    meta = session.meta
    effective_root = resolve_effective_root_abs(meta, paths)
    llm_model = meta.llm_model or resolve_session_model(list(meta.topics))
    scope_kind = (
        scope_fields.terminal_scope_kind
        if scope_fields is not None
        else str(meta.terminal_scope_kind or "agent")
    )
    terminal_cwd = (
        scope_fields.terminal_cwd
        if scope_fields is not None
        else str(meta.terminal_cwd or "")
    )
    harness = str(meta.harness or "terminal")
    workspace = welcome_workspace_name(effective_root)
    greet1, greet2 = build_time_greeting_lines(resume=resume, workspace=workspace)
    root_label = format_welcome_root_label(effective_root)
    left = (
        greet1,
        greet2,
        root_label,
    )
    right = ("Ctrl+C 停止 · 新会话 · exit · /model · /clear · /compact",)
    return WelcomeContent(
        effective_root=effective_root,
        llm_model=llm_model,
        terminal_scope_kind=scope_kind,
        harness=harness,
        terminal_cwd=terminal_cwd,
        session_id=session.conversation_id,
        resume=resume,
        workspace_name=workspace,
        left_lines=left,
        right_lines=right,
    )


def build_status_bar(
    *,
    session: Any,
    paths: Any,
    status: str = "idle",
    active_tool: str = "",
    activity_detail: str = "",
) -> StatusBarContent:
    meta = session.meta
    effective_root = resolve_effective_root_abs(meta, paths)
    root_path = Path(effective_root)
    root_short = root_path.name or shorten_middle_path(effective_root, max_len=24)
    llm_model = meta.llm_model or resolve_session_model(list(meta.topics))
    session_id = session.conversation_id
    suffix = session_id if len(session_id) <= 12 else f"…{session_id[-8:]}"
    return StatusBarContent(
        llm_model=llm_model,
        turn_mode=str(meta.turn_mode or "agent"),
        root_short=root_short,
        session_suffix=suffix,
        status=status,
        active_tool=active_tool,
        activity_detail=activity_detail,
    )


def format_status_bar_line(bar: StatusBarContent) -> str:
    status = format_activity_status(
        bar.status,
        active_tool=bar.active_tool,
        activity_detail=bar.activity_detail,
    )
    dot = welcome_status_dot(bar.status)
    return f"{bar.llm_model}  ·  {bar.root_short}  ·  {dot} {status}"


def prompt_model_short(model_id: str) -> str:
    """Compact model label for the input prompt (avoids repeating full status bars)."""
    raw = (model_id or "").strip()
    if not raw:
        return "model"
    tail = raw.rsplit("-", 1)[-1]
    if tail in {"flash", "pro"}:
        return tail
    if len(tail) > 12:
        return f"{tail[:10]}…"
    return tail


def _ansi_tty_available(stream: Any | None = None) -> bool:
    target = stream if stream is not None else sys.stdout
    return bool(getattr(target, "isatty", lambda: False)())


def locate_welcome_model_line(rendered: str, model: str) -> tuple[int, str, str] | None:
    """Return (1-based row, prefix, suffix) for the model row inside a welcome capture."""
    for row, line in enumerate(rendered.splitlines(), start=1):
        idx = line.find(model)
        if idx >= 0:
            return row, line[:idx], line[idx + len(model) :]
    return None


def patch_terminal_line(row: int, text: str, *, stream: Any | None = None) -> None:
    """Overwrite one terminal row in-place (save/restore cursor)."""
    target = stream if stream is not None else sys.stdout
    target.write("\033[s")
    target.write(f"\033[{row};1H")
    target.write("\033[2K")
    target.write(text)
    target.write("\033[u")
    target.flush()


def resolve_terminal_backend_kind(*, stdout: Any | None = None) -> str:
    """Return ``rich`` or ``plain`` for the active terminal UI backend."""
    mode = _env_ui_mode()
    if mode == "plain":
        return "plain"
    if mode == "rich":
        return "rich" if _rich_import_ok() else "plain"
    stream = stdout if stdout is not None else sys.stdout
    is_tty = bool(getattr(stream, "isatty", lambda: False)())
    term = os.environ.get("TERM", "").strip().casefold()
    if not is_tty or term == "dumb" or os.environ.get("NO_COLOR"):
        return "plain"
    return "rich" if _rich_import_ok() else "plain"


@dataclass
class ToolPanelRecord:
    call_id: str
    tool: str
    summary: str
    running: bool = True
    ok: bool | None = None
    end_summary: str = ""
    rendered: str = ""


@dataclass
class TranscriptState:
    """In-memory transcript for tests and renderers."""

    backend_kind: str = "plain"
    turn_count: int = 0
    assistant_streaming: bool = False
    assistant_text: str = ""
    tool_panels: dict[str, ToolPanelRecord] = field(default_factory=dict)
    lines: list[str] = field(default_factory=list)
    reasoning_text: str = ""
    reasoning_streaming: bool = False

    def clear(self) -> None:
        kind = self.backend_kind
        self.turn_count = 0
        self.assistant_streaming = False
        self.assistant_text = ""
        self.tool_panels.clear()
        self.lines.clear()
        self.reasoning_text = ""
        self.reasoning_streaming = False
        self.backend_kind = kind

    def record_line(self, text: str) -> None:
        if text:
            self.lines.append(text)

    def begin_turn(self, intent_label: str = "") -> None:
        self.turn_count += 1
        self.assistant_streaming = False
        self.assistant_text = ""
        if not turn_separator_enabled():
            return
        label = intent_label.strip()
        if label:
            self.record_line(f"{_TURN_SEP} {label}")

    def record_user(self, text: str) -> None:
        stripped = text.strip()
        if stripped:
            self.record_line(stripped)

    def append_assistant_delta(self, text: str) -> None:
        if not text:
            return
        self.assistant_streaming = True
        self.assistant_text += text

    def finish_assistant(self, text: str = "") -> None:
        if text and not self.assistant_text:
            self.assistant_text = text
        if self.assistant_text:
            self.record_line(self.assistant_text)
        self.assistant_streaming = False
        self.assistant_text = ""

    def start_tool(self, call_id: str, tool: str, summary: str, rendered: str) -> None:
        self.tool_panels[call_id] = ToolPanelRecord(
            call_id=call_id,
            tool=tool,
            summary=summary,
            rendered=rendered,
        )
        self.record_line(rendered)

    def end_tool(
        self,
        call_id: str,
        tool: str,
        ok: bool,
        summary: str,
        rendered: str,
    ) -> None:
        panel = self.tool_panels.get(call_id)
        if panel is None:
            panel = ToolPanelRecord(call_id=call_id, tool=tool, summary=summary)
            self.tool_panels[call_id] = panel
        panel.running = False
        panel.ok = ok
        panel.end_summary = summary
        panel.rendered = rendered
        self.record_line(rendered)


class TerminalBackend(ABC):
    kind: str

    def __init__(self, *, write: OutputFn) -> None:
        self._write = write

    @abstractmethod
    def render_turn_start(self, intent_label: str) -> str: ...

    @abstractmethod
    def render_turn_end(self, finish_reason: str | None) -> str: ...

    @abstractmethod
    def render_user(self, text: str) -> str: ...

    @abstractmethod
    def render_assistant_delta(self, text: str) -> None: ...

    @abstractmethod
    def render_assistant_done(self, text: str) -> None: ...

    @abstractmethod
    def render_tool_start(self, tool: str, call_id: str, summary: str) -> str: ...

    @abstractmethod
    def render_tool_end(
        self,
        tool: str,
        call_id: str,
        ok: bool,
        summary: str,
    ) -> str: ...

    @abstractmethod
    def render_notice(self, text: str) -> None: ...

    @abstractmethod
    def render_welcome(self, panel: WelcomeContent) -> str: ...

    @abstractmethod
    def render_status_bar(self, bar: StatusBarContent) -> str: ...

    def render_prompt_footer(self, bar: StatusBarContent) -> str:
        """Single-line footer printed immediately before the input prompt."""
        return self.render_status_bar(bar)

    def patch_prompt_footer(self, bar: StatusBarContent) -> bool:
        """Rewrite the active footer in-place (no scroll). Returns False if unsupported."""
        return False

    def render_meta_notice(self, text: str) -> None:
        """Low-noise feedback for slash/meta commands (not part of the chat transcript)."""
        self.render_notice(text)

    def render_line(self, text: str) -> None:
        if text:
            self._write(text)

    def render_reasoning_delta(self, text: str) -> None:
        """Optional reasoning stream (MY_AGENT_TERMINAL_REASONING=1)."""

    def finish_reasoning(self) -> None:
        """Close an open reasoning block, if any."""


def _plain_notice_rows(text: str, *, inner: int = 62) -> list[str]:
    body = text.strip()
    if len(body) > inner:
        body = body[: inner - 1] + "…"
    return _plain_box_rows([body], inner=inner)


def _plain_tool_rows(tool: str, detail: str, *, mark: str = "", inner: int = 58) -> list[str]:
    title = f"⎿ {tool}"
    if mark:
        title = f"{title} {mark}"
    rows = [title]
    if detail.strip():
        rows.append(f"  {detail.strip()}")
    return _plain_box_rows(rows, inner=inner)


def _plain_box_rows(rows: list[str], *, inner: int = 66) -> list[str]:
    top = "╭" + "─" * (inner + 2) + "╮"
    bottom = "╰" + "─" * (inner + 2) + "╯"
    body: list[str] = []
    for row in rows:
        text = row
        if len(text) > inner:
            text = text[: inner - 1] + "…"
        body.append(f"│  {text.ljust(inner)} │")
    return [top, *body, bottom]


class PlainTerminalBackend(TerminalBackend):
    kind = "plain"

    def __init__(
        self,
        *,
        write: OutputFn,
        stream_write: Callable[[str], None] | None = None,
        stream_begin: Callable[[str], None] | None = None,
        stream_finalize: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(write=write)
        self._stream_write = stream_write or self._stdout_stream_write
        self._stream_begin = stream_begin
        self._stream_finalize = stream_finalize
        self._assistant_open = False
        self._reasoning_open = False
        self._assistant_buffer = ""

    def _uses_custom_stream(self) -> bool:
        return self._stream_write is not self._stdout_stream_write

    def _should_finalize_format(self) -> bool:
        return self._uses_custom_stream() and terminal_transcript_format_enabled()

    @staticmethod
    def _stdout_stream_write(text: str) -> None:
        if not text:
            return
        sys.stdout.write(text)
        sys.stdout.flush()

    def render_turn_start(self, intent_label: str) -> str:
        self._assistant_buffer = ""
        self._assistant_open = False
        if not turn_separator_enabled():
            return ""
        label = intent_label.strip()
        line = f"{_TURN_SEP} {label}" if label else _TURN_SEP
        self._write(line)
        return line

    def render_turn_end(self, finish_reason: str | None) -> str:
        if finish_reason == "cancelled":
            line = "(stopped)"
            self._write(line)
            self._write("")
            return line
        self._write("")
        return ""

    def render_user(self, text: str) -> str:
        stripped = text.strip()
        if not stripped:
            return ""
        line = format_terminal_user_line(stripped)
        self._write(line)
        self._write("")
        return line

    def render_assistant_delta(self, text: str) -> None:
        if not text:
            return
        self._assistant_buffer += text
        if not self._assistant_open:
            self._assistant_open = True
            if self._should_finalize_format() and self._stream_begin is not None:
                header = f"◆ {terminal_assistant_name()}\n\n"
                self._stream_begin(header)
            else:
                self._stream_write("  ")
        self._stream_write(text)

    def render_assistant_done(self, text: str) -> None:
        body = self._assistant_buffer
        self._assistant_buffer = ""
        if text.strip():
            if not body.strip() or len(text.strip()) >= len(body.strip()):
                body = text
        if self._assistant_open and self._should_finalize_format():
            self._assistant_open = False
            if body.strip() and self._stream_finalize is not None:
                block = format_terminal_assistant_block(body)
                if block:
                    self._stream_finalize(block)
            return
        if self._assistant_open:
            self._stream_write("\n")
            self._assistant_open = False
            return
        if body.strip() or text.strip():
            final = body.strip() or text.strip()
            payload = (
                format_terminal_assistant_block(final)
                if terminal_transcript_format_enabled()
                else final
            )
            self._write(payload)
            self._write("")

    def render_tool_start(self, tool: str, call_id: str, summary: str) -> str:
        detail = summary.strip() or call_id
        rows = _plain_tool_rows(tool, detail, mark="…")
        rendered = "\n".join(rows)
        for line in rows:
            self._write(line)
        return rendered

    def render_tool_end(self, tool: str, call_id: str, ok: bool, summary: str) -> str:
        mark = _TOOL_OK_MARK if ok else _TOOL_FAIL_MARK
        detail = summary.strip() or call_id
        rows = _plain_tool_rows(tool, detail, mark=mark)
        rendered = "\n".join(rows)
        for line in rows:
            self._write(line)
        return rendered

    def render_notice(self, text: str) -> None:
        stripped = text.strip()
        if not stripped:
            return
        for line in _plain_notice_rows(stripped):
            self._write(line)

    def render_meta_notice(self, text: str) -> None:
        stripped = text.strip()
        if stripped:
            self._write(f"· {stripped}")

    def render_reasoning_delta(self, text: str) -> None:
        if not text:
            return
        if not self._reasoning_open:
            self._reasoning_open = True
            self._reasoning_buffer = ""
            self._write("  ╭─ 思考 " + "─" * 24)
        self._reasoning_buffer = getattr(self, "_reasoning_buffer", "") + text
        self._stream_write(text)

    def finish_reasoning(self) -> None:
        if not self._reasoning_open:
            return
        self._stream_write("\n")
        self._write(f"  ╰{'─' * 27}")
        self._reasoning_open = False
        self._reasoning_buffer = ""

    def render_welcome(self, panel: WelcomeContent) -> str:
        greet, workspace, root = panel.left_lines[:3]
        header = f"my-agent terminal".ljust(48) + f"v{_TERMINAL_UI_VERSION}"
        hints = panel.right_lines[0] if panel.right_lines else ""
        rows = _plain_box_rows(
            [
                header,
                "",
                greet,
                workspace,
                root,
                hints,
            ]
        )
        rendered = "\n".join(rows)
        for line in rows:
            self._write(line)
        self._write("")
        return rendered

    def render_status_bar(self, bar: StatusBarContent) -> str:
        line = format_status_bar_line(bar)
        self._write(line)
        return line

    def patch_prompt_footer(self, bar: StatusBarContent) -> bool:
        if not _ansi_tty_available():
            return False
        line = format_status_bar_line(bar)
        # Cursor sits on the line after ``❯ /command``; footer is two lines above.
        sys.stdout.write("\033[2A\r\033[2K")
        sys.stdout.write(line)
        sys.stdout.write("\n\033[1B")
        sys.stdout.flush()
        return True

    def refresh_welcome(self, panel: WelcomeContent) -> None:
        return


class RichTerminalBackend(TerminalBackend):
    kind = "rich"

    def __init__(self, *, write: OutputFn, console: Any | None = None) -> None:
        super().__init__(write=write)
        from rich.console import Console

        self._console = console or Console(highlight=False, soft_wrap=True)
        self._assistant_open = False
        self._reasoning_open = False

    def _tool_panel(
        self,
        tool: str,
        detail: str,
        *,
        mark: str = "",
        mark_style: str = "green",
        running: bool = False,
    ) -> Any:
        from rich import box
        from rich.panel import Panel
        from rich.text import Text

        title = Text("⎿ ", style="dim")
        title.append(tool, style="bold")
        if running:
            title.append(" …", style="dim italic")
        elif mark:
            title.append(f" {mark}", style=mark_style)
        body = Text(detail, style="dim") if detail.strip() else Text()
        return Panel(
            body,
            title=title,
            title_align="left",
            box=box.ROUNDED,
            border_style="grey50",
            padding=(0, 1),
            expand=True,
        )

    def _welcome_renderable(self, panel: WelcomeContent) -> Any:
        from rich import box
        from rich.console import Group
        from rich.panel import Panel
        from rich.table import Table
        from rich.text import Text

        greet, workspace, root = panel.left_lines[:3]

        top = Table.grid(expand=True, padding=(0, 1))
        top.add_column(ratio=1)
        top.add_column(justify="right", no_wrap=True)
        brand = Text("my-agent", style="bold")
        top.add_row(brand, Text(f"v{_TERMINAL_UI_VERSION}", style="dim"))

        body = Group(
            top,
            Text(),
            Text(greet),
            Text(),
            Text(workspace, style="bold"),
            Text(root, style="dim"),
        )
        return Panel(
            body,
            box=box.ROUNDED,
            border_style="grey70",
            padding=(1, 2),
            expand=True,
        )

    def mount_welcome(self, panel: WelcomeContent) -> str:
        renderable = self._welcome_renderable(panel)
        with self._console.capture() as capture:
            self._console.print(renderable)
            self._console.print()
        rendered = capture.get().rstrip("\n")
        self._write(rendered)
        return rendered

    def refresh_welcome(self, panel: WelcomeContent) -> None:
        return

    def render_welcome(self, panel: WelcomeContent) -> str:
        return self.mount_welcome(panel)

    def render_turn_start(self, intent_label: str) -> str:
        if not turn_separator_enabled():
            return ""
        from rich.text import Text

        label = intent_label.strip()
        text = Text()
        text.append(_TURN_SEP, style="dim")
        if label:
            text.append(f" {label}", style="dim")
        rendered = f"{_TURN_SEP} {label}" if label else _TURN_SEP
        with self._console.capture() as capture:
            self._console.print(text)
        self._write(capture.get().rstrip("\n"))
        return rendered

    def render_turn_end(self, finish_reason: str | None) -> str:
        from rich.text import Text

        if finish_reason == "cancelled":
            text = Text("(stopped)", style="dim italic")
            with self._console.capture() as capture:
                self._console.print(text)
            line = capture.get().rstrip("\n")
            self._write(line)
            self._console.print()
            return line
        self._console.print()
        return ""

    def render_user(self, text: str) -> str:
        stripped = text.strip()
        if not stripped:
            return ""
        from rich.text import Text

        body = Text()
        body.append(stripped, style="bold")
        with self._console.capture() as capture:
            self._console.print(body)
        line = capture.get().rstrip("\n")
        self._write(line)
        return line

    def render_assistant_delta(self, text: str) -> None:
        if not text:
            return
        if not self._assistant_open:
            self._assistant_open = True
            from rich.text import Text

            self._console.print(Text("  ", style=""), end="")
        self._console.print(text, end="", soft_wrap=True)
        self._console.file.flush()

    def render_assistant_done(self, text: str) -> None:
        if self._assistant_open:
            self._console.print()
            self._assistant_open = False
            return
        if text:
            self._write(text)

    def render_tool_start(self, tool: str, call_id: str, summary: str) -> str:
        detail = summary.strip() or call_id
        renderable = self._tool_panel(tool, detail, running=True)
        with self._console.capture() as capture:
            self._console.print(renderable)
        rendered = capture.get().rstrip("\n")
        self._write(rendered)
        return rendered

    def render_tool_end(self, tool: str, call_id: str, ok: bool, summary: str) -> str:
        mark = _TOOL_OK_MARK if ok else _TOOL_FAIL_MARK
        style = "green" if ok else "red"
        detail = summary.strip() or call_id
        renderable = self._tool_panel(tool, detail, mark=mark, mark_style=style)
        with self._console.capture() as capture:
            self._console.print(renderable)
        rendered = capture.get().rstrip("\n")
        self._write(rendered)
        return rendered

    def render_notice(self, text: str) -> None:
        stripped = text.strip()
        if not stripped:
            return
        from rich import box
        from rich.panel import Panel
        from rich.text import Text

        renderable = Panel(
            Text(stripped, style="yellow"),
            box=box.ROUNDED,
            border_style="yellow3",
            padding=(0, 1),
            expand=True,
        )
        with self._console.capture() as capture:
            self._console.print(renderable)
        self._write(capture.get().rstrip("\n"))

    def render_meta_notice(self, text: str) -> None:
        stripped = text.strip()
        if not stripped:
            return
        from rich.text import Text

        body = Text(f"· {stripped}", style="dim italic")
        with self._console.capture() as capture:
            self._console.print(body)
        self._write(capture.get().rstrip("\n"))

    def render_reasoning_delta(self, text: str) -> None:
        if not text:
            return
        from rich.text import Text

        if not self._reasoning_open:
            self._reasoning_open = True
            with self._console.capture() as capture:
                self._console.print(Text("┌─ reasoning ─", style="dim italic"))
            self._write(capture.get().rstrip("\n"))
        self._console.print(Text(text, style="dim italic"), end="")
        self._console.file.flush()

    def finish_reasoning(self) -> None:
        if not self._reasoning_open:
            return
        self._console.print()
        with self._console.capture() as capture:
            self._console.print(Text("└─", style="dim italic"))
        self._write(capture.get().rstrip("\n"))
        self._reasoning_open = False

    def render_status_bar(self, bar: StatusBarContent) -> str:
        return self.render_prompt_footer(bar)

    def render_prompt_footer(self, bar: StatusBarContent) -> str:
        from rich.text import Text

        left = Text()
        left.append(bar.llm_model, style="dim")
        left.append("  ·  ", style="bright_black")
        left.append(bar.root_short, style="cyan dim")
        status = format_activity_status(
            bar.status,
            active_tool=bar.active_tool,
            activity_detail=bar.activity_detail,
        )
        dot = welcome_status_dot(bar.status)
        status_style = {
            "working": "yellow",
            "cancelled": "red",
        }.get(bar.status, "green")
        left.append("  ·  ", style="bright_black")
        left.append(f"{dot} ", style=status_style)
        left.append(status, style=status_style)
        with self._console.capture() as capture:
            self._console.print(left)
        line = capture.get().rstrip("\n")
        self._write(line)
        return line

    def patch_prompt_footer(self, bar: StatusBarContent) -> bool:
        if not _ansi_tty_available(self._console.file):
            return False
        line = format_status_bar_line(bar)
        self._console.file.write("\033[2A\r\033[2K")
        self._console.file.write(line)
        self._console.file.write("\n\033[1B")
        self._console.file.flush()
        return True


def build_terminal_backend(
    *,
    write: OutputFn,
    kind: str | None = None,
    console: Any | None = None,
    stream_write: Callable[[str], None] | None = None,
    stream_begin: Callable[[str], None] | None = None,
    stream_finalize: Callable[[str], None] | None = None,
) -> TerminalBackend:
    resolved = kind or resolve_terminal_backend_kind()
    if resolved == "rich":
        return RichTerminalBackend(write=write, console=console)
    return PlainTerminalBackend(
        write=write,
        stream_write=stream_write,
        stream_begin=stream_begin,
        stream_finalize=stream_finalize,
    )


@dataclass
class TerminalEventSink:
    """Subscribe to agent turn / stream / executor events for terminal rendering."""

    backend: TerminalBackend
    state: TranscriptState = field(default_factory=TranscriptState)
    _pending_user_text: str = ""
    status_listener: Callable[[str], None] | None = field(default=None, repr=False)
    _console: Any | None = field(default=None, repr=False)

    def bind_console(self, console: Any) -> None:
        self._console = console

    def __post_init__(self) -> None:
        self.state.backend_kind = self.backend.kind

    def set_pending_user_text(self, text: str) -> None:
        self._pending_user_text = text

    def emit(self, event: dict[str, Any]) -> None:
        event_type = event.get("type")
        if event_type == "turn.start":
            self.backend.finish_reasoning()
            if self.status_listener is not None:
                self.status_listener("working")
            if self._pending_user_text.strip():
                rendered = self.backend.render_user(self._pending_user_text)
                self.state.record_user(self._pending_user_text)
                if rendered:
                    pass
                self._pending_user_text = ""
            label = str(event.get("intent_label", "")).strip()
            rendered = self.backend.render_turn_start(label)
            self.state.begin_turn(label)
            if rendered:
                self.state.record_line(rendered)
            return
        if event_type == "turn.end":
            finish_reason = event.get("finish_reason")
            reason = str(finish_reason) if finish_reason else None
            if self.status_listener is not None:
                self.status_listener("idle")
            if self._console is not None:
                self._console.clear_activity()
            rendered = self.backend.render_turn_end(reason)
            if rendered:
                self.state.record_line(rendered)
            return
        if event_type == "turn.notice":
            level = str(event.get("level", "info"))
            text = str(event.get("text", "")).strip()
            if not text:
                return
            if level == "warn":
                text = f"[提醒] {text}"
            self.backend.render_notice(text)
            self.state.record_line(text)
            return
        if event_type == "assistant.delta":
            text = str(event.get("text", ""))
            self.backend.render_assistant_delta(text)
            self.state.append_assistant_delta(text)
            return
        if event_type == "reasoning.delta":
            if not reasoning_enabled():
                return
            text = str(event.get("text", ""))
            self.backend.render_reasoning_delta(text)
            if text:
                self.state.reasoning_streaming = True
                self.state.reasoning_text += text
            return
        if event_type == "assistant.done":
            self.backend.finish_reasoning()
            self.state.reasoning_streaming = False
            text = str(event.get("text", ""))
            self.backend.render_assistant_done(text)
            self.state.finish_assistant(text)
            return
        if event_type == "tool.start":
            self._handle_tool_start(event)
            return
        if event_type == "tool.end":
            self._handle_tool_end(event)
            return

    def on_executor_event(self, event_type: str, payload: dict[str, Any]) -> None:
        if event_type == "tool.start":
            if self._console is not None:
                tool = str(payload.get("tool", "tool"))
                summary = str(payload.get("summary", ""))
                self._console.note_tool_start(tool, summary)
        elif event_type == "tool.progress":
            if self._console is not None:
                text = str(payload.get("text", ""))
                self._console.note_tool_progress(text)
        elif event_type == "tool.end":
            if self._console is not None:
                self._console.note_tool_end()
        if event_type in {"tool.start", "tool.end", "tool.progress"}:
            self.emit({"type": event_type, **payload})
            return
        if event_type == "guard.notice":
            text = payload.get("text")
            if isinstance(text, str) and text.strip():
                self.emit({"type": "turn.notice", "level": "info", "text": text})

    def _handle_tool_start(self, event: dict[str, Any]) -> None:
        if not tool_panels_enabled():
            return
        tool = str(event.get("tool", "tool"))
        call_id = str(event.get("call_id", ""))
        summary = str(event.get("summary", ""))
        rendered = self.backend.render_tool_start(tool, call_id, summary)
        self.state.start_tool(call_id, tool, summary, rendered)

    def _handle_tool_end(self, event: dict[str, Any]) -> None:
        if not tool_panels_enabled():
            return
        tool = str(event.get("tool", "tool"))
        call_id = str(event.get("call_id", ""))
        ok = bool(event.get("ok"))
        summary = str(event.get("summary", ""))
        rendered = self.backend.render_tool_end(tool, call_id, ok, summary)
        self.state.end_tool(call_id, tool, ok, summary, rendered)

    def stream_handlers(self) -> StreamHandlers:
        return StreamHandlers(
            on_content_delta=lambda text: self.emit({"type": "assistant.delta", "text": text}),
            on_reasoning_delta=lambda text: self.emit({"type": "reasoning.delta", "text": text}),
        )

    def emit_assistant_done(self, text: str) -> None:
        self.emit({"type": "assistant.done", "text": text or ""})


@dataclass
class TerminalConsole:
    """Terminal REPL console: output_fn + event sink wiring."""

    sink: TerminalEventSink
    backend_kind: str = "plain"
    _lines: list[str] = field(default_factory=list)
    session: Any | None = field(default=None, repr=False)
    paths: Any | None = field(default=None, repr=False)
    scope_fields: TerminalScopeFields | None = field(default=None, repr=False)
    _status: str = "idle"
    _active_tool: str = ""
    _activity_detail: str = ""
    _footer_active: bool = False
    _status_change_listener: Callable[[], None] | None = field(default=None, repr=False)

    @classmethod
    def create(
        cls,
        *,
        write: OutputFn | None = None,
        stream_write: Callable[[str], None] | None = None,
        stream_begin: Callable[[str], None] | None = None,
        stream_finalize: Callable[[str], None] | None = None,
        kind: str | None = None,
        console: Any | None = None,
        session: Any | None = None,
        paths: Any | None = None,
        scope_fields: TerminalScopeFields | None = None,
    ) -> TerminalConsole:
        captured: list[str] = []

        def _write(text: str) -> None:
            captured.append(text)
            if write is not None:
                write(text)
            else:
                print(text)

        backend = build_terminal_backend(
            write=_write,
            kind=kind,
            console=console,
            stream_write=stream_write,
            stream_begin=stream_begin,
            stream_finalize=stream_finalize,
        )
        terminal = cls(
            sink=TerminalEventSink(backend=backend),
            backend_kind=backend.kind,
            _lines=captured,
            session=session,
            paths=paths,
            scope_fields=scope_fields,
        )
        terminal.sink.status_listener = terminal.set_status
        terminal.sink.bind_console(terminal)
        return terminal

    @property
    def state(self) -> TranscriptState:
        return self.sink.state

    @property
    def captured_lines(self) -> list[str]:
        return list(self._lines)

    def emit_meta_notice(self, text: str) -> None:
        """Slash/meta feedback — visible but not stored in the chat transcript."""
        stripped = text.strip()
        if not stripped:
            return
        self.sink.backend.render_meta_notice(stripped)

    def output_fn(self, text: str) -> None:
        stripped = text.strip()
        if not stripped:
            return
        if stripped.startswith("Terminal ·"):
            return
        if stripped.startswith("Commands:"):
            if self.backend_kind == "rich":
                # Rich welcome footer already lists commands.
                return
            self.sink.backend.render_line(stripped)
            self.state.record_line(stripped)
            return
        if stripped.startswith("[warn]"):
            self.sink.backend.render_notice(stripped)
            self.state.record_line(stripped)
            return
        if stripped.startswith("error:") or stripped.startswith("llm error:"):
            self.sink.backend.render_notice(stripped)
            self.state.record_line(stripped)
            return
        if stripped == "(cancelled)":
            self.sink.backend.render_notice(stripped)
            self.state.record_line(stripped)
            return
        self.sink.backend.render_line(stripped)
        self.state.record_line(stripped)

    def assistant_output_fn(self, text: str) -> None:
        self.sink.emit_assistant_done(text)

    def input_prompt(self) -> str:
        return "❯ " if self.backend_kind == "rich" else "you> "

    @property
    def status(self) -> str:
        return self._status

    def set_status(self, status: str) -> None:
        if status in {"idle", "working", "cancelled"}:
            self._status = status

    def set_status_change_listener(self, listener: Callable[[], None] | None) -> None:
        self._status_change_listener = listener

    def _notify_status_change(self) -> None:
        if self._status_change_listener is not None:
            self._status_change_listener()
        self.refresh_status_bar()

    def note_tool_start(self, tool: str, summary: str) -> None:
        self._active_tool = _activity_tool_label(tool, summary)
        self._activity_detail = ""
        self._notify_status_change()

    def note_tool_progress(self, text: str) -> None:
        stripped = text.strip()
        if not stripped:
            return
        self._activity_detail = stripped
        self._notify_status_change()

    def note_tool_end(self) -> None:
        if not self._active_tool and not self._activity_detail:
            return
        self._active_tool = ""
        self._activity_detail = ""
        self._notify_status_change()

    def set_activity(self, label: str, detail: str = "") -> None:
        self._active_tool = label.strip()
        self._activity_detail = detail.strip()
        self._notify_status_change()

    def clear_activity(self) -> None:
        if not self._active_tool and not self._activity_detail:
            return
        self._active_tool = ""
        self._activity_detail = ""
        self._notify_status_change()

    @property
    def active_tool(self) -> str:
        return self._active_tool

    @property
    def activity_detail(self) -> str:
        return self._activity_detail

    def _current_status_bar(self) -> StatusBarContent | None:
        if self.session is None or self.paths is None:
            return None
        return build_status_bar(
            session=self.session,
            paths=self.paths,
            status=self._status,
            active_tool=self._active_tool,
            activity_detail=self._activity_detail,
        )

    def begin_prompt_cycle(self) -> StatusBarContent | None:
        """Print a one-line footer immediately before blocking for input."""
        bar = self._current_status_bar()
        if bar is None:
            self._footer_active = False
            return None
        self.sink.backend.render_prompt_footer(bar)
        self._footer_active = True
        return bar

    def patch_prompt_footer(self) -> bool:
        """Update the active footer in-place (e.g. after ``/model``)."""
        if not self._footer_active:
            return False
        bar = self._current_status_bar()
        if bar is None:
            return False
        return self.sink.backend.patch_prompt_footer(bar)

    def refresh_status_bar(self) -> StatusBarContent | None:
        if self.patch_prompt_footer():
            return self._current_status_bar()
        return self.begin_prompt_cycle()

    def show_welcome(self, *, resume: bool = False) -> WelcomeContent | None:
        if not welcome_enabled():
            return None
        if self.session is None or self.paths is None:
            return None
        panel = build_welcome(
            session=self.session,
            paths=self.paths,
            scope_fields=self.scope_fields,
            resume=resume,
        )
        self.sink.backend.render_welcome(panel)
        return panel

    def refresh_welcome(self, *, resume: bool = True) -> WelcomeContent | None:
        if not welcome_enabled():
            return None
        if self.session is None or self.paths is None:
            return None
        panel = build_welcome(
            session=self.session,
            paths=self.paths,
            scope_fields=self.scope_fields,
            resume=resume,
        )
        backend = self.sink.backend
        if hasattr(backend, "refresh_welcome"):
            backend.refresh_welcome(panel)
        return panel

    def render_status_bar(self) -> StatusBarContent | None:
        return self.begin_prompt_cycle()

    def plain_banner_line(self) -> str:
        if self.session is None or self.paths is None:
            return "Terminal · exit 结束"
        root = resolve_effective_root_abs(self.session.meta, self.paths)
        return f"Terminal · {root} · exit 结束 | session {self.session.conversation_id}"

    def clear_transcript(self) -> None:
        kind = self.sink.state.backend_kind
        self.sink.backend.finish_reasoning()
        self.sink.state.clear()
        self.sink.state.backend_kind = kind
        line = "— transcript cleared —"
        self.sink.backend.render_line(line)
        self.sink.state.record_line(line)

    def bind_context(
        self,
        session: Any,
        paths: Any,
        scope_fields: TerminalScopeFields | None = None,
    ) -> None:
        self.session = session
        self.paths = paths
        if scope_fields is not None:
            self.scope_fields = scope_fields

    def wire_repl(self, repl: Any) -> None:
        """Attach stream handlers and event callbacks to a TerminalRepl / ConversationRepl."""
        repl.stream_handlers = self.sink.stream_handlers()
        repl.agent.stream_handlers = repl.stream_handlers
        repl.assistant_output_fn = self.assistant_output_fn
        repl.agent.on_turn_event = self.sink.emit
        repl.agent.executor.on_event = self.sink.on_executor_event

        original_rebind = repl._rebind_agent

        def rebind() -> None:
            original_rebind()
            repl.stream_handlers = self.sink.stream_handlers()
            repl.agent.stream_handlers = repl.stream_handlers
            repl.assistant_output_fn = self.assistant_output_fn
            repl.agent.on_turn_event = self.sink.emit
            repl.agent.executor.on_event = self.sink.on_executor_event

        repl._rebind_agent = rebind  # type: ignore[method-assign]
        self.sink.status_listener = self.set_status

    def begin_user_turn(self, text: str) -> None:
        self.sink.set_pending_user_text(text)

    def end_turn(self, *, finish_reason: str | None, ok: bool = True) -> None:
        self.sink.emit(
            {
                "type": "turn.end",
                "ok": ok,
                "finish_reason": finish_reason or "completed",
            }
        )


def tool_panel_text(state: TranscriptState, call_id: str) -> str:
    panel = state.tool_panels.get(call_id)
    if panel is None:
        return ""
    return panel.rendered


def make_test_console(*, kind: str = "rich") -> tuple[TerminalConsole, io.StringIO]:
    """Build a Rich console writing into a StringIO (for IT-577)."""
    from rich.console import Console

    buffer = io.StringIO()

    def write(text: str) -> None:
        buffer.write(text)
        if not text.endswith("\n"):
            buffer.write("\n")

    rich_console = Console(file=buffer, force_terminal=True, width=100, highlight=False)
    terminal = TerminalConsole.create(write=write, kind=kind, console=rich_console)
    return terminal, buffer
