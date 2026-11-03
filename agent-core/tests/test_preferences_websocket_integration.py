from __future__ import annotations

import asyncio
import json
import sys
import uuid
from pathlib import Path

import pytest

from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

CORE = Path(__file__).resolve().parents[1]
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))
from paths import AgentPaths
from server import WsSessionHandler
from session import create_new
from work_records import WorkRecordStore


async def _event(ws, event_type):
    async def read():
        while True:
            event = json.loads(await ws.recv())
            if event.get("type") == event_type:
                return event
    return await asyncio.wait_for(read(), 5)


async def _send(ws, payload, event_type):
    await ws.send(json.dumps(payload))
    return await _event(ws, event_type)


def test_local_websocket_role_models_recognition_and_existing_work_records(tmp_path):
    root = tmp_path / "isolated-agent"
    (root / "evolve").mkdir(parents=True)
    (root / "evolve" / "_index.core.toml").write_text('[[topic]]\nid = "common"\n', encoding="utf-8")
    (root / "workspace").mkdir()
    paths = AgentPaths.from_root(root)
    session = create_new(paths, conversation_id="integration-session")
    store = WorkRecordStore(session)
    item = store.create_item(title="preexisting record", scope={"kind": "independent"})
    before = store.list_items()

    async def exercise():
        handler = WsSessionHandler(paths)
        async with serve(handler.handle, "127.0.0.1", 0) as service:
            port = service.sockets[0].getsockname()[1]
            url = f"ws://127.0.0.1:{port}"
            async with connect(url) as ws:
                banner = await _event(ws, "session.banner")
                assert banner["session_id"] == session.conversation_id
                state = await _event(ws, "work-record.auto-recognition.state")
                assert state["enabled"] is True
                initial = await _event(ws, "llm_role_models.state")
                assert initial["status"] == "ok"
                assert all(isinstance(m.get("id"), str) and isinstance(m.get("name"), str)
                           and type(m.get("configured")) is bool for m in initial["models"])
                fetched = await _send(ws, {"type": "llm_role_models.get"}, "llm_role_models.state")
                assert fetched["preferences"] == initial["preferences"]
                overview_query = {"type": "work-records.overview.query", "session_id": session.conversation_id,
                                  "request_id": str(uuid.uuid4()), "page_size": 6, "detail_paging": True}
                overview = await _send(ws, overview_query, "work-records.overview.result")
                assert overview["state"] != "error"
                assert item["item_id"] in json.dumps(overview)
                for role, model in (("main", "0x567-flash"), ("subagent", "0x567-pro")):
                    response = await _send(ws, {"type": "llm_role_models.set", "role": role, "model": model}, "llm_role_models.done")
                    assert response["status"] == "ok"
                    assert response["preferences"][role] == model
                error = await _send(ws, {"type": "llm_role_models.set", "role": "main", "model": "invalid-model"}, "llm_role_models.done")
                assert error["status"] == "rejected"
                assert error["preferences"]["main"] == "0x567-flash"
                stale = await _send(ws, {"type": "work-record.auto-recognition.set", "session_id": "wrong-session",
                    "request_id": str(uuid.uuid4()), "action": "revoke"}, "work-record.auto-recognition.done")
                assert stale["status"] == "rejected"
                request_id = str(uuid.uuid4())
                revoke_message = {"type": "work-record.auto-recognition.set", "session_id": session.conversation_id,
                                  "request_id": request_id, "action": "revoke"}
                revoked = await _send(ws, revoke_message, "work-record.auto-recognition.done")
                assert revoked["status"] == "ok" and revoked["enabled"] is False
                replay = await _send(ws, revoke_message, "work-record.auto-recognition.done")
                assert replay["status"] == "rejected" and replay["enabled"] is False
                overview_query["request_id"] = str(uuid.uuid4())
                overview_after = await _send(ws, overview_query, "work-records.overview.result")
                assert overview_after["state"] != "error"
                assert item["item_id"] in json.dumps(overview_after)
                second = create_new(paths, conversation_id="second-integration-session")
                await ws.send(json.dumps({"type": "session.open", "session_id": second.conversation_id}))
                switched = await _event(ws, "session.banner")
                assert switched["session_id"] == second.conversation_id
                switched_recognition = await _event(ws, "work-record.auto-recognition.state")
                assert switched_recognition["session_id"] == second.conversation_id
                assert switched_recognition["enabled"] is False
            async with connect(url) as ws:
                await _event(ws, "session.banner")
                state = await _event(ws, "work-record.auto-recognition.state")
                assert state["enabled"] is False
                role_state = await _event(ws, "llm_role_models.state")
                assert role_state["preferences"] == {"main": "0x567-flash", "subagent": "0x567-pro"}

    asyncio.run(exercise())
    assert store.get_item(item["item_id"])["title"] == "preexisting record"
    assert store.list_items() == before
    assert (paths.data / "llm_role_models.json").is_file()
    assert (paths.data / "auto-recognition-preference.sqlite3").is_file()
    from llm_role_model_preferences import load_role_model_preferences
    from llm_routing import resolve_model_id_for_role
    other_root = tmp_path / "different-agent"
    (other_root / "evolve").mkdir(parents=True)
    (other_root / "evolve" / "_index.core.toml").write_text("", encoding="utf-8")
    other = AgentPaths.from_root(other_root)
    assert load_role_model_preferences(other) == {"main": "0x567-pro", "subagent": "0x567-flash"}
    assert not (other.data / "llm_role_models.json").exists()
    assert resolve_model_id_for_role("main_turn", session.meta, paths=paths) == "0x567-flash"
    assert resolve_model_id_for_role("explore", session.meta, paths=paths) == "0x567-pro"


def test_corrupt_role_preferences_still_deliver_session_and_rejection_over_websocket(tmp_path):
    root = tmp_path / "corrupt-agent"
    (root / "evolve").mkdir(parents=True)
    (root / "evolve" / "_index.core.toml").write_text('[[topic]]\nid = "common"\n', encoding="utf-8")
    (root / "workspace").mkdir()
    paths = AgentPaths.from_root(root)
    session = create_new(paths, conversation_id="corrupt-integration-session")
    target = paths.data / "llm_role_models.json"
    target.write_text("{broken", encoding="utf-8")
    auto_target = paths.data / "auto-recognition-preference.sqlite3"
    auto_target.write_bytes(b"broken sqlite")

    async def exercise():
        async with serve(WsSessionHandler(paths).handle, "127.0.0.1", 0) as service:
            url = f"ws://127.0.0.1:{service.sockets[0].getsockname()[1]}"
            async with connect(url) as ws:
                banner = await _event(ws, "session.banner")
                assert banner["session_id"] == session.conversation_id
                assert banner["llm_model"] == ""
                auto_state = await _event(ws, "work-record.auto-recognition.state")
                assert auto_state["enabled"] is False
                state = await _event(ws, "llm_role_models.state")
                assert state["status"] == "rejected" and state["message"]
                auto_error = await _send(ws, {"type": "work-record.auto-recognition.set", "session_id": session.conversation_id,
                    "request_id": str(uuid.uuid4()), "action": "enable"}, "work-record.auto-recognition.done")
                assert auto_error["status"] == "rejected" and auto_error["enabled"] is False
                response = await _send(ws, {"type": "llm_role_models.get"}, "llm_role_models.done")
                assert response["status"] == "rejected"
    asyncio.run(exercise())
    assert target.read_text(encoding="utf-8") == "{broken"
    assert auto_target.read_bytes() == b"broken sqlite"
    from llm_role_model_preferences import LlmRoleModelPreferenceError
    from llm_routing import resolve_model_id_for_role
    with pytest.raises(LlmRoleModelPreferenceError):
        resolve_model_id_for_role("main_turn", session.meta, paths=paths)
