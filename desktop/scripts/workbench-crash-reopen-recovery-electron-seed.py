"""Seed one companion desktop session with interrupted work records for real Electron recovery H1.

Companion session stays project-agnostic (no session.meta.project_id). Foreign-owner
in_progress rows are fully cleared when the Electron sidecar opens; a persisted
schedule_claim in state unknown keeps recovery unresolved and emits the warn notice.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_CORE = Path(__file__).resolve().parents[2] / "agent-core"
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

from paths import AgentPaths
from session import Session, SessionMeta
from work_records import WorkRecordStore, _WORK_RECORD_PROCESS_INSTANCE_ID, _digest

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


def _seed_unresolved_schedule_claim(
    store: WorkRecordStore,
    item_id: str,
    requirement_id: str,
    requirement_text: str,
    conversation_id: str,
) -> None:
    """Persist a companion-local schedule_claim (project_id=\"\") in state unknown."""

    digest = _digest({"text": requirement_text})

    def change(data):
        for item in data["items"]:
            if item.get("item_id") != item_id:
                continue
            for req in item.get("requirements", []):
                if req.get("requirement_id") != requirement_id:
                    continue
                if req.get("text") != requirement_text:
                    raise RuntimeError("recovery seed requirement text mismatch")
                req["schedule_claim"] = {
                    "source_session_id": conversation_id,
                    "project_id": "",
                    "item_id": item_id,
                    "requirement_id": requirement_id,
                    "revision": data["revision"],
                    "requirement_digest": digest,
                    "owner_id": FOREIGN_OWNER,
                    "reservation_id": "recovery-reservation-1",
                    "parent_run_id": "recovery-parent-1",
                    "state": "unknown",
                }
                return 1, True
        return 0, False

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

    claim_item = store.create_item(title="unresolved schedule claim", scope={"kind": "independent"})
    claim_text = "claim needs human inspection after crash"
    claim_req = store.add_requirement(
        claim_item["item_id"], claim_text, event_id="claim-req",
    )
    _stamp_foreign_execution_owner(
        store, crashed_item["item_id"], crashed_req["requirement_id"], FOREIGN_OWNER,
    )
    _seed_unresolved_schedule_claim(
        store, claim_item["item_id"], claim_req["requirement_id"], claim_text, CONVERSATION_ID,
    )
    session.save()

    marker = {
        "conversation_id": CONVERSATION_ID,
        "foreign_owner": FOREIGN_OWNER,
        "process_instance_id": _WORK_RECORD_PROCESS_INSTANCE_ID,
        "project_bound": bool(session.meta.project_id),
        "durable_unresolved_schedule_claim": True,
    }
    print(json.dumps(marker, ensure_ascii=False))


if __name__ == "__main__":
    main()
