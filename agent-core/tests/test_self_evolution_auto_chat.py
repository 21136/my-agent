from __future__ import annotations

import asyncio
import os
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

import server


def _sandbox_root(tmp_path: Path) -> Path:
    root = tmp_path / "repo" / ".worktrees" / "sandbox"
    root.mkdir(parents=True)
    (root.parent.parent / ".git").mkdir(exist_ok=True)
    return root


def test_auto_policy_off_by_default_and_inherited_env_ignored_outside_sandbox(monkeypatch, tmp_path):
    policy = tmp_path / "policy.json"
    monkeypatch.setenv("MY_AGENT_SELF_EVOLUTION_AUTO_POLICY", str(policy))
    stable_root = tmp_path / "repo"
    stable_root.mkdir()
    stable_agent_core = stable_root / "agent-core"
    stable_agent_core.mkdir()
    (stable_root / ".git").mkdir()
    monkeypatch.setattr(server, "_AGENT_CORE", stable_agent_core)

    assert server._resolve_self_evolution_auto_policy_path(stable_root) is None
    with pytest.raises(ValueError, match="canonical .worktrees/sandbox"):
        server._resolve_self_evolution_auto_policy_path(stable_root, str(policy))
    sandbox = _sandbox_root(tmp_path)
    sandbox_agent_core = sandbox / "agent-core"
    sandbox_agent_core.mkdir()
    monkeypatch.setattr(server, "_AGENT_CORE", sandbox_agent_core)
    policy.touch()
    assert server._resolve_self_evolution_auto_policy_path(sandbox) == policy.resolve()


def test_cli_policy_must_not_conflict_with_environment_and_is_sandbox_only(monkeypatch, tmp_path):
    sandbox = _sandbox_root(tmp_path)
    env_policy = tmp_path / "env.json"
    cli_policy = tmp_path / "cli.json"
    env_policy.touch()
    cli_policy.touch()
    sandbox_agent_core = sandbox / "agent-core"
    sandbox_agent_core.mkdir()
    monkeypatch.setattr(server, "_AGENT_CORE", sandbox_agent_core)
    monkeypatch.setenv("MY_AGENT_SELF_EVOLUTION_AUTO_POLICY", str(env_policy))

    with pytest.raises(ValueError, match="different policies"):
        server._resolve_self_evolution_auto_policy_path(sandbox, str(cli_policy))
    with pytest.raises(ValueError):
        server._resolve_self_evolution_auto_policy_path(sandbox, "relative.json")
    monkeypatch.setenv("MY_AGENT_SELF_EVOLUTION_AUTO_POLICY", str(cli_policy))
    assert server._resolve_self_evolution_auto_policy_path(sandbox, str(cli_policy)) == cli_policy.resolve()


def test_build_parser_exposes_opt_in_policy_path():
    args = server.build_parser().parse_args(["--self-evolution-auto-policy", "C:/policy.json"])
    assert args.self_evolution_auto_policy == "C:/policy.json"


class _Auto:
    def __init__(self, classification: str, result=None, on_run=None, classify_error=None):
        self.classification = classification
        self.result = result or SimpleNamespace(status="proposed", detail="candidate ready", commit="abc123")
        self.seen = []
        self.on_run = on_run
        self.classify_error = classify_error

    def classify(self, text, *, cancelled=lambda: False):
        self.seen.append((text, cancelled))
        if self.classify_error:
            raise self.classify_error
        return self.classification

    def run(self, text, *, cancelled=lambda: False):
        self.seen.append(("run", text, cancelled))
        if self.on_run:
            self.on_run()
        return self.result


def _handler_fixture(tmp_path: Path, auto=None, turn_mode="agent"):
    paths = server.AgentPaths.from_root(Path(server.__file__).resolve().parents[1])
    events = []
    bridge = SimpleNamespace(
        _turn_busy=__import__("threading").Event(),
        _connection_closed=__import__("threading").Event(),
        cancel_event=__import__("threading").Event(),
        lifecycle_conversation_id=None,
        _auto_recognition_session=None,
        _auto_recognition_turn_generation=None,
        _post_turn_generation=0,
        begin_turn=lambda **_kwargs: True,
        lifecycle=SimpleNamespace(snapshot=lambda: SimpleNamespace(run_id="run-auto")),
        emit=events.append,
        emit_turn_event=events.append,
        finish_execution=lambda *_args, **_kwargs: True,
        end_turn=lambda: None,
    )
    session = SimpleNamespace(
        meta=SimpleNamespace(turn_mode=turn_mode, llm_model="configured-model", active_shell="grow", project_root="", harness="desktop", project_id=""),
        conversation_id="session-1",
        messages=[],
        _turn_active_doc="",
        _turn_active_doc_selection="",
        _messages_fully_loaded=True,
        set_turn_mode=lambda mode: setattr(session.meta, "turn_mode", mode),
        append_message_durable=lambda item: session.messages.append(item),
    )
    repl = SimpleNamespace(session=session, agent=SimpleNamespace(session=session))
    handler = server.WsSessionHandler(paths, self_evolution_auto=auto)
    handler._file_stage = SimpleNamespace(take=lambda *_args: [])
    return handler, repl, bridge, events


def test_auto_chat_off_keeps_normal_user_dispatch_path(monkeypatch, tmp_path):
    handler, repl, bridge, _events = _handler_fixture(tmp_path)
    invoked = []

    async def run_line(*args, **kwargs):
        invoked.append((args, kwargs))
        return None

    monkeypatch.setattr(server, "_run_line", run_line)
    asyncio.run(handler._dispatch({"type": "user.message", "text": "please evolve this"}, repl, bridge))
    assert len(invoked) == 1


def test_auto_chat_only_classifies_agent_mode_unattached_non_document_text(monkeypatch, tmp_path):
    auto = _Auto("chat")
    handler, repl, bridge, _events = _handler_fixture(tmp_path, auto=auto)

    assert handler._self_evolution_auto_eligible("please evolve this", (), None, repl)
    repl.session.meta.turn_mode = "ask"
    assert not handler._self_evolution_auto_eligible("please evolve this", (), None, repl)
    repl.session.meta.turn_mode = "agent"
    assert not handler._self_evolution_auto_eligible("please evolve this", (object(),), None, repl)
    assert handler._self_evolution_auto_eligible('"quoted request"', (), None, repl)
    assert not handler._self_evolution_auto_eligible("ordinary", (), "quoted document", repl)


def test_auto_chat_classification_chat_falls_through_without_run(monkeypatch, tmp_path):
    auto = _Auto("chat")
    handler, repl, bridge, _events = _handler_fixture(tmp_path, auto=auto)
    invoked = []

    async def run_line(*args, **kwargs):
        invoked.append(kwargs)

    monkeypatch.setattr(server, "_run_line", run_line)
    asyncio.run(handler._dispatch({"type": "user.message", "text": "hello"}, repl, bridge))
    assert len(invoked) == 1
    assert not any(call[0] == "run" for call in auto.seen)


def test_auto_evolution_outcome_is_persisted_once_and_stable_is_held(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "session_history_event", lambda _session: {"type": "session.history"})
    auto = _Auto("evolve")
    handler, repl, bridge, events = _handler_fixture(tmp_path, auto=auto)
    monkeypatch.setattr(server, "_run_line", lambda *_args, **_kwargs: pytest.fail("must not dispatch Agent"))
    asyncio.run(handler._dispatch({"type": "user.message", "text": "evolve"}, repl, bridge))
    assert [item["role"] for item in repl.session.messages] == ["user", "assistant"]
    assert "candidate ready" in repl.session.messages[-1]["content"]
    assert [event["type"] for event in events if event.get("type") == "turn.end"] == ["turn.end"]

    stable = _Auto("stable")
    handler, repl, bridge, _events = _handler_fixture(tmp_path / "stable", auto=stable)
    asyncio.run(handler._dispatch({"type": "user.message", "text": "change stable"}, repl, bridge))
    assert not any(call[0] == "run" for call in stable.seen)
    assert "stable" in repl.session.messages[-1]["content"].lower()


def test_auto_intercept_persists_request_identity_and_emits_receipt_immediately(monkeypatch, tmp_path):
    request_id = str(uuid.uuid4())
    order = []
    auto = _Auto("clarify")
    handler, repl, bridge, events = _handler_fixture(tmp_path, auto=auto)
    original_append = repl.session.append_message_durable

    def append(row):
        original_append(row)
        order.append(("append", row["role"]))

    repl.session.append_message_durable = append
    bridge.emit = lambda event: (events.append(event), order.append(("emit", event["type"])))
    bridge.emit_turn_event = lambda event: (events.append(event), order.append(("emit", event["type"])))
    monkeypatch.setattr(server, "session_history_event", lambda _session: (
        order.append(("history", None)) or {"type": "session.history"}
    ))

    asyncio.run(handler._dispatch({"type": "user.message", "text": "clarify this", "request_id": request_id}, repl, bridge))

    assert repl.session.messages[0]["_host_internal"]["user_message_request_id"] == request_id
    assert order.index(("emit", "user.message.persisted")) < order.index(("append", "assistant"))
    assert order.index(("emit", "user.message.persisted")) < order.index(("history", None))
    assert order.index(("emit", "user.message.persisted")) < order.index(("emit", "turn.end"))


def test_auto_intercept_receipt_survives_assistant_append_failure(monkeypatch, tmp_path):
    request_id = str(uuid.uuid4())
    auto = _Auto("clarify")
    handler, repl, bridge, events = _handler_fixture(tmp_path, auto=auto)
    original_append = repl.session.append_message_durable

    def append(row):
        if row["role"] == "assistant":
            raise OSError("simulated assistant persistence failure")
        original_append(row)

    repl.session.append_message_durable = append
    monkeypatch.setattr(server, "session_history_event", lambda _session: pytest.fail("history must not emit after assistant append fails"))

    asyncio.run(handler._dispatch({"type": "user.message", "text": "clarify this", "request_id": request_id}, repl, bridge))

    receipts = [event for event in events if event.get("type") == "user.message.persisted"]
    assert len(receipts) == 1 and receipts[0]["request_id"] == request_id
    assert [row["role"] for row in repl.session.messages] == ["user"]
    terminal = next(event for event in events if event.get("type") == "turn.end")
    assert terminal["ok"] is False


def test_session_history_projects_only_canonical_user_request_identity():
    from session import _build_chat_history_from_messages

    request_id = str(uuid.uuid4())
    items, _total = _build_chat_history_from_messages([
        {"role": "user", "content": "identified", "_host_internal": {"user_message_request_id": request_id}},
        {"role": "assistant", "content": "reply", "_host_internal": {"user_message_request_id": request_id}},
        {"role": "user", "content": "legacy"},
        {"role": "user", "content": "invalid", "_host_internal": {"user_message_request_id": "not-a-uuid"}},
    ])

    assert items == [
        {"role": "user", "text": "identified", "request_id": request_id},
        {"role": "assistant", "text": "reply"},
        {"role": "user", "text": "legacy"},
        {"role": "user", "text": "invalid"},
    ]


def test_auto_evolution_cancel_and_busy_do_not_dispatch(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "session_history_event", lambda _session: {"type": "session.history"})
    auto = _Auto("evolve")
    handler, repl, bridge, events = _handler_fixture(tmp_path, auto=auto)
    bridge.cancel_event.set()
    asyncio.run(handler._dispatch({"type": "user.message", "text": "evolve"}, repl, bridge))
    assert not any(call[0] == "run" for call in auto.seen)
    assert any(event.get("type") == "turn.end" for event in events)

    busy = _Auto("evolve")
    handler, repl, bridge, _events = _handler_fixture(tmp_path / "busy", auto=busy)
    bridge.begin_turn = lambda **_kwargs: False
    asyncio.run(handler._dispatch({"type": "user.message", "text": "evolve"}, repl, bridge))
    assert busy.seen == []


def test_auto_publish_result_wins_cancel_race_and_nonpublished_states_are_not_success(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "session_history_event", lambda _session: {"type": "session.history"})
    for status, expected_ok in (("published", True), ("no_change", True), ("busy", False), ("unavailable", False), ("blocked", False), ("failed", False), ("cancelled", False)):
        auto = _Auto("evolve", SimpleNamespace(status=status, detail=f"result {status}", commit="deadbeef" if status == "published" else None))
        handler, repl, bridge, events = _handler_fixture(tmp_path / status, auto=auto)
        if status == "published":
            auto.on_run = bridge.cancel_event.set
        asyncio.run(handler._dispatch({"type": "user.message", "text": "evolve"}, repl, bridge))
        final = next(event for event in events if event.get("type") == "turn.end")
        assert final["ok"] is expected_ok
        if status == "published":
            assert "published" in repl.session.messages[-1]["content"]
            assert "deadbeef" in repl.session.messages[-1]["content"]
            assert "取消" not in repl.session.messages[-1]["content"]


def test_self_evolution_request_dedup_is_session_and_connection_scoped():
    bridge = SimpleNamespace(_post_turn_seen_requests=set())
    request_id = str(uuid.uuid4())
    assert server._claim_self_evolution_auto_request(bridge, "session-a", request_id) == "accepted"
    assert server._claim_self_evolution_auto_request(bridge, "session-a", request_id) == "duplicate"
    assert server._claim_self_evolution_auto_request(bridge, "session-b", request_id) == "accepted"
    assert server._claim_self_evolution_auto_request(bridge, "session-a", "not-a-uuid") == "untracked"


def test_auto_completion_uses_only_policy_model_without_tools_or_response_format(monkeypatch, tmp_path):
    monkeypatch.delenv("MY_AGENT_SELECTED_SYNC_POLICY", raising=False)
    captured = {}

    class Entry:
        id = "operator-model"
        def resolve_api_key(self, _paths):
            return "configured-secret"

    class Registry:
        def resolve(self, model):
            return Entry() if model == "operator-model" else None

    class Client:
        def __init__(self, *, paths):
            captured["paths"] = paths
        def set_cancel_event(self, event):
            captured["cancel_event"] = event
        def chat(self, messages, **kwargs):
            captured["messages"] = messages
            captured["kwargs"] = kwargs
            return SimpleNamespace(content='{"route":"chat"}')

    class Auto:
        def __init__(self, policy, complete, **_kwargs):
            self.complete = complete

    monkeypatch.setattr("llm_models.get_registry", lambda _paths: Registry())
    monkeypatch.setattr("llm_client.LLMClient", Client)
    monkeypatch.setattr("self_evolution_auto.AutoEvolution", Auto)
    handler, _repl, bridge, _events = _handler_fixture(tmp_path, auto=None)
    handler.self_evolution_auto_policy = SimpleNamespace(model_id="operator-model")
    auto = handler._new_self_evolution_auto(bridge)
    answer = auto.complete([{"role": "system", "content": "route"}, {"role": "user", "content": "only this message"}], "operator-model")
    assert answer == '{"route":"chat"}'
    assert captured["kwargs"] == {"model": "operator-model", "tools": [], "temperature": 0.0}
    assert captured["messages"][-1]["content"] == "only this message"
    assert "history" not in captured["messages"][-1]["content"]


def test_classification_unavailable_falls_back_to_normal_chat_with_visible_reason_and_keeps_post_turn_capture(monkeypatch, tmp_path):
    auto = _Auto("evolve", classify_error=RuntimeError("provider secret detail must not leak"))
    handler, repl, bridge, events = _handler_fixture(tmp_path, auto=auto)
    handler._post_turn_commit_ready = True
    request_id = str(uuid.uuid4())
    invoked = []

    async def run_line(*args, **kwargs):
        invoked.append(kwargs)

    monkeypatch.setattr(server, "_run_line", run_line)
    asyncio.run(handler._dispatch({"type": "user.message", "text": "ordinary request", "request_id": request_id}, repl, bridge))
    assert invoked and invoked[0]["prestarted_run_id"] == "run-auto"
    assert invoked[0]["post_turn_request_id"] == request_id
    assert any(event.get("type") == "turn.notice" and "未启动自主改造" in event.get("text", "") for event in events)
    assert not any("provider secret" in str(event) for event in events)
    assert not any(call[0] == "run" for call in auto.seen)


@pytest.mark.parametrize("offered", [None, "my-agent-auto." + "b" * 64])
def test_opted_in_websocket_unauthorized_is_closed_before_session_init(monkeypatch, offered):
    token = "a" * 64
    events = []
    class Headers:
        def get_all(self, name):
            assert name == "Sec-WebSocket-Protocol"
            return [] if offered is None else [offered]
    class Socket:
        request = SimpleNamespace(headers=Headers())
        async def close(self, *, code, reason):
            events.append((code, reason))
    paths = server.AgentPaths.from_root(Path(server.__file__).resolve().parents[1])
    handler = server.WsSessionHandler(paths, self_evolution_auto_token=token)
    monkeypatch.setattr(server, "resume_desktop_or_create", lambda *_args: pytest.fail("session must not initialize"))

    asyncio.run(handler.handle(Socket()))

    assert events and events[0][0] == 1008
    assert "a" * 64 not in events[0][1]


def test_real_websocket_without_operator_subprotocol_closes_1008_before_session_init(monkeypatch, tmp_path):
    from websockets.asyncio.client import connect
    from websockets.asyncio.server import serve
    from websockets.exceptions import ConnectionClosedError

    paths = server.AgentPaths.from_root(Path(server.__file__).resolve().parents[1])
    handler = server.WsSessionHandler(paths, self_evolution_auto_token="a" * 64)
    monkeypatch.setattr(server, "resume_desktop_or_create", lambda *_args: pytest.fail("session must not initialize"))

    async def check():
        async with serve(handler.handle, "127.0.0.1", 0) as listening:
            port = listening.sockets[0].getsockname()[1]
            async with connect(f"ws://127.0.0.1:{port}") as websocket:
                with pytest.raises(ConnectionClosedError) as closed:
                    await websocket.recv()
                assert closed.value.rcvd.code == 1008
                assert "a" * 64 not in closed.value.rcvd.reason

    asyncio.run(check())


@pytest.mark.parametrize("launch_kwargs", [
    {"fixed_host_layout": {"code_root": "unused"}},
    {"managed_readonly_context": object()},
    {"managed_application": object()},
    {"experimental_self_evolution_static_ast": True},
])
def test_auto_policy_env_cannot_enable_fixed_managed_or_legacy_launch_modes(monkeypatch, launch_kwargs):
    monkeypatch.setenv("MY_AGENT_SELF_EVOLUTION_AUTO_POLICY", "C:/policy.json")
    assert asyncio.run(server.run_server("127.0.0.1", 8765, **launch_kwargs)) == 4


def test_auto_policy_env_cannot_enable_packaged_launch(monkeypatch):
    monkeypatch.setenv("MY_AGENT_SELF_EVOLUTION_AUTO_POLICY", "C:/policy.json")
    monkeypatch.setattr(server.sys, "frozen", True, raising=False)
    assert asyncio.run(server.run_server("127.0.0.1", 8765)) == 4


def test_opted_in_startup_requires_canonical_loopback_and_32_byte_hex_token():
    token = "a" * 64
    assert server._require_self_evolution_auto_auth("127.0.0.1", token) == token
    assert server._require_self_evolution_auto_auth("::1", token) == token
    with pytest.raises(ValueError, match="canonical loopback"):
        server._require_self_evolution_auto_auth("localhost", token)
    with pytest.raises(ValueError, match="TOKEN"):
        server._require_self_evolution_auto_auth("127.0.0.1", "short")


def test_websocket_auth_requires_exact_offered_subprotocol():
    headers = SimpleNamespace(get_all=lambda _name: ["chat, my-agent-auto." + "a" * 64])
    assert server._websocket_offers_self_evolution_auto_token(headers, "a" * 64)
    assert not server._websocket_offers_self_evolution_auto_token(headers, "b" * 64)


def test_auto_websocket_negotiates_exact_protocol_for_browser_clients():
    from websockets.asyncio.client import connect
    from websockets.exceptions import InvalidStatus

    async def round_trip():
        token = "a" * 64
        expected = "my-agent-auto." + token

        async def handle(socket):
            await socket.send("ready")

        async with server.serve(handle, "127.0.0.1", 0, **server._auto_websocket_options(token)) as listener:
            port = listener.sockets[0].getsockname()[1]
            uri = f"ws://127.0.0.1:{port}"
            async with connect(uri, subprotocols=[expected]) as socket:
                assert socket.subprotocol == expected
                assert await socket.recv() == "ready"
            for protocols in ([], ["my-agent-auto." + "b" * 64]):
                with pytest.raises(InvalidStatus) as rejected:
                    async with connect(uri, subprotocols=protocols):
                        pytest.fail("invalid operator unexpectedly negotiated the socket")
                assert token.encode() not in rejected.value.response.body

    assert server._auto_websocket_options(None) == {}
    asyncio.run(round_trip())


def test_one_click_stable_runtime_accepts_only_its_registered_sandbox_and_policy(monkeypatch, tmp_path):
    stable = tmp_path / "stable"
    sandbox = stable / ".worktrees" / "sandbox"
    data = sandbox / "data"
    data.mkdir(parents=True)
    (stable / ".git").mkdir()
    policy = data / "self-evolution-auto.policy.json"
    policy.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(server, "_AGENT_CORE", stable / "agent-core")
    monkeypatch.setenv("MY_AGENT_SELF_EVOLUTION_AUTO_POLICY", str(policy))
    assert server._resolve_self_evolution_auto_policy_path(sandbox) is None
    assert server._resolve_self_evolution_auto_policy_path(sandbox, stable_launcher=True) == policy
    with pytest.raises(ValueError, match="canonical"):
        server._resolve_self_evolution_auto_policy_path(stable, stable_launcher=True)
    other = data / "other.json"
    other.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("MY_AGENT_SELF_EVOLUTION_AUTO_POLICY", str(other))
    with pytest.raises(ValueError, match="registered policy"):
        server._resolve_self_evolution_auto_policy_path(sandbox, stable_launcher=True)


def test_selected_sync_semantic_route_executes_optional_service_once_and_persists_scope(monkeypatch, tmp_path):
    class SyncService:
        def __init__(self):
            self.calls = []
        def run(self, text, *, cancelled):
            self.calls.append((text, cancelled))
            return SimpleNamespace(status="candidate", reason="已应用到 sandbox worktree，等待同步。")

    monkeypatch.setattr(server, "session_history_event", lambda _session: {"type": "session.history"})
    from self_evolution_auto import AutoEvolution
    policy = SimpleNamespace(model_id="0x567-flash")
    service = SyncService()
    tool_requests = []
    def classify_with_tool(messages, tools):
        tool_requests.append((messages, tools))
        return SimpleNamespace(content=None, tool_calls=[{
            "type": "function", "function": {"name": "sync_selected", "arguments": "{}"},
        }])
    auto = AutoEvolution(policy, lambda *_args: "{}", selected_sync=service, complete_tools=classify_with_tool)
    handler, repl, bridge, events = _handler_fixture(tmp_path, auto=auto)
    monkeypatch.setattr(server, "_run_line", lambda *_args, **_kwargs: pytest.fail("sync must not fall through to Agent"))

    asyncio.run(handler._dispatch({"type": "user.message", "text": "请把选定文件从稳定区同步到 sandbox", "request_id": str(uuid.uuid4())}, repl, bridge))

    assert [row["role"] for row in repl.session.messages] == ["user", "assistant"]
    assert service.calls and service.calls[0][0] == "请把选定文件从稳定区同步到 sandbox"
    assert tool_requests and tool_requests[0][1][0]["function"]["name"] == "sync_selected"
    assert "已应用到 sandbox worktree" in repl.session.messages[-1]["content"]
    assert "实验提交：" not in repl.session.messages[-1]["content"]
    assert "commit" not in repl.session.messages[-1]["content"]
    assert [event["type"] for event in events if event.get("type") == "turn.end"] == ["turn.end"]


def test_sync_route_without_configured_service_is_fail_closed():
    from self_evolution_auto import AutoEvolution
    auto = AutoEvolution(SimpleNamespace(model_id="0x567-flash"), lambda *_args: '{"route":"sync"}')
    assert auto.classify("sync stable to sandbox") == "sync_unavailable"
    assert auto.sync_selected("sync stable to sandbox").status == "blocked"


def test_sync_execution_serializes_with_auto_run_and_sanitizes_result():
    from self_evolution_auto import AutoEvolution
    class Service:
        def run(self, _text, *, cancelled):
            return SimpleNamespace(status="published", reason="完成\n安全范围总结", commit="must-not-claim")
    auto = AutoEvolution(SimpleNamespace(model_id="0x567-flash"), lambda *_args: "{}", selected_sync=Service())
    result = auto.sync_selected("stable→sandbox scoped request")
    assert result.status == "published"
    assert "安全范围总结" in result.detail
    assert "must-not-claim" not in result.detail


def test_sync_semantic_tool_call_is_not_selected_for_reverse_or_ambiguous_text():
    from self_evolution_auto import AutoEvolution
    class Service:
        def run(self, *_args, **_kwargs):
            pytest.fail("reverse/ambiguous request must not call sync service")
    auto = AutoEvolution(
        SimpleNamespace(model_id="0x567-flash"), lambda *_args: "{}", selected_sync=Service(),
        complete_tools=lambda _messages, _tools: SimpleNamespace(
            content='{"route":"chat"}', tool_calls=[],
        ),
    )
    assert auto.classify("能不能把 sandbox 反向同步回稳定区？") == "chat"


def test_sync_factory_requires_authenticated_operator_exact_model_and_env(monkeypatch, tmp_path):
    from self_evolution_auto import AutoEvolution
    captured = {}
    class Entry:
        id = "0x567-flash"
        def resolve_api_key(self, _paths): return "configured"
    class Registry:
        def resolve(self, model): return Entry() if model == "0x567-flash" else None
    class Client:
        def __init__(self, *, paths): pass
        def set_cancel_event(self, _event): pass
        def chat(self, messages, **kwargs):
            captured["messages"], captured["kwargs"] = messages, kwargs
            from llm_client import LLMResponse
            return LLMResponse("0x567-flash", None, [], "stop", None, {})
    class SelectedSync:
        @classmethod
        def from_environment(cls, root, complete_tools):
            captured["root"], captured["complete_tools"] = root, complete_tools
            return object()
    monkeypatch.setenv("MY_AGENT_SELECTED_SYNC_POLICY", "configured-policy")
    monkeypatch.setattr("llm_models.get_registry", lambda _paths: Registry())
    monkeypatch.setattr("llm_client.LLMClient", Client)
    monkeypatch.setitem(sys.modules, "self_evolution_selected_sync", SimpleNamespace(SelectedSync=SelectedSync))
    handler, _repl, bridge, _events = _handler_fixture(tmp_path)
    handler.self_evolution_auto_token = "a" * 64
    handler.self_evolution_auto_policy = SimpleNamespace(model_id="0x567-flash")
    auto = handler._new_self_evolution_auto(bridge)
    assert isinstance(auto, AutoEvolution)
    auto._complete_tools([{"role":"user", "content":"x"}], [{"type":"function"}])
    assert captured["root"] == handler.paths.agent_root
    assert captured["kwargs"] == {"model": "0x567-flash", "tools": [{"type":"function"}], "temperature": 0.0}

    handler.self_evolution_auto = None
    handler.self_evolution_auto_token = None
    no_auth = handler._new_self_evolution_auto(bridge)
    assert no_auth._selected_sync is None


def test_selected_sync_factory_failure_does_not_disable_existing_auto(monkeypatch, tmp_path):
    from self_evolution_auto import AutoEvolution
    class Entry:
        id = "0x567-flash"
        def resolve_api_key(self, _paths): return "configured"
    class Registry:
        def resolve(self, _model): return Entry()
    class Client:
        def __init__(self, *, paths): pass
        def set_cancel_event(self, _event): pass
        def chat(self, *_args, **_kwargs): return SimpleNamespace(content='{"route":"chat"}')
    class SelectedSync:
        @classmethod
        def from_environment(cls, *_args): raise ValueError("invalid sync policy")
    monkeypatch.setenv("MY_AGENT_SELECTED_SYNC_POLICY", "bad-policy")
    monkeypatch.setattr("llm_models.get_registry", lambda _paths: Registry())
    monkeypatch.setattr("llm_client.LLMClient", Client)
    monkeypatch.setitem(sys.modules, "self_evolution_selected_sync", SimpleNamespace(SelectedSync=SelectedSync))
    handler, _repl, bridge, _events = _handler_fixture(tmp_path)
    handler.self_evolution_auto_token = "b" * 64
    handler.self_evolution_auto_policy = SimpleNamespace(model_id="0x567-flash")
    auto = handler._new_self_evolution_auto(bridge)
    assert isinstance(auto, AutoEvolution)
    assert auto._selected_sync is None
    assert auto.classify("normal chat") == "chat"


def test_selected_sync_shares_auto_run_lock():
    from self_evolution_auto import AutoEvolution
    class Service:
        def run(self, *_args, **_kwargs):
            pytest.fail("busy sync must not reach service")
    auto = AutoEvolution(SimpleNamespace(model_id="0x567-flash"), lambda *_args: "{}", selected_sync=Service())
    assert auto._active.acquire(blocking=False)
    try:
        result = auto.sync_selected("explicit stable to sandbox selection")
    finally:
        auto._active.release()
    assert result.status == "blocked"
    assert "正在运行" in result.detail
