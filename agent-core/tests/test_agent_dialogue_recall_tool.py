from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent import Agent
from llm_client import LLMResponse
from session import create_new
from tests.isolation_helpers import temporary_agent_paths


class _MainModel:
    def __init__(self, *, call_recall: bool):
        self.call_recall = call_recall
        self.calls = []

    def chat(self, messages, *, tools=None, **_kwargs):
        self.calls.append((messages, tools))
        if self.call_recall and len(self.calls) == 1:
            return LLMResponse(
                model="main-fixture", content=None,
                tool_calls=[{
                    "id": "recall-current-dialogue-1", "type": "function",
                    "function": {"name": "recall_current_dialogue", "arguments": "{}"},
                }],
                finish_reason="tool_calls", usage=None, raw={},
            )
        return LLMResponse(
            model="main-fixture", content="ordinary answer", tool_calls=[],
            finish_reason="stop", usage=None, raw={},
        )


@pytest.mark.parametrize("question", [
    "阈值最终定成什么？",
    "上一轮的结论是什么？",
])
def test_main_model_can_choose_bound_current_dialogue_recall_tool(question, monkeypatch):
    from dialogue_recall import DialogueRecallResult
    import dialogue_recall
    from work_records import WorkRecordStore
    from work_records_luna_recall import WorkRecordsLunaRecall
    from llm_client import LLMClient

    with temporary_agent_paths() as paths:
        session = create_new(paths)
        session.meta.turn_mode = "ask"
        session.append_message({"role": "user", "content": "我们决定阈值设为37。"})
        session.save()
        client = LLMClient(paths=paths)
        matcher = WorkRecordsLunaRecall(client)
        controller = SimpleNamespace(_store=WorkRecordStore(session), _semantic_matcher=matcher)
        model = _MainModel(call_recall=True)
        confirms = []
        agent = Agent.create(
            session, llm=model, confirm_fn=lambda preview, allow_all: confirms.append((preview, allow_all)) or "y",
            enable_work_record_recall=True, work_record_recall_controller=controller,
        )
        calls = []

        def recall_spy(session_arg, query, **kwargs):
            calls.append((session_arg, query, kwargs))
            assert session_arg is session
            assert query == question
            index = kwargs["exclude_message_index"]
            assert session.messages[index] == {"role": "user", "content": question}
            assert kwargs["current_session"]() is session
            assert callable(kwargs["confirm"])
            assert kwargs["confirm"]("exact history disclosure preview") is True
            return DialogueRecallResult("match", "回答：阈值设为37。")

        monkeypatch.setattr(dialogue_recall, "recall_current_dialogue", recall_spy)
        result = agent.run_turn(question, spawn_explore=False)

        assert result.finish_reason == "dialogue_recall_tool"
        assert "回答：阈值设为37。" in result.assistant_text
        assert "隐私提示" in result.assistant_text
        assert len(model.calls) == 1, "retrieved history must not be sent back to the main model"
        tool_names = {tool["function"]["name"] for tool in model.calls[0][1]}
        assert "recall_current_dialogue" in tool_names
        if question.startswith("上一轮"):
            assert tool_names == {"recall_current_dialogue"}, "read-only recall intent exposes only its recall tool"
        assert len(calls) == len(confirms) == 1
        assert confirms[0] == ("exact history disclosure preview", False)
        assert sum(message.get("role") == "user" and message.get("content") == question
                   for message in session.messages) == 1
        assert not any(message.get("role") == "tool" for message in session.messages)
        assert not any(message.get("role") == "assistant" and message.get("tool_calls")
                       for message in session.messages if message.get("tool_calls"))


def test_ordinary_qa_without_main_model_tool_choice_has_no_recall_confirmation():
    from work_records import WorkRecordStore
    from work_records_luna_recall import WorkRecordsLunaRecall
    from llm_client import LLMClient

    with temporary_agent_paths() as paths:
        session = create_new(paths)
        session.meta.turn_mode = "ask"
        controller = SimpleNamespace(
            _store=WorkRecordStore(session),
            _semantic_matcher=WorkRecordsLunaRecall(LLMClient(paths=paths)),
        )
        model = _MainModel(call_recall=False)
        confirmations = []
        agent = Agent.create(
            session, llm=model,
            confirm_fn=lambda preview, allow_all: confirmations.append((preview, allow_all)) or "y",
            enable_work_record_recall=True, work_record_recall_controller=controller,
        )

        result = agent.run_turn("解释一下二分查找", spawn_explore=False)

        assert result.assistant_text == "ordinary answer"
        assert len(model.calls) == 1
        assert confirmations == []

@pytest.mark.parametrize("choice", ["y", "n"])
def test_server_ws_main_model_recall_tool_uses_same_session_and_confirmation(
    tmp_path, monkeypatch, choice,
):
    """Actual server-built Agent + correlated WS consent; only synthetic provider transport."""
    import json
    import threading
    import time
    import httpx
    import llm_client
    import llm_models
    import work_records_luna_recall as luna
    from llm_models import ModelEntry, ModelRegistry
    from paths import AgentPaths
    from server import WsBridge, WsSessionHandler, _build_repl
    from session import Session, SessionMeta

    root = tmp_path / f"synthetic-ws-recall-{choice}"
    paths = AgentPaths(root, root / "workspace", root / "data", root / "evolve")
    for directory in (paths.workspace, paths.data, paths.evolve):
        directory.mkdir(parents=True, exist_ok=True)
    (paths.evolve / "_index.core.toml").write_text('[[topic]]\nid = "common"\n', encoding="utf-8")
    session_id = f"synthetic-session-{choice}"
    session_dir = paths.data / "sessions" / session_id
    session_dir.mkdir(parents=True)
    session = Session(session_id, session_dir, "synthetic only", SessionMeta(turn_mode="ask"), [], paths)
    session.save()
    fact = "Synthetic history: the final threshold was 37 units."
    session.append_message({"role": "user", "content": fact})
    session.save()

    entry = ModelEntry(
        id="0x567-flash", name="Synthetic Luna", vendor="0x567",
        base_url="https://synthetic.provider.invalid", provider_model="gpt-6-luna",
        api_key="synthetic-test-key", max_input_tokens=1_000_000,
    )
    registry = ModelRegistry(models=(entry,), default_flash_id=entry.id,
                             default_pro_id=entry.id, alias_map={})
    monkeypatch.setattr(ModelRegistry, "load", classmethod(lambda _cls, *_args, **_kwargs: registry))
    monkeypatch.setattr(luna, "get_registry", lambda *_args, **_kwargs: registry)
    monkeypatch.setattr(llm_client, "get_registry", lambda *_args, **_kwargs: registry)
    monkeypatch.setattr(llm_models, "get_registry", lambda *_args, **_kwargs: registry)

    provider_requests = []
    real_httpx_client = httpx.Client

    def mock_client(*args, **kwargs):
        def respond(request):
            provider_requests.append(request)
            payload = json.loads(request.content)
            candidates = json.loads(payload["messages"][1]["content"])["candidates"]
            selected = next(row["id"] for row in candidates if fact in row["excerpt"])
            return httpx.Response(200, json={"model": "gpt-6-luna", "choices": [{
                "index": 0, "finish_reason": "stop", "message": {
                    "role": "assistant",
                    "content": json.dumps({"status": "match", "candidate_ids": [selected]}),
                },
            }]}, request=request)
        return real_httpx_client(*args, transport=httpx.MockTransport(respond), **kwargs)

    monkeypatch.setattr(httpx, "Client", mock_client)
    events = []
    bridge = WsBridge(emit=events.append, paths=paths, confirm_timeout=2.0)
    repl = _build_repl(session, paths, bridge)
    main_model = _MainModel(call_recall=True)
    repl.agent.llm = main_model
    controller = repl.agent.work_record_recall_controller
    assert repl.agent._dialogue_recall_tool_enabled()
    assert controller._store._session is session

    question = "阈值最终定成什么？"
    outcome = []
    worker = threading.Thread(
        target=lambda: outcome.append(repl.agent.run_turn(question, spawn_explore=False)), daemon=True,
    )
    worker.start()
    deadline = time.monotonic() + 2.0
    request = None
    while time.monotonic() < deadline and worker.is_alive():
        request = next((event for event in events if event.get("type") == "confirm.request"), None)
        if request is not None:
            break
        time.sleep(0.005)
    assert request is not None, "server-built Agent did not request WS confirmation"
    assert request["allow_approve_all"] is False
    preview = json.loads(request["preview"])
    assert preview["session_id"] == session_id
    assert provider_requests == [], "semantic matcher must wait for WS consent"
    assert len(main_model.calls) == 1
    assert "recall_current_dialogue" in {
        tool["function"]["name"] for tool in main_model.calls[0][1]
    }

    handler = object.__new__(WsSessionHandler)
    handled = handler._dispatch_inline(
        {"type": "confirm.response", "request_id": request["request_id"], "choice": choice}, bridge,
    )
    assert handled is True
    worker.join(timeout=2.0)
    assert not worker.is_alive(), "WS confirmation did not release the Agent turn"
    assert len(outcome) == 1
    result = outcome[0]
    assert result.finish_reason == "dialogue_recall_tool"
    assert repl.agent.session is session
    assert controller._store._session is session
    assert sum(message.get("role") == "user" and message.get("content") == question
               for message in session.messages) == 1
    persisted = [json.loads(line) for line in session.messages_path.read_text(encoding="utf-8").splitlines()]
    assert sum(message.get("role") == "user" and message.get("content") == question
               for message in persisted) == 1
    assert not any(message.get("role") == "tool" for message in session.messages)
    assert not any(message.get("role") == "assistant" and message.get("tool_calls")
                   for message in session.messages)
    assert len(main_model.calls) == 1, "retrieved history must not be sent back to the main model"
    if choice == "y":
        assert len(provider_requests) == 1
        assert fact in result.assistant_text
    else:
        assert provider_requests == [], "refusal must prevent semantic outbound"
        assert fact not in result.assistant_text
        assert "\u672a\u83b7\u6388\u6743" in result.assistant_text


def test_current_dialogue_recall_answer_survives_tool_output_spill_threshold(tmp_path, monkeypatch):
    import dialogue_recall
    from dialogue_recall import DialogueRecallResult
    from work_records import WorkRecordStore
    from work_records_luna_recall import WorkRecordsLunaRecall
    from llm_client import LLMClient

    monkeypatch.setenv("TOOL_OUTPUT_SPILL_CHARS", "1")
    with temporary_agent_paths() as paths:
        session = create_new(paths)
        session.meta.turn_mode = "ask"
        session.append_message({"role": "user", "content": "Synthetic source fact: 37 units."})
        session.save()
        store = WorkRecordStore(session)
        matcher = WorkRecordsLunaRecall(LLMClient(paths=paths))
        controller = SimpleNamespace(_store=store, _semantic_matcher=matcher)
        model = _MainModel(call_recall=True)
        confirmation_count = []
        agent = Agent.create(
            session, llm=model,
            confirm_fn=lambda _preview, _allow_all: confirmation_count.append(True) or "y",
            enable_work_record_recall=True, work_record_recall_controller=controller,
        )
        answer = "SYNTHETIC-RECALL-ANSWER-" + ("x" * 1000)
        calls = []

        def recalled(session_arg, query, **kwargs):
            calls.append((session_arg, query))
            assert session_arg is session
            assert callable(kwargs["confirm"]) and kwargs["confirm"]("synthetic preview") is True
            return DialogueRecallResult("match", answer)

        monkeypatch.setattr(dialogue_recall, "recall_current_dialogue", recalled)
        result = agent.run_turn("What was the synthetic source fact?", spawn_explore=False)

        assert result.finish_reason == "dialogue_recall_tool"
        assert result.assistant_text.startswith(answer)
        assert calls == [(session, "What was the synthetic source fact?")]
        assert confirmation_count == [True]
        assert not list((session.session_dir / "tool_outputs").glob("*"))
        assert len(model.calls) == 1
