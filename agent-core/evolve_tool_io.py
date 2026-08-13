"""Shared stdin/stdout JSON protocol for evolve tool entry scripts."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

_INVALID_STDIN = {"ok": False, "error": "stdin must be a JSON object"}


def normalize_newlines(text: str) -> str:
    """Collapse CR/LF variants to LF (BUG-025)."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def write_utf8_text(path: Path, content: str) -> None:
    """Write UTF-8 text without platform newline translation (BUG-025)."""
    from file_guard import atomic_write_text

    atomic_write_text(path, content)


def ensure_utf8_stdio() -> None:
    """Best-effort UTF-8 reconfigure for text-mode stdout/stderr."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass


def emit_json(payload: dict[str, Any]) -> None:
    """Write JSON to stdout as UTF-8 bytes (avoids Windows GBK console errors)."""
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8", errors="replace")
    sys.stdout.buffer.write(data + b"\n")
    sys.stdout.buffer.flush()


def run_tool_main(run_fn: Callable[[dict[str, Any]], dict[str, Any]]) -> None:
    """Standard evolve tool main: read stdin JSON, run, emit JSON, exit on failure."""
    ensure_utf8_stdio()
    payload = json.load(sys.stdin)
    if not isinstance(payload, dict):
        emit_json(_INVALID_STDIN)
        raise SystemExit(1)
    result = run_fn(payload)
    emit_json(result)
    if result.get("ok") is False:
        raise SystemExit(1)
