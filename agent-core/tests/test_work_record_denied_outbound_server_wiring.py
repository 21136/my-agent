from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

import llm_client
import server
import work_records_luna_intent
import work_records_service
from paths import AgentPaths
from server import WsBridge, WsSessionHandler
from session import Session, SessionMeta
from work_records import WorkRecordStore
from work_records_luna_intent import EXPECTED_PROVIDER_MODEL, EXPECTED_VENDOR, MODEL_ID

REQUEST_ID = "59e0ff7d-a330-4c6b-9a40-178f1c914a24"
TEXT = "请记录一下独立事项：交付一份合成报告，报告须包含两条观察。"


class FakeClassifier:
    def __init__(self, paths):
        self.paths = paths
        self.calls = []

    def set_cancel_event(self, event):
        self.cancel_event = event

    def chat(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        return json.dumps({"action": "none", "kind": "execution", "title": None,
                           "requirement": None, "scope": None, "item_id": None})


@pytest.fixture
def wiring(tmp_path, monkeypatch):
    paths = AgentPaths(tmp_path, tmp_path / "workspace", tmp_path / "data", tmp_path / "evolve")
    session_dir = paths.data / "sessions" / "current-session"
    session_dir.mkdir(parents=True)
    session = Session("current-session", session_dir, "", SessionMeta(), [], paths)
    events = []
    bridge = WsBridge(emit=events.append, paths=paths, confirm_timeout=3)
    client = FakeClassifier(paths)
    monkeypatch.setattr(llm_client, "LLMClient", lambda **_kwargs: client)
    registry = SimpleNamespace(get=lambda model_id: SimpleNamespace(
        id=MODEL_ID, vendor=EXPECTED_VENDOR, provider_model=EXPECTED_PROVIDER_MODEL,
        max_input_tokens=100_000,
    ) if model_id == MODEL_ID else None)
    monkeypatch.setattr(work_records_service, "get_registry", lambda *_args: registry)
    monkeypatch.setattr(work_records_luna_intent, "get_registry", lambda *_args: registry)
    monkeypatch.setattr(server, "TURN_LOCK", asyncio.Lock())
    executions = []

    async def run_line(_repl, _bridge, line, *_args, **_kwargs):
        executions.append(line)

    monkeypatch.setattr(server, "_run_line", run_line)
    repl = SimpleNamespace(session=session, last_turn_finish_reason=None, _rebind_agent=lambda: None)
    handler = object.__new__(WsSessionHandler)
    handler.paths = paths
    handler.managed_readonly_context = None
    handler._file_stage = SimpleNamespace(take=lambda *_args: [])
    return SimpleNamespace(paths=paths, session=session, events=events, bridge=bridge,
                           client=client, executions=executions, repl=repl, handler=handler)


async def wait_confirm(events, index):
    deadline = asyncio.get_running_loop().time() + 2
    while asyncio.get_running_loop().time() < deadline:
        requests = [event for event in events if event.get("type") == "confirm.request"]
        if len(requests) > index:
            return requests[index]
        await asyncio.sleep(0.005)
    raise AssertionError("missing confirmation")


async def send(w, choice, index):
    task = asyncio.create_task(w.handler._handle_incoming(
        {"type": "user.message", "text": TEXT, "request_id": REQUEST_ID}, w.repl, w.bridge,
    ))
    request = await wait_confirm(w.events, index)
    assert TEXT in request["preview"]
    await w.handler._handle_incoming({"type": "confirm.response", "request_id": request["request_id"],
                                      "choice": choice}, w.repl, w.bridge)
    await asyncio.wait_for(task, timeout=3)
    return request


def test_refusal_and_same_id_redelivery_stop_before_model_or_agent(wiring):
    w = wiring

    async def scenario():
        first = await send(w, "n", 0)
        assert w.executions == []
        assert w.client.calls == []
        second_task = asyncio.create_task(w.handler._handle_incoming(
            {"type": "user.message", "text": TEXT, "request_id": REQUEST_ID}, w.repl, w.bridge,
        ))
        second = await wait_confirm(w.events, 1)
        assert second["request_id"] != first["request_id"]
        assert w.executions == [] and w.client.calls == []
        await w.handler._handle_incoming({"type": "confirm.response", "request_id": second["request_id"],
                                          "choice": "n"}, w.repl, w.bridge)
        await asyncio.wait_for(second_task, timeout=3)

    asyncio.run(scenario())
    assert w.executions == [] and w.client.calls == []
    assert WorkRecordStore(w.session).list_items() == []
    assert any(event.get("type") == "turn.notice" and "停止" in event.get("text", "")
               and "未执行" in event.get("text", "") for event in w.events)
    assert any(message.get("role") == "user" and message.get("content") == TEXT
               for message in w.session.messages)
    assert all(message.get("role") == "user" for message in w.session.messages if message.get("content") == TEXT)
    persisted = [json.loads(line) for line in w.session.messages_path.read_text(encoding="utf-8").splitlines()]
    assert any(message.get("role") == "user" and message.get("content") == TEXT for message in persisted)
    assert w.bridge.lifecycle.snapshot().finish_reason == "stopped"
    assert any(event.get("type") == "turn.end" and event.get("finish_reason") == "stopped" for event in w.events)


def test_model_nonrecord_decision_still_passes_original_message_to_agent(wiring):
    w = wiring
    asyncio.run(send(w, "y", 0))
    assert len(w.client.calls) == 1
    assert w.executions == [TEXT]
    assert WorkRecordStore(w.session).list_items() == []


def test_cancel_during_outbound_confirmation_preserves_cancel_reason(wiring):
    w = wiring

    async def scenario():
        task = asyncio.create_task(w.handler._handle_incoming(
            {"type": "user.message", "text": TEXT, "request_id": REQUEST_ID}, w.repl, w.bridge,
        ))
        await wait_confirm(w.events, 0)
        await w.handler._handle_incoming({"type": "turn.cancel"}, w.repl, w.bridge)
        await asyncio.wait_for(task, timeout=3)

    asyncio.run(scenario())
    assert w.executions == [] and w.client.calls == []
    assert w.bridge.lifecycle.snapshot().finish_reason == "cancelled"
    assert any(event.get("type") == "turn.end" and event.get("finish_reason") == "cancelled" for event in w.events)
    assert w.session.messages == []
    assert WorkRecordStore(w.session).list_items() == []


def test_session_switch_waits_and_rejected_text_stays_with_original_session(wiring, monkeypatch):
    w = wiring
    target_dir = w.paths.data / "sessions" / "target-session"
    target_dir.mkdir(parents=True)
    target = Session("target-session", target_dir, "", SessionMeta(), [], w.paths)
    monkeypatch.setattr(server, "load_session_for_harness", lambda *_args, **_kwargs: target)
    monkeypatch.setattr(w.bridge, "emit_session_state", lambda _session: None)
    monkeypatch.setattr(server, "emit_corruption_notices", lambda *_args: None)
    monkeypatch.setattr(server, "_emit_pending_work_card_references", lambda *_args: None)

    async def scenario():
        task = asyncio.create_task(w.handler._handle_incoming(
            {"type": "user.message", "text": TEXT, "request_id": REQUEST_ID}, w.repl, w.bridge,
        ))
        request = await wait_confirm(w.events, 0)
        switch = asyncio.create_task(w.handler._handle_incoming(
            {"type": "session.open", "session_id": "target-session"}, w.repl, w.bridge,
        ))
        await asyncio.sleep(0.02)
        assert w.repl.session is w.session and not switch.done()
        await w.handler._handle_incoming({"type": "confirm.response", "request_id": request["request_id"],
                                          "choice": "n"}, w.repl, w.bridge)
        await asyncio.wait_for(task, timeout=3)
        await asyncio.wait_for(switch, timeout=3)

    asyncio.run(scenario())
    assert w.repl.session is target
    assert any(row.get("role") == "user" and row.get("content") == TEXT for row in w.session.messages)
    assert target.messages == []
    assert w.executions == [] and w.client.calls == []


def test_history_write_failure_after_refusal_never_runs_agent(wiring, monkeypatch):
    w = wiring
    def fail_history(_message):
        raise OSError("synthetic history failure")
    monkeypatch.setattr(w.session, "append_message", fail_history)
    asyncio.run(send(w, "n", 0))
    assert w.executions == [] and w.client.calls == []
    assert w.bridge.lifecycle.snapshot().finish_reason == "stopped"
    assert any(event.get("type") == "turn.notice" and "保存未能确认" in event.get("text", "")
               for event in w.events)


def test_not_recorded_invalid_mode_still_closes_run_with_error(wiring):
    w = wiring
    w.session.meta.harness = "terminal"

    async def scenario():
        task = asyncio.create_task(w.handler._handle_incoming({
            "type": "user.message", "text": TEXT, "request_id": REQUEST_ID, "turn_mode": "ask",
        }, w.repl, w.bridge))
        request = await wait_confirm(w.events, 0)
        await w.handler._handle_incoming({"type": "confirm.response", "request_id": request["request_id"],
                                          "choice": "y"}, w.repl, w.bridge)
        await asyncio.wait_for(task, timeout=3)

    asyncio.run(scenario())
    assert len(w.client.calls) == 1 and w.executions == []
    assert any(event.get("type") == "error" for event in w.events)
    assert w.bridge.lifecycle.snapshot().finish_reason == "error"
    assert not w.bridge._turn_busy.is_set()


def test_not_recorded_context_exception_still_closes_run_with_error(wiring, monkeypatch):
    import companion_world_context
    w = wiring
    monkeypatch.setattr(companion_world_context, "is_companion_world_context_allowed", lambda **_kwargs: True)
    def fail_context(_value):
        raise RuntimeError("synthetic context failure")
    monkeypatch.setattr(companion_world_context, "format_companion_world_context", fail_context)
    asyncio.run(send(w, "y", 0))
    assert len(w.client.calls) == 1 and w.executions == []
    assert any(event.get("type") == "error" for event in w.events)
    assert w.bridge.lifecycle.snapshot().finish_reason == "error"
    assert not w.bridge._turn_busy.is_set()
