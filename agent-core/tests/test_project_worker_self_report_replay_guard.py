from __future__ import annotations

from project_worker_work_record_settlement import settle_project_worker_work_record
from tests.test_project_worker_requirement_integration import setup_case


def test_consumed_self_report_capability_does_not_accumulate_on_long_lived_bridge(tmp_path):
    bridge, host, session, store, binding, outcome, live, _events = setup_case(tmp_path)
    token = bridge.verify_project_worker_requirement(
        host=host, session=session, store=store, binding=binding, outcome=outcome,
        target="test_feature.py::test_target", live_binding_check=live,
    )
    assert token is not None
    forged = type(token)()
    forged_result = settle_project_worker_work_record(
        store, binding, outcome, sequence=bridge.lifecycle.snapshot().sequence,
        verification_receipt=forged, verification_host=host, verification_bridge=bridge,
    )
    assert forged_result.status == "rejected"
    assert store.get_item(binding.execution.item_id)["runs"] == []
    assert bridge.finish_execution("completed", ok=True, run_id=binding.execution.run_id)

    first = settle_project_worker_work_record(
        store, binding, outcome, sequence=bridge.lifecycle.snapshot().sequence,
        verification_receipt=token, verification_host=host, verification_bridge=bridge,
    )
    second = settle_project_worker_work_record(
        store, binding, outcome, sequence=bridge.lifecycle.snapshot().sequence,
        verification_receipt=token, verification_host=host, verification_bridge=bridge,
    )

    assert first.status == "recorded"
    assert second.status == "rejected" and second.reason == "worker_self_report_receipt_replayed"
    assert not hasattr(bridge, "_consumed_project_worker_self_report_receipts")
    assert not host._requirement_verifications
    rows = store.get_item(binding.execution.item_id)["runs"]
    assert len(rows) == 1 and rows[0]["status"] == "unknown"


def test_cross_connection_self_report_ticket_cannot_replace_existing_run_result(tmp_path):
    import server
    from types import SimpleNamespace

    bridge, host, session, store, binding, outcome, live, events = setup_case(tmp_path)
    token = bridge.verify_project_worker_requirement(
        host=host, session=session, store=store, binding=binding, outcome=outcome,
        target="test_feature.py::test_target", live_binding_check=live,
    )
    assert token is not None
    execution = binding.execution
    store.record_result_if_current_run(
        execution.item_id, execution.requirement_id, run_id=execution.run_id,
        result_id="existing-result", status="unknown", summary="preserve this result",
        verification={"status": "not_verified"}, sources=[],
    )
    before = store.get_item(execution.item_id)["runs"]
    assert bridge.finish_execution("completed", ok=True, run_id=execution.run_id)

    foreign_bridge = server.WsBridge(emit=events.append, paths=session.paths)
    # Give the foreign connection the strongest possible lifecycle coincidence;
    # the receipt still belongs to the bridge that issued it.
    foreign_bridge.lifecycle = bridge.lifecycle
    foreign_bridge.lifecycle_conversation_id = bridge.lifecycle_conversation_id
    foreign_bridge._session_provider = lambda: session
    repl = SimpleNamespace(session=session, agent=SimpleNamespace(session=session))
    server._settle_work_record_run(
        current_session=session, bridge=foreign_bridge, store=store, binding=execution,
        emit=events.append, repl=repl,
        worker_work_record_result=(binding, outcome), worker_verification_receipt=token,
        agent=SimpleNamespace(project_worker_host=host, session=session),
    )

    assert store.get_item(execution.item_id)["runs"] == before


def test_foreign_host_cannot_turn_a_valid_ticket_into_lifecycle_fallback(tmp_path):
    import server
    from types import SimpleNamespace
    from project_worker_host import ProjectWorkerHost

    bridge, host, session, store, binding, outcome, live, events = setup_case(tmp_path)
    token = bridge.verify_project_worker_requirement(
        host=host, session=session, store=store, binding=binding, outcome=outcome,
        target="test_feature.py::test_target", live_binding_check=live,
    )
    assert token is not None
    execution = binding.execution
    store.record_result_if_current_run(
        execution.item_id, execution.requirement_id, run_id=execution.run_id,
        result_id="existing-result", status="unknown", summary="preserve this result",
        verification={"status": "not_verified"}, sources=[],
    )
    before = store.get_item(execution.item_id)["runs"]
    assert bridge.finish_execution("completed", ok=True, run_id=execution.run_id)
    repl = SimpleNamespace(session=session, agent=SimpleNamespace(session=session))
    server._settle_work_record_run(
        current_session=session, bridge=bridge, store=store, binding=execution,
        emit=events.append, repl=repl,
        worker_work_record_result=(binding, outcome), worker_verification_receipt=token,
        agent=SimpleNamespace(project_worker_host=ProjectWorkerHost(lambda: None), session=session),
    )

    assert store.get_item(execution.item_id)["runs"] == before


def test_cross_run_ticket_cannot_replace_the_newer_run_result(tmp_path):
    import server
    from types import SimpleNamespace
    from project_worker_host import ProjectWorkerHostOutcome, ProjectWorkerHostRequest
    from project_worker_work_record_settlement import ProjectWorkerWorkRecordBinding
    from work_record_settlement import BoundExecution

    bridge, host, session, store, binding, outcome, live, events = setup_case(tmp_path)
    token = bridge.verify_project_worker_requirement(
        host=host, session=session, store=store, binding=binding, outcome=outcome,
        target="test_feature.py::test_target", live_binding_check=live,
    )
    assert token is not None
    first_run = binding.execution.run_id
    assert bridge.finish_execution("completed", ok=True, run_id=first_run)
    assert bridge.begin_turn(mode="ordinary")
    second_run = bridge.lifecycle.snapshot().run_id
    store.begin_requirement_run(
        binding.execution.item_id, binding.execution.requirement_id,
        run_id=second_run, expected_current_run_id=first_run,
    )
    execution = BoundExecution(
        binding.execution.session_id, binding.execution.item_id,
        binding.execution.requirement_id, second_run,
    )
    request = ProjectWorkerHostRequest("project-a", "task-2", "Feature works", (), ())
    second_binding = ProjectWorkerWorkRecordBinding(execution, request)
    second_outcome = ProjectWorkerHostOutcome(
        worker_id="", run_id=second_run, turn_epoch=0,
        conversation_id=session.conversation_id, project_id="project-a", task_id="task-2",
        status="model_turn_finished",
    )
    store.record_result_if_current_run(
        execution.item_id, execution.requirement_id, run_id=second_run,
        result_id="newer-result", status="unknown", summary="newer run result",
        verification={"status": "not_verified"}, sources=[],
    )
    before = store.get_item(execution.item_id)["runs"]
    assert bridge.finish_execution("completed", ok=True, run_id=second_run)
    repl = SimpleNamespace(session=session, agent=SimpleNamespace(session=session))
    server._settle_work_record_run(
        current_session=session, bridge=bridge, store=store, binding=execution,
        emit=events.append, repl=repl,
        worker_work_record_result=(second_binding, second_outcome),
        worker_verification_receipt=token,
        agent=SimpleNamespace(project_worker_host=host, session=session),
    )

    assert store.get_item(execution.item_id)["runs"] == before


def test_unknown_forged_receipt_keeps_existing_lifecycle_fallback(tmp_path):
    import server
    from types import SimpleNamespace

    bridge, host, session, store, binding, outcome, _live, events = setup_case(tmp_path)
    assert bridge.finish_execution("completed", ok=True, run_id=binding.execution.run_id)
    repl = SimpleNamespace(session=session, agent=SimpleNamespace(session=session))
    server._settle_work_record_run(
        current_session=session, bridge=bridge, store=store, binding=binding.execution,
        emit=events.append, repl=repl, worker_work_record_result=(binding, outcome),
        worker_verification_receipt=object(),
        agent=SimpleNamespace(project_worker_host=host, session=session),
    )

    rows = store.get_item(binding.execution.item_id)["runs"]
    assert len(rows) == 1 and rows[0]["status"] == "unknown"
    assert "project_test" not in rows[0]["verification"]
