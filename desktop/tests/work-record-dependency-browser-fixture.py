"""Isolated real WebSocket fixture with two synthetic same-session work items."""
from __future__ import annotations
import asyncio, json, pathlib, sys, uuid

core = pathlib.Path(sys.argv[1]).resolve()
sys.path[:0] = [str(core), str(core / "tests")]
import pytest, server
from websockets.asyncio.server import serve
from session import create_new
from work_records import WorkRecordStore
from tests.isolation_helpers import temporary_agent_paths

async def main():
    with temporary_agent_paths(copy_tool_dirs=("common/interactive_terminal", "common/run_service")) as paths:
        session = create_new(paths, conversation_id="browser-dependency-isolated")
        session.meta.turn_mode = "agent"; session.meta.harness = "desktop"; session.save()
        store = WorkRecordStore(session)
        source = store.create_item(title="Synthetic prerequisite item", scope={"kind": "independent"})
        source_req = store.add_requirement(source["item_id"], "Synthetic prerequisite task", event_id="browser-prerequisite")
        target = store.create_item(title="Synthetic dependent item", scope={"kind": "independent"})
        target_req = store.add_requirement(target["item_id"], "Synthetic dependent task", event_id="browser-dependent")
        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(server, "resume_desktop_or_create", lambda _paths: session)
        monkeypatch.setattr(server, "emit_corruption_notices", lambda *_a: None)
        monkeypatch.setattr(server, "_emit_pending_work_card_references", lambda *_a: None)
        async with serve(server.WsSessionHandler(paths).handle, "127.0.0.1", 0) as ws:
            print(json.dumps({"port": ws.sockets[0].getsockname()[1], "session_id": session.conversation_id,
                              "source_item": source["item_id"], "source_requirement": source_req["requirement_id"],
                              "target_item": target["item_id"], "target_requirement": target_req["requirement_id"]}), flush=True)
            await asyncio.to_thread(sys.stdin.buffer.readline)
        monkeypatch.undo()
asyncio.run(main())
