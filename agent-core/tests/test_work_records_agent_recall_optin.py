from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

from agent import Agent
from llm_client import LLMResponse
from paths import AgentPaths
from session import Session, SessionMeta
from work_records import WorkRecordStore
from work_records_experiment import WorkRecordExperimentController
from work_records_semantic import SearchResult
import work_records_luna_recall as luna
from work_records_luna_recall import WorkRecordsLunaRecall


class FakeLLM:
    def __init__(self):
        self.calls = []

    def chat(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        return LLMResponse(model="fake", content="normal answer", tool_calls=[], finish_reason="stop", usage=None, raw={})


def make_agent(tmp_path: Path, *, recall_controller=None, confirm=None, enable_recall=False):
    root = tmp_path / "agent-root"
    (root / "data" / "sessions").mkdir(parents=True)
    paths = AgentPaths(agent_root=root, workspace=root / "workspace", data=root / "data", evolve=root / "evolve")
    session_dir = paths.data / "sessions" / "current-session"
    session_dir.mkdir(parents=True)
    session = Session(
        conversation_id="current-session", session_dir=session_dir, goal="",
        meta=SessionMeta(turn_mode="ask"), messages=[], paths=paths,
    )
    session.save()
    llm = FakeLLM()
    agent = Agent.create(session, llm=llm, confirm_fn=confirm)
    agent.enable_work_record_recall = enable_recall
    return agent, llm, session


def make_recall(agent, matcher):
    store = WorkRecordStore(agent.session)
    item = store.create_item(title="privacy synthetic task", scope={"kind": "independent"})
    store.add_requirement(item["item_id"], "synthetic requirement details", event_id="event-1", idempotency_key="test-requirement-1")
    controller = WorkRecordExperimentController(
        agent=agent, store=store,
        recognizer=lambda _question, _items: {"action": "recall"},
        semantic_matcher=matcher,
    )
    return controller, store


def test_default_agent_recall_is_unchanged_without_explicit_injection(tmp_path):
    agent, llm, session = make_agent(tmp_path)

    result = agent.run_turn("\u521a\u624d\u6211\u4eec\u8bf4\u4e86\u4ec0\u4e48", spawn_explore=False)

    assert result.assistant_text == "normal answer"
    assert len(llm.calls) == 1


def install_fake_luna(monkeypatch, response_builder):
    monkeypatch.setattr(luna, "get_registry", lambda *_args: SimpleNamespace(
        get=lambda model_id: SimpleNamespace(
            id=model_id, vendor="0x567", provider_model="gpt-6-luna", max_input_tokens=1_000_000,
            chat_completions_url=lambda: "https://synthetic.provider.example/v1/chat/completions",
        ) if model_id == "0x567-flash" else None,
    ))
    calls = []

    class Client:
        def chat_confirmed_json(self, messages, *, model_id, session_id, expected_preview_json, confirm_fn):
            from llm_client import LLMOutboundConfirmationError
            if confirm_fn(expected_preview_json, False) != "y":
                raise LLMOutboundConfirmationError("synthetic confirmation denied")
            kwargs = {"model": model_id, "tools": [], "temperature": 0.0,
                      "response_format": {"type": "json_object"}}
            calls.append((messages, kwargs, expected_preview_json, session_id))
            return SimpleNamespace(content=json.dumps(response_builder(messages)), tool_calls=[])
        def chat(self, messages, **kwargs):
            calls.append((messages, kwargs))
            return SimpleNamespace(content=json.dumps(response_builder(messages)), tool_calls=[])

    return WorkRecordsLunaRecall(Client()), calls


def selected_first(messages):
    payload = json.loads(messages[1]["content"])
    return {"status": "match", "candidate_ids": [payload["candidates"][0]["id"]]}


def test_opted_in_authorized_recall_confirms_exact_scope_and_persists_once(tmp_path, monkeypatch):
    matcher, model_calls = install_fake_luna(monkeypatch, selected_first)
    agent, llm, session = make_agent(tmp_path, enable_recall=True)
    controller, store = make_recall(agent, matcher)
    confirmations = []
    agent.work_record_recall_controller = controller
    agent.executor.confirm_fn = lambda preview, allow: confirmations.append((preview, allow)) or "y"
    before = store.list_items()

    question = "\u67e5\u4e00\u4e0b\u4e4b\u524d\u90a3\u6761\u5de5\u4f5c\u8bb0\u5f55\u8bf4\u4e86\u4ec0\u4e48"
    result = agent.run_turn(question, spawn_explore=False)

    assert result.turn_intent == "recall" and result.tool_rounds == 0
    assert "synthetic requirement details" in result.assistant_text
    assert len(confirmations) == 1
    preview, allow_approve_all = confirmations[0]
    assert allow_approve_all is False
    disclosure = json.loads(preview)
    assert disclosure["target_session_id"] == "current-session"
    assert disclosure["candidate_disclosure"] == [{
        "id": before[0]["item_id"], "title": "privacy synthetic task",
        "requirements": ["synthetic requirement details"],
    }]
    exact_payload = json.loads(disclosure["exact_provider_messages"][1]["content"])
    assert exact_payload["question"] == question
    assert len(model_calls) == 1
    assert disclosure["exact_provider_messages"] == model_calls[0][0]
    assert llm.calls == []
    assert store.list_items() == before
    assert [m["role"] for m in session.messages] == ["user", "assistant"]
    lines = session.messages_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert [json.loads(line)["role"] for line in lines] == ["user", "assistant"]

def test_default_json_excerpt_confirmation_matches_exact_provider_payload_with_escaped_text(tmp_path, monkeypatch):
    matcher, model_calls = install_fake_luna(monkeypatch, selected_first)
    agent, llm, _session = make_agent(tmp_path, confirm=lambda _preview, _allow: 'y', enable_recall=True)
    store = WorkRecordStore(agent.session)
    item = store.create_item(title='synthetic quoted task', scope={'kind': 'independent'})
    text = 'quote: "keep"\nnext line\\folder'
    store.add_requirement(item['item_id'], text, event_id='escaped-agent-requirement')
    controller = WorkRecordExperimentController(
        agent=agent, store=store, recognizer=lambda *_: {'action': 'recall'},
        semantic_matcher=matcher,
    )
    agent.work_record_recall_controller = controller
    confirmations = []
    agent.executor.confirm_fn = lambda preview, allow: confirmations.append((preview, allow)) or 'y'

    question = '查一下之前那条工作记录的要求细节'
    result = agent.run_turn(question, spawn_explore=False)

    assert result.finish_reason == 'work_record_recall'
    assert len(confirmations) == 1 and confirmations[0][1] is False
    disclosure = json.loads(confirmations[0][0])
    assert disclosure['candidate_disclosure'][0]['requirements'] == [text]
    exact_messages = disclosure['exact_provider_messages']
    provider_payload = json.loads(exact_messages[1]['content'])
    assert provider_payload['question'] == question
    assert provider_payload['candidates'][0]['excerpt'] == json.dumps(
        {'title': 'synthetic quoted task', 'requirements': [text]}, ensure_ascii=False, separators=(',', ':'),
    )
    assert len(model_calls) == 1 and model_calls[0][0] == exact_messages
    assert llm.calls == []


def test_missing_or_refused_consent_does_not_call_matcher_or_llm(tmp_path, monkeypatch):
    for confirm in (None, lambda _preview, _allow: "n"):
        case = tmp_path / ("missing" if confirm is None else "refused")
        case.mkdir()
        matcher, model_calls = install_fake_luna(monkeypatch, selected_first)
        agent, llm, session = make_agent(case, confirm=confirm, enable_recall=True)
        controller, _store = make_recall(agent, matcher)
        agent.work_record_recall_controller = controller

        result = agent.run_turn("\u67e5\u4e00\u4e0b\u4e4b\u524d\u90a3\u6761\u5de5\u4f5c\u8bb0\u5f55\u8bf4\u4e86\u4ec0\u4e48", spawn_explore=False)

        assert model_calls == []
        assert llm.calls == []
        assert result.tool_rounds == 0
        assert len(session.messages) == 2
        assert "\u786e\u8ba4" in result.assistant_text or "\u6388\u6743" in result.assistant_text

@pytest.mark.parametrize("question", ["刚才我们说了什么", "刚才我们聊到哪了？"])
def test_recent_chat_recap_refuses_untrusted_recall_client_without_reading_work_records(tmp_path, monkeypatch, question):
    matcher, model_calls = install_fake_luna(monkeypatch, selected_first)
    agent, llm, session = make_agent(tmp_path, confirm=lambda _preview, _allow: "y", enable_recall=True)
    source = "The synthetic lighthouse code is amber-37."
    session.append_message({"role": "user", "content": source})
    controller, store = make_recall(agent, matcher)
    agent.work_record_recall_controller = controller
    monkeypatch.setattr(store, "list_items", lambda: pytest.fail("dialogue recall must not read work records"))
    confirmations = []
    agent.executor.confirm_fn = lambda *args: confirmations.append(args) or "y"

    result = agent.run_turn(question, spawn_explore=False)

    assert result.finish_reason == "dialogue_recall"
    assert "client 类型" in result.assistant_text and "paths 绑定" in result.assistant_text
    assert llm.calls == [] and model_calls == [] and confirmations == []
    assert json.loads(session.messages_path.read_text(encoding="utf-8").splitlines()[-2])["content"] == question

def test_explicit_previous_project_lookup_routes_to_work_record_recall(tmp_path, monkeypatch):
    matcher, model_calls = install_fake_luna(monkeypatch, selected_first)
    agent, llm, _session = make_agent(tmp_path, confirm=lambda _preview, _allow: "y", enable_recall=True)
    controller, _store = make_recall(agent, matcher)
    agent.work_record_recall_controller = controller

    result = agent.run_turn("\u4e4b\u524d\u9879\u76eeA\u505a\u4e86\u4ec0\u4e48\uff1f", spawn_explore=False)

    assert result.finish_reason == "work_record_recall"
    assert len(model_calls) == 1
    assert llm.calls == []


def test_english_zeta_mission_log_recap_routes_to_synthetic_work_record(tmp_path, monkeypatch):
    matcher, model_calls = install_fake_luna(monkeypatch, selected_first)
    agent, llm, _session = make_agent(tmp_path, confirm=lambda _preview, _allow: "y", enable_recall=True)
    store = WorkRecordStore(agent.session)
    item = store.create_item(title="Synthetic mission log Zeta-41", scope={"kind": "independent"})
    store.add_requirement(item["item_id"], "Include two fictional observations.", event_id="zeta-1", idempotency_key="zeta-req-1")
    store.add_requirement(item["item_id"], "Include an acceptance note.", event_id="zeta-2", idempotency_key="zeta-req-2")
    controller = WorkRecordExperimentController(
        agent=agent, store=store,
        recognizer=lambda _question, _items: {"action": "recall"},
        semantic_matcher=matcher,
        semantic_excerpt_policy=lambda title, requirements: "\n".join((title, *requirements)),
    )
    agent.work_record_recall_controller = controller
    question = "Recap the requirements recorded for the synthetic Zeta-41 mission-log item."

    result = agent.run_turn(question, spawn_explore=False)

    assert result.finish_reason == "work_record_recall"
    assert "Include two fictional observations." in result.assistant_text
    assert "Include an acceptance note." in result.assistant_text
    assert len(model_calls) == 1 and llm.calls == []
    sent = json.loads(model_calls[0][0][1]["content"])
    assert sent["question"] == question
    assert sent["candidates"][0]["title"] == "Synthetic mission log Zeta-41"


def test_historical_project_lookup_with_execution_request_keeps_original_route(tmp_path, monkeypatch):
    agent, llm, _session = make_agent(tmp_path, enable_recall=True)
    controller, store = make_recall(agent, SimpleNamespace(match=lambda *_: pytest.fail("execution request must not query records")))
    agent.work_record_recall_controller = controller
    monkeypatch.setattr(store, "list_items", lambda: pytest.fail("execution request must not read work records"))

    result = agent.run_turn("之前项目A做了什么？请继续实现", spawn_explore=False)

    assert result.assistant_text == "normal answer"
    assert len(llm.calls) == 1


def test_historical_project_document_lookup_is_not_misclassified_as_record_recall(tmp_path, monkeypatch):
    agent, llm, _session = make_agent(tmp_path, enable_recall=True)
    controller, store = make_recall(agent, SimpleNamespace(match=lambda *_: pytest.fail("document lookup must not query records")))
    agent.work_record_recall_controller = controller
    monkeypatch.setattr(store, "list_items", lambda: pytest.fail("document lookup must not read work records"))

    result = agent.run_turn("查一下之前项目A的设计文档", spawn_explore=False)

    assert result.assistant_text == "normal answer"
    assert len(llm.calls) == 1


def test_unrelated_turn_never_reads_records_or_calls_confirmation(tmp_path, monkeypatch):
    agent, llm, _session = make_agent(tmp_path)
    controller, store = make_recall(agent, SimpleNamespace(match=lambda *_: pytest.fail("must not match")))
    agent.work_record_recall_controller = controller
    monkeypatch.setattr(store, "list_items", lambda: pytest.fail("must not read records"))

    result = agent.run_turn("\u4eca\u5929\u5929\u6c14\u600e\u4e48\u6837", spawn_explore=False)

    assert result.assistant_text == "normal answer"
    assert len(llm.calls) == 1


def test_model_no_result_is_uncertain_not_a_claim_of_no_history(tmp_path, monkeypatch):
    matcher, model_calls = install_fake_luna(monkeypatch, lambda _messages: {"status": "not_found", "candidate_ids": []})
    agent, llm, _session = make_agent(tmp_path, confirm=lambda _preview, _allow: "y", enable_recall=True)
    controller, _store = make_recall(agent, matcher)
    agent.work_record_recall_controller = controller

    result = agent.run_turn("\u67e5\u4e00\u4e0b\u4e4b\u524d\u90a3\u6761\u5de5\u4f5c\u8bb0\u5f55\u8bf4\u4e86\u4ec0\u4e48", spawn_explore=False)

    assert "\u8fd9\u4e0d\u4ee3\u8868\u76f8\u5173\u5de5\u4f5c\u6ca1\u6709\u53d1\u751f" in result.assistant_text
    assert model_calls and llm.calls == []


def test_record_change_during_confirmation_aborts_without_model_request(tmp_path, monkeypatch):
    matcher, model_calls = install_fake_luna(monkeypatch, selected_first)
    agent, llm, _session = make_agent(tmp_path, enable_recall=True)
    controller, store = make_recall(agent, matcher)

    def mutate_then_approve(_preview, _allow):
        store.create_item(title="changed after preview", scope={"kind": "independent"})
        return "y"

    agent.executor.confirm_fn = mutate_then_approve
    agent.work_record_recall_controller = controller

    result = agent.run_turn("\u67e5\u4e00\u4e0b\u4e4b\u524d\u90a3\u6761\u5de5\u4f5c\u8bb0\u5f55\u8bf4\u4e86\u4ec0\u4e48", spawn_explore=False)

    assert "\u8bb0\u5f55\u53d1\u751f\u53d8\u5316" in result.assistant_text
    assert model_calls == []
    assert llm.calls == []


def test_session_change_during_confirmation_aborts_and_saves_to_original_session(tmp_path, monkeypatch):
    matcher, model_calls = install_fake_luna(monkeypatch, selected_first)
    agent, llm, original_session = make_agent(tmp_path, enable_recall=True)
    controller, _store = make_recall(agent, matcher)
    agent.work_record_recall_controller = controller

    def switch_session(_preview, _allow):
        agent.session = SimpleNamespace(conversation_id="other-session")
        return "y"

    agent.executor.confirm_fn = switch_session
    result = agent.run_turn("\u67e5\u4e00\u4e0b\u4e4b\u524d\u90a3\u6761\u5de5\u4f5c\u8bb0\u5f55\u8bf4\u4e86\u4ec0\u4e48", spawn_explore=False)

    assert "\u4f1a\u8bdd\u7ed1\u5b9a\u53d1\u751f\u53d8\u5316" in result.assistant_text
    assert model_calls == [] and llm.calls == []
    assert [message["role"] for message in original_session.messages] == ["user", "assistant"]
    assert len(original_session.messages_path.read_text(encoding="utf-8").splitlines()) == 2


LIVE_AGENT_RECALL_OPT_IN = "MY_AGENT_LIVE_AGENT_RECALL_OPT_IN"


@pytest.mark.skipif(
    os.environ.get(LIVE_AGENT_RECALL_OPT_IN) != "1",
    reason=f"real GPT-6 Luna Agent recall is opt-in; set {LIVE_AGENT_RECALL_OPT_IN}=1 explicitly",
)
def test_live_luna_recall_through_opted_in_agent_run_turn(tmp_path):
    """Live provider acceptance over synthetic records only; never run by default."""
    from llm_client import LLMClient, load_config
    from llm_models import get_registry
    from paths import AgentPaths
    from session import create_new

    from work_records_luna_recall import EXPECTED_PROVIDER_MODEL, MODEL_ID, WorkRecordsLunaRecall

    runtime_paths = AgentPaths.discover(start=Path(__file__).resolve())
    entry = get_registry(runtime_paths).get(MODEL_ID)
    if (entry is None or entry.id != "0x567-flash" or entry.vendor != "0x567"
            or entry.provider_model != EXPECTED_PROVIDER_MODEL or EXPECTED_PROVIDER_MODEL != "gpt-6-luna"):
        pytest.fail("sandbox registry does not pin 0x567-flash to the expected GPT-6 Luna upstream")
    if not entry.resolve_api_key(runtime_paths):
        pytest.skip("0x567-flash credential unavailable; no live request made")

    root = tmp_path / "synthetic-live-agent"
    root.mkdir()
    temp_paths = AgentPaths(
        agent_root=root,
        workspace=root / "workspace",
        data=root / "data",
        evolve=root / "evolve",
    )
    session = create_new(temp_paths, conversation_id="synthetic-live-recall-session")
    store = WorkRecordStore(session)
    stored = store.create_item(
        title="虚构海棠组演示安排",
        scope={"kind": "independent"},
    )
    requirement_text = "演示从周二改到周四下午，地点是虚构的海棠室。"
    store.add_requirement(
        stored["item_id"], requirement_text,
        event_id="synthetic-live-recall-requirement",
    )

    class _ForbiddenAgentModel:
        """The ordinary Agent model must never be reached by recall routing."""
        def __init__(self):
            self.calls = []

        def chat(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            raise AssertionError("recall branch must not call the normal Agent model")

    live_client = LLMClient(load_config(), paths=runtime_paths)

    class _ObservedProviderClient:
        def __init__(self, delegate):
            self.delegate = delegate
            self.paths = runtime_paths
            self.calls = []
            self.responses = []

        def chat(self, messages, **kwargs):
            self.calls.append((messages, kwargs))
            response = self.delegate.chat(messages, **kwargs)
            self.responses.append(response)
            return response

    provider_client = _ObservedProviderClient(live_client)
    matcher = WorkRecordsLunaRecall(provider_client)
    forbidden_agent_model = _ForbiddenAgentModel()
    approved_previews = []

    def confirm_fn(preview, allow_approve_all):
        assert allow_approve_all is False
        disclosure = json.loads(preview)
        assert disclosure["target_session_id"] == session.conversation_id
        assert disclosure["candidate_disclosure"] == [{
            "id": stored["item_id"],
            "title": "虚构海棠组演示安排",
            "requirements": [requirement_text],
        }]
        messages = disclosure["exact_provider_messages"]
        assert len(messages) == 2 and messages[0]["role"] == "system" and messages[1]["role"] == "user"
        outbound = messages[1]["content"]
        assert str(root) not in outbound
        assert session.conversation_id not in outbound
        payload = json.loads(outbound)
        assert payload["question"] == "刚才说的那个演示后来改到哪天？"
        assert payload["candidates"] == [{
            "id": stored["item_id"],
            "title": "虚构海棠组演示安排",
            "excerpt": requirement_text,
        }]
        approved_previews.append(disclosure)
        return "y"  # Test-only approval; no UI or live user-data confirmation.

    agent = Agent.create(
        session,
        llm=forbidden_agent_model,
        confirm_fn=confirm_fn,
        enable_work_record_recall=True,
    )
    agent.work_record_recall_controller = WorkRecordExperimentController(
        agent=agent,
        store=store,
        recognizer=lambda _question, _items: {"action": "recall"},
        semantic_matcher=matcher,
        semantic_excerpt_policy=lambda _title, requirements: "\n".join(requirements),
    )

    result = agent.run_turn("刚才说的那个演示后来改到哪天？", spawn_explore=False)

    assert result.turn_intent == "recall" and result.tool_rounds == 0
    assert "虚构海棠组演示安排" in result.assistant_text
    assert requirement_text in result.assistant_text
    assert len(approved_previews) == 1
    assert len(provider_client.calls) == 1 and len(provider_client.responses) == 1
    assert forbidden_agent_model.calls == []

    approved_messages = approved_previews[0]["exact_provider_messages"]
    sent_messages, sent_kwargs = provider_client.calls[0]
    assert sent_messages == approved_messages
    assert sent_kwargs == {
        "model": "0x567-flash",
        "tools": [],
        "temperature": 0.0,
        "response_format": {"type": "json_object"},
    }
    serialized_request = json.dumps(sent_messages, ensure_ascii=False)
    assert str(root) not in serialized_request
    assert str(temp_paths.agent_root) not in serialized_request
    assert session.conversation_id not in serialized_request

    response = provider_client.responses[0]
    assert response.model == "gpt-6-luna"
    assert response.raw.get("model") == "gpt-6-luna"
    assert response.content and response.content.strip()
    assert response.tool_calls == []
    upstream_result = json.loads(response.content)
    assert upstream_result == {"status": "match", "candidate_ids": [stored["item_id"]]}
    assert len(session.messages) == 2
    assert [message["role"] for message in session.messages] == ["user", "assistant"]
    assert store.list_items()[0]["requirements"][0]["text"] == requirement_text
