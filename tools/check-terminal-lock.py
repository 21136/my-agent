"""Preflight check for start-terminal.bat (interface lock before WT hand-off)."""

from __future__ import annotations

import sys
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1] / "agent-core"
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from interface_lock import format_holder_message, format_takeover_hint, is_stale, read_lock
from paths import AgentPaths


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    holder = read_lock(AgentPaths.from_root(root))
    if holder is None or is_stale(holder):
        return 0
    print(format_holder_message(holder, requesting_ui="terminal"), file=sys.stderr)
    print(format_takeover_hint("terminal"), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
