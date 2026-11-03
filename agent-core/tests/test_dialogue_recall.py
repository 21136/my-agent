from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import httpx

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

import dialogue_recall
import work_records_luna_recall as luna
import llm_client
from llm_client import LLMClient
from llm_models import ModelEntry, ModelRegistry
from paths import AgentPaths
from session import Session, SessionMeta


def make_session(tmp_path, messages, session_id="synthetic-current", root_name="agent-root"):
    root = tmp_path / root_name
    paths = AgentPaths(agent_root=root, workspace=root / "workspace", data=root / "data", evolve=root / "evolve")
    folder = paths.data / "sessions" / session_id
    folder.mkdir(parents=True)
    session = Session(session_id, folder, "", SessionMeta(), messages, paths)
    session.save()
    return session


_ACTIVE_TRANSPORT = None
_REAL_HTTPX_CLIENT = httpx.Client


def install_registry(monkeypatch, limit=1_000_000):
    global _ACTIVE_TRANSPORT
    entry = ModelEntry(
        id="0x567-flash", name="Synthetic Luna", vendor="0x567",
        base_url="https://synthetic.provider.example", provider_model="gpt-6-luna",
        api_key="synthetic-test-key", max_input_tokens=limit,
    )
    registry = ModelRegistry(models=(entry,), default_flash_id=entry.id,
                             default_pro_id=entry.id, alias_map={})
    monkeypatch.setattr(luna, "get_registry", lambda *_: registry)
    monkeypatch.setattr(ModelRegistry, "load", classmethod(lambda _cls, _paths=None: registry))
    state = {"selected_text": None, "calls": [], "on_request": None}
    _ACTIVE_TRANSPORT = state

    def response(request):
        payload = json.loads(request.content)
        messages = payload["messages"]
        state["calls"].append((messages, {
            "model": "0x567-flash", "tools": [], "temperature": payload["temperature"],
            "response_format": payload["response_format"],
        }))
        user_payload = json.loads(messages[1]["content"])
        selected = next((row["id"] for row in user_payload["candidates"]
                         if state["selected_text"] and state["selected_text"] in row["excerpt"]), None)
        result = {"status": "match", "candidate_ids": [selected]} if selected else {"status": "not_found", "candidate_ids": []}
        if callable(state["on_request"]):
            state["on_request"]()
        return httpx.Response(200, json={"model": "gpt-6-luna", "choices": [{
            "index": 0, "finish_reason": "stop",
            "message": {"role": "assistant", "content": json.dumps(result)},
        }]})

    def client_factory(**kwargs):
        return _REAL_HTTPX_CLIENT(transport=httpx.MockTransport(response), **kwargs)
    monkeypatch.setattr(llm_client.httpx, "Client", client_factory)


def SelectingClient(selected_text=None, *, paths, on_request=None):
    """Return the real client class; only its HTTPS transport is an isolated fixture."""
    if _ACTIVE_TRANSPORT is None:
        raise AssertionError("install_registry must initialize the isolated transport")
    _ACTIVE_TRANSPORT["selected_text"] = selected_text
    _ACTIVE_TRANSPORT["on_request"] = on_request
    client = LLMClient(paths=paths)
    client.calls = _ACTIVE_TRANSPORT["calls"]
    return client


def test_semantic_synonym_search_sends_all_raw_current_session_candidates_and_cites_exact_source(tmp_path, monkeypatch):
    install_registry(monkeypatch)
    session = make_session(tmp_path, [
        {"role": "user", "content": "The blue lantern test uses 37 samples."},
        {"role": "assistant", "content": "I will keep that threshold in mind."},
        {"role": "user", "content": "And the backup contact is Mira."},
    ])
    client = SelectingClient("37 samples", paths=session.paths)
    matcher = luna.WorkRecordsLunaRecall(client)
    previews = []

    result = dialogue_recall.recall_current_dialogue(
        session, "How large was the experiment again?", matcher=matcher,
        confirm=lambda preview: previews.append(preview) or True,
        current_session=lambda: session,
    )

    assert result.status == "match"
    assert "37 samples" in result.answer
    assert "当前对话消息序号 1" in result.answer
    assert len(previews) == len(client.calls) == 1
    consent = json.loads(previews[0])
    assert consent["session_id"] == "synthetic-current"
    assert consent["endpoint"] == "https://synthetic.provider.example/v1/chat/completions"
    exact = consent["provider_payload"]["messages"]
    assert exact == client.calls[0][0]
    sent = json.loads(exact[1]["content"])
    assert sent["question"] == "How large was the experiment again?"
    assert [row["excerpt"] for row in sent["candidates"]] == [m["content"] for m in session.messages]
    assert "synthetic-current" not in exact[1]["content"]
    assert client.calls[0][1]["model"] == "0x567-flash"
    assert client.calls[0][1]["tools"] == []


def test_refusal_never_sends_raw_candidates_and_empty_history_is_not_found(tmp_path, monkeypatch):
    install_registry(monkeypatch)
    session = make_session(tmp_path, [{"role": "user", "content": "secret synthetic text"}])
    client = SelectingClient("secret", paths=session.paths)
    previews = []
    denied = dialogue_recall.recall_current_dialogue(
        session, "find that", matcher=luna.WorkRecordsLunaRecall(client),
        confirm=lambda preview: previews.append(preview) or False, current_session=lambda: session,
    )
    assert denied.status == "denied" and client.calls == [] and len(previews) == 1

    empty = make_session(tmp_path, [], "synthetic-empty")
    missing = dialogue_recall.recall_current_dialogue(
        empty, "find that", matcher=luna.WorkRecordsLunaRecall(client),
        confirm=lambda _preview: pytest.fail("empty history requires no external confirmation"),
        current_session=lambda: empty,
    )
    assert missing.status == "not_found" and client.calls == []


def test_session_switch_during_confirmation_blocks_outbound_and_answer(tmp_path, monkeypatch):
    install_registry(monkeypatch)
    session = make_session(tmp_path, [{"role": "assistant", "content": "A synthetic unique phrase."}])
    other = make_session(tmp_path, [{"role": "assistant", "content": "Other session secret."}], "synthetic-other")
    client = SelectingClient("unique phrase", paths=session.paths)
    current = [session]
    result = dialogue_recall.recall_current_dialogue(
        session, "what was it?", matcher=luna.WorkRecordsLunaRecall(client),
        confirm=lambda _preview: current.__setitem__(0, other) or True,
        current_session=lambda: current[0],
    )
    assert result.status == "stale_session"
    assert client.calls == []
    assert "unique phrase" not in result.answer and "Other session secret" not in result.answer


def test_over_budget_reports_full_scope_without_truncation_or_confirmation(tmp_path, monkeypatch):
    install_registry(monkeypatch, limit=2_000)
    session = make_session(tmp_path, [{"role": "user", "content": "x" * 5000}])
    client = SelectingClient("x", paths=session.paths)
    result = dialogue_recall.recall_current_dialogue(
        session, "find it", matcher=luna.WorkRecordsLunaRecall(client),
        confirm=lambda _preview: pytest.fail("over-budget request must not use ordinary confirmation"),
        confirm_batch=lambda _preview: pytest.fail("unrepresentable request must not ask batch approval"),
        current_session=lambda: session,
    )
    assert result.status == "incomplete"
    assert "完整" in result.answer and "1 条" in result.answer
    assert client.calls == []


@pytest.mark.parametrize("question", [
    "之前讨论的阈值是多少？",
    "把当时提到的数字找出来",
    "我们聊过的备份联系人是谁？",
    "What was the threshold we mentioned earlier?",
    "\u4e4b\u524d\u9879\u76eeA\u6211\u4eec\u600e\u4e48\u8ba8\u8bba\u7684",
    "\u4e4b\u524d\u6587\u6863\u6211\u4eec\u8ba8\u8bba\u8fc7\u4ec0\u4e48\uff1f",
])
def test_semantic_dialogue_recall_intent_accepts_synonymous_questions(question):
    assert dialogue_recall.is_explicit_dialogue_recall(question)


@pytest.mark.parametrize("question", [
    "请创建一张新的工作卡",
    "继续实现之前讨论的方案",
    "今天天气怎么样？",
    "当时提到的方案继续实现",
])
def test_dialogue_recall_intent_does_not_capture_execution_or_unrelated_chat(question):
    assert not dialogue_recall.is_explicit_dialogue_recall(question)

def test_agent_routes_plain_dialogue_recall_before_normal_llm_and_work_record_narrow_gate(tmp_path, monkeypatch):
    install_registry(monkeypatch)
    from agent import Agent
    from work_records import WorkRecordStore
    from work_records_experiment import WorkRecordExperimentController

    session = make_session(tmp_path, [
        {"role": "user", "content": "Project A: the blue lantern test uses 37 samples."},
    ], "synthetic-agent")

    class NormalModel:
        def __init__(self):
            self.calls = []
        def chat(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            return SimpleNamespace(content="ordinary fallback", tool_calls=[])

    normal = NormalModel()
    agent = Agent.create(session, llm=normal, confirm_fn=lambda _p, _a: "y")
    client = SelectingClient("37 samples", paths=session.paths)
    store = WorkRecordStore(session)
    controller = WorkRecordExperimentController(
        agent=agent, store=store, recognizer=lambda *_: {"action": "none"},
        semantic_matcher=luna.WorkRecordsLunaRecall(client),
    )
    agent.work_record_recall_controller = controller
    agent.enable_work_record_recall = True
    question = "\u4e4b\u524d\u9879\u76eeA\u6211\u4eec\u600e\u4e48\u8ba8\u8bba\u7684"
    def confirm_before_send(*args):
        assert session.messages[-1] == {"role": "user", "content": question}
        assert json.loads(session.messages_path.read_text(encoding="utf-8").splitlines()[-1])["content"] == question
        return "y"
    agent.executor.confirm_fn = confirm_before_send

    result = agent.run_turn(question, spawn_explore=False)

    assert result.finish_reason == "dialogue_recall"
    assert "37 samples" in result.assistant_text
    assert normal.calls == [] and len(client.calls) == 1
    sent = json.loads(client.calls[0][0][1]["content"])
    assert all(row["excerpt"] != question for row in sent["candidates"])
    assert session.messages[-2]["content"] == question
    assert session.messages[-1]["role"] == "assistant"
    assert json.loads(session.messages_path.read_text(encoding="utf-8").splitlines()[-2])["content"] == question

def test_nonempty_no_match_is_reported_as_uncertain_not_as_proof_of_absence(tmp_path, monkeypatch):
    install_registry(monkeypatch)
    session = make_session(tmp_path, [{"role": "user", "content": "A synthetic private phrase."}], "synthetic-no-match")
    client = SelectingClient("no content can match", paths=session.paths)
    result = dialogue_recall.recall_current_dialogue(
        session, "what was the moon phase?", matcher=luna.WorkRecordsLunaRecall(client),
        confirm=lambda _preview: True, current_session=lambda: session,
    )
    assert result.status == "uncertain"
    assert "这不代表没有匹配" in result.answer
    assert "A synthetic private phrase" not in result.answer
    assert len(client.calls) == 1


def test_session_context_tail_load_is_expanded_to_complete_history_before_selection(tmp_path, monkeypatch):
    install_registry(monkeypatch)
    session = make_session(tmp_path, [
        {"role": "user", "content": "Oldest synthetic phrase: copper kite."},
        {"role": "assistant", "content": "I recorded that clue."},
        {"role": "user", "content": "Most recent synthetic line."},
    ], "synthetic-tail-load")
    partial = Session.load(session.paths, session.conversation_id, message_cap=1)
    assert len(partial.messages) == 1 and not partial._messages_fully_loaded
    client = SelectingClient("copper kite", paths=partial.paths)
    result = dialogue_recall.recall_current_dialogue(
        partial, "What was the very first clue?", matcher=luna.WorkRecordsLunaRecall(client),
        confirm=lambda _preview: True, current_session=lambda: partial,
    )
    assert result.status == "match" and "copper kite" in result.answer
    supplied = json.loads(client.calls[0][0][1]["content"])["candidates"]
    assert len(supplied) == 3
    assert supplied[0]["excerpt"].startswith("Oldest synthetic phrase")

def test_explicit_project_artifact_and_work_card_lookup_stay_on_existing_routes():
    from agent import _is_explicit_work_card_lookup

    assert not dialogue_recall.is_explicit_dialogue_recall("\u67e5\u4e00\u4e0b\u4e4b\u524d\u9879\u76eeA\u7684\u8bbe\u8ba1\u6587\u6863")
    assert not dialogue_recall.is_explicit_dialogue_recall("\u627e\u56de\u4e4b\u524d\u7684\u5de5\u4f5c\u5361")
    assert _is_explicit_work_card_lookup("\u627e\u56de\u4e4b\u524d\u7684\u5de5\u4f5c\u5361")
    assert not dialogue_recall.is_explicit_dialogue_recall("\u67e5\u627e\u4e4b\u524d\u7684\u9879\u76ee\u6587\u4ef6")

def test_tool_call_assistant_message_with_null_content_is_ignored_and_current_question_is_excluded(tmp_path, monkeypatch):
    install_registry(monkeypatch)
    question = "What number did we discuss earlier?"
    session = make_session(tmp_path, [
        {"role": "assistant", "content": None, "tool_calls": [{"id": "call-1"}]},
        {"role": "tool", "tool_call_id": "call-1", "content": "private tool payload"},
        {"role": "user", "content": "The source value was copper-91."},
        {"role": "user", "content": question},
    ], "synthetic-tool-call")
    client = SelectingClient("copper-91", paths=session.paths)
    result = dialogue_recall.recall_current_dialogue(
        session, question, matcher=luna.WorkRecordsLunaRecall(client),
        confirm=lambda _preview: True, current_session=lambda: session, exclude_message_index=3,
    )
    assert result.status == "match" and "copper-91" in result.answer
    payload = json.loads(client.calls[0][0][1]["content"])
    assert [row["excerpt"] for row in payload["candidates"]] == ["The source value was copper-91."]
    assert "private tool payload" not in client.calls[0][0][1]["content"]
    assert question not in [row["excerpt"] for row in payload["candidates"]]



def _append_drift(session):
    with session.messages_path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps({"role": "assistant", "content": "late disk-only change"}) + "\n")

def test_disk_message_change_during_confirmation_blocks_model_call(tmp_path, monkeypatch):
    install_registry(monkeypatch)
    session = make_session(tmp_path, [{"role": "user", "content": "Synthetic source phrase."}], "synthetic-disk-drift")
    client = SelectingClient("source phrase", paths=session.paths, on_request=lambda: _append_drift(session))

    def confirm(_preview):
        _append_drift(session)
        return True

    result = dialogue_recall.recall_current_dialogue(
        session, "what was the phrase?", matcher=luna.WorkRecordsLunaRecall(client),
        confirm=confirm, current_session=lambda: session,
    )
    assert result.status == "stale_session"
    assert "late disk-only change" not in result.answer
    assert client.calls == []


def test_disk_message_change_during_model_call_discards_result(tmp_path, monkeypatch):
    install_registry(monkeypatch)
    session = make_session(tmp_path, [{"role": "user", "content": "Synthetic match phrase."}], "synthetic-post-model-drift")

    client = SelectingClient("match phrase", paths=session.paths, on_request=lambda: _append_drift(session))
    result = dialogue_recall.recall_current_dialogue(
        session, "what phrase?", matcher=luna.WorkRecordsLunaRecall(client),
        confirm=lambda _preview: True, current_session=lambda: session,
    )
    assert result.status == "stale_session"
    assert "Synthetic match phrase" not in result.answer
    assert client.calls

def test_long_dialogue_has_no_small_candidate_count_cap_and_keeps_last_source(tmp_path, monkeypatch):
    install_registry(monkeypatch)
    rows = [{"role": "user", "content": f"Synthetic line {i:04d}: ordinary filler."} for i in range(999)]
    rows[-1]["content"] = "Synthetic final line contains the ultraviolet compass clue."
    session = make_session(tmp_path, rows, "synthetic-999-lines")
    client = SelectingClient("ultraviolet compass", paths=session.paths)
    result = dialogue_recall.recall_current_dialogue(
        session, "What was the unusual navigation clue?", matcher=luna.WorkRecordsLunaRecall(client),
        confirm=lambda _preview: True, current_session=lambda: session,
    )
    assert result.status == "match" and "ultraviolet compass" in result.answer
    sent = json.loads(client.calls[0][0][1]["content"])
    assert len(sent["candidates"]) == 999
    assert sent["candidates"][-1]["id"] == "msg-00000998"
    assert "消息序号 999" in result.answer

@pytest.mark.parametrize("mode", ["decline", "provider_failure", "interrupted"])
def test_dialogue_recall_query_is_durable_before_decline_failure_or_interruption(tmp_path, monkeypatch, mode):
    install_registry(monkeypatch)
    from agent import Agent
    from work_records import WorkRecordStore
    from work_records_experiment import WorkRecordExperimentController

    question = "刚才我们提到的样本数是多少？"
    session = make_session(tmp_path, [{"role": "assistant", "content": "Synthetic source count is 37."}], f"synthetic-{mode}")

    class NormalModel:
        def chat(self, *_args, **_kwargs):
            pytest.fail("explicit dialogue recall must bypass normal chat")

    def before_confirm(preview, allow_all):
        assert session.messages[-1] == {"role": "user", "content": question}
        assert json.loads(session.messages_path.read_text(encoding="utf-8").splitlines()[-1])["content"] == question
        body = json.loads(preview)["provider_payload"]
        sent = json.loads(body["messages"][1]["content"])["candidates"]
        assert all(row["excerpt"] != question for row in sent)
        assert allow_all is False
        if mode == "interrupted":
            raise KeyboardInterrupt("synthetic interruption")
        return "n" if mode == "decline" else "y"

    def provider_failure():
        raise RuntimeError("synthetic HTTP transport failure")

    client = SelectingClient("37", paths=session.paths,
                             on_request=provider_failure if mode == "provider_failure" else None)
    agent = Agent.create(session, llm=NormalModel(), confirm_fn=before_confirm)
    store = WorkRecordStore(session)
    agent.work_record_recall_controller = WorkRecordExperimentController(
        agent=agent, store=store, recognizer=lambda *_: {"action": "none"},
        semantic_matcher=luna.WorkRecordsLunaRecall(client),
    )
    agent.enable_work_record_recall = True

    if mode == "interrupted":
        with pytest.raises(KeyboardInterrupt):
            agent.run_turn(question, spawn_explore=False)
    else:
        result = agent.run_turn(question, spawn_explore=False)
        assert "private synthetic provider diagnostic" not in result.assistant_text
        if mode == "decline":
            assert "未获授权" in result.assistant_text
        else:
            assert "未能安全完成" in result.assistant_text
        assert session.messages[-1]["role"] == "assistant"
    disk_rows = [json.loads(line) for line in session.messages_path.read_text(encoding="utf-8").splitlines()]
    assert any(row == {"role": "user", "content": question} for row in disk_rows)


def test_dialogue_recall_requires_confirmed_json_client_and_never_falls_back_to_chat(tmp_path, monkeypatch):
    install_registry(monkeypatch)
    session = make_session(tmp_path, [{"role": "user", "content": "Synthetic source."}], "synthetic-no-confirm-api")
    calls = []
    client = SimpleNamespace(chat=lambda *args, **kwargs: calls.append((args, kwargs)))
    result = dialogue_recall.recall_current_dialogue(
        session, "what was the source?", matcher=luna.WorkRecordsLunaRecall(client),
        confirm=lambda _preview: pytest.fail("unsupported client must not ask consent"),
        current_session=lambda: session,
    )
    assert result.status == "unavailable"
    assert "paths 绑定无法核验" in result.answer
    assert calls == []

@pytest.mark.parametrize("kind", ["duck", "subclass", "method_override", "wrong_paths"])
def test_recall_rejects_untrusted_client_identity_and_path_bindings_before_consent(tmp_path, monkeypatch, kind):
    install_registry(monkeypatch)
    session = make_session(tmp_path, [{"role": "user", "content": "Synthetic protected source."}], f"synthetic-trust-{kind}")
    other = make_session(tmp_path, [], f"synthetic-trust-other-{kind}", root_name="other-agent-root")
    calls = []
    if kind == "duck":
        client = SimpleNamespace(paths=session.paths, chat=lambda *a, **k: calls.append((a, k)), chat_confirmed_json=lambda *a, **k: calls.append((a, k)))
    elif kind == "subclass":
        class WrappedClient(LLMClient):
            pass
        client = WrappedClient(paths=session.paths)
    elif kind == "wrong_paths":
        client = LLMClient(paths=other.paths)
    else:
        client = LLMClient(paths=session.paths)
        client.chat_confirmed_json = lambda *a, **k: calls.append((a, k))
    result = dialogue_recall.recall_current_dialogue(
        session, "what did we discuss earlier?", matcher=luna.WorkRecordsLunaRecall(client),
        confirm=lambda _preview: pytest.fail("untrusted client must not receive confirmation"),
        current_session=lambda: session,
    )
    assert result.status == "unavailable"
    assert "client 类型" in result.answer and calls == []

def test_recovered_incomplete_assistant_stream_is_cited_as_partial_without_rewriting_source(tmp_path, monkeypatch):
    install_registry(monkeypatch)
    partial = "The recovered answer stopped after the first part: threshold was 37"
    recovered = {
        "role": "assistant",
        "content": partial,
        "_host_internal": {
            "stream_incomplete": True,
            "stream_finish_reason": "process_interrupted",
            "stream_request_id": "0123456789abcdef0123456789abcdef",
            "stream_run_id": "synthetic-run",
        },
    }
    session = make_session(tmp_path, [
        {"role": "user", "content": "What was the full threshold answer?"},
        recovered,
    ], "synthetic-recovered-stream")
    original_disk_rows = [json.loads(line) for line in session.messages_path.read_text(encoding="utf-8").splitlines()]
    client = SelectingClient("threshold was 37", paths=session.paths)

    result = dialogue_recall.recall_current_dialogue(
        session, "What was the full threshold answer?", matcher=luna.WorkRecordsLunaRecall(client),
        confirm=lambda _preview: True, current_session=lambda: session, exclude_message_index=0,
    )

    assert result.status == "match"
    assert partial in result.answer
    assert "流式回复中断" in result.answer
    assert "不代表完整答复" in result.answer
    request_text = client.calls[0][0][1]["content"]
    assert "stream_request_id" not in request_text and "_host_internal" not in request_text
    sent = json.loads(request_text)["candidates"]
    assert [row["excerpt"] for row in sent] == [partial]
    assert [json.loads(line) for line in session.messages_path.read_text(encoding="utf-8").splitlines()] == original_disk_rows
    assert session.messages[1] == recovered

def _make_batched_dialogue_session(tmp_path, session_id="synthetic-batched-dialogue", *, question="What phrase did we discuss earlier?"):
    return make_session(tmp_path, [
        {"role": "assistant", "content": "Recovered source contains the violet compass clue. " + "a" * 3500,
         "_host_internal": {"stream_incomplete": True, "stream_finish_reason": "process_interrupted",
                            "stream_request_id": "0123456789abcdef0123456789abcdef", "stream_run_id": "synthetic-run"}},
        {"role": "user", "content": "Earlier user line. " + "b" * 3500},
        {"role": "assistant", "content": "Later unrelated line. " + "c" * 3500},
        {"role": "user", "content": question},
    ], session_id)


def test_over_budget_dialogue_batches_freeze_full_scope_once_and_keep_possible_hit_uncertain(tmp_path, monkeypatch):
    install_registry(monkeypatch, limit=500_000)
    from work_card_recall import _BATCH_SYSTEM
    question = "What phrase did we discuss earlier?"
    session = _make_batched_dialogue_session(tmp_path, question=question)
    original_rows = [json.loads(line) for line in session.messages_path.read_text(encoding="utf-8").splitlines()]
    client = SelectingClient("violet compass", paths=session.paths)
    matcher = luna.WorkRecordsLunaRecall(client, max_input_tokens=10_000)
    previews = []

    result = dialogue_recall.recall_current_dialogue(
        session, question, matcher=matcher,
        confirm=lambda _preview: pytest.fail("batch preview must use the dedicated session bridge"),
        confirm_batch=lambda preview: previews.append(preview) or True,
        current_session=lambda: session, exclude_message_index=3,
    )

    assert result.status == "uncertain"
    assert "violet compass" in result.answer
    assert "\u65e0\u6cd5\u786e\u8ba4\u552f\u4e00\u6765\u6e90" in result.answer
    assert "\u6d41\u5f0f\u56de\u590d\u4e2d\u65ad" in result.answer
    assert len(previews) == 1 and len(client.calls) > 1
    frozen = json.loads(previews[0])
    assert set(frozen) == {"model_id", "vendor", "endpoint", "session_id", "provider_payloads"}
    assert frozen["session_id"] == session.conversation_id
    assert frozen["endpoint"] == "https://synthetic.provider.example/v1/chat/completions"
    payloads = frozen["provider_payloads"]
    assert len(payloads) == len(client.calls)
    all_sent = []
    for payload, (messages, _options) in zip(payloads, client.calls):
        assert payload["model"] == "gpt-6-luna"
        assert messages[0]["content"] == _BATCH_SYSTEM
        body = json.loads(messages[1]["content"])
        assert body["question"] == question
        assert all("_host_internal" not in row for row in body["candidates"])
        all_sent.extend(body["candidates"])
    assert [row["id"] for row in all_sent] == ["msg-00000000", "msg-00000001", "msg-00000002"]
    assert [row["excerpt"] for row in all_sent] == [row["content"] for row in session.messages[:3]]
    assert all(row["excerpt"] != question for row in all_sent)
    assert [json.loads(line) for line in session.messages_path.read_text(encoding="utf-8").splitlines()] == original_rows


@pytest.mark.parametrize("drift", ["confirmation", "after_first_batch"])
def test_dialogue_batch_stops_on_snapshot_drift_without_claiming_search_complete(tmp_path, monkeypatch, drift):
    install_registry(monkeypatch, limit=500_000)
    question = "What phrase did we discuss earlier?"
    session = _make_batched_dialogue_session(tmp_path, f"synthetic-batch-drift-{drift}", question=question)
    client = SelectingClient("violet compass", paths=session.paths)
    matcher = luna.WorkRecordsLunaRecall(client, max_input_tokens=10_000)
    previews = []

    def change_history():
        session.append_message_durable({"role": "assistant", "content": "synthetic drift"})

    if drift == "after_first_batch":
        _ACTIVE_TRANSPORT["on_request"] = change_history
        confirm_batch = lambda preview: previews.append(preview) or True
    else:
        def confirm_batch(preview):
            previews.append(preview)
            change_history()
            return True

    result = dialogue_recall.recall_current_dialogue(
        session, question, matcher=matcher,
        confirm=lambda _preview: pytest.fail("batch must not use ordinary confirmation"),
        confirm_batch=confirm_batch, current_session=lambda: session, exclude_message_index=3,
    )

    assert previews
    if drift == "confirmation":
        assert result.status == "stale_session"
        assert client.calls == []
        assert "\u672a\u53d1\u9001" in result.answer
    else:
        assert result.status == "incomplete"
        assert len(client.calls) == 1
        assert "\u6279" in result.answer
        assert "\u53ef\u80fd\u5df2\u5916\u53d1" in result.answer
        assert "\u56de\u67e5\u672a\u5b8c\u6210" in result.answer


def test_dialogue_batch_rejects_overridden_confirmed_batch_method(tmp_path, monkeypatch):
    install_registry(monkeypatch, limit=500_000)
    session = _make_batched_dialogue_session(tmp_path, "synthetic-untrusted-batch-method")
    client = SelectingClient("violet compass", paths=session.paths)
    client.chat_confirmed_json_batch = lambda *_args, **_kwargs: pytest.fail("overridden batch method must not run")
    matcher = luna.WorkRecordsLunaRecall(client, max_input_tokens=10_000)

    result = dialogue_recall.recall_current_dialogue(
        session, "What phrase did we discuss earlier?", matcher=matcher,
        confirm=lambda _preview: pytest.fail("untrusted batch method must not use ordinary consent"),
        confirm_batch=lambda _preview: pytest.fail("untrusted batch method must not ask batch consent"),
        current_session=lambda: session, exclude_message_index=3,
    )

    assert result.status == "unavailable"
    assert client.calls == []


def test_dialogue_batch_limit_failure_reports_no_truncation_before_confirmation(tmp_path, monkeypatch):
    install_registry(monkeypatch, limit=10_000)
    session = _make_batched_dialogue_session(tmp_path, "synthetic-batch-limit")
    client = SelectingClient("violet compass", paths=session.paths)
    matcher = luna.WorkRecordsLunaRecall(client, max_input_tokens=10_000)
    monkeypatch.setattr(dialogue_recall, "_MAX_DIALOGUE_BATCH_COUNT", 1, raising=False)

    result = dialogue_recall.recall_current_dialogue(
        session, "What phrase did we discuss earlier?", matcher=matcher,
        confirm=lambda _preview: pytest.fail("over-limit scope must not use ordinary confirmation"),
        confirm_batch=lambda _preview: pytest.fail("over-limit scope must not ask approval"),
        current_session=lambda: session, exclude_message_index=3,
    )

    assert result.status == "incomplete"
    assert "\u672a\u622a\u65ad" in result.answer
    assert "1" in result.answer
    assert client.calls == []

def test_dialogue_batch_multiple_hits_across_chunks_remain_ambiguous(tmp_path, monkeypatch):
    install_registry(monkeypatch, limit=500_000)
    session = _make_batched_dialogue_session(tmp_path, "synthetic-batch-multiple-hits")
    session.messages[1]["content"] += " violet compass is also mentioned here."
    session.save()
    monkeypatch.setattr(dialogue_recall, "_MAX_DIALOGUE_BATCH_PAYLOAD_BYTES", 7000)
    client = SelectingClient("violet compass", paths=session.paths)
    matcher = luna.WorkRecordsLunaRecall(client, max_input_tokens=10_000)

    result = dialogue_recall.recall_current_dialogue(
        session, "What phrase did we discuss earlier?", matcher=matcher,
        confirm=lambda _preview: pytest.fail("batch must not use ordinary confirmation"),
        confirm_batch=lambda _preview: True,
        current_session=lambda: session, exclude_message_index=3,
    )

    assert result.status == "uncertain"
    assert "\u591a\u4e2a\u53ef\u80fd\u6765\u6e90" in result.answer
    assert "\u552f\u4e00\u539f\u6587" in result.answer
    assert len(client.calls) >= 2


def test_dialogue_batch_decline_sends_no_batch(tmp_path, monkeypatch):
    install_registry(monkeypatch, limit=500_000)
    session = _make_batched_dialogue_session(tmp_path, "synthetic-batch-decline")
    client = SelectingClient("violet compass", paths=session.paths)
    matcher = luna.WorkRecordsLunaRecall(client, max_input_tokens=10_000)
    previews = []

    result = dialogue_recall.recall_current_dialogue(
        session, "What phrase did we discuss earlier?", matcher=matcher,
        confirm=lambda _preview: pytest.fail("batch must not use ordinary confirmation"),
        confirm_batch=lambda preview: previews.append(preview) or False,
        current_session=lambda: session, exclude_message_index=3,
    )

    assert result.status == "denied"
    assert len(previews) == 1
    assert client.calls == []


def test_dialogue_batch_rejects_oversized_single_message_without_truncation(tmp_path, monkeypatch):
    install_registry(monkeypatch, limit=500_000)
    session = make_session(tmp_path, [
        {"role": "assistant", "content": "A" * 5000},
        {"role": "user", "content": "What was that?"},
    ], "synthetic-batch-single-message-limit")
    monkeypatch.setattr(dialogue_recall, "_MAX_DIALOGUE_BATCH_PAYLOAD_BYTES", 1024)
    client = SelectingClient("A", paths=session.paths)
    matcher = luna.WorkRecordsLunaRecall(client, max_input_tokens=4000)

    result = dialogue_recall.recall_current_dialogue(
        session, "What was that?", matcher=matcher,
        confirm=lambda _preview: pytest.fail("oversized exact message must not use ordinary confirmation"),
        confirm_batch=lambda _preview: pytest.fail("oversized exact message must not be approved"),
        current_session=lambda: session, exclude_message_index=1,
    )

    assert result.status == "incomplete"
    assert "\u5355\u6279\u9884\u7b97" in result.answer
    assert "\u672a\u622a\u65ad" in result.answer
    assert client.calls == []

def test_agent_routes_oversized_dialogue_preview_through_real_session_bound_batch_bridge(tmp_path, monkeypatch):
    install_registry(monkeypatch, limit=1_000_000)
    from agent import Agent
    from server import WsBridge, _patch_repl
    from work_records import WorkRecordStore
    from work_records_experiment import WorkRecordExperimentController

    question = "What did we discuss earlier?"
    rows = [
        {"role": "user", "content": ("first batch source: violet compass; " if index == 0 else f"ordinary line {index}: ") + "x" * 25_000}
        for index in range(32)
    ]
    session = make_session(tmp_path, rows, "synthetic-agent-large-dialogue-preview")

    class NormalModel:
        def chat(self, *_args, **_kwargs):
            pytest.fail("explicit dialogue recall must not fall through to ordinary chat")

    agent = Agent.create(session, llm=NormalModel(), confirm_fn=lambda *_: pytest.fail("batch must use the dedicated bridge"))
    client = SelectingClient("violet compass", paths=session.paths)
    matcher = luna.WorkRecordsLunaRecall(client, max_input_tokens=40_000)
    store = WorkRecordStore(session)
    agent.work_record_recall_controller = WorkRecordExperimentController(
        agent=agent, store=store, recognizer=lambda *_: {"action": "none"}, semantic_matcher=matcher,
    )
    agent.enable_work_record_recall = True

    events = []
    deliveries = []
    pending_at_emit = []
    bridge = WsBridge(emit=lambda _event: None, paths=session.paths)
    ordinary_confirms = []
    real_confirm = bridge.confirm_fn
    def ordinary_confirm(preview, allow_all):
        ordinary_confirms.append((preview, allow_all))
        return real_confirm(preview, allow_all)
    bridge.confirm_fn = ordinary_confirm
    repl = SimpleNamespace(session=session, agent=agent, _rebind_agent=lambda: None)
    _patch_repl(repl, bridge)

    def emit(event):
        events.append(event)
        if event.get("type") == "confirm.request":
            pending_at_emit.append(bridge._batch_confirm_pending is not None)
            deliveries.append(bridge.deliver_confirm(event["request_id"], "y"))
    bridge.emit = emit

    result = agent.run_turn(question, spawn_explore=False)

    confirmations = [event for event in events if event.get("type") == "confirm.request"]
    assert result.finish_reason == "dialogue_recall"
    assert len(confirmations) == 1, result.assistant_text
    assert pending_at_emit == [True] and deliveries == [True]
    assert ordinary_confirms == []
    assert confirmations[0]["allow_approve_all"] is False
    preview = confirmations[0]["preview"]
    assert 768 * 1024 < len(preview.encode("utf-8")) <= 2 * 1024 * 1024
    frozen = json.loads(preview)
    assert len(frozen["provider_payloads"]) == 32
    assert len(client.calls) == 32
    assert "violet compass" in result.assistant_text
    assert "\u65e0\u6cd5\u786e\u8ba4\u552f\u4e00\u6765\u6e90" in result.assistant_text
