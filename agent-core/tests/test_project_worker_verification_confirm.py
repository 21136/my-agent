from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from paths import AgentPaths
from server import WsBridge
from session import Session, SessionMeta


def test_existing_confirmation_channel_emits_bound_structured_payload(tmp_path):
    paths = AgentPaths(agent_root=tmp_path, workspace=tmp_path / "workspace", data=tmp_path / "data", evolve=tmp_path / "evolve")
    session_dir = paths.data / "sessions" / "s1"; session_dir.mkdir(parents=True)
    session = Session(conversation_id="s1", session_dir=session_dir, paths=paths, meta=SessionMeta(), messages=[], goal="")
    events = []
    def emit(event):
        events.append(event)
        if event.get("type") == "confirm.request":
            bridge.deliver_confirm(event["request_id"], "y")
    bridge = WsBridge(emit=emit, paths=paths)
    bridge._session_provider = lambda: session
    assert bridge.begin_turn(mode="ordinary")
    bridge.lifecycle_conversation_id = "s1"
    run_id = bridge.lifecycle.snapshot().run_id
    payload = {"session_id":"s1", "project_id":"p", "item_id":"i", "requirement_id":"r",
               "project_name":"p", "requirement_text":"required", "pytest_target":"test_a.py::test_ok",
               "worker_run_id":"w", "reservation_id":"res"}
    assert bridge.confirm_project_worker_requirement(session=session, run_id=run_id, payload=payload) == "y"
    event = next(e for e in events if e.get("confirm_subtype") == "project_worker_requirement_verification")
    assert event["project_worker_verification"] == payload
    assert event["session_id"] == "s1" and event["run_id"] == run_id
    assert event["allow_approve_all"] is False
    done = next(e for e in events if e.get("type") == "confirm.done")
    assert (done["request_id"], done["confirm_subtype"], done["session_id"], done["run_id"]) == (
        event["request_id"], event["confirm_subtype"], "s1", run_id)
    assert "conftest" in event["preview"] and "Python" in event["preview"]
    assert "自报告" in event["preview"] and "不认证" in event["preview"] and "不证明整体要求完成" in event["preview"]
    assert bridge.confirm_project_worker_requirement(session=session, run_id="stale", payload=payload) == "n"



def test_confirmation_cancel_and_mismatched_session_fail_closed(tmp_path):
    paths = AgentPaths(agent_root=tmp_path, workspace=tmp_path / "workspace", data=tmp_path / "data", evolve=tmp_path / "evolve")
    session_dir = paths.data / "sessions" / "s1"; session_dir.mkdir(parents=True)
    session = Session(conversation_id="s1", session_dir=session_dir, paths=paths, meta=SessionMeta(), messages=[], goal="")
    events = []
    def emit(event):
        events.append(event)
        if event.get("type") == "confirm.request":
            bridge.cancel_event.set()
            bridge.deliver_confirm(event["request_id"], "y")
    bridge = WsBridge(emit=emit, paths=paths)
    bridge._session_provider = lambda: session
    assert bridge.begin_turn(mode="ordinary")
    bridge.lifecycle_conversation_id = "s1"
    run_id = bridge.lifecycle.snapshot().run_id
    payload = {"session_id":"wrong", "project_id":"p", "item_id":"i", "requirement_id":"r",
               "project_name":"p", "requirement_text":"required", "pytest_target":"test_a.py::test_ok",
               "worker_run_id":"w", "reservation_id":"res"}
    assert bridge.confirm_project_worker_requirement(session=session, run_id=run_id, payload=payload) == "n"
    assert not any(e.get("type") == "confirm.request" for e in events)
    payload["session_id"] = "s1"
    assert bridge.confirm_project_worker_requirement(session=session, run_id=run_id, payload=payload) == "n"
