#!/usr/bin/env python3
"""Sync welcome_mascot_data.py → terminal-ui/src/theme/welcomeMascotData.ts (single source)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parent.parent
_REPO = _AGENT_CORE.parent
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from welcome_mascot_data import SPRITE_LABEL, SPRITE_WIDTH, TRUECOLOR_LINES  # noqa: E402

OUT = _REPO / "terminal-ui" / "src" / "theme" / "welcomeMascotData.ts"


def main() -> None:
    payload = json.dumps(list(TRUECOLOR_LINES), ensure_ascii=False)
    OUT.write_text(
        "\n".join(
            [
                "/** Auto-synced from agent-core/welcome_mascot_data.py — do not edit by hand. */",
                f"export const MASCOT_WIDTH = {SPRITE_WIDTH};",
                f"export const MASCOT_LABEL = {json.dumps(SPRITE_LABEL, ensure_ascii=False)};",
                f"export const MASCOT_LINES: readonly string[] = {payload};",
                "",
            ]
        ),
        encoding="utf-8",
    )
    print(f"[sync] wrote {OUT.relative_to(_REPO)}")


if __name__ == "__main__":
    main()
