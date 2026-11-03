from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from paths import AgentPaths
from session import Session, SessionMeta
from work_records import (
    WorkRecordConflict,
    WorkRecordCorrupt,
    WorkRecordScopeError,
    WorkRecordStaleRun,
    WorkRecordStore,
    WorkRecordWriteError,
)


def _session(root: Path, conversation_id: str = "posthoc-session") -> Session:
    paths = AgentPaths(agent_root=root, workspace=root / "workspace", data=root / "data", evolve=root / "evolve")
    session_dir = paths.data / "sessions" / conversation_id
    session_dir.mkdir(parents=True, exist_ok=True)
    return Session(conversation_id=conversation_id, session_dir=session_dir, goal="", meta=SessionMeta(), messages=[], paths=paths)


def _authorized_requirement(tmp_path: Path, *, conversation_id: str = "posthoc-session"):
    session = _session(tmp_path, conversation_id)
    store = WorkRecordStore(session)
    request_id = "desktop:user.message:request-42"
    item = store.create_item_with_requirement(
        title="ordinary conversation work", scope={"kind": "independent"},
        requirement="Record the associated host run without asserting success.",
        event_id="event-request-42", idempotency_key=request_id,
    )
    return session, store, item["item_id"], item["requirements"][0]["requirement_id"], request_id


def _associate(store, session, item_id, requirement_id, request_id, *, run_id="host-run-7", sequence=3, finish_reason="completed"):
    return store.associate_posthoc_run_if_authorized(
        item_id, requirement_id, session_id=session.conversation_id,
        request_id=request_id, run_id=run_id, sequence=sequence,
        finish_reason=finish_reason,
    )


def test_posthoc_association_records_only_unknown_and_no_fake_start(tmp_path):
    session, store, item_id, requirement_id, request_id = _authorized_requirement(tmp_path)

    result = _associate(store, session, item_id, requirement_id, request_id)

    assert result["status"] == "unknown"
    item = store.get_item(item_id)
    requirement = item["requirements"][0]
    run = item["runs"][0]
    assert requirement["current_run_id"] == "host-run-7"
    assert requirement["status"] == "unknown"
    assert run["run_id"] == "host-run-7"
    assert run["status"] == "unknown"
    assert run["verification"] == {
        "status": "not_verified",
        "evidence_refs": [],
        "host_execution": {"sequence": 3, "finish_reason": "completed"},
    }
    assert not any(event["kind"] == "requirement_run_started" for event in item["history"])
    assert not any(event["kind"] == "requirement_run_started" and event["payload"].get("run_id") == "host-run-7" for event in item["history"])
    assert item["history"][-1]["kind"] == "result_recorded"
    assert "in_progress" not in {requirement["status"], run["status"]}


def test_posthoc_association_replay_is_idempotent_and_changed_facts_conflict(tmp_path):
    session, store, item_id, requirement_id, request_id = _authorized_requirement(tmp_path)
    first = _associate(store, session, item_id, requirement_id, request_id)
    revision = json.loads((session.session_dir / "work-records.v1.json").read_text(encoding="utf-8"))["revision"]

    replay = _associate(WorkRecordStore(_session(tmp_path)), session, item_id, requirement_id, request_id)
    assert replay == first
    assert json.loads((session.session_dir / "work-records.v1.json").read_text(encoding="utf-8"))["revision"] == revision
    with pytest.raises(WorkRecordConflict):
        _associate(store, session, item_id, requirement_id, request_id, sequence=4)
    with pytest.raises(WorkRecordStaleRun):
        _associate(store, session, item_id, requirement_id, request_id, run_id="other-host-run")
    assert len(store.get_item(item_id)["runs"]) == 1


def test_posthoc_association_rejects_cross_session_and_wrong_request_identity(tmp_path):
    session, store, item_id, requirement_id, request_id = _authorized_requirement(tmp_path)
    before = store.get_item(item_id)

    with pytest.raises(WorkRecordScopeError):
        store.associate_posthoc_run_if_authorized(
            item_id, requirement_id, session_id="another-session", request_id=request_id,
            run_id="host-run-7", sequence=3,
        )
    with pytest.raises(WorkRecordConflict):
        _associate(store, session, item_id, requirement_id, "desktop:user.message:wrong-request")
    assert store.get_item(item_id) == before


def test_posthoc_association_refuses_corrupt_store_without_overwriting_it(tmp_path):
    session, store, item_id, requirement_id, request_id = _authorized_requirement(tmp_path)
    sidecar = session.session_dir / "work-records.v1.json"
    sidecar.write_text('{broken', encoding="utf-8")

    with pytest.raises(WorkRecordCorrupt):
        _associate(store, session, item_id, requirement_id, request_id)
    assert sidecar.read_text(encoding="utf-8") == "{broken"


def test_posthoc_association_write_failure_keeps_prior_snapshot(tmp_path, monkeypatch):
    session, store, item_id, requirement_id, request_id = _authorized_requirement(tmp_path)
    sidecar = session.session_dir / "work-records.v1.json"
    before = sidecar.read_bytes()

    def fail_publish(*args, **kwargs):
        raise OSError("synthetic replace failure")

    monkeypatch.setattr(store, "_publish", fail_publish)
    with pytest.raises(WorkRecordWriteError):
        _associate(store, session, item_id, requirement_id, request_id)
    assert sidecar.read_bytes() == before


def test_posthoc_association_rejects_invalid_sequence_and_lifecycle_label(tmp_path):
    session, store, item_id, requirement_id, request_id = _authorized_requirement(tmp_path)
    with pytest.raises(ValueError):
        _associate(store, session, item_id, requirement_id, request_id, sequence=True)
    with pytest.raises(ValueError):
        _associate(store, session, item_id, requirement_id, request_id, finish_reason="model said done")
    assert store.get_item(item_id)["runs"] == []


def test_posthoc_association_concurrent_replay_has_one_atomic_commit(tmp_path):
    session, store, item_id, requirement_id, request_id = _authorized_requirement(tmp_path)
    barrier = Barrier(2)

    def associate_from_fresh_store():
        barrier.wait(timeout=5)
        fresh_session = _session(tmp_path)
        return _associate(WorkRecordStore(fresh_session), fresh_session, item_id, requirement_id, request_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = [future.result(timeout=10) for future in (
            pool.submit(associate_from_fresh_store), pool.submit(associate_from_fresh_store),
        )]
    assert first == second
    item = store.get_item(item_id)
    assert len(item["runs"]) == 1
    assert len([event for event in item["history"] if event["kind"] == "result_recorded"]) == 1



def test_posthoc_association_does_not_relabel_a_run_started_by_normal_lifecycle(tmp_path):
    session, store, item_id, requirement_id, request_id = _authorized_requirement(tmp_path)
    store.begin_requirement_run(
        item_id, requirement_id, run_id="host-run-7", expected_current_run_id=None,
    )

    with pytest.raises(WorkRecordConflict, match="different lifecycle"):
        _associate(store, session, item_id, requirement_id, request_id)
    item = store.get_item(item_id)
    assert item["requirements"][0]["status"] == "in_progress"
    assert item["requirements"][0]["current_run_id"] == "host-run-7"
    assert item["runs"] == []


def test_same_request_and_target_cannot_bind_a_second_host_run_or_overwrite(tmp_path):
    session, store, item_id, requirement_id, request_id = _authorized_requirement(tmp_path)
    _associate(store, session, item_id, requirement_id, request_id, run_id="trusted-run-one")
    sidecar = session.session_dir / "work-records.v1.json"
    before = sidecar.read_bytes()

    with pytest.raises(WorkRecordStaleRun):
        _associate(store, session, item_id, requirement_id, request_id, run_id="arbitrary-run-two")

    assert sidecar.read_bytes() == before
    item = store.get_item(item_id)
    assert [run["run_id"] for run in item["runs"]] == ["trusted-run-one"]
    assert item["requirements"][0]["current_run_id"] == "trusted-run-one"
