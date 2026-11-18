from __future__ import annotations

import json
import sys
import threading
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

from agent import Agent, build_builtin_tool_definition
from llm_client import LLMResponse
from session import SessionMeta, create_new
from tests.isolation_helpers import temporary_agent_paths
from tools.executor import ExecutorSession, ToolExecutor
from tools.registry import ToolRegistry


def _tool_call(arguments, call_id="self-evolution-call"):
    return {"id": call_id, "type": "function", "function": {
        "name": "self_evolution_start", "arguments": json.dumps(arguments),
    }}


def test_builtin_schema_limits_model_input_to_goal_and_acceptance_points():
    definition = build_builtin_tool_definition("self_evolution_start")
    function = definition["function"]
    schema = function["parameters"]

    assert "agent product itself" in function["description"].lower()
    assert "ordinary project" in function["description"].lower()
    assert set(schema["properties"]) == {"user_goal", "acceptance_points"}
    assert schema["required"] == ["user_goal"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["user_goal"]["maxLength"] <= 8192
    assert schema["properties"]["acceptance_points"]["items"]["maxLength"] <= 2048


def test_executor_dispatch_is_host_only_and_rejects_authority_arguments(tmp_path):
    with temporary_agent_paths() as paths:
        registry = ToolRegistry.load(paths)
        executor = ToolExecutor(
            registry=registry,
            session=ExecutorSession(session_dir=paths.data / "sessions" / "s"),
            confirm_fn=lambda *_args: pytest.fail("generic approval must not replace the bound server confirm"),
        )
        invoked = []
        executor.self_evolution_start_dispatch = lambda args: invoked.append(args)

        denied = executor.run("self_evolution_start", {
            "user_goal": "Improve this product", "stable_repository": "C:/forged",
        })
        assert not denied.ok and denied.error.code == "validation_error"
        assert invoked == []

        accepted = executor.run("self_evolution_start", {"user_goal": "Improve this product"})
        assert not accepted.ok and accepted.error.code == "permission_denied"
        assert invoked == [{"user_goal": "Improve this product"}]


def test_agent_can_choose_start_tool_and_server_binds_exact_run_before_confirm(tmp_path):
    from server import WsBridge, WsSessionHandler
    from self_evolution_host import HostRunContextProvider

    class Model:
        def __init__(self):
            self.calls = []
        def set_cancel_event(self, _event):
            pass
        def chat(self, messages, *, tools=None, **_kwargs):
            self.calls.append(tools)
            if len(self.calls) == 1:
                assert any(tool["function"]["name"] == "self_evolution_start" for tool in tools)
                return LLMResponse(
                    model="fixture", content=None, tool_calls=[_tool_call({
                        "user_goal": "Improve the agent safely",
                        "acceptance_points": ["retain session safety"],
                    })], finish_reason="tool_calls", usage=None, raw={},
                )
            return LLMResponse(model="fixture", content="Host gate blocked the start.",
                               tool_calls=[], finish_reason="stop", usage=None, raw={})

    with temporary_agent_paths() as paths:
        session = create_new(paths, conversation_id="self-evolution-tool-session")
        session.meta.turn_mode = "agent"
        model = Model()
        confirmations = []
        agent = Agent.create(session, llm=model, confirm_fn=lambda *_args: "n")
        repl = SimpleNamespace(session=session, agent=agent)
        run = SimpleNamespace(run_id="run-tool-1", sequence=3, state="running", cancel_requested=False)
        provider = HostRunContextProvider()
        events = []
        bridge = WsBridge(emit=events.append, paths=paths, confirm_timeout=1)
        # Bind the bridge's live state to the exact Session/run the normal message turn owns.
        bridge._session_provider = lambda: repl.session
        bridge.lifecycle_conversation_id = session.conversation_id
        bridge._turn_busy.set()
        bridge.lifecycle.snapshot = lambda: run
        accepted = []
        class Integration:
            def _trusted_production_host(self):
                return object()
            def start(self, request, *, request_digest=None):
                context = provider()
                accepted.append((request, request_digest, context))
                return {"status": "blocked", "error_code": "self_evolution_host_not_production_ready"}
        bridge.self_evolution_chat = Integration()
        bridge.confirm_with_receipt = lambda preview, allow: confirmations.append((preview, allow)) or ("y", str(uuid.uuid4()))
        handler = WsSessionHandler(
            paths, self_evolution_host=object(), self_evolution_context_provider=provider,
            self_evolution_blockers=(), self_evolution_tool_dispatch_enabled=True,
        )
        request_id = str(uuid.uuid4())
        raw = "Please improve this agent safely"
        binding_factory = handler._make_self_evolution_start_tool_binding(
            repl, bridge, session, agent, run, raw, request_id, session.conversation_id, "agent",
        )
        agent._self_evolution_turn_dispatch = binding_factory

        result = agent.run_turn(raw, spawn_explore=False)

        assert result.tool_rounds == 1
        assert len(accepted) == 1
        request, digest, context = accepted[0]
        assert request.user_goal == "Improve the agent safely"
        assert request.acceptance_points == ("retain session safety",)
        assert len(digest) == 64
        assert context.conversation_id == session.conversation_id
        assert context.run_id == run.run_id and context.request_id == request_id
        assert context.self_evolution_intent is True
        assert len(confirmations) == 1 and confirmations[0][1] is False
        assert raw in confirmations[0][0] and request.user_goal in confirmations[0][0]
        assert "Host gate blocked the start" in result.assistant_text
        assert len(model.calls) == 2


def test_host_gate_blockers_are_returned_without_confirmation_or_start(tmp_path):
    from server import WsBridge, WsSessionHandler
    from self_evolution_host import HostRunContextProvider

    with temporary_agent_paths() as paths:
        session = create_new(paths, conversation_id="blocked-self-evolution-tool")
        session.meta.turn_mode = "agent"
        model = type("Model", (), {"set_cancel_event": lambda self, _event: None})()
        agent = Agent.create(session, llm=model, confirm_fn=lambda *_args: "n")
        repl = SimpleNamespace(session=session, agent=agent)
        run = SimpleNamespace(run_id="run-blocked", sequence=1, state="running", cancel_requested=False)
        provider = HostRunContextProvider()
        bridge = WsBridge(emit=lambda _event: None, paths=paths)
        bridge._session_provider = lambda: session
        bridge.lifecycle_conversation_id = session.conversation_id
        bridge._turn_busy.set()
        bridge.lifecycle.snapshot = lambda: run
        confirmations = []
        bridge.confirm_with_receipt = lambda *args: confirmations.append(args) or ("y", str(uuid.uuid4()))
        starts = []
        class Integration:
            def _trusted_production_host(self):
                return object()
            def start(self, *args, **kwargs):
                starts.append((args, kwargs))
                return {"status": "started"}
        bridge.self_evolution_chat = Integration()
        blockers = ("production_store_safety_unavailable", "host_production_gate_closed")
        handler = WsSessionHandler(
            paths, self_evolution_context_provider=provider,
            self_evolution_blockers=blockers, self_evolution_tool_dispatch_enabled=True,
        )
        raw = "Improve the Agent product"
        session.messages.append({"role": "user", "content": raw})
        agent.executor.session.ordinary_work_intent = "execute"
        binder = handler._make_self_evolution_start_tool_binding(
            repl, bridge, session, agent, run, raw, str(uuid.uuid4()), session.conversation_id, "agent",
        )
        assert binder(session, raw, len(session.messages) - 1) is None

        result = agent.executor.run("self_evolution_start", {"user_goal": "Improve safely"})

        assert not result.ok and result.error.code == "self_evolution_backend_unavailable"
        assert result.error.details["reason_codes"] == list(blockers)
        assert confirmations == [] and starts == []


def test_session_revocation_during_start_confirmation_prevents_host_dispatch(tmp_path):
    from server import WsBridge, WsSessionHandler
    from self_evolution_host import HostRunContextProvider

    with temporary_agent_paths() as paths:
        session = create_new(paths, conversation_id="revoked-self-evolution-tool")
        session.meta.turn_mode = "agent"
        agent = Agent.create(session, llm=type("Model", (), {"set_cancel_event": lambda self, _e: None})(),
                             confirm_fn=lambda *_args: "n")
        repl = SimpleNamespace(session=session, agent=agent)
        run = SimpleNamespace(run_id="run-revoked", sequence=2, state="running", cancel_requested=False)
        provider = HostRunContextProvider()
        bridge = WsBridge(emit=lambda _event: None, paths=paths)
        bridge._session_provider = lambda: repl.session
        bridge.lifecycle_conversation_id = session.conversation_id
        bridge._turn_busy.set()
        bridge.lifecycle.snapshot = lambda: run
        starts = []
        class Integration:
            def _trusted_production_host(self):
                return object()
            def start(self, *args, **kwargs):
                starts.append((args, kwargs))
                return {"status": "started"}
        bridge.self_evolution_chat = Integration()
        bridge.confirm_with_receipt = lambda *_args: (setattr(repl, "session", create_new(paths)), "receipt")
        handler = WsSessionHandler(paths, self_evolution_context_provider=provider)
        raw = "Improve this Agent product"
        session.messages.append({"role": "user", "content": raw})
        agent.executor.session.ordinary_work_intent = "execute"
        binder = handler._make_self_evolution_start_tool_binding(
            repl, bridge, session, agent, run, raw, str(uuid.uuid4()), session.conversation_id, "agent",
        )
        binder(session, raw, len(session.messages) - 1)

        result = agent.executor.run("self_evolution_start", {"user_goal": "Improve safely"})

        assert not result.ok and result.error.code == "self_evolution_start_not_confirmed"
        assert starts == []
