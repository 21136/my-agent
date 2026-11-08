from __future__ import annotations

import sys
from pathlib import Path

_CORE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_CORE))

from paths import AgentPaths
from project_worker_host import ProjectWorkerHostOutcome, ProjectWorkerHostRequest
from project_worker_model_loop import ProjectWorkerModelOutcome, ProjectWorkerWriteReceipt
from session import Session, SessionMeta
from work_record_settlement import BoundExecution, ExecutionReceipt
from work_records import WorkRecordStore
from project_worker_work_record_settlement import (
    ProjectWorkerWorkRecordBinding, settle_project_worker_work_record,
)


def _fixture(tmp_path: Path):
    paths = AgentPaths(agent_root=tmp_path, workspace=tmp_path / "workspace",
                       data=tmp_path / "data", evolve=tmp_path / "evolve")
    session_dir = paths.data / "sessions" / "worker-settlement-session"
    session_dir.mkdir(parents=True)
    session = Session(conversation_id="worker-settlement-session", session_dir=session_dir,
                      goal="", meta=SessionMeta(project_id="project-a"), messages=[], paths=paths)
    store = WorkRecordStore(session)
    item = store.create_item(title="Worker change", scope={"kind": "project", "project_id": "project-a"})
    requirement = store.add_requirement(item["item_id"], "Change requested file", event_id="worker-req")
    store.begin_requirement_run(item["item_id"], requirement["requirement_id"],
                                run_id="worker-run", expected_current_run_id=None)
    execution = BoundExecution(session.conversation_id, item["item_id"],
                               requirement["requirement_id"], "worker-run")
    approved_request = ProjectWorkerHostRequest(
        project_id="project-a", task_id="task-1", task="Change requested file",
        read_targets=("README.md",), write_targets=("README.md",),
    )
    binding = ProjectWorkerWorkRecordBinding(execution, approved_request)
    outcome = ProjectWorkerHostOutcome(
        worker_id="worker-1", run_id="worker-run", turn_epoch=4,
        conversation_id=session.conversation_id, project_id="project-a", task_id="task-1",
        status="model_turn_finished", acceptance_status="not_run",
    )
    return session, store, item["item_id"], requirement["requirement_id"], binding, outcome


def test_worker_model_turn_settles_only_unknown_lifecycle_without_card_publication(tmp_path):
    session, store, item_id, requirement_id, binding, outcome = _fixture(tmp_path)

    result = settle_project_worker_work_record(
        store, binding, outcome, sequence=7,
    )

    assert result.status == "recorded"
    item = WorkRecordStore(session).get_item(item_id)
    requirement = next(row for row in item["requirements"] if row["requirement_id"] == requirement_id)
    run = next(row for row in item["runs"] if row["run_id"] == "worker-run")
    assert requirement["status"] == run["status"] == "unknown"
    assert run["verification"]["status"] == "not_verified"
    assert run["verification"]["host_execution"] == {
        "sequence": 7, "finish_reason": "unknown",
    }
    assert store.list_publication_intents() == []


def test_mismatched_worker_run_cannot_settle_a_different_recorded_run(tmp_path):
    _session, store, item_id, _requirement_id, binding, outcome = _fixture(tmp_path)
    mismatched = ProjectWorkerHostOutcome(
        worker_id=outcome.worker_id, run_id="other-run", turn_epoch=outcome.turn_epoch,
        conversation_id=outcome.conversation_id, project_id=outcome.project_id,
        task_id=outcome.task_id, status="model_turn_finished",
    )

    result = settle_project_worker_work_record(store, binding, mismatched, sequence=7)

    assert result.status == "rejected"
    assert store.get_item(item_id)["runs"] == []


def test_worker_cannot_settle_a_different_project_or_claim_host_acceptance(tmp_path):
    _session, store, item_id, _requirement_id, binding, outcome = _fixture(tmp_path)
    wrong_project = ProjectWorkerHostOutcome(
        worker_id=outcome.worker_id, run_id=outcome.run_id, turn_epoch=outcome.turn_epoch,
        conversation_id=outcome.conversation_id, project_id="project-b", task_id=outcome.task_id,
        status="model_turn_finished",
    )
    claimed_acceptance = ProjectWorkerHostOutcome(
        worker_id=outcome.worker_id, run_id=outcome.run_id, turn_epoch=outcome.turn_epoch,
        conversation_id=outcome.conversation_id, project_id=outcome.project_id, task_id=outcome.task_id,
        status="model_turn_finished", acceptance_status="accepted",
    )

    assert settle_project_worker_work_record(store, binding, wrong_project, sequence=7).status == "rejected"
    assert settle_project_worker_work_record(store, binding, claimed_acceptance, sequence=7).status == "rejected"
    assert store.get_item(item_id)["runs"] == []


def test_same_project_different_task_under_same_parent_run_cannot_settle_wrong_requirement(tmp_path):
    session, store, item_id, first_requirement_id, first_binding, first_outcome = _fixture(tmp_path)
    second = store.add_requirement(item_id, "Task two requirement", event_id="worker-req-2")
    store.begin_requirement_run(item_id, second["requirement_id"],
                                run_id="worker-run", expected_current_run_id=None)
    second_task_outcome = ProjectWorkerHostOutcome(
        worker_id="worker-2", run_id="worker-run", turn_epoch=4,
        conversation_id=first_outcome.conversation_id, project_id="project-a", task_id="task-2",
        status="model_turn_finished", acceptance_status="not_run",
    )

    result = settle_project_worker_work_record(store, first_binding, second_task_outcome, sequence=7)

    assert result.status == "rejected"
    assert store.get_item(item_id)["runs"] == []

    second_request = ProjectWorkerHostRequest(
        project_id="project-a", task_id="task-2", task="Task two requirement",
        read_targets=("README.md",), write_targets=("README.md",),
    )
    second_binding = ProjectWorkerWorkRecordBinding(
        BoundExecution(session.conversation_id, item_id, second["requirement_id"], "worker-run"),
        second_request,
    )
    correctly_bound = settle_project_worker_work_record(
        store, second_binding, second_task_outcome, sequence=7,
    )
    assert correctly_bound.status == "recorded"
    run = store.get_item(item_id)["runs"][0]
    assert run["requirement_id"] == second["requirement_id"]
    assert run["requirement_id"] != first_requirement_id


def test_worker_settlement_rejects_a_parent_lifecycle_receipt_for_another_run(tmp_path):
    _session, store, item_id, _requirement_id, binding, outcome = _fixture(tmp_path)
    foreign_receipt = ExecutionReceipt(
        session_id=binding.execution.session_id,
        item_id=binding.execution.item_id,
        requirement_id=binding.execution.requirement_id,
        run_id="other-run", sequence=7, outcome="succeeded", finish_reason="completed",
    )

    result = settle_project_worker_work_record(
        store, binding, outcome, sequence=7, execution_receipt=foreign_receipt,
    )

    assert result.status == "rejected"
    assert store.get_item(item_id)["runs"] == []


def test_settlement_persists_bound_read_attempts_separately_from_parent_tool_receipts(tmp_path):
    from project_worker_execution import ProjectReadAttempt
    from project_worker_model_loop import ProjectWorkerModelOutcome

    session, store, item_id, _requirement_id, binding, outcome = _fixture(tmp_path)
    model_turn = ProjectWorkerModelOutcome(
        project_id=outcome.project_id, status="model_turn_finished",
        read_attempts=(
            ProjectReadAttempt(1, "README.md", 1, "retrying", "sharing_violation"),
            ProjectReadAttempt(1, "README.md", 1, "cancelled"),
        ),
    )
    bound_outcome = ProjectWorkerHostOutcome(
        worker_id=outcome.worker_id, run_id=outcome.run_id, turn_epoch=outcome.turn_epoch,
        conversation_id=outcome.conversation_id, project_id=outcome.project_id,
        task_id=outcome.task_id, status=outcome.status, model_turn=model_turn,
    )

    result = settle_project_worker_work_record(store, binding, bound_outcome, sequence=7)
    assert result.status == "recorded"
    replay = settle_project_worker_work_record(store, binding, bound_outcome, sequence=7)
    assert replay == result
    run = WorkRecordStore(session).get_item(item_id)["runs"][0]
    assert run["status"] == "unknown"
    assert run["verification"]["status"] == "not_verified"
    assert run["verification"]["host_execution"]["project_worker_read_attempts"] == {
        "available": True,
        "attempts": [
            {"read_id": 1, "path": "README.md", "attempt": 1,
             "result": "retrying", "error_code": "sharing_violation"},
            {"read_id": 1, "path": "README.md", "attempt": 1,
             "result": "cancelled", "error_code": ""},
        ],
    }
    assert "host_tool_receipts" not in run["verification"]["host_execution"]


def test_settlement_persists_missing_worker_read_evidence_as_unavailable(tmp_path):
    from project_worker_model_loop import ProjectWorkerModelOutcome

    session, store, item_id, _requirement_id, binding, outcome = _fixture(tmp_path)
    outcome = ProjectWorkerHostOutcome(
        worker_id=outcome.worker_id, run_id=outcome.run_id, turn_epoch=outcome.turn_epoch,
        conversation_id=outcome.conversation_id, project_id=outcome.project_id,
        task_id=outcome.task_id, status=outcome.status,
        model_turn=ProjectWorkerModelOutcome(
            project_id=outcome.project_id, status="model_turn_finished", read_attempts=None,
        ),
    )

    result = settle_project_worker_work_record(store, binding, outcome, sequence=7)

    assert result.status == "recorded"
    run = WorkRecordStore(session).get_item(item_id)["runs"][0]
    assert run["verification"]["host_execution"]["project_worker_read_attempts"] == {
        "available": False, "attempts": [],
    }

def test_single_parent_run_binding_rejects_wrong_actual_host_reservation(tmp_path):
    from dataclasses import replace
    from project_worker_host import ProjectWorkerReservation
    session, store, item_id, _, old_binding, _ = _fixture(tmp_path)
    reservation = ProjectWorkerReservation(
        reservation_id="host-reservation", worker_id="actual-worker", worker_run_id="actual-worker-run",
        parent_run_id=old_binding.execution.run_id, turn_epoch=4, conversation_id=session.conversation_id,
        project_id="project-a", task_id="task-1",
    )
    binding = ProjectWorkerWorkRecordBinding(old_binding.execution, old_binding.request, reservation)
    outcome = ProjectWorkerHostOutcome(
        worker_id=reservation.worker_id, run_id=reservation.parent_run_id, turn_epoch=reservation.turn_epoch,
        conversation_id=session.conversation_id, project_id="project-a", task_id="task-1",
        status="model_turn_finished", parent_run_id=reservation.parent_run_id,
        worker_run_id=reservation.worker_run_id, reservation_id=reservation.reservation_id,
    )
    for bad in (replace(outcome, reservation_id="other"), replace(outcome, worker_id="other"),
                replace(outcome, worker_run_id="other"), replace(outcome, turn_epoch=5)):
        assert settle_project_worker_work_record(store, binding, bad, sequence=7).status == "rejected"
        assert store.get_item(item_id)["runs"] == []
    assert settle_project_worker_work_record(store, binding, outcome, sequence=7).status == "recorded"


def test_worker_settlement_persists_verified_write_receipts_without_claiming_completion(tmp_path):
    session, store, item_id, requirement_id, binding, outcome = _fixture(tmp_path)
    model_turn = ProjectWorkerModelOutcome(
        project_id="project-a", status="model_turn_finished",
        writes=(ProjectWorkerWriteReceipt(
            path="NOTES.md", status="written", verification="content_match",
            characters_read_back=123, readback_succeeded=True,
        ),),
    )
    outcome = ProjectWorkerHostOutcome(
        worker_id=outcome.worker_id, run_id=outcome.run_id, turn_epoch=outcome.turn_epoch,
        conversation_id=outcome.conversation_id, project_id=outcome.project_id,
        task_id=outcome.task_id, status="model_turn_finished", model_turn=model_turn,
        acceptance_status="not_run",
    )

    result = settle_project_worker_work_record(store, binding, outcome, sequence=8)

    assert result.status == "recorded"
    item = WorkRecordStore(session).get_item(item_id)
    requirement = next(row for row in item["requirements"] if row["requirement_id"] == requirement_id)
    run = next(row for row in item["runs"] if row["run_id"] == "worker-run")
    assert requirement["status"] == run["status"] == "unknown"
    assert run["verification"]["status"] == "not_verified"
    assert run["result_summary"] == (
        "Worker write receipts: 1 target(s) were read back and matched; overall requirement outcome remains unverified."
    )
    assert run["verification"]["host_execution"]["project_worker_write_receipts"] == [{
        "path": "NOTES.md", "status": "written", "verification": "content_match",
        "characters_read_back": 123, "readback_succeeded": True, "error_code": "",
    }]
