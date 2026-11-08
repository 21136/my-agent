
import asyncio
import json
import sys
import threading
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import llm_client
import loader
from llm_client import LLMCancelledError, LLMResponse
from server import WsBridge, WsSessionHandler
from session import create_new
from tests.isolation_helpers import temporary_agent_paths


def _active_fixture(paths):
    session = create_new(paths)
    events = []
    bridge = WsBridge(emit=events.append, paths=paths)
    assert bridge.begin_turn(mode="ordinary")
    bridge._turn_busy.set()
    bridge.lifecycle_conversation_id = session.conversation_id
    repl = SimpleNamespace(session=session, agent=SimpleNamespace(llm=object()))
    return session, events, bridge, repl, WsSessionHandler(paths)


class _ParallelClient:
    instances = []
    cancel_event = None
    started = None
    wait_for_cancel = False
    wait_for_release = False
    fail_provider = False
    last_messages = None

    def __init__(self, *, paths=None):
        self.paths = paths
        self.cancel_event = threading.Event()
        self.started = threading.Event()
        self.release = threading.Event()
        type(self).instances.append(self)

    def set_cancel_event(self, event):
        self.cancel_event = event

    def cancel_current_request(self):
        self.cancel_event.set()

    def chat(self, messages, *, tools=None, stream=None, model=None, **_kwargs):
        type(self).last_messages = messages
        assert tools == []
        assert model
        assert all(row.get("role") in {"system", "user", "assistant"} for row in messages)
        if messages and "ACTIVE_WORK_MESSAGE_ROUTER" in messages[0].get("content", ""):
            utterance = messages[-1].get("content", "")
            if "\u505c\u6b62" in utterance:
                decision = {"route": "clarify", "confidence": "high"}
            elif "\u8865\u5145" in utterance:
                decision = {"route": "work_instruction", "confidence": "high"}
            elif "?" in utterance or "？" in utterance:
                decision = {"route": "chat", "confidence": "high"}
            else:
                decision = {"route": "clarify", "confidence": "low"}
            return LLMResponse("fixture", json.dumps(decision), [], "stop", None, {})
        self.started.set()
        if self.fail_provider:
            raise RuntimeError("private provider detail")
        if self.wait_for_cancel:
            if not self.cancel_event.wait(5):
                raise AssertionError("parallel chat was not cancelled")
            raise LLMCancelledError("cancelled")
        if self.wait_for_release and not self.release.wait(5):
            raise AssertionError("parallel chat was not released")
        text = "A real fixture response"
        if stream and stream.on_content_delta:
            stream.on_content_delta(text)
        return LLMResponse("fixture", text, [], "stop", None, {})


def test_active_work_natural_question_gets_reply_in_shared_session(tmp_path, monkeypatch):
    with temporary_agent_paths() as paths:
        _ParallelClient.instances = []
        _ParallelClient.wait_for_cancel = False
        _ParallelClient.wait_for_release = False
        _ParallelClient.fail_provider = False
        _ParallelClient.wait_for_release = False
        _ParallelClient.fail_provider = False
        monkeypatch.setattr(llm_client, "LLMClient", _ParallelClient)
        monkeypatch.setattr(loader, "build_system_prompt", lambda *args, **kwargs: SimpleNamespace(prompt="PERSONA_MARKER"))
        session, events, bridge, repl, handler = _active_fixture(paths)
        request_id = str(uuid.uuid4())
        asyncio.run(handler._handle_incoming({
            "type": "user.message", "request_id": request_id,
            "session_id": session.conversation_id, "text": "parallel question?",
        }, repl, bridge))

        assert [event["type"] for event in events if event["type"].startswith("chat.")] == [
            "chat.start", "chat.delta", "chat.done",
        ]
        assert all(event.get("request_id") == request_id for event in events if event["type"].startswith("chat."))
        assert all(event.get("work_run_id") == bridge.lifecycle.snapshot().run_id
                   for event in events if event["type"].startswith("chat."))
        assert not any(event["type"] in {"assistant.delta", "assistant.done"} for event in events)
        assert _ParallelClient.instances[0] is not repl.agent.llm
        system_prompt = _ParallelClient.last_messages[0]["content"]
        assert "PERSONA_MARKER" in system_prompt
        assert "NOT sent to" not in system_prompt
        assert "cannot observe those facts" not in system_prompt
        assert [row["content"] for row in session.messages[-2:]] == ["parallel question?", "A real fixture response"]
        from context import build_llm_messages
        visible_history = repr(build_llm_messages(session))
        assert "parallel question?" in visible_history
        assert "A real fixture response" in visible_history
        current = bridge.lifecycle.snapshot()
        assert current is not None and current.state == "running"
        assert not bridge.cancel_event.is_set()



def test_clear_stop_phrase_stops_exact_active_run(tmp_path, monkeypatch):
    with temporary_agent_paths() as paths:
        session, events, bridge, repl, handler = _active_fixture(paths)
        active = bridge.lifecycle.snapshot()
        assert active is not None
        request_id = str(uuid.uuid4())
        asyncio.run(handler._handle_incoming({
            "type": "user.message", "request_id": request_id,
            "session_id": session.conversation_id, "text": "\u505c\u6b62\u5f53\u524d\u5de5\u4f5c",
        }, repl, bridge))

        current = bridge.lifecycle.snapshot()
        assert current is not None and current.run_id == active.run_id
        assert current.state == "stopping" and current.cancel_requested
        result = next(event for event in events if event.get("type") == "execution.stop.result")
        assert result["accepted"] is True and result["run_id"] == active.run_id
        assert result["request_id"] == request_id
        assert not any(event.get("type") == "work.instruction.state" for event in events)
        assert session.messages == []


def test_ambiguous_pause_phrase_only_asks_clarification(tmp_path, monkeypatch):
    with temporary_agent_paths() as paths:
        _ParallelClient.instances = []
        monkeypatch.setattr(llm_client, "LLMClient", _ParallelClient)
        monkeypatch.setattr(loader, "build_system_prompt", lambda *args, **kwargs: SimpleNamespace(prompt="PERSONA_MARKER"))
        session, events, bridge, repl, handler = _active_fixture(paths)
        active = bridge.lifecycle.snapshot()
        request_id = str(uuid.uuid4())
        asyncio.run(handler._handle_incoming({
            "type": "user.message", "request_id": request_id,
            "session_id": session.conversation_id, "text": "\u505c\u4e00\u4e0b",
        }, repl, bridge))

        current = bridge.lifecycle.snapshot()
        assert current is not None and current.run_id == active.run_id and current.state == "running"
        assert not any(event.get("type") == "execution.stop.result" for event in events)
        assert any(event.get("type") == "chat.done" for event in events)
        assert not bridge._work_instructions





def test_idle_legacy_user_message_still_uses_ordinary_dispatch():
    with temporary_agent_paths() as paths:
        session = create_new(paths)
        bridge = WsBridge(emit=lambda _event: None, paths=paths)
        repl = SimpleNamespace(session=session)
        handler = WsSessionHandler(paths)
        dispatched = []

        async def capture_dispatch(message, current_repl, current_bridge, **_kwargs):
            dispatched.append((message, current_repl, current_bridge))

        handler._dispatch = capture_dispatch
        legacy_message = {"type": "user.message", "text": "ordinary legacy chat"}
        asyncio.run(handler._handle_incoming(legacy_message, repl, bridge))

        assert dispatched == [(legacy_message, repl, bridge)]
        assert not bridge._active_message_seen_requests


def test_legacy_user_message_during_prompt_wait_is_not_mistaken_for_tool_input_or_work():
    with temporary_agent_paths() as paths:
        session, events, bridge, repl, handler = _active_fixture(paths)
        pending_request_id = str(uuid.uuid4())
        active = bridge.lifecycle.snapshot()
        bridge._pending_input_request = (pending_request_id, active.run_id, session.conversation_id)
        bridge._awaiting_input.set()

        async def classifier_would_treat_as_work(_text, _session):
            return "work_instruction", None

        handler._classify_active_work_message = classifier_would_treat_as_work
        asyncio.run(handler._handle_incoming({
            "type": "user.message", "request_id": str(uuid.uuid4()),
            "session_id": session.conversation_id, "text": "42",
        }, repl, bridge))

        assert bridge._input_queue.empty()
        assert not bridge._work_instructions
        assert session.messages == []
        error = next((event for event in events if event.get("code") == "prompt_response_required"), None)
        assert error is not None
        assert error["prompt_request_id"] == pending_request_id
        assert error["run_id"] == active.run_id


def test_router_unavailable_asks_clarification_without_delivering_or_fake_error(tmp_path, monkeypatch):
    class UnavailableClient:
        def __init__(self, **_kwargs):
            raise RuntimeError("provider unavailable")

    with temporary_agent_paths() as paths:
        monkeypatch.setattr(llm_client, "LLMClient", UnavailableClient)
        session, events, bridge, repl, handler = _active_fixture(paths)
        request_id = str(uuid.uuid4())
        asyncio.run(handler._handle_incoming({
            "type": "user.message", "request_id": request_id,
            "session_id": session.conversation_id, "text": "maybe change the thing",
        }, repl, bridge))

        assert not bridge._work_instructions
        terminal = [event for event in events if event.get("type") in {"chat.done", "chat.error"}]
        assert len(terminal) == 1 and terminal[0]["type"] == "chat.done"
        assert "\u672a\u6536\u5230\u65b0\u6307\u4ee4" in terminal[0]["text"]
        assert session.messages[-2]["content"] == "maybe change the thing"
        assert session.messages[-1]["content"] == terminal[0]["text"]


def test_active_work_explicit_supplement_queues_for_agent_checkpoint(tmp_path, monkeypatch):
    with temporary_agent_paths() as paths:
        _ParallelClient.instances = []
        monkeypatch.setattr(llm_client, "LLMClient", _ParallelClient)
        monkeypatch.setattr(loader, "build_system_prompt", lambda *args, **kwargs: SimpleNamespace(prompt="PERSONA_MARKER"))
        session, events, bridge, repl, handler = _active_fixture(paths)
        request_id = str(uuid.uuid4())
        asyncio.run(handler._handle_incoming({
            "type": "user.message", "request_id": request_id,
            "session_id": session.conversation_id,
            "text": "\u8865\u5145\u5f53\u524d\u5de5\u4f5c\uff1a\u8bf7\u518d\u6dfb\u52a0\u4e00\u4e2a\u8fb9\u754c\u6d4b\u8bd5",
        }, repl, bridge))

        assert any(event.get("type") == "work.instruction.state" and event.get("state") == "queued"
                   for event in events)
        assert not any(event.get("type") == "chat.done" for event in events)
        assert bridge._work_instructions[request_id].text.endswith("边界测试")
        assert session.messages == []



def test_route_lock_preserves_message_order_and_dedupes_same_request(tmp_path):
    with temporary_agent_paths() as paths:
        session, events, bridge, repl, handler = _active_fixture(paths)
        first_entered = asyncio.Event()
        release_first = asyncio.Event()
        classifier_calls = []
        dispatched = []

        async def classify(text, _session):
            classifier_calls.append(text)
            if len(classifier_calls) == 1:
                first_entered.set()
                await release_first.wait()
            return "work_instruction", None

        handler._classify_active_work_message = classify
        first_id = str(uuid.uuid4())
        second_id = str(uuid.uuid4())
        first = {"type": "user.message", "request_id": first_id,
                 "session_id": session.conversation_id, "text": "first instruction"}
        second = {"type": "user.message", "request_id": second_id,
                  "session_id": session.conversation_id, "text": "second instruction"}

        async def exercise():
            task1 = asyncio.create_task(handler._handle_incoming(first, repl, bridge))
            await first_entered.wait()
            task2 = asyncio.create_task(handler._handle_incoming(second, repl, bridge))
            await asyncio.sleep(0.05)
            assert classifier_calls == ["first instruction"]
            release_first.set()
            await asyncio.gather(task1, task2)

        asyncio.run(exercise())
        assert classifier_calls == ["first instruction", "second instruction"]
        queued = [event["request_id"] for event in events
                  if event.get("type") == "work.instruction.state" and event.get("state") == "queued"]
        assert queued == [first_id, second_id]


def test_stop_control_remains_immediate_while_route_classifier_waits(tmp_path):
    with temporary_agent_paths() as paths:
        session, events, bridge, repl, handler = _active_fixture(paths)
        entered = asyncio.Event()
        release = asyncio.Event()
        active = bridge.lifecycle.snapshot()
        request_id = str(uuid.uuid4())

        async def classify(_text, _session):
            entered.set()
            await release.wait()
            return "work_instruction", None

        handler._classify_active_work_message = classify
        message = {"type": "user.message", "request_id": request_id,
                   "session_id": session.conversation_id, "text": "add a check"}

        async def exercise():
            route_task = asyncio.create_task(handler._handle_incoming(message, repl, bridge))
            await entered.wait()
            await asyncio.wait_for(handler._handle_incoming({
                "type": "confirm.response", "request_id": "confirm-request", "choice": "y",
            }, repl, bridge), timeout=0.2)
            await asyncio.wait_for(handler._handle_incoming({
                "type": "user.message", "request_id": str(uuid.uuid4()),
                "session_id": session.conversation_id, "text": "\u505c\u6b62\u5f53\u524d\u5de5\u4f5c",
            }, repl, bridge), timeout=0.2)
            assert bridge.lifecycle.snapshot().state == "stopping"
            release.set()
            await route_task

        asyncio.run(exercise())
        assert not any(event.get("state") == "queued" for event in events
                       if event.get("type") == "work.instruction.state")
        assert any(event.get("type") == "execution.stop.result" and event.get("accepted") for event in events)
        stale = [event for event in events if event.get("type") == "chat.error"]
        assert len(stale) == 1 and stale[0].get("code") == "run_ended_during_routing"


def test_natural_message_is_not_stolen_by_pending_interactive_input(tmp_path, monkeypatch):
    with temporary_agent_paths() as paths:
        _ParallelClient.instances = []
        monkeypatch.setattr(llm_client, "LLMClient", _ParallelClient)
        monkeypatch.setattr(loader, "build_system_prompt", lambda *args, **kwargs: SimpleNamespace(prompt="PERSONA_MARKER"))
        session, events, bridge, repl, handler = _active_fixture(paths)
        bridge._awaiting_input.set()
        consumed = []
        bridge.try_route_input = lambda text: consumed.append(text) or True
        request_id = str(uuid.uuid4())
        asyncio.run(handler._handle_incoming({
            "type": "user.message", "request_id": request_id,
            "session_id": session.conversation_id, "text": "what is the current approach?",
        }, repl, bridge))

        assert consumed == []
        assert any(event.get("type") == "chat.done" for event in events)


def test_chat_cancel_only_cancels_its_request_not_work_run(tmp_path, monkeypatch):
    with temporary_agent_paths() as paths:
        _ParallelClient.instances = []
        _ParallelClient.wait_for_cancel = True
        _ParallelClient.wait_for_release = False
        _ParallelClient.fail_provider = False
        monkeypatch.setattr(llm_client, "LLMClient", _ParallelClient)
        session, events, bridge, repl, handler = _active_fixture(paths)
        request_id = str(uuid.uuid4())

        async def exercise():
            task = asyncio.create_task(handler._handle_incoming({
                "type": "user.message", "request_id": request_id,
                "session_id": session.conversation_id, "text": "cancel only this chat?",
            }, repl, bridge))
            for _ in range(300):
                client = next((item for item in _ParallelClient.instances if item.started.is_set()), None)
                if client is not None:
                    break
                await asyncio.sleep(0.01)
            assert client is not None
            assert handler._dispatch_inline({"type": "chat.cancel", "request_id": request_id}, bridge)
            await asyncio.wait_for(task, timeout=3)

        asyncio.run(exercise())
        assert any(event["type"] == "chat.error" and event.get("code") == "cancelled" for event in events)
        current = bridge.lifecycle.snapshot()
        assert current is not None and current.state == "running"
        assert not bridge.cancel_event.is_set()
        _ParallelClient.wait_for_cancel = False
        assert session.messages[-1]["role"] == "user"
        assert session.messages[-1]["content"] == "cancel only this chat?"
        assert session.messages[-1]["_host_internal"]["parallel_chat"] is True


def test_execution_stop_wire_rejects_missing_or_stale_id():
    with temporary_agent_paths() as paths:
        events = []
        bridge = WsBridge(emit=events.append, paths=paths)
        handler = WsSessionHandler(paths)
        assert bridge.begin_turn(mode="ordinary")
        active = bridge.lifecycle.snapshot()
        assert active is not None

        assert handler._dispatch_inline({"type": "execution.stop", "run_id": "stale"}, bridge)
        assert bridge.lifecycle.snapshot().state == "running"
        assert not bridge.cancel_event.is_set()
        assert handler._dispatch_inline({"type": "execution.stop"}, bridge)
        assert bridge.lifecycle.snapshot().state == "running"
        assert not bridge.cancel_event.is_set()
        assert bridge._accepted_user_stop is None
        results = [event for event in events if event.get("type") == "execution.stop.result"]
        assert [row["accepted"] for row in results] == [False, False]
        assert results[0]["run_id"] == "stale"
        assert "run_id" not in results[1]



def test_parallel_chat_provider_failure_never_emits_done_or_persists_fake_reply(monkeypatch):
    with temporary_agent_paths() as paths:
        _ParallelClient.instances = []
        _ParallelClient.wait_for_cancel = False
        _ParallelClient.wait_for_release = False
        _ParallelClient.fail_provider = False
        _ParallelClient.wait_for_release = False
        _ParallelClient.fail_provider = True
        monkeypatch.setattr(llm_client, "LLMClient", _ParallelClient)
        session, events, bridge, repl, handler = _active_fixture(paths)
        request_id = str(uuid.uuid4())
        asyncio.run(handler._handle_incoming({
            "type": "user.message", "request_id": request_id,
            "session_id": session.conversation_id, "text": "question that fails?",
        }, repl, bridge))

        terminal = [event for event in events if event.get("type") in {"chat.done", "chat.error"}]
        assert len(terminal) == 1 and terminal[0]["type"] == "chat.error"
        assert terminal[0]["code"] == "chat_failed"
        assert "private provider detail" not in terminal[0]["message"]
        assert session.messages[-1]["role"] == "user"
        assert session.messages[-1]["content"] == "question that fails?"
        assert session.messages[-1]["_host_internal"]["parallel_chat"] is True
        assert not any(row.get("role") == "assistant" and row.get("content") == "A real fixture response"
                       for row in session.messages)
        _ParallelClient.fail_provider = False


def test_execution_stop_wire_accepts_only_the_current_run():
    with temporary_agent_paths() as paths:
        events = []
        bridge = WsBridge(emit=events.append, paths=paths)
        handler = WsSessionHandler(paths)
        assert bridge.begin_turn(mode="ordinary")
        active = bridge.lifecycle.snapshot()
        assert active is not None

        assert handler._dispatch_inline({"type": "execution.stop", "run_id": active.run_id}, bridge)
        current = bridge.lifecycle.snapshot()
        assert current is not None and current.run_id == active.run_id
        assert current.state == "stopping" and current.cancel_requested
        assert bridge.cancel_event.is_set()
        assert bridge._accepted_user_stop is not None
        assert bridge._accepted_user_stop[0] == active.run_id
        assert bridge._accepted_user_stop[1]
        result = [event for event in events if event.get("type") == "execution.stop.result"][-1]
        assert result == {"type": "execution.stop.result", "accepted": True, "run_id": active.run_id}


def test_parallel_chat_append_does_not_overwrite_work_messages(tmp_path, monkeypatch):
    with temporary_agent_paths() as paths:
        _ParallelClient.instances = []
        _ParallelClient.wait_for_cancel = False
        _ParallelClient.wait_for_release = False
        _ParallelClient.fail_provider = False
        _ParallelClient.wait_for_release = True
        _ParallelClient.fail_provider = False
        monkeypatch.setattr(llm_client, "LLMClient", _ParallelClient)
        session, events, bridge, repl, handler = _active_fixture(paths)
        request_id = str(uuid.uuid4())

        async def exercise():
            task = asyncio.create_task(handler._handle_incoming({
                "type": "user.message", "request_id": request_id,
                "session_id": session.conversation_id, "text": "chat while work writes?",
            }, repl, bridge))
            for _ in range(300):
                client = next((item for item in _ParallelClient.instances if item.started.is_set()), None)
                if client is not None:
                    break
                await asyncio.sleep(0.01)
            assert client is not None
            await asyncio.to_thread(session.append_message, {"role": "assistant", "content": "work progress"})
            client.release.set()
            await asyncio.wait_for(task, timeout=3)

        asyncio.run(exercise())
        reloaded = type(session).load(paths, session.conversation_id)
        tail = reloaded.messages[-3:]
        assert tail[0]["role"] == "user" and tail[0]["content"] == "chat while work writes?"
        assert tail[0]["_host_internal"]["parallel_chat"] is True
        assert tail[1:] == [
            {"role": "assistant", "content": "work progress"},
            {"role": "assistant", "content": "A real fixture response",
             "_host_internal": tail[0]["_host_internal"]},
        ]
        assert not any(event.get("type") == "error" for event in events)
        _ParallelClient.wait_for_release = False


def test_active_chat_messages_are_serialized_and_cancel_isolated(tmp_path, monkeypatch):
    with temporary_agent_paths() as paths:
        _ParallelClient.instances = []
        _ParallelClient.wait_for_cancel = False
        _ParallelClient.wait_for_release = False
        _ParallelClient.fail_provider = False
        _ParallelClient.wait_for_release = True
        _ParallelClient.fail_provider = False
        monkeypatch.setattr(llm_client, "LLMClient", _ParallelClient)
        session, events, bridge, repl, handler = _active_fixture(paths)
        first_id, second_id = str(uuid.uuid4()), str(uuid.uuid4())

        async def exercise():
            tasks = [
                asyncio.create_task(handler._handle_incoming({
                    "type": "user.message", "request_id": request_id,
                    "session_id": session.conversation_id, "text": f"question {index}?",
                }, repl, bridge))
                for index, request_id in enumerate((first_id, second_id), start=1)
            ]
            for _ in range(300):
                response_clients = [client for client in _ParallelClient.instances if client.started.is_set()]
                if response_clients:
                    break
                await asyncio.sleep(0.01)
            assert len(response_clients) == 1
            assert handler._dispatch_inline({"type": "chat.cancel", "request_id": first_id}, bridge)
            response_clients[0].release.set()
            for _ in range(300):
                response_clients = [client for client in _ParallelClient.instances if client.started.is_set()]
                if len(response_clients) == 2:
                    break
                await asyncio.sleep(0.01)
            assert len(response_clients) == 2
            response_clients[1].release.set()
            await asyncio.gather(*tasks)

        asyncio.run(exercise())
        first_terminal = [event["type"] for event in events if event.get("request_id") == first_id
                          and event["type"] in {"chat.done", "chat.error"}]
        second_terminal = [event["type"] for event in events if event.get("request_id") == second_id
                           and event["type"] in {"chat.done", "chat.error"}]
        assert first_terminal == ["chat.error"]
        assert second_terminal == ["chat.done"]
        current = bridge.lifecycle.snapshot()
        assert current is not None and current.state == "running"
        assert not bridge.cancel_event.is_set()
        _ParallelClient.wait_for_release = False


def test_legacy_turn_cancel_without_run_id_remains_compatible():
    with temporary_agent_paths() as paths:
        events = []
        bridge = WsBridge(emit=events.append, paths=paths)
        handler = WsSessionHandler(paths)
        assert bridge.begin_turn(mode="ordinary")
        active = bridge.lifecycle.snapshot()
        assert active is not None

        assert handler._dispatch_inline({"type": "turn.cancel"}, bridge)
        current = bridge.lifecycle.snapshot()
        assert current is not None and current.run_id == active.run_id
        assert current.state == "stopping"
        assert bridge.cancel_event.is_set()


def test_shared_chat_messages_enter_following_work_model_context():
    with temporary_agent_paths() as paths:
        session = create_new(paths)
        session.append_message({"role": "user", "content": "work task"})
        session.append_message({"role": "user", "content": "private chat instruction",
                                "_host_internal": {"parallel_chat": True, "request_id": str(uuid.uuid4())}})
        session.append_message({"role": "assistant", "content": "private chat answer",
                                "_host_internal": {"parallel_chat": True, "request_id": str(uuid.uuid4())}})
        session.append_message({"role": "user", "content": "instruction from an ended run",
                                "_host_internal": {"work_instruction": True, "work_instruction_run_id": "old-run"}})

        from context import build_llm_messages
        work_context = build_llm_messages(session)
        serialized = repr(work_context)
        assert "work task" in serialized
        assert "private chat instruction" in serialized
        assert "private chat answer" in serialized
        assert "instruction from an ended run" not in serialized


def test_prompt_response_is_run_bound_and_inline():
    with temporary_agent_paths() as paths:
        _session, events, bridge, _repl, handler = _active_fixture(paths)
        snapshot = bridge.lifecycle.snapshot()
        request_id = str(uuid.uuid4())
        bridge._pending_input_request = (request_id, snapshot.run_id, bridge.lifecycle_conversation_id)
        bridge._awaiting_input.set()

        assert handler._dispatch_inline({
            "type": "prompt.response", "request_id": request_id,
            "run_id": snapshot.run_id, "session_id": bridge.lifecycle_conversation_id,
            "text": "explicit tool input",
        }, bridge)
        assert bridge._input_queue.get_nowait() == "explicit tool input"
        accepted = [event for event in events if event.get("type") == "prompt.response.result"][-1]
        assert accepted["accepted"] is True and accepted["request_id"] == request_id

        assert handler._dispatch_inline({
            "type": "prompt.response", "request_id": request_id,
            "run_id": "stale-run", "session_id": bridge.lifecycle_conversation_id,
            "text": "stale input",
        }, bridge)
        rejected = [event for event in events if event.get("type") == "prompt.response.result"][-1]
        assert rejected["accepted"] is False
        assert bridge._input_queue.empty()



def test_input_fn_emits_run_bound_prompt_fields_and_accepts_response():
    with temporary_agent_paths() as paths:
        session, events, bridge, _repl, handler = _active_fixture(paths)
        result = []

        def wait_for_input():
            result.append(bridge.input_fn("value?"))

        worker = threading.Thread(target=wait_for_input)
        worker.start()
        for _ in range(200):
            prompt = next((event for event in events if event.get("type") == "prompt.request"), None)
            if prompt:
                break
            time.sleep(0.005)
        assert prompt is not None
        assert prompt["run_id"] == bridge.lifecycle.snapshot().run_id
        assert prompt["session_id"] == session.conversation_id
        assert handler._dispatch_inline({
            "type": "prompt.response", "request_id": prompt["request_id"],
            "run_id": prompt["run_id"], "session_id": prompt["session_id"], "text": "answer",
        }, bridge)
        worker.join(timeout=2)
        assert not worker.is_alive() and result == ["answer"]


def test_duplicate_active_natural_request_is_routed_once():
    with temporary_agent_paths() as paths:
        session, events, bridge, repl, handler = _active_fixture(paths)
        entered = asyncio.Event()
        release = asyncio.Event()
        calls = []

        async def classify(text, _session):
            calls.append(text)
            entered.set()
            await release.wait()
            return "work_instruction", None

        handler._classify_active_work_message = classify
        request_id = str(uuid.uuid4())
        message = {"type": "user.message", "request_id": request_id,
                   "session_id": session.conversation_id, "text": "add a test"}

        async def exercise():
            first = asyncio.create_task(handler._handle_incoming(message, repl, bridge))
            await entered.wait()
            second = asyncio.create_task(handler._handle_incoming(message, repl, bridge))
            await asyncio.sleep(0.02)
            release.set()
            await asyncio.gather(first, second)

        asyncio.run(exercise())
        states = [event for event in events if event.get("type") == "work.instruction.state"]
        assert [event["state"] for event in states] == ["queued"]
        assert len(calls) == 1
        assert any(event.get("code") == "duplicate_request" for event in events)


def test_run_instruction_is_queued_then_included_at_agent_model_checkpoint(tmp_path, monkeypatch):
    from agent import Agent
    from llm_client import LLMResponse
    from tools.executor import ExecutorSession, ToolExecutor
    from tools.registry import ToolRegistry

    class CaptureLLM:
        def __init__(self):
            self.payloads = []

        def set_cancel_event(self, _event):
            pass

        def chat(self, messages, **_kwargs):
            self.payloads.append(messages)
            return LLMResponse("fixture", "ack", [], "stop", None, {})

    with temporary_agent_paths() as paths:
        _ParallelClient.instances = []
        monkeypatch.setattr(llm_client, "LLMClient", _ParallelClient)
        session, events, bridge, repl, handler = _active_fixture(paths)
        session.meta.turn_mode = "agent"
        snapshot = bridge.lifecycle.snapshot()
        assert snapshot is not None
        request_id = str(uuid.uuid4())
        llm = CaptureLLM()
        executor = ToolExecutor(
            registry=ToolRegistry.load(paths),
            session=ExecutorSession(session_dir=session.session_dir),
            confirm_fn=lambda _request: True,
        )
        agent = Agent(session=session, executor=executor, llm=llm)
        repl.agent = agent
        asyncio.run(handler._handle_incoming({
            "type": "user.message", "request_id": request_id,
            "session_id": session.conversation_id,
            "text": "\u8865\u5145\u5f53\u524d\u5de5\u4f5c\uff1a\u8bf7\u518d\u6dfb\u52a0\u4e00\u4e2a\u8fb9\u754c\u6d4b\u8bd5",
        }, repl, bridge))
        def instruction_checkpoint(phase, current, model_context):
            if phase == "prepare":
                bridge.prepare_work_instructions_at_checkpoint(current, snapshot.run_id)
            elif phase == "deliver":
                return bridge.commit_work_instruction_delivery(current, snapshot.run_id, model_context)

        agent._work_instruction_checkpoint = instruction_checkpoint
        agent.run_turn("implement feature")

        assert "\u8bf7\u518d\u6dfb\u52a0\u4e00\u4e2a\u8fb9\u754c\u6d4b\u8bd5" in repr(llm.payloads[0])
        persisted_rows = [row for row in session.messages if row.get("_host_internal", {}).get("work_instruction_request_id") == request_id]
        assert persisted_rows, repr(session.messages)
        persisted = persisted_rows[0]
        assert persisted["content"] == "\u8865\u5145\u5f53\u524d\u5de5\u4f5c\uff1a\u8bf7\u518d\u6dfb\u52a0\u4e00\u4e2a\u8fb9\u754c\u6d4b\u8bd5"
        states = [event["state"] for event in events if event.get("request_id") == request_id
                  and event.get("type") == "work.instruction.state"]
        assert states == ["queued", "delivered"]


def test_work_instruction_queue_is_bound_and_reports_undelivered_on_run_finish(tmp_path):
    with temporary_agent_paths() as paths:
        session = create_new(paths)
        events = []
        bridge = WsBridge(emit=events.append, paths=paths)
        assert bridge.begin_turn(mode="ordinary")
        bridge._turn_busy.set()
        bridge.lifecycle_conversation_id = session.conversation_id
        snapshot = bridge.lifecycle.snapshot()
        assert snapshot is not None
        request_id = str(uuid.uuid4())

        assert bridge.enqueue_work_instruction(
            request_id=request_id, run_id=snapshot.run_id, session=session, text="follow up instruction",
        ) == "queued"
        assert bridge.finish_work_instructions(snapshot.run_id, session.conversation_id, "completed") == 1
        state = [event for event in events if event.get("type") == "work.instruction.state"]
        assert [event["state"] for event in state] == ["queued", "undelivered"]
        assert state[-1]["reason"] == "run_ended_before_checkpoint"
        assert session.messages == []


def test_client_selected_work_instruction_route_is_rejected_fail_closed():
    with temporary_agent_paths() as paths:
        session, events, bridge, repl, handler = _active_fixture(paths)
        active = bridge.lifecycle.snapshot()
        assert active is not None
        asyncio.run(handler._handle_incoming({
            "type": "work.instruction", "request_id": str(uuid.uuid4()),
            "run_id": active.run_id, "session_id": session.conversation_id,
            "text": "explicit work instruction",
        }, repl, bridge))
        state = [event for event in events if event.get("type") == "work.instruction.state"]
        assert len(state) == 1 and state[0]["state"] == "undelivered"
        assert state[0]["reason"] == "client_selected_route_disabled_use_user_message"
        assert not bridge._work_instructions and session.messages == []


def test_work_instruction_queue_rejects_over_capacity_explicitly(tmp_path):
    with temporary_agent_paths() as paths:
        session = create_new(paths)
        events = []
        bridge = WsBridge(emit=events.append, paths=paths)
        assert bridge.begin_turn(mode="ordinary")
        bridge._turn_busy.set()
        bridge.lifecycle_conversation_id = session.conversation_id
        snapshot = bridge.lifecycle.snapshot()
        assert snapshot is not None
        for index in range(9):
            bridge.enqueue_work_instruction(
                request_id=str(uuid.uuid4()), run_id=snapshot.run_id, session=session, text=f"instruction {index}",
            )
        states = [event["state"] for event in events if event.get("type") == "work.instruction.state"]
        assert states == ["queued"] * 8 + ["undelivered"]
        assert [event["reason"] for event in events if event.get("state") == "undelivered"] == ["queue_full"]


def test_run_instruction_arriving_during_tool_batch_is_delivered_at_next_boundary(tmp_path):
    import json
    import server
    from llm_client import LLMResponse
    from session import create_new
    from tools.executor import ExecutorSession, ToolExecutor
    from tools.registry import ToolRegistry
    from main import ConversationRepl, ReplConfig

    with temporary_agent_paths() as paths:
        session = create_new(paths)
        session.meta.turn_mode = "agent"
        events = []
        bridge = WsBridge(emit=events.append, paths=paths)
        from server import _build_repl
        repl = _build_repl(session, paths, bridge)
        assert bridge.begin_turn(mode="ordinary")
        snapshot = bridge.lifecycle.snapshot()
        assert snapshot is not None
        bridge.lifecycle_conversation_id = session.conversation_id
        bridge._turn_busy.set()
        request_id = str(uuid.uuid4())
        calls = []

        class QueueingLLM:
            def set_cancel_event(self, _event):
                pass

            def chat(self, messages, **_kwargs):
                calls.append(messages)
                if len(calls) == 1:
                    assert bridge.enqueue_work_instruction(
                        request_id=request_id, run_id=snapshot.run_id, session=session,
                        text="verify the generated test before finishing",
                    ) == "queued"
                    call = {"id": "call_1", "type": "function", "function": {
                        "name": "list_dir", "arguments": json.dumps({"path": "."}),
                    }}
                    return LLMResponse("fixture", None, [call], "tool_calls", None, {})
                if len(calls) == 2:
                    call = {"id": "call_2", "type": "function", "function": {
                        "name": "list_dir", "arguments": json.dumps({"path": "workspace"}),
                    }}
                    return LLMResponse("fixture", None, [call], "tool_calls", None, {})
                return LLMResponse("fixture", "I will verify it now.", [], "stop", None, {})

        repl.agent.llm = QueueingLLM()
        repl.agent.llm.set_cancel_event(repl.agent.cancel_event)
        asyncio.run(server._run_line(
            repl, bridge, "implement a test", paths, lock_held=True,
            prestarted_run_id=snapshot.run_id,
        ))

        assert len(calls) >= 3
        assert "verify the generated test before finishing" in repr(calls[1])
        assert "verify the generated test before finishing" in repr(calls[2])
        states = [event["state"] for event in events if event.get("type") == "work.instruction.state"
                  and event.get("request_id") == request_id]
        assert states == ["queued", "delivered"]
        assert any(row.get("_host_internal", {}).get("work_instruction_request_id") == request_id
                   for row in session.messages)
        final = bridge.lifecycle.snapshot()
        assert final is not None and final.state == "settled"


def test_run_instruction_finishing_turn_without_checkpoint_is_undelivered(tmp_path):
    import server
    from llm_client import LLMResponse
    from session import create_new

    with temporary_agent_paths() as paths:
        session = create_new(paths)
        events = []
        bridge = WsBridge(emit=events.append, paths=paths)
        from server import _build_repl
        repl = _build_repl(session, paths, bridge)
        assert bridge.begin_turn(mode="ordinary")
        snapshot = bridge.lifecycle.snapshot()
        assert snapshot is not None
        bridge.lifecycle_conversation_id = session.conversation_id
        bridge._turn_busy.set()
        request_id = str(uuid.uuid4())

        class FinalLLM:
            def set_cancel_event(self, _event):
                pass

            def chat(self, _messages, **_kwargs):
                assert bridge.enqueue_work_instruction(
                    request_id=request_id, run_id=snapshot.run_id, session=session,
                    text="finish one more verification",
                ) == "queued"
                return LLMResponse("fixture", "finished", [], "stop", None, {})

        repl.agent.llm = FinalLLM()
        repl.agent.llm.set_cancel_event(repl.agent.cancel_event)
        asyncio.run(server._run_line(
            repl, bridge, "complete this work", paths, lock_held=True,
            prestarted_run_id=snapshot.run_id,
        ))

        states = [event["state"] for event in events if event.get("type") == "work.instruction.state"
                  and event.get("request_id") == request_id]
        assert states == ["queued", "undelivered"]
        terminal = [event for event in events if event.get("type") == "work.instruction.state"
                    and event.get("request_id") == request_id][-1]
        assert terminal["reason"] == "run_ended_before_checkpoint"
        assert not any(row.get("_host_internal", {}).get("work_instruction_request_id") == request_id
                       for row in session.messages)


def test_stale_execution_stop_does_not_invalidate_other_pending_work():
    with temporary_agent_paths() as paths:
        events = []
        bridge = WsBridge(emit=events.append, paths=paths)
        handler = WsSessionHandler(paths)
        assert bridge.begin_turn(mode="ordinary")
        invalidations = []
        bridge.invalidate_post_turn = lambda: invalidations.append(True)

        assert handler._dispatch_inline({"type": "execution.stop", "run_id": "stale-run"}, bridge)
        assert invalidations == []
        assert bridge.lifecycle.snapshot().state == "running"


def test_user_message_context_router_admits_clear_supplement(tmp_path):
    with temporary_agent_paths() as paths:
        session, events, bridge, repl, handler = _active_fixture(paths)
        snapshot = bridge.lifecycle.snapshot()
        assert snapshot is not None
        request_id = str(uuid.uuid4())

        async def clear_work_route(_text, _session):
            return "work_instruction", None

        handler._classify_active_work_message = clear_work_route
        asyncio.run(handler._handle_incoming({
            "type": "user.message", "request_id": request_id,
            "session_id": session.conversation_id, "text": "add one focused regression",
        }, repl, bridge))

        state = [event for event in events if event.get("type") == "work.instruction.state"]
        assert state == [{
            "type": "work.instruction.state", "request_id": request_id, "run_id": snapshot.run_id,
            "session_id": session.conversation_id, "state": "queued",
        }]
        assert session.messages == []


def test_staged_instruction_stopped_before_model_handoff_is_not_reported_delivered():
    from context import build_llm_messages

    with temporary_agent_paths() as paths:
        session = create_new(paths)
        events = []
        bridge = WsBridge(emit=events.append, paths=paths)
        assert bridge.begin_turn(mode="ordinary")
        bridge._turn_busy.set()
        bridge.lifecycle_conversation_id = session.conversation_id
        snapshot = bridge.lifecycle.snapshot()
        assert snapshot is not None
        request_id = str(uuid.uuid4())
        assert bridge.enqueue_work_instruction(
            request_id=request_id, run_id=snapshot.run_id, session=session, text="do one check",
        ) == "queued"
        assert bridge.prepare_work_instructions_at_checkpoint(session, snapshot.run_id) == 1
        assert "do one check" not in repr(build_llm_messages(session))

        assert bridge.request_execution_stop(snapshot.run_id)
        assert bridge.commit_work_instruction_delivery(session, snapshot.run_id, []) == []
        assert bridge.finish_work_instructions(snapshot.run_id, session.conversation_id, "cancelled") == 1

        terminal = [event for event in events if event.get("type") == "work.instruction.state"
                    and event.get("request_id") == request_id][-1]
        assert terminal["state"] == "undelivered"
        assert terminal["reason"] == "run_stopped_before_model_request"
