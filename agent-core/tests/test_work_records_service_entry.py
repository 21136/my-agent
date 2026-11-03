from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

import server
from paths import AgentPaths
from server import WsBridge, WsSessionHandler
from session import Session, SessionMeta


def make_repl(tmp_path):
    paths = AgentPaths(tmp_path, tmp_path / "workspace", tmp_path / "data", tmp_path / "evolve")
    session_dir = paths.data / "sessions" / "current-session"
    session_dir.mkdir(parents=True)
    paths.workspace.mkdir()
    paths.evolve.mkdir()
    session = Session("current-session", session_dir, "", SessionMeta(), [], paths)
    return paths, SimpleNamespace(session=session)


def test_explicit_message_dispatch_builds_service_for_exact_current_session(tmp_path, monkeypatch):
    paths, repl = make_repl(tmp_path)
    events = []
    bridge = WsBridge(emit=events.append, paths=paths)
    calls = []

    class FakeClient:
        def __init__(self, *, paths):
            calls.append(("client", self, paths))
        def set_cancel_event(self, event):
            calls.append(("cancel", event))

    class FakeService:
        def __init__(self, *, session, client, confirm):
            calls.append(("service", session, client, confirm))
        def capture(self, text, *, request_id):
            calls.append(("capture", text, request_id))
            return SimpleNamespace(status="not_authorized", message="not authorized", item_id=None, requirement_id=None)

    monkeypatch.setattr(server, "LLMClient", FakeClient, raising=False)
    monkeypatch.setitem(sys.modules, "llm_client", SimpleNamespace(LLMClient=FakeClient))
    monkeypatch.setitem(sys.modules, "work_records_service", SimpleNamespace(WorkRecordCaptureService=FakeService))
    handler = WsSessionHandler(paths)

    asyncio.run(handler._dispatch_work_record_capture({
        "type": "work-record.capture", "session_id": "current-session", "request_id": "11111111-1111-4111-8111-111111111111", "text": "synthetic text"
    }, repl, bridge))

    assert calls[0][0] == "client" and calls[0][2] is paths
    assert calls[-1] == ("capture", "synthetic text", "11111111-1111-4111-8111-111111111111")
    assert calls[1][0] == "cancel"
    assert calls[2][0:3] == ("service", repl.session, calls[0][1])
    assert events[-1] == {
        "type": "work-record.capture.done", "request_id": "11111111-1111-4111-8111-111111111111", "session_id": "current-session",
        "status": "not_authorized", "message": "not authorized", "item_id": None, "requirement_id": None,
    }


def test_explicit_message_rejects_noncurrent_session_before_service_creation(tmp_path, monkeypatch):
    paths, repl = make_repl(tmp_path)
    events = []
    bridge = WsBridge(emit=events.append, paths=paths)
    monkeypatch.setitem(sys.modules, "llm_client", SimpleNamespace(LLMClient=lambda **_kwargs: pytest.fail("unexpected client")))
    monkeypatch.setitem(sys.modules, "work_records_service", SimpleNamespace(WorkRecordCaptureService=lambda **_kwargs: pytest.fail("unexpected service")))
    handler = WsSessionHandler(paths)

    import pytest
    asyncio.run(handler._dispatch_work_record_capture({
        "type": "work-record.capture", "session_id": "other-session", "text": "synthetic text"
    }, repl, bridge))

    assert events == [{"type": "error", "message": "work-record.capture must target the current session"}]




def test_work_record_capture_is_a_real_dispatch_message_type(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock
    paths, repl = make_repl(tmp_path)
    bridge = WsBridge(emit=lambda _event: None, paths=paths)
    handler = WsSessionHandler(paths)
    route = AsyncMock()
    handler._dispatch_work_record_capture = route

    asyncio.run(handler._dispatch({"type": "work-record.capture"}, repl, bridge))

    route.assert_awaited_once()


def _make_session(paths, conversation_id):
    session_dir = paths.data / "sessions" / conversation_id
    session_dir.mkdir(parents=True, exist_ok=True)
    return Session(conversation_id, session_dir, "synthetic", SessionMeta(), [], paths)


def _wait_for_event(events, event_type, index=0, timeout=1.5):
    import time
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        matches = [event for event in events if event.get("type") == event_type]
        if len(matches) > index:
            return matches[index]
        time.sleep(0.005)
    raise AssertionError(f"missing event {event_type} #{index}")


def test_real_handler_serializes_session_open_with_capture_but_keeps_confirm_inline(tmp_path, monkeypatch):
    import threading
    import llm_client
    import work_records_service

    paths, _ = make_repl(tmp_path)
    old_session = _make_session(paths, "old-current")
    new_session = _make_session(paths, "new-target")
    repl = SimpleNamespace(session=old_session, _rebind_agent=lambda: None)
    events = []
    bridge = WsBridge(emit=events.append, paths=paths)
    bridge.confirm_timeout = 2.0
    provider_started = threading.Event()
    provider_release = threading.Event()
    outbound = []

    class FakeClient:
        def __init__(self):
            self.paths = paths
        def set_cancel_event(self, _event):
            pass
        def chat(self, messages, **kwargs):
            outbound.append((messages, kwargs))
            provider_started.set()
            assert provider_release.wait(2.0), "test did not release provider"
            return '{"action":"record","kind":"requirement","title":"synthetic title","requirement":"synthetic requirement","scope":"independent","item_id":null}'

    class Entry:
        id = "0x567-flash"
        vendor = "0x567"
        provider_model = "gpt-6-luna"
        max_input_tokens = 96000
    class Registry:
        def get(self, _model):
            return Entry()

    monkeypatch.setattr(server, "TURN_LOCK", asyncio.Lock())
    monkeypatch.setattr(llm_client, "LLMClient", lambda **_kwargs: FakeClient())
    monkeypatch.setattr(work_records_service, "get_registry", lambda *_args, **_kwargs: Registry())
    monkeypatch.setattr(server, "load_session_for_harness", lambda *_args, **_kwargs: new_session)
    monkeypatch.setattr(bridge, "emit_session_state", lambda _session: None)
    monkeypatch.setattr(server, "emit_corruption_notices", lambda *_args, **_kwargs: None)
    handler = WsSessionHandler(paths)

    async def scenario():
        capture_task = asyncio.create_task(handler._handle_incoming({
            "type": "work-record.capture", "session_id": "old-current", "request_id": "22222222-2222-4222-8222-222222222222", "text": "synthetic original"
        }, repl, bridge))
        outbound_confirm = await asyncio.to_thread(_wait_for_event, events, "confirm.request", 0)
        await handler._handle_incoming({"type": "confirm.response", "request_id": outbound_confirm["request_id"], "choice": "yes"}, repl, bridge)
        await asyncio.wait_for(asyncio.to_thread(provider_started.wait, 1.0), 1.2)

        switch_task = asyncio.create_task(handler._handle_incoming({"type": "session.open", "session_id": "new-target"}, repl, bridge))
        await asyncio.sleep(0.05)
        assert repl.session is old_session, "session.open must wait while capture is bound to the old current session"
        assert not switch_task.done()

        provider_release.set()
        storage_confirm = await asyncio.to_thread(_wait_for_event, events, "confirm.request", 1)
        # confirm.response must bypass the waiting session-switch lock.
        await asyncio.wait_for(handler._handle_incoming({
            "type": "confirm.response", "request_id": storage_confirm["request_id"], "choice": "yes"
        }, repl, bridge), timeout=0.5)
        await asyncio.wait_for(capture_task, timeout=1.5)
        await asyncio.wait_for(switch_task, timeout=1.5)

    try:
        asyncio.run(scenario())
    finally:
        provider_release.set()

    assert repl.session is new_session
    assert len(outbound) == 1
    assert any(event.get("type") == "work-record.capture.done" and event.get("status") == "recorded" for event in events)
    assert (old_session.session_dir / "work-records.v1.json").exists()
    assert not (new_session.session_dir / "work-records.v1.json").exists()


def test_session_open_switch_wait_is_bounded_and_does_not_replace_session(tmp_path, monkeypatch):
    import pytest
    paths, repl = make_repl(tmp_path)
    old_session = _make_session(paths, "old-current")
    new_session = _make_session(paths, "new-target")
    repl.session = old_session
    repl._rebind_agent = lambda: None
    events = []
    bridge = WsBridge(emit=events.append, paths=paths)
    monkeypatch.setattr(server, "TURN_LOCK", asyncio.Lock())
    monkeypatch.setattr(server, "SESSION_SWITCH_WAIT_SECONDS", 0.01, raising=False)
    monkeypatch.setattr(server, "load_session_for_harness", lambda *_args, **_kwargs: new_session)
    monkeypatch.setattr(bridge, "emit_session_state", lambda _session: None)
    monkeypatch.setattr(server, "emit_corruption_notices", lambda *_args, **_kwargs: None)
    handler = WsSessionHandler(paths)

    async def scenario():
        await server.TURN_LOCK.acquire()
        try:
            await asyncio.wait_for(handler._handle_incoming({"type": "session.open", "session_id": "new-target"}, repl, bridge), timeout=0.5)
        finally:
            server.TURN_LOCK.release()

    asyncio.run(scenario())

    assert repl.session is old_session
    assert any(event.get("type") == "error" and "busy" in event.get("message", "").casefold() for event in events)

