from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

from agent import TurnResult
from paths import AgentPaths
from session import Session, SessionMeta
from work_records import WorkRecordStore
from work_records_agent_recording import WorkRecordAgentRecording
import work_records_luna_intent as luna


def _install_synthetic_registry(monkeypatch):
    entry = SimpleNamespace(
        id=luna.MODEL_ID, vendor=luna.EXPECTED_VENDOR,
        provider_model=luna.EXPECTED_PROVIDER_MODEL, max_input_tokens=100_000,
    )
    monkeypatch.setattr(
        luna, "get_registry",
        lambda *_args, **_kwargs: SimpleNamespace(get=lambda model_id: entry if model_id == luna.MODEL_ID else None),
    )


class _FakeClient:
    paths = None

    def __init__(self, *payloads):
        self.payloads = list(payloads)
        self.calls = []

    def chat(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        payload = self.payloads.pop(0)
        return SimpleNamespace(content=json.dumps(payload, ensure_ascii=False), tool_calls=[])


def _payload(action, kind, *, title=None, requirement=None, scope=None, item_id=None):
    return {
        "action": action, "kind": kind, "title": title,
        "requirement": requirement, "scope": scope, "item_id": item_id,
    }


def _bound_store(tmp_path: Path, *, project_id: str = "synthetic-project"):
    root = tmp_path / "agent-root"
    paths = AgentPaths(
        agent_root=root, workspace=root / "workspace",
        data=root / "data", evolve=root / "evolve",
    )
    paths.agent_root.mkdir(parents=True, exist_ok=True)
    paths.workspace.mkdir(parents=True, exist_ok=True)
    paths.data.mkdir(parents=True, exist_ok=True)
    paths.evolve.mkdir(parents=True, exist_ok=True)
    session_id = str(uuid.uuid4())
    session_dir = paths.data / "sessions" / session_id
    session_dir.mkdir(parents=True)
    paths.workspace.mkdir(parents=True, exist_ok=True)
    session = Session(session_id, session_dir, "", SessionMeta(project_id=project_id), [], paths)
    store = WorkRecordStore(session)
    return session, store


def _agent_recorder(session, store, recognizer):
    executed = []
    agent = SimpleNamespace(session=session, run_turn=lambda *_args, **_kwargs: TurnResult(
        assistant_text="Synthetic host reply", tool_rounds=0, finish_reason="stop",
    ))
    recorder = WorkRecordAgentRecording(
        agent=agent, store=store, recognizer=recognizer,
        idempotency_key_provider=lambda _text: f"synthetic-request:{uuid.uuid4()}",
    )

    def execute():
        executed.append(True)
        return agent.run_turn("synthetic", spawn_explore=False)

    return recorder, execute, executed


def test_luna_recognition_sends_only_allowlisted_synthetic_text_and_record_view(monkeypatch):
    _install_synthetic_registry(monkeypatch)
    client = _FakeClient(_payload("none", "qa"))
    recognizer = luna.WorkRecordsLunaIntent(
        client, outbound_approved=True, current_project_id="private-local-project-id",
    )
    recognition_view = [
        {"item_id": "synthetic-project-item", "title": "Synthetic project title",
         "scope": {"kind": "project", "project_id": "private-local-project-id"}},
        {"item_id": "synthetic-independent-item", "title": "Synthetic standalone title",
         "scope": {"kind": "independent", "object_ref": None}},
    ]

    decision = recognizer("Synthetic message with no durable task", recognition_view)

    assert decision["action"] == "none" and decision["kind"] == "qa"
    assert len(client.calls) == 1
    messages, kwargs = client.calls[0]
    assert kwargs == {
        "model": luna.MODEL_ID, "tools": [], "temperature": 0.0,
        "response_format": {"type": "json_object"},
    }
    outbound = json.loads(messages[1]["content"])
    assert set(outbound) == {"text", "recognition_view", "current_project_available"}
    assert outbound["text"] == "Synthetic message with no durable task"
    assert outbound["current_project_available"] is True
    assert outbound["recognition_view"] == [
        {"item_id": "synthetic-project-item", "title": "Synthetic project title", "scope": {"kind": "project"}},
        {"item_id": "synthetic-independent-item", "title": "Synthetic standalone title", "scope": {"kind": "independent"}},
    ]
    assert "private-local-project-id" not in messages[1]["content"]
    assert "object_ref" not in messages[1]["content"]
    assert len(messages) == 2 and messages[0]["role"] == "system" and messages[1]["role"] == "user"


def test_none_candidate_executes_once_without_record_write_and_uncertain_candidate_executes_neither(
    tmp_path, monkeypatch,
):
    _install_synthetic_registry(monkeypatch)
    session, store = _bound_store(tmp_path / "none")
    none_client = _FakeClient(_payload("none", "qa"))
    none_recognizer = luna.WorkRecordsLunaIntent(
        none_client, outbound_approved=True, current_project_id="synthetic-project",
    )
    none_recorder, execute_none, none_executions = _agent_recorder(session, store, none_recognizer)
    none_outcome = none_recorder.run_turn(
        "Synthetic general question", execute=execute_none,
    )
    assert none_outcome.turn is not None and none_executions == [True]
    assert store.list_items() == []

    uncertain_session, uncertain_store = _bound_store(tmp_path / "uncertain")
    uncertain_client = _FakeClient(_payload("uncertain", "ambiguous"))
    uncertain_recognizer = luna.WorkRecordsLunaIntent(
        uncertain_client, outbound_approved=True, current_project_id="synthetic-project",
    )
    uncertain_recorder, execute_uncertain, uncertain_executions = _agent_recorder(
        uncertain_session, uncertain_store, uncertain_recognizer,
    )
    uncertain = uncertain_recorder.run_turn(
        "Synthetic ambiguous possible task", execute=execute_uncertain,
    )
    assert uncertain.turn is None and uncertain.item_id is None
    assert uncertain_executions == []
    assert uncertain_store.list_items() == []
    assert len(none_client.calls) == len(uncertain_client.calls) == 1


def test_project_and_independent_candidate_scopes_are_local_and_record_is_not_execution_evidence(
    tmp_path, monkeypatch,
):
    _install_synthetic_registry(monkeypatch)
    session, store = _bound_store(tmp_path / "project")
    existing_project = store.create_item(
        title="Synthetic existing project item",
        scope={"kind": "project", "project_id": "synthetic-project"},
    )
    existing_independent = store.create_item(
        title="Synthetic existing independent item", scope={"kind": "independent"},
    )
    view = [
        {"item_id": existing_project["item_id"], "title": existing_project["title"], "scope": existing_project["scope"]},
        {"item_id": existing_independent["item_id"], "title": existing_independent["title"], "scope": existing_independent["scope"]},
    ]
    initial_items = store.list_items()

    # Recognition alone is a pure candidate result and cannot mutate the store.
    project_candidate = _payload(
        "record", "requirement", title="Synthetic project continuation",
        requirement="Add one synthetic validation; acceptance: focused test passes",
        scope="current_project", item_id=existing_project["item_id"],
    )
    candidate_client = _FakeClient(project_candidate)
    project_recognizer = luna.WorkRecordsLunaIntent(
        candidate_client, outbound_approved=True, current_project_id="synthetic-project",
    )
    recognized = project_recognizer("Continue the named synthetic project item", view)
    assert recognized == {
        "action": "record", "kind": "requirement",
        "title": "Synthetic project continuation",
        "requirement": "Add one synthetic validation; acceptance: focused test passes",
        "scope": {"kind": "project", "project_id": "synthetic-project"},
        "item_id": existing_project["item_id"],
    }
    assert store.list_items() == initial_items

    # The same project-scoped decision routed through the existing adapter adds
    # one requirement to its exact recognized item, without creating another item.
    project_record_client = _FakeClient(project_candidate)
    project_record_recognizer = luna.WorkRecordsLunaIntent(
        project_record_client, outbound_approved=True, current_project_id="synthetic-project",
    )
    project_recorder, project_execute, project_executions = _agent_recorder(
        session, store, project_record_recognizer,
    )
    project_outcome = project_recorder.run_turn(
        "Continue the named synthetic project item", execute=project_execute,
    )
    assert project_outcome.item_id == existing_project["item_id"]
    assert project_executions == [True]
    assert len(store.list_items()) == 2
    updated_project = store.get_item(existing_project["item_id"])
    assert updated_project["scope"] == {"kind": "project", "project_id": "synthetic-project"}
    assert updated_project["requirements"][-1]["text"] == project_candidate["requirement"]
    project_run = updated_project["runs"][-1]
    assert project_run["status"] == "unknown"
    assert project_run["verification"]["status"] == "not_recorded"

    # A second synthetic recognition goes through the existing recorder adapter.
    # Its output creates an independent item, but persisted run evidence remains unknown.
    independent_candidate = _payload(
        "record", "requirement", title="Synthetic standalone deliverable",
        requirement="Prepare one synthetic note; acceptance: note has two sections",
        scope="independent", item_id=None,
    )
    independent_client = _FakeClient(independent_candidate)
    independent_recognizer = luna.WorkRecordsLunaIntent(
        independent_client, outbound_approved=True, current_project_id="synthetic-project",
    )
    recorder, execute, executions = _agent_recorder(session, store, independent_recognizer)
    outcome = recorder.run_turn("Prepare a standalone synthetic note", execute=execute)

    assert outcome.item_id is not None and executions == [True]
    assert len(independent_client.calls) == 1
    persisted = store.get_item(outcome.item_id)
    assert persisted["scope"] == {"kind": "independent", "object_ref": None}
    assert len(persisted["requirements"]) == 1
    assert persisted["runs"] and len(persisted["runs"]) == 1
    run = persisted["runs"][0]
    assert run["status"] == "unknown"
    assert run["verification"]["status"] == "not_recorded"
    assert "lifecycle_run_id=unavailable" in run["result_summary"]
    assert len(store.list_items()) == 3

