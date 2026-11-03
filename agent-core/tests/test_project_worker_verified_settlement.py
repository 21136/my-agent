from __future__ import annotations

import sys
from pathlib import Path

import pytest

_CORE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_CORE))

from paths import AgentPaths
from project_worker_host import ProjectWorkerHostOutcome, ProjectWorkerHostRequest
from project_worker_work_record_settlement import (
    ProjectWorkerVerificationReceipt,
    ProjectWorkerWorkRecordBinding,
    settle_project_worker_work_record,
)
from session import Session, SessionMeta
from work_record_settlement import BoundExecution
from work_records import WorkRecordStore


def _fixture(tmp_path, conversation_id="verified-worker-session"):
    paths = AgentPaths(agent_root=tmp_path, workspace=tmp_path / "workspace",
                       data=tmp_path / "data", evolve=tmp_path / "evolve")
    session_dir = paths.data / "sessions" / conversation_id
    session_dir.mkdir(parents=True)
    session = Session(conversation_id=conversation_id, session_dir=session_dir,
                      goal="", meta=SessionMeta(project_id="project-a"), messages=[], paths=paths)
    store = WorkRecordStore(session)
    item = store.create_item(title="Worker change", scope={"kind": "project", "project_id": "project-a"})
    requirement = store.add_requirement(item["item_id"], "Change requested file", event_id="verified-worker-req")
    store.begin_requirement_run(item["item_id"], requirement["requirement_id"],
                                run_id="worker-run", expected_current_run_id=None)
    execution = BoundExecution(session.conversation_id, item["item_id"],
                               requirement["requirement_id"], "worker-run")
    request = ProjectWorkerHostRequest(
        project_id="project-a", task_id="task-1", task="Change requested file",
        read_targets=("README.md",), write_targets=("README.md",),
    )
    binding = ProjectWorkerWorkRecordBinding(execution, request)
    outcome = ProjectWorkerHostOutcome(
        worker_id="worker-1", run_id="worker-run", turn_epoch=4,
        conversation_id=session.conversation_id, project_id="project-a", task_id="task-1",
        status="model_turn_finished", acceptance_status="not_run",
    )
    return session, store, item["item_id"], requirement["requirement_id"], binding, outcome


def _verification(binding, **overrides):
    values = {
        "session_id": binding.execution.session_id,
        "item_id": binding.execution.item_id,
        "requirement_id": binding.execution.requirement_id,
        "run_id": binding.execution.run_id,
        "project_id": binding.request.project_id,
        "task_id": binding.request.task_id,
        "reservation_id": binding.reservation.reservation_id if binding.reservation else "",
        "requirement_text": binding.request.task,
        "verifier_id": "host_project_test_runner",
        "status": "passed",
        "evidence_refs": ("tests/test_feature.py::test_requested_behavior",),
    }
    values.update(overrides)
    return ProjectWorkerVerificationReceipt(**values)


def test_caller_constructed_verification_claim_is_not_accepted(tmp_path):
    from project_worker_host import ProjectWorkerReservation

    session, store, item_id, requirement_id, binding, _outcome = _fixture(tmp_path)
    reservation = ProjectWorkerReservation(
        reservation_id="reservation-verified", worker_id="worker-1", worker_run_id="worker-run",
        parent_run_id="parent-run", turn_epoch=4, conversation_id=session.conversation_id,
        project_id="project-a", task_id="task-1",
    )
    binding = ProjectWorkerWorkRecordBinding(binding.execution, binding.request, reservation)
    outcome = ProjectWorkerHostOutcome(
        worker_id="worker-1", run_id="parent-run", turn_epoch=4,
        conversation_id=session.conversation_id, project_id="project-a", task_id="task-1",
        status="model_turn_finished", parent_run_id="parent-run", worker_run_id="worker-run",
        reservation_id="reservation-verified",
    )

    result = settle_project_worker_work_record(
        store, binding, outcome, sequence=7,
        verification_receipt=_verification(binding),
    )

    assert result.status == "rejected"
    assert result.reason == "worker_requirement_verifier_not_integrated"
    assert store.get_item(item_id)["runs"] == []
    assert store.list_publication_intents() == []


def test_worker_turn_and_write_receipts_without_verification_remain_unknown(tmp_path):
    _session, store, item_id, _requirement_id, binding, outcome = _fixture(tmp_path)

    result = settle_project_worker_work_record(store, binding, outcome, sequence=7)

    assert result.status == "recorded"
    assert store.get_item(item_id)["runs"][0]["status"] == "unknown"


def test_mismatched_or_nonpassing_verification_never_marks_task_complete(tmp_path):
    _session, store, item_id, _requirement_id, binding, outcome = _fixture(tmp_path)
    wrong_requirement = _verification(binding, requirement_text="different task")

    result = settle_project_worker_work_record(
        store, binding, outcome, sequence=7,
        verification_receipt=wrong_requirement,
    )

    assert result.record_status == "unknown"
    assert store.get_item(item_id)["runs"] == []


def test_matching_reserved_host_block_is_recorded_as_blocked_not_unknown(tmp_path):
    from project_worker_host import ProjectWorkerReservation

    session, store, item_id, _requirement_id, binding, _outcome = _fixture(tmp_path)
    reservation = ProjectWorkerReservation(
        reservation_id="reservation-1", worker_id="worker-1", worker_run_id="worker-run",
        parent_run_id="parent-run", turn_epoch=4, conversation_id=session.conversation_id,
        project_id="project-a", task_id="task-1",
    )
    binding = ProjectWorkerWorkRecordBinding(binding.execution, binding.request, reservation)
    blocked = ProjectWorkerHostOutcome(
        worker_id="worker-1", run_id="parent-run", turn_epoch=4,
        conversation_id=session.conversation_id, project_id="project-a", task_id="task-1",
        status="blocked", parent_run_id="parent-run", worker_run_id="worker-run",
        reservation_id="reservation-1",
    )

    result = settle_project_worker_work_record(store, binding, blocked, sequence=8, publish_project_update=False)

    assert store.list_publication_intents() == []
    assert result.status == "recorded"
    run = store.get_item(item_id)["runs"][0]
    assert run["status"] == "blocked"
    assert run["verification"]["status"] == "not_verified"
    assert run["verification"]["host_execution"]["host_status"] == "blocked"

from project_worker_work_record_settlement import ProjectWorkerCancellationReceipt


def test_generic_worker_cancelled_event_remains_unknown_without_user_action_evidence(tmp_path):
    from project_worker_host import ProjectWorkerReservation

    session, store, item_id, _requirement_id, binding, _outcome = _fixture(tmp_path)
    reservation = ProjectWorkerReservation(
        reservation_id="reservation-cancel", worker_id="worker-1", worker_run_id="worker-run",
        parent_run_id="parent-run", turn_epoch=4, conversation_id=session.conversation_id,
        project_id="project-a", task_id="task-1",
    )
    binding = ProjectWorkerWorkRecordBinding(binding.execution, binding.request, reservation)
    cancelled = ProjectWorkerHostOutcome(
        worker_id="worker-1", run_id="parent-run", turn_epoch=4,
        conversation_id=session.conversation_id, project_id="project-a", task_id="task-1",
        status="cancelled", parent_run_id="parent-run", worker_run_id="worker-run",
        reservation_id="reservation-cancel",
    )

    result = settle_project_worker_work_record(store, binding, cancelled, sequence=9)

    assert result.record_status == "unknown"
    assert store.get_item(item_id)["runs"][0]["status"] == "unknown"


def test_exact_host_user_action_receipt_maps_cancelled_worker_to_stopped(tmp_path):
    from project_worker_host import ProjectWorkerReservation

    session, store, item_id, _requirement_id, binding, _outcome = _fixture(tmp_path)
    reservation = ProjectWorkerReservation(
        reservation_id="reservation-cancel", worker_id="worker-1", worker_run_id="worker-run",
        parent_run_id="parent-run", turn_epoch=4, conversation_id=session.conversation_id,
        project_id="project-a", task_id="task-1",
    )
    binding = ProjectWorkerWorkRecordBinding(binding.execution, binding.request, reservation)
    cancelled = ProjectWorkerHostOutcome(
        worker_id="worker-1", run_id="parent-run", turn_epoch=4,
        conversation_id=session.conversation_id, project_id="project-a", task_id="task-1",
        status="cancelled", parent_run_id="parent-run", worker_run_id="worker-run",
        reservation_id="reservation-cancel",
    )
    receipt = ProjectWorkerCancellationReceipt(
        session_id=session.conversation_id, item_id=item_id,
        requirement_id=binding.execution.requirement_id, run_id="worker-run",
        project_id="project-a", task_id="task-1", reservation_id="reservation-cancel",
        user_action_id="user-stop-17", source="user_action",
    )

    result = settle_project_worker_work_record(
        store, binding, cancelled, sequence=9, cancellation_receipt=receipt,
    )

    assert result.record_status == "stopped"
    run = store.get_item(item_id)["runs"][0]
    assert run["status"] == "stopped"
    assert run["verification"]["status"] == "not_verified"
    assert "remain unverified" in run["result_summary"]

def test_cancel_receipt_for_another_reservation_is_rejected_and_not_stopped(tmp_path):
    from project_worker_host import ProjectWorkerReservation

    session, store, item_id, _requirement_id, binding, _outcome = _fixture(tmp_path)
    reservation = ProjectWorkerReservation(
        reservation_id="reservation-cancel", worker_id="worker-1", worker_run_id="worker-run",
        parent_run_id="parent-run", turn_epoch=4, conversation_id=session.conversation_id,
        project_id="project-a", task_id="task-1",
    )
    binding = ProjectWorkerWorkRecordBinding(binding.execution, binding.request, reservation)
    cancelled = ProjectWorkerHostOutcome(
        worker_id="worker-1", run_id="parent-run", turn_epoch=4,
        conversation_id=session.conversation_id, project_id="project-a", task_id="task-1",
        status="cancelled", parent_run_id="parent-run", worker_run_id="worker-run",
        reservation_id="reservation-cancel",
    )
    receipt = ProjectWorkerCancellationReceipt(
        session_id=session.conversation_id, item_id=item_id,
        requirement_id=binding.execution.requirement_id, run_id="worker-run",
        project_id="project-a", task_id="task-1", reservation_id="foreign-reservation",
        user_action_id="user-stop-18", source="user_action",
    )

    result = settle_project_worker_work_record(
        store, binding, cancelled, sequence=10, cancellation_receipt=receipt,
    )

    assert result.status == "rejected"
    assert result.record_status == "unknown"
    assert store.get_item(item_id)["runs"] == []

def _current_dialogue_batch_fixture(tmp_path):
    import hashlib
    from project_worker_host import ProjectWorkerReservation

    paths = AgentPaths(agent_root=tmp_path, workspace=tmp_path / "workspace",
                       data=tmp_path / "data", evolve=tmp_path / "evolve")
    session_dir = paths.data / "sessions" / "verified-worker-batch-session"
    session_dir.mkdir(parents=True)
    session = Session(conversation_id="verified-worker-batch-session", session_dir=session_dir,
                      goal="", meta=SessionMeta(), messages=[], paths=paths)
    store = WorkRecordStore(session)
    batch_digest = "b" * 64
    original = "Split this request across two projects."
    item = store.create_item_with_requirement(
        title="多项目任务批次", scope={"kind": "independent"}, requirement=original,
        event_id=f"worker-batch-original-{batch_digest}",
        idempotency_key=f"worker-batch-original:{batch_digest}",
    )
    bindings = []
    outcomes = []
    for index, project_id in enumerate(("project-alpha", "project-beta")):
        task_text = f"Update {project_id} requirement."
        added = store.add_requirement(
            item["item_id"], task_text,
            event_id=f"worker-batch-task-{batch_digest}-{index}",
            source_ref={"kind": "project", "id": project_id},
            idempotency_key=f"worker-batch-task:{batch_digest}:{index}",
        )
        requirement = added
        requirement_id = requirement["requirement_id"]
        task_id = "task-" + hashlib.sha256(
            "\0".join((session.conversation_id, item["item_id"], requirement_id, project_id)).encode("utf-8")
        ).hexdigest()
        worker_run_id = f"worker-run-{index}"
        reservation = ProjectWorkerReservation(
            reservation_id=f"reservation-{index}", worker_id=f"worker-{index}",
            worker_run_id=worker_run_id, parent_run_id="parent-run", turn_epoch=12,
            conversation_id=session.conversation_id, project_id=project_id, task_id=task_id,
        )
        store.begin_requirement_run(item["item_id"], requirement_id,
                                    run_id=worker_run_id, expected_current_run_id=None)
        binding = ProjectWorkerWorkRecordBinding(
            BoundExecution(session.conversation_id, item["item_id"], requirement_id, worker_run_id),
            ProjectWorkerHostRequest(project_id, task_id, task_text, ("README.md",), ("README.md",)),
            reservation,
        )
        outcome = ProjectWorkerHostOutcome(
            worker_id=reservation.worker_id, run_id=reservation.parent_run_id,
            turn_epoch=reservation.turn_epoch, conversation_id=session.conversation_id,
            project_id=project_id, task_id=task_id, status="model_turn_finished",
            parent_run_id=reservation.parent_run_id, worker_run_id=worker_run_id,
            reservation_id=reservation.reservation_id,
        )
        bindings.append(binding)
        outcomes.append(outcome)
    return store, item["item_id"], tuple(bindings), tuple(outcomes)


def test_caller_constructed_claims_cannot_verify_batch_requirements(tmp_path):
    store, item_id, bindings, outcomes = _current_dialogue_batch_fixture(tmp_path)

    results = [settle_project_worker_work_record(
        store, binding, outcome, sequence=20 + index,
        verification_receipt=_verification(binding),
    ) for index, (binding, outcome) in enumerate(zip(bindings, outcomes))]

    assert [result.status for result in results] == ["rejected", "rejected"]
    assert [result.reason for result in results] == [
        "worker_requirement_verifier_not_integrated", "worker_requirement_verifier_not_integrated",
    ]
    assert store.get_item(item_id)["runs"] == []
    assert store.list_publication_intents() == []
    assert bindings[0].request.project_id != bindings[1].request.project_id
    assert bindings[0].request.task_id != bindings[1].request.task_id


def test_independent_item_with_ordinary_project_source_ref_is_not_worker_batch_authority(tmp_path):
    import hashlib
    from project_worker_host import ProjectWorkerReservation

    paths = AgentPaths(agent_root=tmp_path, workspace=tmp_path / "workspace",
                       data=tmp_path / "data", evolve=tmp_path / "evolve")
    session_dir = paths.data / "sessions" / "ordinary-source-ref-session"
    session_dir.mkdir(parents=True)
    session = Session(conversation_id="ordinary-source-ref-session", session_dir=session_dir,
                      goal="", meta=SessionMeta(), messages=[], paths=paths)
    store = WorkRecordStore(session)
    item = store.create_item(title="Ordinary independent note", scope={"kind": "independent"})
    requirement = store.add_requirement(
        item["item_id"], "Change project alpha.", event_id="ordinary-project-source",
        source_ref={"kind": "project", "id": "project-alpha"},
    )
    req_id = requirement["requirement_id"]
    task_id = "task-" + hashlib.sha256(
        "\0".join((session.conversation_id, item["item_id"], req_id, "project-alpha")).encode("utf-8")
    ).hexdigest()
    reservation = ProjectWorkerReservation(
        reservation_id="ordinary-reservation", worker_id="ordinary-worker", worker_run_id="ordinary-run",
        parent_run_id="ordinary-parent", turn_epoch=1, conversation_id=session.conversation_id,
        project_id="project-alpha", task_id=task_id,
    )
    store.begin_requirement_run(item["item_id"], req_id, run_id="ordinary-run", expected_current_run_id=None)
    binding = ProjectWorkerWorkRecordBinding(
        BoundExecution(session.conversation_id, item["item_id"], req_id, "ordinary-run"),
        ProjectWorkerHostRequest("project-alpha", task_id, "Change project alpha.", ("README.md",), ("README.md",)),
        reservation,
    )
    outcome = ProjectWorkerHostOutcome(
        worker_id="ordinary-worker", run_id="ordinary-parent", turn_epoch=1,
        conversation_id=session.conversation_id, project_id="project-alpha", task_id=task_id,
        status="model_turn_finished", parent_run_id="ordinary-parent", worker_run_id="ordinary-run",
        reservation_id="ordinary-reservation",
    )

    result = settle_project_worker_work_record(
        store, binding, outcome, sequence=2, verification_receipt=_verification(binding),
    )

    assert result.status == "rejected"
    assert result.record_status == "unknown"
    assert store.get_item(item["item_id"])["runs"] == []


def test_current_dialogue_batch_rejects_verification_crossed_between_projects(tmp_path):
    store, item_id, bindings, outcomes = _current_dialogue_batch_fixture(tmp_path)
    wrong_project_receipt = _verification(bindings[0], project_id=bindings[1].request.project_id)

    result = settle_project_worker_work_record(
        store, bindings[0], outcomes[0], sequence=30,
        verification_receipt=wrong_project_receipt,
    )

    assert result.status == "rejected"
    assert result.record_status == "unknown"
    assert store.get_item(item_id)["runs"] == []

def test_caller_constructed_verification_claim_cannot_publish_completion(tmp_path):
    from uuid import uuid4
    from project_worker_host import ProjectWorkerReservation
    session, store, item_id, requirement_id, binding, _ = _fixture(tmp_path, "abc12345-1234-4abc-8def-1234567890ab")
    run_id = str(uuid4())
    store.begin_requirement_run(item_id, requirement_id, run_id=run_id, expected_current_run_id="worker-run")
    execution = BoundExecution(session.conversation_id, item_id, requirement_id, run_id)
    reservation = ProjectWorkerReservation(
        reservation_id="verified-reservation", worker_id="worker-1", worker_run_id=run_id,
        parent_run_id="parent-run", turn_epoch=4, conversation_id=session.conversation_id,
        project_id="project-a", task_id="task-1")
    binding = ProjectWorkerWorkRecordBinding(execution, binding.request, reservation)
    outcome = ProjectWorkerHostOutcome(worker_id="worker-1", run_id="parent-run", turn_epoch=4,
        conversation_id=session.conversation_id, project_id="project-a", task_id="task-1",
        status="model_turn_finished", parent_run_id="parent-run", worker_run_id=run_id,
        reservation_id="verified-reservation")

    result = settle_project_worker_work_record(
        store, binding, outcome, sequence=7,
        verification_receipt=_verification(binding), publish_project_update=True,
    )

    assert result.status == "rejected"
    assert result.reason == "worker_requirement_verifier_not_integrated"
    assert store.get_item(item_id)["runs"] == []
    assert store.list_publication_intents() == []


def test_reserved_blocked_worker_project_intent_is_atomic_and_publishable(tmp_path, monkeypatch):
    from uuid import uuid4
    from dataclasses import replace
    from project_worker_host import ProjectWorkerReservation
    session, store, item_id, requirement_id, original, _ = _fixture(tmp_path, "abc12345-1234-4abc-8def-1234567890ab")
    run_id = str(uuid4())
    store.begin_requirement_run(item_id, requirement_id, run_id=run_id, expected_current_run_id="worker-run")
    execution = BoundExecution(session.conversation_id, item_id, requirement_id, run_id)
    reservation = ProjectWorkerReservation(reservation_id="blocked-reservation", worker_id="worker-1", worker_run_id=run_id,
        parent_run_id="parent-run", turn_epoch=4, conversation_id=session.conversation_id, project_id="project-a", task_id="task-1")
    binding = ProjectWorkerWorkRecordBinding(execution, original.request, reservation)
    outcome = ProjectWorkerHostOutcome(worker_id="worker-1", run_id="parent-run", turn_epoch=4,
        conversation_id=session.conversation_id, project_id="project-a", task_id="task-1", status="blocked",
        parent_run_id="parent-run", worker_run_id=run_id, reservation_id=reservation.reservation_id)
    kwargs = dict(sequence=7, publish_project_update=True)
    assert settle_project_worker_work_record(store, binding, replace(outcome, reservation_id="wrong"), **kwargs).status == "rejected"
    assert store.list_publication_intents() == [] and store.get_item(item_id)["runs"] == []
    original_upsert = store._upsert_server_publication_intent
    monkeypatch.setattr(store, "_upsert_server_publication_intent", lambda *_a, **_k: (_ for _ in ()).throw(OSError("intent failure")))
    assert settle_project_worker_work_record(store, binding, outcome, **kwargs).status == "executed_not_recorded"
    assert store.list_publication_intents() == [] and store.get_item(item_id)["runs"] == []
    monkeypatch.setattr(store, "_upsert_server_publication_intent", original_upsert)
    result = settle_project_worker_work_record(store, binding, outcome, **kwargs)
    assert result.status == "recorded" and result.record_status == "blocked"
    pending = store.list_publication_intents()
    assert len(pending) == 1 and pending[0]["result_id"] == result.result_id
    from work_card_publish_outbox import WorkCardPublicationOutbox
    assert WorkCardPublicationOutbox(session, store).remaining_seconds(pending[0]) == 0
    reopened = WorkRecordStore(session)
    assert WorkCardPublicationOutbox(session, reopened).remaining_seconds(reopened.list_publication_intents()[0]) == 0
    import work_records
    with monkeypatch.context() as restart:
        restart.setattr(work_records, "_PUBLICATION_PROCESS_INSTANCE_ID", "another-process-instance")
        assert WorkCardPublicationOutbox(session, reopened).remaining_seconds(reopened.list_publication_intents()[0]) == 0
    assert settle_project_worker_work_record(store, binding, outcome, **kwargs) == result
    assert store.list_publication_intents() == pending
    from types import SimpleNamespace
    from server import WsBridge, _publish_pending_project_work_cards
    events = []
    bridge = WsBridge(emit=events.append, paths=session.paths)
    repl = SimpleNamespace(session=session, agent=SimpleNamespace(session=session))
    assert _publish_pending_project_work_cards(repl, bridge, session, store) == 1, events
    cards = [event["card"] for event in events if event.get("type") == "work-card.present"]
    assert len(cards) == 1
    requirement = cards[0]["snapshot"]["cards"][0]["requirements"][0]
    assert requirement["status"] == "blocked" and requirement["latest_result"]["verification_status"] == "not_verified"

def test_server_bound_blocked_worker_settlement_schedules_persisted_notice(tmp_path, monkeypatch):
    import asyncio
    from types import SimpleNamespace
    import server
    from project_worker_host import ProjectWorkerReservation
    session, store, item_id, requirement_id, original, _ = _fixture(tmp_path, "abc12345-1234-4abc-8def-1234567890ab")
    events = []
    bridge = server.WsBridge(emit=events.append, paths=session.paths)
    assert bridge.begin_turn(mode="ordinary")
    run = bridge.lifecycle.snapshot()
    bridge.lifecycle_conversation_id = session.conversation_id
    store.begin_requirement_run(item_id, requirement_id, run_id=run.run_id, expected_current_run_id="worker-run")
    execution = BoundExecution(session.conversation_id, item_id, requirement_id, run.run_id)
    reservation = ProjectWorkerReservation(reservation_id="server-reservation", worker_id="worker-1",
        worker_run_id="separate-worker-run", parent_run_id=run.run_id, turn_epoch=1,
        conversation_id=session.conversation_id, project_id="project-a", task_id="task-1")
    binding = ProjectWorkerWorkRecordBinding(execution, original.request, reservation)
    outcome = ProjectWorkerHostOutcome(worker_id=reservation.worker_id, run_id=reservation.parent_run_id,
        turn_epoch=reservation.turn_epoch, conversation_id=session.conversation_id, project_id="project-a", task_id="task-1",
        status="blocked", parent_run_id=reservation.parent_run_id, worker_run_id=reservation.worker_run_id,
        reservation_id=reservation.reservation_id)
    assert bridge.finish_execution("completed", ok=True, run_id=run.run_id)
    repl = SimpleNamespace(session=session, agent=SimpleNamespace(session=session))
    monkeypatch.setattr(server, "PROJECT_WORK_CARD_MERGE_SECONDS", 30.0)
    async def settle():
        from uuid import uuid4
        from work_record_settlement import WorkRecordSettlementAdapter, ExecutionReceipt
        previous = store.add_requirement(item_id, "Prior ordinary update", event_id="ordinary-before-blocked")
        prior_run = uuid4().hex
        store.begin_requirement_run(item_id, previous["requirement_id"], run_id=prior_run, expected_current_run_id=None)
        previous_binding = BoundExecution(session.conversation_id, item_id, previous["requirement_id"], prior_run)
        receipt = ExecutionReceipt(session.conversation_id, item_id, previous["requirement_id"], prior_run, 1, "succeeded")
        assert WorkRecordSettlementAdapter(store, previous_binding).settle(receipt, publish_project_update=True).status == "recorded"
        assert server._schedule_settled_project_work_card(repl=repl, bridge=bridge, session=session, store=store, binding=previous_binding)
        assert len(store.list_publication_intents()) == 1
        assert not [event for event in events if event.get("type") == "work-card.present"]
        assert server._settle_work_record_run(current_session=session, bridge=bridge, store=store,
            binding=execution, emit=events.append, repl=repl, worker_work_record_result=(binding, outcome))
        assert len([event for event in events if event.get("type") == "work-card.present"]) == 1, events
        await asyncio.sleep(0)
    asyncio.run(settle())
    cards = [event["card"] for event in events if event.get("type") == "work-card.present"]
    assert len(cards) == 1, events
    requirement = next(row for row in cards[0]["snapshot"]["cards"][0]["requirements"] if row["requirement_id"] == requirement_id)
    assert requirement["status"] == "blocked" and requirement["latest_result"]["verification_status"] == "not_verified"

def test_server_never_promotes_worker_model_test_claims_or_runs_verification(tmp_path, monkeypatch):
    import subprocess
    import project_verify
    import server
    from project_worker_host import ProjectWorkerReservation
    from project_worker_model_loop import ProjectWorkerModelOutcome
    session, store, item_id, requirement_id, original, _ = _fixture(tmp_path, "abc12345-1234-4abc-8def-1234567890ab")
    events = []
    bridge = server.WsBridge(emit=events.append, paths=session.paths)
    assert bridge.begin_turn(mode="ordinary")
    run = bridge.lifecycle.snapshot()
    bridge.lifecycle_conversation_id = session.conversation_id
    store.begin_requirement_run(item_id, requirement_id, run_id=run.run_id, expected_current_run_id="worker-run")
    execution = BoundExecution(session.conversation_id, item_id, requirement_id, run.run_id)
    reservation = ProjectWorkerReservation(reservation_id="model-claim-reservation", worker_id="worker-1",
        worker_run_id="separate-worker-run", parent_run_id=run.run_id, turn_epoch=1,
        conversation_id=session.conversation_id, project_id="project-a", task_id="task-1")
    binding = ProjectWorkerWorkRecordBinding(execution, original.request, reservation)
    model = ProjectWorkerModelOutcome(project_id="project-a", status="model_turn_finished",
        final_text="All tests passed, fully verified", tests_run=("synthetic historical test",), verification_level="passed")
    outcome = ProjectWorkerHostOutcome(worker_id=reservation.worker_id, run_id=run.run_id, turn_epoch=1,
        conversation_id=session.conversation_id, project_id="project-a", task_id="task-1", status="model_turn_finished",
        parent_run_id=run.run_id, worker_run_id=reservation.worker_run_id,
        reservation_id=reservation.reservation_id, model_turn=model)
    assert bridge.finish_execution("completed", ok=True, run_id=run.run_id)
    def forbidden(*_args, **_kwargs):
        pytest.fail("settlement must not run an unapproved verification command")
    monkeypatch.setattr(project_verify, "run_project_tests", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    assert server._settle_work_record_run(current_session=session, bridge=bridge, store=store,
        binding=execution, emit=events.append, worker_work_record_result=(binding, outcome))
    result = store.get_item(item_id)["runs"][0]
    assert result["status"] == "unknown" and result["verification"]["status"] == "not_verified"
    assert "All tests passed" not in result["result_summary"]


def test_explicit_verified_receipt_from_another_run_is_never_current(tmp_path):
    store, item_id, bindings, outcomes = _current_dialogue_batch_fixture(tmp_path)
    stale = _verification(bindings[0], run_id="previous-run")
    result = settle_project_worker_work_record(store, bindings[0], outcomes[0], sequence=30,
                                                verification_receipt=stale)
    assert result.status == "rejected" and result.record_status == "unknown"
    assert store.get_item(item_id)["runs"] == []

@pytest.mark.parametrize("publish", [False, True])
def test_blocked_outcome_without_host_reservation_remains_unknown(tmp_path, publish):
    from dataclasses import replace
    from uuid import uuid4
    session, store, item_id, req_id, binding, outcome = _fixture(
        tmp_path, "abc12345-1234-4abc-8def-1234567890ab")
    run_id = uuid4().hex
    store.begin_requirement_run(item_id, req_id, run_id=run_id, expected_current_run_id="worker-run")
    binding = ProjectWorkerWorkRecordBinding(
        BoundExecution(session.conversation_id, item_id, req_id, run_id), binding.request)
    # An outcome's self-declared reservation/worker is not a host-captured binding.
    blocked = replace(outcome, run_id=run_id, status="blocked",
                      reservation_id="untrusted-reservation", worker_run_id="untrusted-worker-run")
    result = settle_project_worker_work_record(store, binding, blocked, sequence=8,
                                                publish_project_update=publish)
    assert result.status == "recorded" and result.record_status == "unknown"
    item = WorkRecordStore(session).get_item(item_id)
    requirement = next(row for row in item["requirements"] if row["requirement_id"] == req_id)
    run = item["runs"][0]
    assert requirement["status"] == run["status"] == "unknown"
    assert run["verification"]["status"] == "not_verified"
    assert run["verification"]["host_execution"]["finish_reason"] == "unknown"
    intents = store.list_publication_intents()
    assert len(intents) == int(publish)
    if publish:
        from work_card_publish_outbox import WorkCardPublicationOutbox
        assert intents[0]["result_id"] == result.result_id
        assert WorkCardPublicationOutbox(session, store).remaining_seconds(intents[0]) > 0


def test_publicly_constructible_verification_receipt_cannot_authenticate_host_execution(tmp_path):
    from project_worker_host import ProjectWorkerReservation

    session, store, item_id, requirement_id, binding, _ = _fixture(tmp_path, "untrusted-verifier-session")
    reservation = ProjectWorkerReservation(
        reservation_id="reservation-untrusted", worker_id="worker-1", worker_run_id="worker-run",
        parent_run_id="parent-run", turn_epoch=4, conversation_id=session.conversation_id,
        project_id="project-a", task_id="task-1",
    )
    binding = ProjectWorkerWorkRecordBinding(binding.execution, binding.request, reservation)
    outcome = ProjectWorkerHostOutcome(
        worker_id="worker-1", run_id="parent-run", turn_epoch=4,
        conversation_id=session.conversation_id, project_id="project-a", task_id="task-1",
        status="model_turn_finished", parent_run_id="parent-run", worker_run_id="worker-run",
        reservation_id="reservation-untrusted",
    )

    result = settle_project_worker_work_record(
        store, binding, outcome, sequence=7,
        verification_receipt=_verification(binding),
    )

    assert result.status == "rejected"
    assert result.reason == "worker_requirement_verifier_not_integrated"
    assert store.get_item(item_id)["runs"] == []
    requirement = next(row for row in store.get_item(item_id)["requirements"]
                       if row["requirement_id"] == requirement_id)
    assert requirement["status"] == "in_progress"
    assert "latest_result" not in requirement


def test_settlement_rejects_record_store_from_another_session(tmp_path):
    from dataclasses import replace

    _session, store, _item_id, _requirement_id, binding, outcome = _fixture(
        tmp_path, "store-session-binding")
    wrong_execution = replace(binding.execution, session_id="other-session")
    wrong_binding = ProjectWorkerWorkRecordBinding(wrong_execution, binding.request)
    wrong_outcome = replace(outcome, conversation_id="other-session")
    from project_worker_work_record_settlement import _requirement_matches_bound_scope
    assert not _requirement_matches_bound_scope(store, store.get_item(_item_id), wrong_binding)

    result = settle_project_worker_work_record(
        store, wrong_binding, wrong_outcome, sequence=7,
    )

    assert result.status == "rejected"
    assert store.get_item(_item_id)["runs"] == []
