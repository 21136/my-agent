from __future__ import annotations

import asyncio
import json
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

CORE = Path(__file__).resolve().parents[1]
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))

import llm_role_model_preferences
import llm_client
from llm_client import LLMResponse
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


class _SemanticRouterFixture:
    decision_by_text = {}
    last_messages = None

    def __init__(self, *, paths=None):
        self.paths = paths

    def chat(self, messages, **kwargs):
        type(self).last_messages = messages
        assert kwargs["tools"] == []
        text = messages[-1]["content"]
        decision = self.decision_by_text[text]
        return LLMResponse("fixture", json.dumps(decision), [], "stop", None, {})


def test_active_router_uses_semantic_multi_project_classification_not_keywords(monkeypatch):
    with temporary_agent_paths() as paths:
        session = create_new(paths)
        monkeypatch.setattr(llm_client, "LLMClient", _SemanticRouterFixture)
        monkeypatch.setattr(
            llm_role_model_preferences,
            "load_role_model_preferences",
            lambda _paths: {"main": "fixture-model"},
        )
        texts = {
            "Do not update two projects": {"route": "clarify", "confidence": "high"},
            'Explain the phrase "update two projects" only': {"route": "chat", "confidence": "high"},
            "Update project A and project B": {"route": "multi_project_work_instruction", "confidence": "high"},
            "Stop this active run": {"route": "stop", "confidence": "high"},
            "Maybe stop it?": {"route": "clarify", "confidence": "high"},
        }
        _SemanticRouterFixture.decision_by_text = texts
        handler = WsSessionHandler(paths)

        results = [asyncio.run(handler._classify_active_work_message(text, session)) for text in texts]

        assert results == [
            ("clarify", None),
            ("chat", None),
            ("multi_project_work_instruction", None),
            ("stop", None),
            ("clarify", None),
        ]
        assert "never infer it from negation, quotation" in _SemanticRouterFixture.last_messages[0]["content"]
        assert _SemanticRouterFixture.last_messages[-1] == {
            "role": "user", "content": "Maybe stop it?"
        }


def test_active_multi_project_route_enqueues_original_user_text():
    with temporary_agent_paths() as paths:
        session, events, bridge, repl, handler = _active_fixture(paths)
        text = "同时推进项目 A 和项目 B；保持原话和各自作用域"

        async def semantic_route(_text, _session):
            return "multi_project_work_instruction", None

        handler._classify_active_work_message = semantic_route
        request_id = str(uuid.uuid4())
        asyncio.run(handler._handle_incoming({
            "type": "user.message", "request_id": request_id,
            "session_id": session.conversation_id, "text": text,
        }, repl, bridge))

        instruction = bridge._work_instructions[request_id]
        assert instruction.companion_orchestration is True
        assert instruction.text.endswith(text)
        assert instruction.surface_text == text
        assert session.messages == []
        assert not any(event.get("type") == "chat.done" for event in events)


def test_multi_project_route_does_not_enqueue_after_run_ends_during_classification():
    with temporary_agent_paths() as paths:
        session, events, bridge, repl, handler = _active_fixture(paths)
        request_id = str(uuid.uuid4())
        text = "同时推进项目 A 和项目 B"

        async def semantic_route(_text, _session):
            assert bridge.request_execution_stop(bridge.lifecycle.snapshot().run_id)
            return "multi_project_work_instruction", None

        handler._classify_active_work_message = semantic_route
        asyncio.run(handler._handle_incoming({
            "type": "user.message", "request_id": request_id,
            "session_id": session.conversation_id, "text": text,
        }, repl, bridge))

        assert request_id not in bridge._work_instructions
        assert any(event.get("code") == "run_ended_during_routing" for event in events)


def test_natural_stop_uses_semantic_route_and_current_run_binding():
    with temporary_agent_paths() as paths:
        session, events, bridge, repl, handler = _active_fixture(paths)
        snapshot = bridge.lifecycle.snapshot()
        assert snapshot is not None
        calls = []

        async def semantic_route(text, routed_session):
            calls.append((text, routed_session))
            return "stop", None

        handler._classify_active_work_message = semantic_route
        request_id = str(uuid.uuid4())
        asyncio.run(handler._handle_incoming({
            "type": "user.message", "request_id": request_id,
            "session_id": session.conversation_id, "text": "stop current work",
        }, repl, bridge))

        result = next(event for event in events if event.get("type") == "execution.stop.result")
        assert calls == [("stop current work", session)]
        assert result["accepted"] is True
        assert result["run_id"] == snapshot.run_id
        assert result["request_id"] == request_id


def test_unavailable_semantic_router_fails_closed_for_natural_stop(monkeypatch):
    with temporary_agent_paths() as paths:
        session = create_new(paths)

        class UnavailableRouter:
            def __init__(self, *, paths=None):
                self.paths = paths

            def chat(self, *_args, **_kwargs):
                raise RuntimeError("synthetic offline model")

        monkeypatch.setattr(llm_client, "LLMClient", UnavailableRouter)
        monkeypatch.setattr(
            llm_role_model_preferences, "load_role_model_preferences",
            lambda _paths: {"main": "offline-fixture"},
        )
        route, reason = asyncio.run(
            WsSessionHandler(paths)._classify_active_work_message("stop current work", session)
        )

        assert route == "clarify"
        assert reason == "router_unavailable"


def test_stop_route_is_discarded_if_its_run_ends_during_classification():
    with temporary_agent_paths() as paths:
        session, events, bridge, repl, handler = _active_fixture(paths)
        snapshot = bridge.lifecycle.snapshot()
        assert snapshot is not None
        request_id = str(uuid.uuid4())

        async def semantic_route(_text, _session):
            assert bridge.request_execution_stop(snapshot.run_id)
            return "stop", None

        handler._classify_active_work_message = semantic_route
        asyncio.run(handler._handle_incoming({
            "type": "user.message", "request_id": request_id,
            "session_id": session.conversation_id, "text": "Stop this active run",
        }, repl, bridge))

        assert not any(event.get("type") == "execution.stop.result" for event in events)
        assert any(event.get("code") == "run_ended_during_routing" for event in events)
