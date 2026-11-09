"""Seed one companion desktop session with interrupted work records for real Electron recovery H1.

Companion session stays project-agnostic (no session.meta.project_id). Mirrors the independent
slice of agent-core/tests/test_workbench_crash_reopen_recovery_harness.py — no live model.
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path

_CORE = Path(__file__).resolve().parents[2] / "agent-core"
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

from paths import AgentPaths
from session import Session, SessionMeta
from work_records import WorkRecordStore, _WORK_RECORD_PROCESS_INSTANCE_ID

CONVERSATION_ID = os.environ.get("WORKBENCH_RECOVERY_SESSION_ID", "workbench-recovery-electron")
FOREIGN_OWNER = "crashed-electron-sidecar-process"


def _stamp_foreign_execution_owner(
    store: WorkRecordStore, item_id: str, requirement_id: str, owner_id: str,
) -> None:
    def change(data):
        changed = 0
        for item in data["items"]:
            if item.get("item_id") != item_id:
                continue
            for req in item.get("requirements", []):
                if req.get("requirement_id") == requirement_id:
                    req["execution_owner_id"] = owner_id
                    changed += 1
        return changed, changed > 0

    store._mutate(change)


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: workbench-crash-reopen-recovery-electron-seed.py <app_root>")
    app_root = Path(sys.argv[1]).resolve(strict=True)
    paths = AgentPaths(app_root, app_root / "workspace", app_root / "data", app_root / "evolve")
    paths.workspace.mkdir(parents=True, exist_ok=True)
    session_dir = paths.data / "sessions" / CONVERSATION_ID
    session_dir.mkdir(parents=True, exist_ok=True)
    meta = SessionMeta(active_shell="grow", harness="desktop", turn_mode="agent")
    session = Session(CONVERSATION_ID, session_dir, "", meta, [], paths)
    session.save()

    store = WorkRecordStore(session)
    crashed_item = store.create_item(title="crashed worker task", scope={"kind": "independent"})
    crashed_req = store.add_requirement(
        crashed_item["item_id"], "stale in-flight requirement", event_id="stale-req",
    )
    store.begin_requirement_run(
        crashed_item["item_id"], crashed_req["requirement_id"],
        run_id="stale-run", expected_current_run_id=None,
    )

    live_item = store.create_item(title="same-process live task", scope={"kind": "independent"})
    live_req = store.add_requirement(
        live_item["item_id"], "live in-flight requirement", event_id="live-req",
    )
    store.begin_requirement_run(
        live_item["item_id"], live_req["requirement_id"],
        run_id="live-run", expected_current_run_id=None,
    )

    _stamp_foreign_execution_owner(
        store, crashed_item["item_id"], crashed_req["requirement_id"], FOREIGN_OWNER,
    )
    session.save()

    marker = {
        "conversation_id": CONVERSATION_ID,
        "foreign_owner": FOREIGN_OWNER,
        "process_instance_id": _WORK_RECORD_PROCESS_INSTANCE_ID,
        "project_bound": bool(session.meta.project_id),
    }
    print(json.dumps(marker, ensure_ascii=False))


if __name__ == "__main__":
    main()
