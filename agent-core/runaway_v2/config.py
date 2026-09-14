"""Runaway v2 environment configuration."""

from __future__ import annotations

import os


def runaway_v2_env_enabled() -> bool:
    return os.environ.get("MY_AGENT_RUNAWAY_V2", "0").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def directed_after() -> int:
    raw = os.environ.get("MY_AGENT_RUNAWAY_V2_DIRECTED_AFTER", "2").strip()
    try:
        value = int(raw)
    except ValueError:
        value = 2
    return max(1, value)


def _bounded_int(name: str, default: int, *, minimum: int = 1, maximum: int = 1000) -> int:
    raw = os.environ.get(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError:
        value = default
    return min(maximum, max(minimum, value))


def no_progress_turns() -> int:
    """Automatic turns with the same project state before pausing."""
    return _bounded_int("MY_AGENT_RUNAWAY_V2_NO_PROGRESS_TURNS", 3, maximum=100)


def max_auto_turns() -> int:
    """Maximum automatic continuation turns in one runaway session."""
    return _bounded_int("MY_AGENT_RUNAWAY_V2_MAX_AUTO_TURNS", 24, maximum=1000)


def hook_timeout_sec() -> float:
    raw = os.environ.get("MY_AGENT_RUNAWAY_V2_HOOK_TIMEOUT_SEC", "180").strip()
    try:
        value = float(raw)
    except ValueError:
        value = 180.0
    return max(5.0, value)
