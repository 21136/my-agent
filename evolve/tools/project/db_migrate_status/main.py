"""db_migrate_status — read-only alembic/prisma migration status (Phase 45)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_AGENT_CORE = Path(__file__).resolve().parents[4] / "agent-core"
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from paths import AgentPaths
from project_quality import db_migrate_status


def run(args: dict[str, Any]) -> dict[str, Any]:
    paths = AgentPaths.discover()
    working_dir = str(args.get("working_dir") or "").strip()
    if not working_dir:
        return {"ok": False, "error": "working_dir is required"}
    backend = str(args.get("backend") or "auto")
    dry_run = bool(args.get("dry_run", False))
    timeout_sec = int(args.get("timeout_sec", 120))
    return db_migrate_status(
        paths,
        working_dir=working_dir,
        backend=backend,
        dry_run=dry_run,
        timeout_sec=timeout_sec,
    )


if __name__ == "__main__":
    import json

    raw = sys.stdin.read()
    print(json.dumps(run(json.loads(raw) if raw.strip() else {}), ensure_ascii=False))
