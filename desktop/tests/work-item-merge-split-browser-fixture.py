"""Companion-home WebSocket fixture: independent items for merge/split overlay GUI E2E."""
from __future__ import annotations

import asyncio
import json
import pathlib
import sys

core = pathlib.Path(sys.argv[1]).resolve()
sys.path[:0] = [str(core), str(core / "tests")]

import pytest
import server
from session import create_new
from tests.isolation_helpers import temporary_agent_paths
from websockets.asyncio.server import serve
from work_records import WorkRecordStore

COMPANION_ID = "20261110-aaaaaaaa"
SPLIT_PARENT_TITLE = "Split parent item"
TARGET_TITLE = "AAA merge target item"
SOURCE_TITLE = "ZZZ merge source item"
SPLIT_TEXT = "Requirement to split into new item"
MOVE_TEXT = "Requirement to merge into target"


async def main() -> None:
    with temporary_agent_paths(copy_tool_dirs=("common/interactive_terminal", "common/run_service")) as paths:
        companion = create_new(paths, conversation_id=COMPANION_ID)
        companion.meta.harness = "desktop"
        companion.meta.turn_mode = "agent"
        companion.meta.active_shell = "grow"
        companion.save()
        assert not (companion.meta.project_id or "").strip()

        store = WorkRecordStore(companion)
        split_parent = store.create_item(title=SPLIT_PARENT_TITLE, scope={"kind": "independent"})
        split_req = store.add_requirement(split_parent["item_id"], SPLIT_TEXT, event_id="split-req")
        target = store.create_item(title=TARGET_TITLE, scope={"kind": "independent"})
        target_req = store.add_requirement(target["item_id"], "Target keeps this", event_id="target-req")
        source = store.create_item(title=SOURCE_TITLE, scope={"kind": "independent"})
        source_req = store.add_requirement(source["item_id"], MOVE_TEXT, event_id="move-req")

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(server, "resume_desktop_or_create", lambda _paths: companion)
        monkeypatch.setattr(server, "emit_corruption_notices", lambda *_a: None)
        monkeypatch.setattr(server, "_emit_pending_work_card_references", lambda *_a: None)

        ready = {
            "port": 0,
            "session_id": COMPANION_ID,
            "companion_unbound": True,
            "split_parent_item": split_parent["item_id"],
            "split_requirement": split_req["requirement_id"],
            "target_item": target["item_id"],
            "target_requirement": target_req["requirement_id"],
            "source_item": source["item_id"],
            "source_requirement": source_req["requirement_id"],
        }

        async with serve(server.WsSessionHandler(paths).handle, "127.0.0.1", 0) as ws:
            ready["port"] = ws.sockets[0].getsockname()[1]
            print(json.dumps(ready), flush=True)
            await asyncio.to_thread(sys.stdin.buffer.readline)
        monkeypatch.undo()


if __name__ == "__main__":
    asyncio.run(main())
