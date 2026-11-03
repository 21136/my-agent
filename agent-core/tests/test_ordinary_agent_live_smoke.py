from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

TOOLS = Path(__file__).resolve().parents[2] / "tools"
sys.path.insert(0, str(TOOLS))

from ordinary_agent_live_smoke import (  # noqa: E402
    LIVE_OPT_IN,
    PROMPT,
    LivePolicyError,
    OneRequestTransport,
    SingleCallLLM,
    SocketEgressPolicy,
    temporary_agent_paths,
    run_live_smoke,
    LIVE_PAYLOAD_REVIEWED,
    _contains_absolute_path,
)


class CountingTransport(httpx.BaseTransport):
    def __init__(self):
        self.calls = 0

    def handle_request(self, request):
        self.calls += 1
        return httpx.Response(200, request=request, json={"ok": True})


def test_one_request_transport_allows_only_the_fixed_provider_once():
    delegate = CountingTransport()
    transport = OneRequestTransport(delegate)
    req = httpx.Request("POST", "https://api-cdn.0x567.com/v1/chat/completions")

    response = transport.handle_request(req)

    assert response.status_code == 200
    assert transport.calls == 1
    assert delegate.calls == 1
    with pytest.raises(LivePolicyError):
        transport.handle_request(req)
    assert delegate.calls == 1


@pytest.mark.parametrize("url", [
    "http://api-cdn.0x567.com/v1/chat/completions",
    "https://example.com/v1/chat/completions",
    "https://api-cdn.0x567.com/other",
    "https://api-cdn.0x567.com:444/v1/chat/completions",
])
def test_one_request_transport_rejects_non_allowlisted_destinations(url):
    delegate = CountingTransport()
    transport = OneRequestTransport(delegate)

    with pytest.raises(LivePolicyError):
        transport.handle_request(httpx.Request("POST", url))
    assert transport.calls == 0
    assert delegate.calls == 0


def test_single_call_llm_rejects_second_call_and_tool_capability():
    class Delegate:
        calls = 0

        def chat(self, messages, **kwargs):
            self.calls += 1
            return object()

    delegate = Delegate()
    class PermitTestEnvelope:
        def validate_messages(self, *_args, **_kwargs): return True
    client = SingleCallLLM(delegate, payload_guard=PermitTestEnvelope())
    prompt = [{"role": "system", "content": "Fixed built-in safety instructions."},
              {"role": "user", "content": PROMPT}]
    client.chat(prompt, model="0x567-flash", tools=[])
    assert client.calls == 1
    assert delegate.calls == 1

    with pytest.raises(LivePolicyError):
        client.chat(prompt, model="0x567-flash", tools=[])
    with pytest.raises(LivePolicyError):
        SingleCallLLM(delegate).chat(prompt, model="0x567-flash", tools=[{"function": {}}])
    assert delegate.calls == 1


def test_live_opt_in_name_is_explicit_and_stable():
    assert LIVE_OPT_IN == "MY_AGENT_ORDINARY_AGENT_LIVE_SMOKE_OPTIN"




def test_live_runner_requires_explicit_opt_in_without_touching_configuration(monkeypatch, capsys):
    monkeypatch.delenv(LIVE_OPT_IN, raising=False)

    assert run_live_smoke() == 2
    output = capsys.readouterr().out
    assert output == '{"status":"OPT_IN_REQUIRED","model_id":"0x567-flash","provider_model":"gpt-6-luna","calls":0,"tool_calls":0,"confirmations":0,"answer_present":false}' + "\n"
    assert PROMPT not in output


def test_socket_policy_rejects_other_hosts_and_ports_without_resolving(monkeypatch):
    policy = SocketEgressPolicy()
    calls = []

    def resolver(host, port, *args, **kwargs):
        calls.append((host, port))
        return []

    with pytest.raises(LivePolicyError):
        policy._guarded_getaddrinfo(resolver, "example.com", 443)
    with pytest.raises(LivePolicyError):
        policy._guarded_getaddrinfo(resolver, "api-cdn.0x567.com", 80)
    assert calls == []


def test_socket_policy_only_authorizes_resolved_tcp_endpoint():
    policy = SocketEgressPolicy()
    records = [(2, 1, 6, "", ("203.0.113.9", 443))]
    assert policy._guarded_getaddrinfo(lambda *_args, **_kwargs: records,
                                       "api-cdn.0x567.com", 443) == records
    policy._authorize_socket(2, 1, 6, ("203.0.113.9", 443))
    with pytest.raises(LivePolicyError):
        policy._authorize_socket(2, 1, 6, ("203.0.113.10", 443))
    with pytest.raises(LivePolicyError):
        policy._authorize_socket(2, 2, 17, ("203.0.113.9", 443))


def test_temporary_agent_cleanup_stays_inside_owned_temp_root(tmp_path, monkeypatch):
    import tempfile

    temp_base = tmp_path / "fixture-temp"
    temp_base.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temp_base))
    outside = tmp_path / "preserve.txt"
    outside.write_text("keep", encoding="utf-8")

    with temporary_agent_paths() as paths:
        root = paths.agent_root
        assert root.parent == temp_base.resolve()
        assert all(path.parent == root for path in (paths.workspace, paths.data, paths.evolve))
        assert outside.read_text(encoding="utf-8") == "keep"

    assert not root.exists()
    assert temp_base.exists()
    assert outside.read_text(encoding="utf-8") == "keep"




def test_socket_policy_restores_process_socket_api_after_exit():
    import socket

    originals = (socket.getaddrinfo, socket.socket.__init__, socket.socket.connect,
                 socket.socket.connect_ex, socket.gethostbyname,
                 socket.gethostbyname_ex, socket.gethostbyaddr)
    policy = SocketEgressPolicy()
    with pytest.raises(RuntimeError):
        with policy.installed():
            assert socket.getaddrinfo is not originals[0]
            raise RuntimeError("synthetic test interruption")
    assert (socket.getaddrinfo, socket.socket.__init__, socket.socket.connect,
            socket.socket.connect_ex, socket.gethostbyname,
            socket.gethostbyname_ex, socket.gethostbyaddr) == originals



def test_complete_payload_rejects_environment_paths_and_unexpected_messages():
    class Delegate:
        calls = 0

        def chat(self, *_args, **_kwargs):
            self.calls += 1
            return object()

    delegate = Delegate()
    class PermitTestEnvelope:
        def validate_messages(self, *_args, **_kwargs): return True
    client = SingleCallLLM(delegate, payload_guard=PermitTestEnvelope())
    assert _contains_absolute_path("settings: C:\\Users\\someone\\profile.json")
    assert _contains_absolute_path("config loaded from /home/alice/.agent/config")
    assert not _contains_absolute_path("provider https://api-cdn.0x567.com/v1/chat/completions")

    assert LIVE_PAYLOAD_REVIEWED is True


def test_live_payload_policy_is_enabled_but_live_runner_requires_opt_in():
    assert LIVE_PAYLOAD_REVIEWED is True


def test_agent_run_turn_payload_is_bound_to_fresh_synthetic_session(tmp_path, monkeypatch):
    import copy
    import sys
    from types import SimpleNamespace

    core = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(core))
    from agent import Agent
    import agent as agent_module
    import loader
    import session as session_module
    from paths import AgentPaths
    from session import create_new
    from ordinary_agent_live_smoke import PayloadSourceGuard

    paths = AgentPaths(agent_root=tmp_path, workspace=tmp_path / "workspace",
                       data=tmp_path / "data", evolve=tmp_path / "evolve")
    for directory in (paths.workspace, paths.data, paths.evolve):
        directory.mkdir()
    monkeypatch.setattr(session_module.Session, "save", lambda *_a, **_k: None)
    original_append = session_module.Session.append_message
    monkeypatch.setattr(session_module.Session, "append_message",
                        lambda self, message, *, persist=True: original_append(self, message, persist=False))
    session = create_new(paths)
    session.meta.turn_mode = "ask"
    session.meta.llm_model = "0x567-flash"
    session.meta.llm_model_override = True
    guard = PayloadSourceGuard(session, paths, PROMPT)
    outbound_validations = []
    original_validate = guard.validate_messages
    def capture_outbound_validation(*args, **kwargs):
        valid = original_validate(*args, **kwargs)
        outbound_validations.append(valid)
        return valid
    monkeypatch.setattr(guard, "validate_messages", capture_outbound_validation)
    monkeypatch.setattr(loader, "build_system_prompt",
                        guard.wrap_system_builder(loader.build_system_prompt))
    monkeypatch.setattr(agent_module, "build_llm_tools", lambda *_a, **_k: [])

    class FakeLLM:
        calls = 0
        messages = None

        def chat(self, messages, **_kwargs):
            self.calls += 1
            self.messages = copy.deepcopy(messages)
            return SimpleNamespace(model="offline-fake", content="synthetic answer",
                                   tool_calls=[], finish_reason="stop", usage=None, raw={})

    fake = FakeLLM()
    llm = SingleCallLLM(fake, payload_guard=guard)
    agent = Agent.create(session, llm=llm, tool_loop_max=1,
                         confirm_fn=lambda *_a, **_k: False)
    result = agent.run_turn(PROMPT, spawn_explore=False)

    assert fake.calls == llm.calls == 1
    assert result.tool_rounds == 0
    assert [message["role"] for message in fake.messages] == ["system", "user", "user"]
    assert outbound_validations == [True]
    assert list(paths.evolve.iterdir()) == []
    assert list(paths.workspace.iterdir()) == []
    assert not list(paths.data.rglob("*.jsonl"))





