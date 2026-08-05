"""run_quality — run ENV.md quality.commands and parse violations (Phase 45)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_AGENT_CORE = Path(__file__).resolve().parents[4] / "agent-core"
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from paths import AgentPaths
from project_quality import run_quality


def run(args: dict[str, Any]) -> dict[str, Any]:
    paths = AgentPaths.discover()
    working_dir = args.get("working_dir")
    if working_dir is not None and not isinstance(working_dir, str):
        return {"ok": False, "error": "working_dir must be a string"}
    only = args.get("only")
    if only is not None and not isinstance(only, list):
        return {"ok": False, "error": "only must be an array of command ids"}
    fail_fast = bool(args.get("fail_fast", True))
    dry_run = bool(args.get("dry_run", False))
    timeout_sec = int(args.get("timeout_sec", 120))
    return run_quality(
        paths,
        working_dir=working_dir.strip() if isinstance(working_dir, str) and working_dir.strip() else None,
        only=[str(x) for x in only] if isinstance(only, list) else None,
        fail_fast=fail_fast,
        dry_run=dry_run,
        timeout_sec=timeout_sec,
    )


if __name__ == "__main__":
    import json

    raw = sys.stdin.read()
    print(json.dumps(run(json.loads(raw) if raw.strip() else {}), ensure_ascii=False))
