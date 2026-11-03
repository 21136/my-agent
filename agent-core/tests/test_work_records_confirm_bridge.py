from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

import agent as agent_module
import work_records_luna_recall as luna
from agent import Agent
from paths import AgentPaths
from server import WsBridge, WsSessionHandler
from session import Session, SessionMeta
from work_records import WorkRecordStore
from work_records_experiment import WorkRecordExperimentController
from work_records_luna_recall import MODEL_ID, WorkRecordsLunaRecall

QUESTION = "查一下之前那条工作记录说了什么"
RECORD_TITLE = "synthetic private work record"
REQUIREMENT = "synthetic detail that must not leave without approval"


class _ForbiddenAgentModel:
    def __init__(self) -> None:
        self.calls: list[Any] = []

    def chat(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append((args, kwargs))
        raise AssertionError("recall must not call the ordinary Agent model")


class _FakeRecallProvider:
    """In-memory provider double; no credentials, sockets, or network access."""

    def __init__(self) -> None:
        self.calls: list[tuple[list[dict[str, str]], dict[str, Any]]] = []

    def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> Any:
        self.calls.append((messages, kwargs))
        payload = json.loads(messages[1]["content"])
        candidate_id = payload["candidates"][0]["id"]
        return SimpleNamespace(
            content=json.dumps({"status": "match", "candidate_ids": [candidate_id]}),
            tool_calls=[],
        )


def _make_case(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / "synthetic-agent-root"
    paths = AgentPaths(
        agent_root=root,
        workspace=root / "workspace",
        data=root / "data",
        evolve=root / "evolve",
    )
    session_dir = paths.data / "sessions" / "synthetic-current-session"
    session_dir.mkdir(parents=True)
    session = Session(
        conversation_id="synthetic-current-session",
        session_dir=session_dir,
        goal="",
        meta=SessionMeta(turn_mode="ask"),
        messages=[],
        paths=paths,
    )
    session.save()

    events: list[dict[str, Any]] = []
    bridge = WsBridge(emit=events.append, paths=paths, confirm_timeout=2.0)
    ordinary_model = _ForbiddenAgentModel()
    agent = Agent.create(
        session,
        llm=ordinary_model,
        confirm_fn=bridge.confirm_fn,
        enable_work_record_recall=True,
    )
    store = WorkRecordStore(session)
    item = store.create_item(title=RECORD_TITLE, scope={"kind": "independent"})
    store.add_requirement(item["item_id"], REQUIREMENT, event_id="synthetic-event")

    provider = _FakeRecallProvider()
    matcher = WorkRecordsLunaRecall(provider)
    registry_entry = SimpleNamespace(
        id=MODEL_ID,
        vendor="0x567",
        provider_model="gpt-6-luna",
        max_input_tokens=100_000,
    )
    monkeypatch.setattr(luna, "get_registry", lambda *_args: SimpleNamespace(
        get=lambda model_id: registry_entry if model_id == MODEL_ID else None,
    ))
    agent.work_record_recall_controller = WorkRecordExperimentController(
        agent=agent,
        store=store,
        recognizer=lambda _question, _items: {"action": "recall"},
        semantic_matcher=matcher,
        semantic_excerpt_policy=lambda _title, requirements: "\n".join(requirements),
    )
    handler = object.__new__(WsSessionHandler)
    return agent, ordinary_model, provider, bridge, events, handler, paths


def _start_turn(agent: Agent) -> tuple[threading.Thread, list[Any]]:
    outcome: list[Any] = []

    def run() -> None:
        try:
            outcome.append(agent.run_turn(QUESTION, spawn_explore=False))
        except BaseException as exc:  # surface worker failures in the test thread
            outcome.append(exc)

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    return worker, outcome


def _wait_for_request(events: list[dict[str, Any]], worker: threading.Thread) -> dict[str, Any]:
    deadline = time.monotonic() + 1.5
    while time.monotonic() < deadline:
        request = next((event for event in events if event.get("type") == "confirm.request"), None)
        if request is not None:
            return request
        if not worker.is_alive():
            break
        time.sleep(0.005)
    pytest.fail("Agent did not issue the expected confirmation request")


def _deliver(handler: WsSessionHandler, bridge: WsBridge, request_id: str, choice: str) -> None:
    handled = handler._dispatch_inline(
        {"type": "confirm.response", "request_id": request_id, "choice": choice},
        bridge,
    )
    assert handled is True


def _finish(worker: threading.Thread, outcome: list[Any]) -> Any:
    worker.join(timeout=1.5)
    assert not worker.is_alive(), "confirmation did not release the synthetic Agent turn"
    assert len(outcome) == 1 and not isinstance(outcome[0], BaseException), outcome
    return outcome[0]


def _assert_no_non_preview_message_leak(
    events: list[dict[str, Any]], provider_messages: list[dict[str, str]],
) -> None:
    serialized_messages = json.dumps(provider_messages, ensure_ascii=False)
    non_preview_events = [event for event in events if event.get("type") != "confirm.request"]
    assert all(serialized_messages not in json.dumps(event, ensure_ascii=False) for event in non_preview_events)
    done_events = [event for event in events if event.get("type") == "confirm.done"]
    assert done_events
    assert all("preview" not in event and "content" not in event for event in done_events)


def test_bridge_approval_message_path_composes_with_agent_recall(tmp_path, monkeypatch):
    agent, ordinary_model, provider, bridge, events, handler, paths = _make_case(tmp_path, monkeypatch)
    worker, outcome = _start_turn(agent)
    request = _wait_for_request(events, worker)

    assert request["request_id"]
    assert request["allow_approve_all"] is False
    preview = json.loads(request["preview"])
    assert preview["target_session_id"] == "synthetic-current-session"
    assert preview["candidate_disclosure"][0]["title"] == RECORD_TITLE
    assert preview["exact_provider_messages"][1]["content"].find(REQUIREMENT) >= 0
    assert str(paths.agent_root) not in json.dumps(preview["exact_provider_messages"], ensure_ascii=False)
    assert "synthetic-current-session" not in json.dumps(preview["exact_provider_messages"], ensure_ascii=False)

    _deliver(handler, bridge, request["request_id"], "y")
    result = _finish(worker, outcome)

    assert REQUIREMENT in result.assistant_text
    assert len(provider.calls) == 1
    sent_messages, sent_kwargs = provider.calls[0]
    assert sent_messages == preview["exact_provider_messages"]
    assert sent_kwargs == {
        "model": MODEL_ID,
        "tools": [],
        "temperature": 0.0,
        "response_format": {"type": "json_object"},
    }
    assert ordinary_model.calls == []
    _assert_no_non_preview_message_leak(events, preview["exact_provider_messages"])


def test_bridge_rejection_does_not_send_recall_messages(tmp_path, monkeypatch):
    agent, ordinary_model, provider, bridge, events, handler, _paths = _make_case(tmp_path, monkeypatch)
    worker, outcome = _start_turn(agent)
    request = _wait_for_request(events, worker)

    _deliver(handler, bridge, request["request_id"], "n")
    result = _finish(worker, outcome)

    assert "未获外发授权" in result.assistant_text
    assert provider.calls == []
    assert ordinary_model.calls == []
    preview = json.loads(request["preview"])
    _assert_no_non_preview_message_leak(events, preview["exact_provider_messages"])


def test_expired_request_id_is_rejected_then_current_rejection_stays_local(tmp_path, monkeypatch):
    agent, ordinary_model, provider, bridge, events, handler, _paths = _make_case(tmp_path, monkeypatch)
    worker, outcome = _start_turn(agent)
    request = _wait_for_request(events, worker)
    request_id = request["request_id"]

    _deliver(handler, bridge, "expired-" + request_id, "y")
    assert bridge._pending_confirm_id == request_id
    assert worker.is_alive()
    assert provider.calls == []
    assert any(
        event.get("type") == "confirm.done"
        and event.get("request_id") == "expired-" + request_id
        and event.get("choice") == "stale"
        for event in events
    )

    _deliver(handler, bridge, request_id, "n")
    result = _finish(worker, outcome)
    assert "未获外发授权" in result.assistant_text
    assert provider.calls == []
    assert ordinary_model.calls == []
    preview = json.loads(request["preview"])
    _assert_no_non_preview_message_leak(events, preview["exact_provider_messages"])


def test_inline_cancel_dominates_pending_recall_confirmation(tmp_path, monkeypatch):
    agent, ordinary_model, provider, bridge, events, handler, _paths = _make_case(tmp_path, monkeypatch)
    worker, outcome = _start_turn(agent)
    request = _wait_for_request(events, worker)
    bridge._turn_busy.set()  # model the in-flight turn required by turn.cancel

    handled = handler._dispatch_inline({"type": "turn.cancel"}, bridge)
    assert handled is True
    result = _finish(worker, outcome)

    assert "未获外发授权" in result.assistant_text
    assert provider.calls == []
    assert ordinary_model.calls == []
    assert any(
        event.get("type") == "confirm.done"
        and event.get("request_id") == request["request_id"]
        and event.get("choice") == "cancelled"
        for event in events
    )
    preview = json.loads(request["preview"])
    _assert_no_non_preview_message_leak(events, preview["exact_provider_messages"])


def _synthetic_record_items(count: int) -> list[dict[str, Any]]:
    return [
        {
            "item_id": f"synthetic-{index:04d}",
            "title": f"合成工作记录 {index:04d}",
            "requirements": [{"text": f"合成需求 {index:04d}：保留完整记录内容。"}],
            "scope": {"kind": "independent", "object_ref": None},
        }
        for index in range(1, count + 1)
    ]


def test_full_999_record_preview_is_exact_and_single_confirmed_request(tmp_path, monkeypatch):
    agent, ordinary_model, provider, bridge, events, handler, _paths = _make_case(tmp_path, monkeypatch)
    store = agent.work_record_recall_controller._store
    matcher = agent.work_record_recall_controller._semantic_matcher
    matcher._input_budget = 400_000
    registry_entry = SimpleNamespace(
        id=MODEL_ID, vendor="0x567", provider_model="gpt-6-luna", max_input_tokens=1_000_000,
    )
    monkeypatch.setattr(luna, "get_registry", lambda *_args: SimpleNamespace(
        get=lambda model_id: registry_entry if model_id == MODEL_ID else None,
    ))
    items = _synthetic_record_items(999)
    by_id = {item["item_id"]: item for item in items}
    store.list_items = lambda: items
    store.get_item = lambda record_id: by_id.get(record_id)

    worker, outcome = _start_turn(agent)
    request = _wait_for_request(events, worker)
    preview = json.loads(request["preview"])
    assert len(preview["candidate_disclosure"]) == 999
    assert preview["candidate_disclosure"][-1]["id"] == "synthetic-0999"
    exact_messages = preview["exact_provider_messages"]
    payload = json.loads(exact_messages[1]["content"])
    assert len(payload["candidates"]) == 999
    assert payload["candidates"][-1]["id"] == "synthetic-0999"
    event_bytes = len(json.dumps(request, ensure_ascii=False).encode("utf-8"))
    assert event_bytes > 16_000  # Regression: complete 999-record preview exceeds old cap.
    assert event_bytes <= agent_module.WORK_RECORD_RECALL_CONFIRM_EVENT_MAX_BYTES

    _deliver(handler, bridge, request["request_id"], "y")
    result = _finish(worker, outcome)
    assert "合成工作记录 0001" in result.assistant_text
    assert len(provider.calls) == 1
    assert provider.calls[0][0] == exact_messages
    assert ordinary_model.calls == []


def test_preview_over_controlled_byte_limit_is_rejected_without_confirmation_or_send(tmp_path, monkeypatch):
    agent, _ordinary_model, provider, _bridge, events, _handler, _paths = _make_case(tmp_path, monkeypatch)
    store = agent.work_record_recall_controller._store
    oversized = _synthetic_record_items(1)
    oversized[0]["requirements"][0]["text"] = "合成内容" * 200_000
    store.list_items = lambda: oversized

    reply = agent._work_record_recall_reply(QUESTION)

    assert "外发确认载荷超过安全上限" in reply
    assert not any(event.get("type") == "confirm.request" for event in events)
    assert provider.calls == []


def test_snapshot_change_during_confirmation_stops_before_provider_call(tmp_path, monkeypatch):
    agent, _ordinary_model, provider, _bridge, _events, _handler, _paths = _make_case(tmp_path, monkeypatch)
    store = agent.work_record_recall_controller._store
    original_confirm = agent.executor.confirm_fn

    def confirm_then_mutate(preview: str, allow_approve_all: bool) -> str:
        assert allow_approve_all is False
        assert json.loads(preview)["candidate_disclosure"]
        store.add_requirement(
            store.list_items()[0]["item_id"], "确认期间新增的合成需求",
            event_id="synthetic-confirm-time-change",
        )
        return "y"

    agent.executor.confirm_fn = confirm_then_mutate
    reply = agent._work_record_recall_reply(QUESTION)

    assert "记录发生变化" in reply
    assert provider.calls == []
    agent.executor.confirm_fn = original_confirm


def test_default_matcher_budget_covers_full_999_record_preview_and_request(tmp_path, monkeypatch):
    agent, _ordinary_model, provider, bridge, events, handler, _paths = _make_case(tmp_path, monkeypatch)
    store = agent.work_record_recall_controller._store
    matcher = agent.work_record_recall_controller._semantic_matcher
    registry_entry = SimpleNamespace(id=MODEL_ID, vendor="0x567", provider_model="gpt-6-luna", max_input_tokens=1_000_000)
    monkeypatch.setattr(luna, "get_registry", lambda *_args: SimpleNamespace(
        get=lambda model_id: registry_entry if model_id == MODEL_ID else None,
    ))
    assert matcher._input_budget == luna.DEFAULT_INPUT_TOKEN_BUDGET
    items = _synthetic_record_items(999)
    store.list_items = lambda: items
    candidates = tuple(
        luna.WorkRecordCandidate(
            record_id=item["item_id"],
            title=item["title"],
            excerpt=item["requirements"][0]["text"],
            session_id=agent.session.conversation_id,
        )
        for item in items
    )
    messages = matcher._messages(QUESTION, matcher._validate_candidates(candidates))
    body_bytes = len(json.dumps(messages, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    assert body_bytes + matcher._output_reserve + 512 <= matcher._input_budget

    worker, outcome = _start_turn(agent)
    request = _wait_for_request(events, worker)
    preview = json.loads(request["preview"])
    assert len(preview["candidate_disclosure"]) == 999
    assert preview["exact_provider_messages"] == messages
    _deliver(handler, bridge, request["request_id"], "y")
    result = _finish(worker, outcome)
    assert len(provider.calls) == 1
    assert provider.calls[0][0] == messages
    assert result.finish_reason == "work_record_recall"
