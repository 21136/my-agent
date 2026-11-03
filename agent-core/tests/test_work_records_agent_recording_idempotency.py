from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

from paths import AgentPaths
from session import Session, SessionMeta
from work_records import WorkRecordStore
from work_records_agent_recording import WorkRecordAgentRecording


def make_context(tmp_path: Path):
    root = tmp_path / "agent-root"
    paths = AgentPaths(root, root / "workspace", root / "data", root / "evolve")
    for directory in (paths.workspace, paths.data, paths.evolve):
        directory.mkdir(parents=True, exist_ok=True)
    session_dir = paths.data / "sessions" / "session-recording"
    session_dir.mkdir(parents=True)
    session = Session("session-recording", session_dir, "", SessionMeta(), [], paths)
    fake_agent = SimpleNamespace(session=session, run_turn=lambda _text: None)
    store = WorkRecordStore(session)
    return fake_agent, store


def decision(*, item_id=None, requirement="synthetic requirement"):
    return {
        "action": "record",
        "title": "synthetic task",
        "requirement": requirement,
        "scope": {"kind": "independent"},
        "item_id": item_id,
    }


def test_missing_request_id_never_leaves_empty_item_but_runs_agent_once(tmp_path):
    agent, store = make_context(tmp_path)
    executions = []
    controller = WorkRecordAgentRecording(
        agent=agent,
        store=store,
        recognizer=lambda *_args: decision(),
    )

    outcome = controller.run_turn(
        "explicit synthetic request",
        execute=lambda: executions.append("ran") or "answer",
    )

    assert executions == ["ran"]
    assert store.list_items() == []
    assert outcome.storage_error == "WorkRecordIdempotencyRequired"


def test_stable_request_id_atomically_creates_item_with_requirement(tmp_path):
    agent, store = make_context(tmp_path)
    controller = WorkRecordAgentRecording(
        agent=agent,
        store=store,
        recognizer=lambda *_args: decision(),
        idempotency_key_provider=lambda _text: "synthetic-request:create-1",
    )

    outcome = controller.run_turn("explicit synthetic request", execute=lambda: "answer")

    assert outcome.item_id is not None
    item = store.get_item(outcome.item_id)
    assert len(item["requirements"]) == 1
    assert item["requirements"][0]["text"] == "synthetic requirement"
    assert outcome.storage_error is None


def test_same_stable_request_replay_does_not_duplicate_requirement_or_execute(tmp_path):
    agent, store = make_context(tmp_path)
    executions = []
    controller = WorkRecordAgentRecording(
        agent=agent,
        store=store,
        recognizer=lambda *_args: decision(),
        idempotency_key_provider=lambda _text: "synthetic-request:replay-1",
    )
    first = controller.run_turn("explicit synthetic request", execute=lambda: executions.append(1) or "answer")
    replay = controller.run_turn("explicit synthetic request", execute=lambda: executions.append(2) or "answer")

    assert executions == [1]
    assert replay.turn is None
    assert replay.reply is not None
    assert len(store.list_items()) == 1
    assert len(store.list_items()[0]["requirements"]) == 1
    assert first.item_id == store.list_items()[0]["item_id"]


def test_stable_request_id_appends_to_existing_item_once(tmp_path):
    agent, store = make_context(tmp_path)
    item = store.create_item(title="existing synthetic", scope={"kind": "independent"})
    controller = WorkRecordAgentRecording(
        agent=agent,
        store=store,
        recognizer=lambda *_args: decision(item_id=item["item_id"], requirement="append synthetic"),
        idempotency_key_provider=lambda _text: "synthetic-request:append-1",
    )

    first = controller.run_turn("append synthetic requirement", execute=lambda: "answer")
    replay = controller.run_turn("append synthetic requirement", execute=lambda: pytest.fail("replay must not execute"))

    saved = store.get_item(item["item_id"])
    assert len(saved["requirements"]) == 1
    assert saved["requirements"][0]["text"] == "append synthetic"
    assert first.item_id == item["item_id"]
    assert replay.reply is not None
    assert replay.reply is not None
