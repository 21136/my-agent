from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

import pytest

from paths import AgentPaths
from project_worker_host import ProjectWorkerHost, ProjectWorkerHostOutcome, ProjectWorkerHostRequest
from project_worker_work_record_settlement import ProjectWorkerWorkRecordBinding
from session import Session, SessionMeta
from work_record_settlement import BoundExecution
from work_records import (WorkRecordStore, _valid_server_publication_result,
                          _server_settlement_result_id)
import server


def _setup(tmp_path):
    paths = AgentPaths(agent_root=tmp_path, workspace=tmp_path / "workspace",
                       data=tmp_path / "data", evolve=tmp_path / "evolve")
    for directory in (paths.workspace, paths.data, paths.evolve):
        directory.mkdir(parents=True, exist_ok=True)
    root = paths.workspace / "project-a"
    root.mkdir()
    (root / "PROJECT.md").write_text("fixture", encoding="utf-8")
    (root / "test_feature.py").write_text("def test_target(): assert True\n", encoding="utf-8")
    session_id = str(uuid.uuid4())
    session_dir = paths.data / "sessions" / session_id
    session_dir.mkdir(parents=True)
    session = Session(conversation_id=session_id, session_dir=session_dir, goal="",
                      meta=SessionMeta(project_id="project-a", project_root="workspace/project-a"),
                      messages=[], paths=paths)
    store = WorkRecordStore(session)
    item = store.create_item(title="Synthetic", scope={"kind": "project", "project_id": "project-a"})
    requirement = store.add_requirement(item["item_id"], "Feature works", event_id="requirement")
    events = []
    bridge = server.WsBridge(emit=events.append, paths=paths)
    bridge._session_provider = lambda: session
    assert bridge.begin_turn(mode="ordinary")
    bridge.lifecycle_conversation_id = session.conversation_id
    parent_run = bridge.lifecycle.snapshot().run_id
    store.begin_requirement_run(item["item_id"], requirement["requirement_id"], run_id=parent_run,
                                expected_current_run_id=None)
    execution = BoundExecution(session.conversation_id, item["item_id"], requirement["requirement_id"], parent_run)
    request = ProjectWorkerHostRequest("project-a", "task-1", "Feature works", (), ("a.txt",))
    host = ProjectWorkerHost(lambda: None)
    live = lambda candidate, run, turn: candidate is session and run == parent_run
    reservation, = host.reserve_many(session, [request], parent_run_id=parent_run,
                                     turn_epoch=bridge.lifecycle.snapshot().sequence,
                                     live_binding_check=live)
    host._consume_reservations(session, [request], (reservation,), parent_run_id=parent_run,
                               turn_epoch=bridge.lifecycle.snapshot().sequence)
    binding = ProjectWorkerWorkRecordBinding(execution, request, reservation)
    outcome = ProjectWorkerHostOutcome(
        worker_id=reservation.worker_id, run_id=parent_run, turn_epoch=reservation.turn_epoch,
        conversation_id=session.conversation_id, project_id="project-a", task_id="task-1",
        status="model_turn_finished", parent_run_id=parent_run,
        worker_run_id=reservation.worker_run_id, reservation_id=reservation.reservation_id,
    )
    return bridge, host, session, store, binding, outcome, live, events


def _verification(bridge, binding, target="test_feature.py::test_target"):
    e = binding.execution
    reservation = binding.reservation
    return {
        "status": "not_verified", "evidence_refs": [],
        "project_test": {"status": "passed", "source": "user_authorized_project_self_report",
                          "pytest_target": target, "exit_code": 0},
        "host_execution": {"sequence": bridge.lifecycle.snapshot().sequence,
            "session_id": e.session_id, "project_id": binding.request.project_id,
            "item_id": e.item_id, "requirement_id": e.requirement_id,
            "parent_run_id": reservation.parent_run_id,
            "worker_run_id": reservation.worker_run_id, "worker_id": reservation.worker_id,
            "task_id": reservation.task_id, "reservation_id": reservation.reservation_id},
    }


def test_self_report_direct_project_item_uses_atomic_30_second_publication(tmp_path, monkeypatch):
    bridge, host, session, store, binding, outcome, live, events = _setup(tmp_path)
    monkeypatch.setattr(bridge, "confirm_work_card_batch", lambda *args, **kwargs: "y")
    token = bridge.verify_project_worker_requirement(
        host=host, session=session, store=store, binding=binding, outcome=outcome,
        target="test_feature.py::test_target", live_binding_check=live,
    )
    assert token is not None
    assert bridge.finish_execution("completed", ok=True, run_id=binding.execution.run_id)
    repl = SimpleNamespace(session=session, agent=SimpleNamespace(session=session))
    monkeypatch.setattr(server, "PROJECT_WORK_CARD_MERGE_SECONDS", 0.2, raising=False)

    async def settle_and_wait():
        assert server._settle_work_record_run(
            current_session=session, bridge=bridge, store=store, binding=binding.execution,
            emit=events.append, repl=repl, worker_work_record_result=(binding, outcome),
            worker_verification_receipt=token,
            agent=SimpleNamespace(project_worker_host=host, session=session),
        ) is True
        pending = store.list_publication_intents()
        assert len(pending) == 1 and pending[0]["window_seconds"] == 0.2
        assert pending[0]["latest_run_id"] == binding.execution.run_id
        await asyncio.sleep(0.25)

    asyncio.run(settle_and_wait())
    if bridge._work_card_update_coalescer is not None:
        bridge._work_card_update_coalescer.close()
    row = WorkRecordStore(session).get_item(binding.execution.item_id)["runs"][0]
    assert row["status"] == "unknown" and row["verification"]["status"] == "not_verified"
    assert any(event.get("type") == "work-card.present" for event in events), events
    assert store.list_publication_intents() == []

    # Replaying the consumed self-report capability must not fall back to a new
    # lifecycle result after the original card reference has already been queued.
    before = store.get_item(binding.execution.item_id)
    server._settle_work_record_run(
        current_session=session, bridge=bridge, store=store, binding=binding.execution,
        emit=events.append, repl=repl, worker_work_record_result=(binding, outcome),
        worker_verification_receipt=token,
        agent=SimpleNamespace(project_worker_host=host, session=session),
    )
    after = store.get_item(binding.execution.item_id)
    assert after["runs"] == before["runs"]


def test_self_report_publication_cas_and_validator_fail_closed(tmp_path):
    bridge, host, session, store, binding, _outcome, _live, _events = _setup(tmp_path)
    assert bridge.finish_execution("completed", ok=True, run_id=binding.execution.run_id)
    e = binding.execution
    verification = _verification(bridge, binding)
    result_id = "worker-self-report-" + __import__("hashlib").sha256(
        "\0".join((e.session_id, e.item_id, e.requirement_id, e.run_id,
                    verification["project_test"]["pytest_target"])).encode("utf-8")
    ).hexdigest()
    with pytest.raises(Exception):
        store.record_project_self_reported_worker_settlement_if_current_run(
            e.item_id, e.requirement_id, run_id=e.run_id, result_id=result_id,
            verification=verification, expected_project_id="different-project",
            expected_requirement_text=binding.request.task, publish_project_update=True,
        )
    assert store.get_item(e.item_id)["runs"] == []

    run = {"run_id": e.run_id, "result_id": result_id, "status": "unknown", "verification": verification}
    assert _valid_server_publication_result(e.session_id, e.item_id, e.requirement_id, run, "project-a")
    for malformed in (
        {**run, "run_id": None},
        {**run, "run_id": "not-a-run"},
        {**run, "verification": {**verification, "project_test": {**verification["project_test"], "pytest_target": "../bad.py::test_x"}}},
        {**run, "verification": {**verification, "host_execution": {**verification["host_execution"], "parent_run_id": "different-run"}}},
    ):
        assert _valid_server_publication_result(e.session_id, e.item_id, e.requirement_id,
                                                malformed, "project-a") is False
    # worker_run_id identifies the worker reservation, not the parent run CAS.
    assert verification["host_execution"]["worker_run_id"] != e.run_id
    # Existing ordinary unknown intents keep their pre-self-report run-id contract.
    legacy_run_id = "legacy-run-id"
    ordinary_unknown = {
        "run_id": legacy_run_id,
        "result_id": _server_settlement_result_id(e.session_id, e.item_id, e.requirement_id, legacy_run_id),
        "status": "unknown", "verification": {"host_execution": {"sequence": 1}},
    }
    assert _valid_server_publication_result(e.session_id, e.item_id, e.requirement_id, ordinary_unknown)


