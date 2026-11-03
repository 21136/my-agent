from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

from agent import Agent, TurnResult
from llm_client import LLMResponse
from paths import AgentPaths
from session import Session, SessionMeta
from work_records import WorkRecordStore
from work_records_agent_recording import WorkRecordAgentRecording


class FakeLLM:
    def __init__(self):
        self.calls = []

    def chat(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        return LLMResponse(model="fake", content="normal answer", tool_calls=[], finish_reason="stop", usage=None, raw={})


def make_agent(tmp_path: Path, *, project_id="", recording=False, recognizer=None):
    root = tmp_path / "agent-root"
    paths = AgentPaths(agent_root=root, workspace=root / "workspace", data=root / "data", evolve=root / "evolve")
    session_dir = paths.data / "sessions" / "session-1"
    session_dir.mkdir(parents=True)
    session = Session(
        conversation_id="session-1", session_dir=session_dir, goal="",
        meta=SessionMeta(turn_mode="ask", project_id=project_id), messages=[], paths=paths,
    )
    session.save()
    llm = FakeLLM()
    agent = Agent.create(session, llm=llm)
    if recording:
        store = WorkRecordStore(session)
        agent.enable_work_record_recording = True
        # This isolated fixture supplies a stable synthetic caller key; it is
        # not evidence that desktop user.message currently carries one.
        agent.work_record_recording_controller = WorkRecordAgentRecording(
            agent=agent,
            store=store,
            recognizer=recognizer,
            idempotency_key_provider=lambda text: "synthetic-test-request:" + hashlib.sha256(
                (session.conversation_id + "\0" + text).encode("utf-8")
            ).hexdigest(),
        )
    return agent, llm, WorkRecordStore(session)


def record_decision(scope=None):
    return {"action": "record", "title": "prepare synthetic report", "requirement": "write one synthetic report", "scope": scope or {"kind": "independent"}}


def test_recording_is_off_by_default_and_existing_agent_path_is_unchanged(tmp_path):
    calls = []
    agent, llm, store = make_agent(tmp_path)
    agent.work_record_recording_controller = SimpleNamespace(run_turn=lambda *_a, **_k: calls.append("called"))

    result = agent.run_turn("What is 2 + 2?", spawn_explore=False)

    assert result.assistant_text == "normal answer"
    assert len(llm.calls) == 1 and calls == []
    assert store.list_items() == []


def test_explicit_record_runs_agent_once_and_persists_unknown_result_for_readback(tmp_path):
    recognitions = []
    agent, llm, store = make_agent(
        tmp_path, project_id="project-synthetic", recording=True,
        recognizer=lambda text, items: recognitions.append((text, items)) or record_decision({"kind": "project", "project_id": "project-synthetic"}),
    )

    result = agent.run_turn("prepare synthetic report", spawn_explore=False)

    items = store.list_items()
    assert result.assistant_text == "normal answer"
    assert len(llm.calls) == 1 and len(recognitions) == 1
    assert len(items) == 1 and items[0]["scope"] == {"kind": "project", "project_id": "project-synthetic"}
    assert items[0]["title"] == "prepare synthetic report"
    assert len(items[0]["requirements"]) == 1
    assert len(items[0]["runs"]) == 1
    assert items[0]["runs"][0]["status"] == "unknown"
    assert items[0]["runs"][0]["verification"]["status"] == "not_recorded"
    assert store.get_item(items[0]["item_id"]) == items[0]


def test_ordinary_question_is_not_recorded_and_ambiguous_request_is_not_executed(tmp_path):
    agent, llm, store = make_agent(tmp_path, recording=True, recognizer=lambda _t, _i: {"action": "none"})
    result = agent.run_turn("prepare synthetic report", spawn_explore=False)
    assert result.assistant_text == "normal answer" and len(llm.calls) == 1 and store.list_items() == []

    agent2, llm2, store2 = make_agent(tmp_path / "ambiguous", recording=True, recognizer=lambda _t, _i: {"action": "uncertain"})
    result2 = agent2.run_turn("do that thing", spawn_explore=False)
    assert "\u8865\u5145" in result2.assistant_text
    assert llm2.calls == [] and store2.list_items() == []


def test_record_scope_must_be_current_project_and_each_session_is_isolated(tmp_path):
    a1, llm1, store1 = make_agent(tmp_path / "one", project_id="p1", recording=True, recognizer=lambda *_: record_decision({"kind": "project", "project_id": "p1"}))
    a2, llm2, store2 = make_agent(tmp_path / "two", project_id="p2", recording=True, recognizer=lambda *_: record_decision({"kind": "project", "project_id": "p2"}))
    a1.run_turn("synthetic work request 1", spawn_explore=False)
    a2.run_turn("synthetic work request 2", spawn_explore=False)
    assert len(store1.list_items()) == len(store2.list_items()) == 1
    assert store1.list_items()[0]["item_id"] != store2.list_items()[0]["item_id"]
    assert len(llm1.calls) == len(llm2.calls) == 1


def test_record_save_failure_is_reported_but_normal_agent_runs_once(tmp_path, monkeypatch):
    agent, llm, store = make_agent(tmp_path, recording=True, recognizer=lambda *_: record_decision())
    monkeypatch.setattr(agent.work_record_recording_controller._store, "create_item_with_requirement", lambda **_kwargs: (_ for _ in ()).throw(OSError("synthetic write failure")))

    result = agent.run_turn("prepare synthetic report", spawn_explore=False)

    assert len(llm.calls) == 1
    assert "\u8bb0\u5f55" in result.assistant_text and "\u5931\u8d25" in result.assistant_text
    assert store.list_items() == []


def test_cancelled_agent_result_is_recorded_unknown_not_completed(tmp_path):
    agent, _llm, store = make_agent(tmp_path, recording=True, recognizer=lambda *_: record_decision())
    agent._run_turn_impl = lambda *_a, **_k: TurnResult("cancelled synthetic", 0, "cancelled")

    result = agent.run_turn("prepare synthetic report", spawn_explore=False)

    item = store.list_items()[0]
    assert result.finish_reason == "cancelled"
    assert item["runs"][0]["status"] == "unknown"
    assert item["runs"][0]["verification"]["status"] == "not_recorded"
    assert item["runs"][0]["run_id"].startswith("experimental-turn:")
    assert "lifecycle_run_id=unavailable" in item["runs"][0]["result_summary"]
    assert "\u56de\u6eda" in result.assistant_text and "\u672a\u77e5" in result.assistant_text


def test_agent_exception_records_unknown_once_and_is_not_retried(tmp_path):
    agent, _llm, store = make_agent(tmp_path, recording=True, recognizer=lambda *_: record_decision())
    calls = []
    def explode(*_a, **_k):
        calls.append(1)
        raise RuntimeError("synthetic failure")
    agent._run_turn_impl = explode

    with pytest.raises(RuntimeError, match="synthetic failure"):
        agent.run_turn("请准备合成报告")

    item = store.list_items()[0]
    assert calls == [1]
    assert len(item["runs"]) == 1 and item["runs"][0]["status"] == "unknown"


def test_controller_rejects_cross_session_store_binding(tmp_path):
    one, _llm1, store1 = make_agent(tmp_path / "one")
    two, _llm2, _store2 = make_agent(tmp_path / "two")
    with pytest.raises(ValueError, match="exact Session object"):
        WorkRecordAgentRecording(agent=two, store=store1, recognizer=lambda *_: {"action": "none"})


def test_result_write_failure_is_reported_without_repeating_agent(tmp_path, monkeypatch):
    agent, llm, store = make_agent(tmp_path, recording=True, recognizer=lambda *_: record_decision())
    controller_store = agent.work_record_recording_controller._store
    monkeypatch.setattr(controller_store, "record_result", lambda *_a, **_k: (_ for _ in ()).throw(OSError("synthetic result failure")))

    result = agent.run_turn("prepare synthetic report", spawn_explore=False)

    assert len(llm.calls) == 1
    assert "\u8bb0\u5f55" in result.assistant_text and "\u5931\u8d25" in result.assistant_text
    item = store.list_items()[0]
    assert len(item["requirements"]) == 1 and item["runs"] == []


def test_existing_item_id_adds_requirement_without_creating_duplicate_item(tmp_path):
    agent, llm, store = make_agent(tmp_path, recording=True, recognizer=lambda *_: record_decision())
    existing = store.create_item(title="existing synthetic project", scope={"kind": "independent"})
    first = store.add_requirement(existing["item_id"], "first requirement", event_id="first-event", idempotency_key="synthetic-test:first-event")
    decision = record_decision() | {"item_id": existing["item_id"], "event_id": "second-event"}
    agent.work_record_recording_controller._recognizer = lambda *_: decision

    agent.run_turn("add another synthetic requirement", spawn_explore=False)

    items = store.list_items()
    assert len(items) == 1 and items[0]["item_id"] == existing["item_id"]
    assert [req["text"] for req in items[0]["requirements"]] == ["first requirement", "write one synthetic report"]
    assert len(items[0]["runs"]) == 1 and items[0]["runs"][0]["requirement_id"] != first["requirement_id"]
    assert len(llm.calls) == 1


def test_untrusted_or_scope_mismatched_item_id_fails_closed_without_create_or_execute(tmp_path):
    agent, llm, store = make_agent(tmp_path, project_id="project-a", recording=True, recognizer=lambda *_: record_decision())
    existing = store.create_item(title="independent synthetic", scope={"kind": "independent"})
    decision = record_decision({"kind": "project", "project_id": "project-a"}) | {"item_id": existing["item_id"]}
    agent.work_record_recording_controller._recognizer = lambda *_: decision

    result = agent.run_turn("add a project requirement", spawn_explore=False)

    assert "\u8865\u5145" in result.assistant_text
    assert llm.calls == []
    assert [item["item_id"] for item in store.list_items()] == [existing["item_id"]]
    assert store.get_item(existing["item_id"])["requirements"] == []


def test_unknown_item_id_never_falls_back_to_creating_a_new_item(tmp_path):
    agent, llm, store = make_agent(tmp_path, recording=True, recognizer=lambda *_: record_decision() | {"item_id": "foreign-session-id"})

    agent.run_turn("add a requirement", spawn_explore=False)

    assert llm.calls == [] and store.list_items() == []


def test_replayed_event_id_does_not_duplicate_existing_item_requirement(tmp_path):
    agent, llm, store = make_agent(tmp_path, recording=True, recognizer=lambda *_: record_decision())
    existing = store.create_item(title="existing synthetic", scope={"kind": "independent"})
    event_id = "replayed-event-1"
    decision = record_decision() | {"item_id": existing["item_id"], "event_id": event_id}
    agent.work_record_recording_controller._recognizer = lambda *_: decision

    agent.run_turn("add requirement", spawn_explore=False)
    replay = agent.run_turn("replay same requirement event", spawn_explore=False)

    item = store.get_item(existing["item_id"])
    assert len(store.list_items()) == 1
    assert len(item["requirements"]) == 1
    assert len(item["runs"]) == 1
    assert len(llm.calls) == 1
    assert "\u672a\u518d\u6b21\u6267\u884c" in replay.assistant_text


def test_item_id_from_another_session_is_rejected_without_fallback(tmp_path):
    source, _source_llm, source_store = make_agent(tmp_path / "source")
    foreign = source_store.create_item(title="foreign session item", scope={"kind": "independent"})
    target, target_llm, target_store = make_agent(
        tmp_path / "target", recording=True,
        recognizer=lambda *_: record_decision() | {"item_id": foreign["item_id"]},
    )

    result = target.run_turn("add to prior item", spawn_explore=False)

    assert "\u8865\u5145" in result.assistant_text
    assert target_llm.calls == [] and target_store.list_items() == []
    assert source_store.get_item(foreign["item_id"])["requirements"] == []


def test_reused_event_id_with_different_requirement_stays_a_conflict(tmp_path):
    agent, llm, store = make_agent(tmp_path, recording=True, recognizer=lambda *_: record_decision())
    existing = store.create_item(title="existing synthetic", scope={"kind": "independent"})
    event_id = "replayed-event-conflict"
    first_decision = record_decision() | {"item_id": existing["item_id"], "event_id": event_id}
    agent.work_record_recording_controller._recognizer = lambda *_: first_decision
    agent.run_turn("first event payload", spawn_explore=False)

    conflicting_decision = first_decision | {"requirement": "different payload for same event"}
    agent.work_record_recording_controller._recognizer = lambda *_: conflicting_decision
    result = agent.run_turn("conflicting replay", spawn_explore=False)

    item = store.get_item(existing["item_id"])
    assert len(item["requirements"]) == 1
    assert len(llm.calls) == 1
    assert "\u6838\u5bf9" in result.assistant_text


@pytest.mark.parametrize("item_id_value", ["absent", None])
def test_external_event_id_without_explicit_item_target_is_rejected(item_id_value, tmp_path):
    decision = record_decision() | {"event_id": "unbound-external-event"}
    if item_id_value != "absent":
        decision["item_id"] = item_id_value
    agent, llm, store = make_agent(tmp_path, recording=True, recognizer=lambda *_: decision)

    result = agent.run_turn("record this synthetic request", spawn_explore=False)

    assert "\u6838\u5bf9" in result.assistant_text
    assert llm.calls == [] and store.list_items() == []


def test_null_item_id_is_equivalent_to_absent_and_creates_one_new_item(tmp_path):
    agent, llm, store = make_agent(
        tmp_path, recording=True,
        recognizer=lambda *_: record_decision() | {"item_id": None},
    )

    result = agent.run_turn("create a synthetic independent task", spawn_explore=False)

    items = store.list_items()
    assert result.assistant_text == "normal answer"
    assert len(items) == 1
    assert items[0]["scope"] == {"kind": "independent", "object_ref": None}
    assert len(items[0]["requirements"]) == 1
    assert len(items[0]["runs"]) == 1
    assert items[0]["runs"][0]["status"] == "unknown"
    assert len(llm.calls) == 1


@pytest.mark.parametrize("invalid_item_id", ["", 17, True, [], {}])
def test_non_string_non_null_item_id_fails_closed(invalid_item_id, tmp_path):
    agent, llm, store = make_agent(
        tmp_path, recording=True,
        recognizer=lambda *_: record_decision() | {"item_id": invalid_item_id},
    )

    result = agent.run_turn("create a synthetic independent task", spawn_explore=False)

    assert "\u8865\u5145" in result.assistant_text
    assert llm.calls == []
    assert store.list_items() == []

