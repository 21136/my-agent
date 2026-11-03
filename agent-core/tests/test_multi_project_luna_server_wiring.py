from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

import agent as agent_module
import llm_client
import multi_project_luna_intent as multi_intent
import server
from main import ConversationRepl
from paths import AgentPaths
from server import WsBridge
from session import Session, SessionMeta


CURRENT_TEXT = "\u8bf7\u67e5\u770b\u6240\u6709\u9879\u76ee\u7684\u5de5\u4f5c\u6982\u51b5"


class NoCallAgentModel:
    def __init__(self, *, paths):
        self.paths = paths
        self.cancel_event = None
        self.calls = []

    def set_cancel_event(self, event):
        self.cancel_event = event

    def chat(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        raise AssertionError("overview intent wiring must not call the main Agent model")


class FakeIntentClient:
    def __init__(self, *, paths):
        self.paths = paths
        self.cancel_event = None
        self.calls = []

    def set_cancel_event(self, event):
        self.cancel_event = event

    def chat(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        return SimpleNamespace(
            content=json.dumps({"intent": "all", "project_names": []}),
            tool_calls=[],
        )


def make_session(paths: AgentPaths, conversation_id: str, project_id: str = "") -> Session:
    session_dir = paths.data / "sessions" / conversation_id
    session_dir.mkdir(parents=True, exist_ok=True)
    session = Session(
        conversation_id=conversation_id,
        session_dir=session_dir,
        goal="synthetic multi-project recognizer test",
        meta=SessionMeta(project_id=project_id),
        messages=[],
        paths=paths,
    )
    session.save()
    return session


def make_paths(tmp_path: Path) -> AgentPaths:
    root = tmp_path / "agent-root"
    root.mkdir()
    return AgentPaths(
        agent_root=root,
        workspace=root / "workspace",
        data=root / "data",
        evolve=root / "evolve",
    )


def install_offline_clients(monkeypatch, clients):
    main_models = []

    def make_main_model(*, paths):
        model = NoCallAgentModel(paths=paths)
        main_models.append(model)
        return model

    def make_intent_client(*, paths):
        client = FakeIntentClient(paths=paths)
        clients.append(client)
        return client

    monkeypatch.setattr(agent_module, "LLMClient", make_main_model)
    monkeypatch.setattr(llm_client, "LLMClient", make_intent_client)
    registry_entry = SimpleNamespace(
        id=multi_intent.MODEL_ID,
        vendor="0x567",
        provider_model="gpt-6-luna",
        max_input_tokens=100_000,
        chat_completions_url=lambda: "https://api-cdn.0x567.com/v1/chat/completions",
    )
    monkeypatch.setattr(multi_intent, "get_registry", lambda *_args: SimpleNamespace(
        get=lambda model_id: registry_entry if model_id == multi_intent.MODEL_ID else None,
    ))
    return main_models


def wait_and_respond(bridge: WsBridge, events: list[dict], choice: str):
    deadline = time.monotonic() + 3
    request = None
    while time.monotonic() < deadline:
        request = next((event for event in events if event.get("type") == "confirm.request"), None)
        if request is not None:
            break
        time.sleep(0.01)
    assert request is not None, "recognizer did not request exact outbound consent"
    assert bridge.deliver_confirm(request["request_id"], choice) is True
    return request


def invoke_recognizer(bridge, events, recognizer, text, choice):
    result_box = []
    error_box = []

    def run():
        try:
            result_box.append(recognizer(text))
        except BaseException as exc:
            error_box.append(exc)

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    request = wait_and_respond(bridge, events, choice)
    worker.join(timeout=3)
    assert not worker.is_alive()
    assert not error_box
    assert len(result_box) == 1
    return result_box[0], request


def test_factory_wires_exact_text_provider_bound_intent_and_consent_before_fake_client_call(tmp_path, monkeypatch):
    paths = make_paths(tmp_path)
    session = make_session(paths, "multi-intent-session")
    events: list[dict] = []
    bridge = WsBridge(emit=events.append, paths=paths, confirm_timeout=3)
    intent_clients = []
    main_models = install_offline_clients(monkeypatch, intent_clients)

    repl = server._build_repl(
        session,
        paths,
        bridge,
        work_record_controller_factory=None,
    )
    recognizer = getattr(repl.agent, "multi_project_overview_recognizer", None)
    assert isinstance(recognizer, multi_intent.MultiProjectLunaIntent)
    assert len(intent_clients) == 1
    intent_client = intent_clients[0]
    assert intent_client.cancel_event is repl.agent.cancel_event
    assert repl.agent.work_record_recall_controller._store._session is session

    candidate, request = invoke_recognizer(bridge, events, recognizer, CURRENT_TEXT, "y")
    preview = request["preview"]
    assert request["allow_approve_all"] is False
    assert "0x567" in preview and "gpt-6-luna" in preview
    assert CURRENT_TEXT in preview
    assert "\u610f\u56fe\u8bc6\u522b" in preview
    assert "\u4e0d\u53d1\u9001\u9879\u76ee\u8bb0\u5f55\u6e05\u5355" in preview
    assert "\u4e0d\u662f\u8bfb\u53d6\u3001\u8bbf\u95ee\u6216\u5199\u5165\u6388\u6743" in preview
    assert "project_sessions" not in preview
    assert "messages.jsonl" not in preview
    assert candidate.intent == "all"
    assert intent_client.calls
    assert intent_client.calls[0][0][1]["content"] == CURRENT_TEXT
    assert intent_client.calls[0][1]["tools"] == []
    assert intent_client.calls[0][1]["model"] == multi_intent.MODEL_ID
    assert all(model.calls == [] for model in main_models)


@pytest.mark.parametrize("choice", ["n", "cancelled", "yes"])
def test_refusal_cancel_or_nonexact_yes_never_sends_to_fake_client(tmp_path, monkeypatch, choice):
    paths = make_paths(tmp_path)
    session = make_session(paths, f"multi-intent-{choice}")
    events: list[dict] = []
    bridge = WsBridge(emit=events.append, paths=paths, confirm_timeout=3)
    intent_clients = []
    main_models = install_offline_clients(monkeypatch, intent_clients)
    repl = server._build_repl(session, paths, bridge)
    recognizer = repl.agent.multi_project_overview_recognizer

    candidate, request = invoke_recognizer(bridge, events, recognizer, CURRENT_TEXT, choice)
    assert request["allow_approve_all"] is False
    assert candidate.intent == "uncertain"
    assert intent_clients[0].calls == []
    assert all(model.calls == [] for model in main_models)


def test_agent_rebind_creates_new_recognizer_and_rebinds_client_cancel_event(tmp_path, monkeypatch):
    paths = make_paths(tmp_path)
    first_session = make_session(paths, "multi-rebind-a")
    second_session = make_session(paths, "multi-rebind-b")
    bridge = WsBridge(emit=lambda _event: None, paths=paths, confirm_timeout=3)
    intent_clients = []
    main_models = install_offline_clients(monkeypatch, intent_clients)
    repl = server._build_repl(first_session, paths, bridge)

    first_agent = repl.agent
    first_recognizer = first_agent.multi_project_overview_recognizer
    first_client = intent_clients[0]
    assert first_client.cancel_event is first_agent.cancel_event

    repl.session = second_session
    repl._rebind_agent()
    second_agent = repl.agent
    second_recognizer = second_agent.multi_project_overview_recognizer
    second_client = intent_clients[1]

    assert second_agent is not first_agent
    assert second_recognizer is not first_recognizer
    assert second_client is not first_client
    assert first_client.cancel_event is first_agent.cancel_event
    assert second_client.cancel_event is second_agent.cancel_event
    assert second_agent.work_record_recall_controller._store._session is second_session
    assert all(model.calls == [] for model in main_models)