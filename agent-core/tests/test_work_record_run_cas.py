from __future__ import annotations

import multiprocessing
import os
import subprocess
import sys
from pathlib import Path

import pytest

_CORE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_CORE))
from paths import AgentPaths
from session import Session, SessionMeta
from work_records import WorkRecordConflict, WorkRecordStaleRun, WorkRecordStore
from work_record_settlement import BoundExecution, ExecutionReceipt, VerificationReceipt, WorkRecordSettlementAdapter
from server import _recover_interrupted_work_records


def _session(root: Path, conversation_id: str = "session-cas", project_id: str = "") -> Session:
    paths = AgentPaths(agent_root=root, workspace=root / "workspace", data=root / "data", evolve=root / "evolve")
    session_dir = paths.data / "sessions" / conversation_id
    session_dir.mkdir(parents=True, exist_ok=True)
    return Session(conversation_id=conversation_id, session_dir=session_dir, goal="", meta=SessionMeta(project_id=project_id), messages=[], paths=paths)


def _cross_process_begin(root: str, item_id: str, requirement_id: str, run_id: str, expected: str | None, queue) -> None:
    store = WorkRecordStore(_session(Path(root)))
    queue.put(store.begin_requirement_run(item_id, requirement_id, run_id=run_id, expected_current_run_id=expected)["current_run_id"])


def _cross_process_schedule_claim(root: str, item_id: str, requirement_id: str, revision: int,
                                 owner: str, reservation: str, barrier, queue) -> None:
    store = WorkRecordStore(_session(Path(root), project_id="project-cas"))
    barrier.wait(10)
    try:
        store.claim_scheduled_requirement(
            "project-cas", item_id, requirement_id, expected_revision=revision,
            owner_id=owner, reservation_id=reservation, parent_run_id="parent",
        )
        store.begin_claimed_schedule_run(
            "project-cas", item_id, requirement_id, run_id="worker-" + owner,
            owner_id=owner, reservation_id=reservation, parent_run_id="parent",
            expected_requirement_text="synthetic scheduled requirement",
        )
        queue.put(("ok", owner))
    except WorkRecordConflict:
        queue.put(("conflict", owner))


def _cross_process_first_begin(root: str, item_id: str, requirement_id: str, run_id: str, barrier, queue) -> None:
    store = WorkRecordStore(_session(Path(root)))
    barrier.wait(10)
    try:
        value = store.begin_requirement_run(item_id, requirement_id, run_id=run_id, expected_current_run_id=None)
        queue.put(("ok", value["current_run_id"]))
    except WorkRecordStaleRun:
        queue.put(("stale", run_id))


def _fixture(tmp_path: Path):
    store = WorkRecordStore(_session(tmp_path))
    item = store.create_item(title="CAS synthetic", scope={"kind": "independent"})
    requirement = store.add_requirement(item["item_id"], "synthetic requirement", event_id="req-cas")
    return store, item["item_id"], requirement["requirement_id"]


def _project_fixture(tmp_path: Path):
    paths = AgentPaths(agent_root=tmp_path, workspace=tmp_path / "workspace", data=tmp_path / "data", evolve=tmp_path / "evolve")
    session_dir = paths.data / "sessions" / "session-cas"
    session_dir.mkdir(parents=True, exist_ok=True)
    session = Session("session-cas", session_dir, "", SessionMeta(project_id="project-cas"), [], paths)
    store = WorkRecordStore(session)
    item = store.create_item(title="CAS project", scope={"kind": "project", "project_id": "project-cas"})
    requirement = store.add_requirement(item["item_id"], "synthetic scheduled requirement", event_id="req-scheduled")
    return store, item["item_id"], requirement["requirement_id"]


def _save(store, item_id, requirement_id, run_id, *, result_id=None, status="unknown"):
    return store.record_result_if_current_run(
        item_id, requirement_id, run_id=run_id, result_id=result_id or f"result-{run_id}",
        status=status, summary="synthetic structured receipt only", verification={"status": "not_verified"}, sources=[],
    )


def test_schedule_claim_and_reservation_start_are_atomic_and_settlement_consumes_claim(tmp_path):
    store, item_id, requirement_id = _project_fixture(tmp_path)
    revision = store._read()["revision"]
    claim = store.claim_scheduled_requirement(
        "project-cas", item_id, requirement_id, expected_revision=revision,
        owner_id="worker-owner", reservation_id="reservation-one", parent_run_id="parent-one",
    )
    assert claim["state"] == "claimed"
    started = store.begin_claimed_schedule_run(
        "project-cas", item_id, requirement_id, run_id="worker-run-one",
        owner_id="worker-owner", reservation_id="reservation-one", parent_run_id="parent-one",
        expected_requirement_text="synthetic scheduled requirement",
    )
    assert started["status"] == "in_progress"
    assert started["current_run_id"] == "worker-run-one"
    from work_record_settlement import BoundExecution, ExecutionReceipt, WorkRecordSettlementAdapter
    result = WorkRecordSettlementAdapter(
        store, BoundExecution("session-cas", item_id, requirement_id, "worker-run-one",
                          reservation_id="reservation-one", parent_run_id="parent-one")
    ).settle(ExecutionReceipt("session-cas", item_id, requirement_id, "worker-run-one", 3,
                              "succeeded", finish_reason="completed"))
    assert result.status == "recorded"
    assert "schedule_claim" not in store.get_item(item_id)["requirements"][0]


def test_unknown_claim_cannot_be_replaced_and_old_revision_is_rejected(tmp_path):
    store, item_id, requirement_id = _project_fixture(tmp_path)
    revision = store._read()["revision"]
    store.claim_scheduled_requirement(
        "project-cas", item_id, requirement_id, expected_revision=revision,
        owner_id="dead-owner", reservation_id="reservation-dead", parent_run_id="parent-dead",
    )
    store.mark_schedule_claim_unknown(
        item_id, requirement_id, owner_id="dead-owner", reservation_id="reservation-dead",
        parent_run_id="parent-dead",
    )
    with pytest.raises(WorkRecordConflict, match="persistent claim"):
        store.claim_scheduled_requirement(
            "project-cas", item_id, requirement_id, expected_revision=store._read()["revision"],
            owner_id="live-owner", reservation_id="reservation-live", parent_run_id="parent-live",
        )
    assert store.get_item(item_id)["requirements"][0]["schedule_claim"]["owner_id"] == "dead-owner"
    with pytest.raises(WorkRecordConflict, match="persistent claim"):
        store.claim_scheduled_requirement(
            "project-cas", item_id, requirement_id, expected_revision=revision,
            owner_id="another-owner", reservation_id="reservation-again", parent_run_id="parent-again",
        )


def test_old_revision_rejected_before_first_claim(tmp_path):
    store, item_id, requirement_id = _project_fixture(tmp_path)
    old_revision = store._read()["revision"]
    store.add_requirement(item_id, "another synthetic requirement", event_id="another-req")
    with pytest.raises(WorkRecordConflict, match="revision changed"):
        store.claim_scheduled_requirement(
            "project-cas", item_id, requirement_id, expected_revision=old_revision,
            owner_id="owner", reservation_id="reservation", parent_run_id="parent",
        )


def test_restart_recovery_preserves_schedule_claim_as_exclusion_marker(tmp_path):
    store, item_id, requirement_id = _project_fixture(tmp_path)
    store.claim_scheduled_requirement(
        "project-cas", item_id, requirement_id,
        expected_revision=store._read()["revision"], owner_id="host-owner",
        reservation_id="reservation-live", parent_run_id="parent-live",
    )
    store.begin_claimed_schedule_run(
        "project-cas", item_id, requirement_id, run_id="worker-live",
        owner_id="host-owner", reservation_id="reservation-live", parent_run_id="parent-live",
        expected_requirement_text="synthetic scheduled requirement",
    )

    assert store.mark_interrupted_runs_unknown(
        event_id_prefix="restart-recovery", current_owner_id="different-process",
    ) == 1
    reopened = WorkRecordStore(_session(tmp_path, project_id="project-cas"))
    requirement = reopened.get_item(item_id)["requirements"][0]
    assert requirement["status"] == "unknown"
    assert requirement["schedule_claim"]["reservation_id"] == "reservation-live"
    with pytest.raises(WorkRecordConflict, match="persistent claim"):
        reopened.claim_scheduled_requirement(
            "project-cas", item_id, requirement_id,
            expected_revision=reopened._read()["revision"], owner_id="new-owner",
            reservation_id="new-reservation", parent_run_id="new-parent",
        )


def test_two_processes_cannot_consume_one_schedule_claim_twice(tmp_path):
    store, item_id, requirement_id = _project_fixture(tmp_path)
    revision = store._read()["revision"]
    ctx = multiprocessing.get_context("spawn")
    queue = ctx.Queue()
    barrier = ctx.Barrier(2)
    # Claims are serialized by the same file lock used by WorkRecord mutations.
    processes = [ctx.Process(target=_cross_process_schedule_claim,
                             args=(str(tmp_path), item_id, requirement_id, revision,
                                   owner, "reservation-" + owner, barrier, queue))
                 for owner in ("owner-a", "owner-b")]
    for process in processes:
        process.start()
    for process in processes:
        process.join(15)
        assert process.exitcode == 0
    assert sorted(queue.get(timeout=2)[0] for _ in processes) == ["conflict", "ok"]


def test_start_run_is_durable_and_same_run_start_is_idempotent(tmp_path):
    store, item_id, requirement_id = _fixture(tmp_path)
    first = store.begin_requirement_run(item_id, requirement_id, run_id="run-a", expected_current_run_id=None)
    revision = store.list_items()[0]
    second = WorkRecordStore(_session(tmp_path)).begin_requirement_run(item_id, requirement_id, run_id="run-a", expected_current_run_id=None)
    assert first["current_run_id"] == second["current_run_id"] == "run-a"
    assert second["status"] == "in_progress"
    assert len(revision["history"]) == len(store.list_items()[0]["history"])


def test_old_run_result_after_new_run_cannot_overwrite_even_after_store_reopen(tmp_path):
    store, item_id, requirement_id = _fixture(tmp_path)
    store.begin_requirement_run(item_id, requirement_id, run_id="run-old", expected_current_run_id=None)
    WorkRecordStore(_session(tmp_path)).begin_requirement_run(item_id, requirement_id, run_id="run-new", expected_current_run_id="run-old")
    reopened = WorkRecordStore(_session(tmp_path))
    with pytest.raises(WorkRecordStaleRun):
        _save(reopened, item_id, requirement_id, "run-old", status="completed")
    current = reopened.get_item(item_id)
    req = current["requirements"][0]
    assert req["status"] == "in_progress"
    assert req["current_run_id"] == "run-new"
    assert current["runs"] == []


def test_current_run_result_is_idempotent_and_conflicting_replay_rejected(tmp_path):
    store, item_id, requirement_id = _fixture(tmp_path)
    store.begin_requirement_run(item_id, requirement_id, run_id="run-current", expected_current_run_id=None)
    first = _save(store, item_id, requirement_id, "run-current", result_id="stable-result")
    reopened = WorkRecordStore(_session(tmp_path))
    assert _save(reopened, item_id, requirement_id, "run-current", result_id="stable-result") == first
    with pytest.raises(WorkRecordConflict):
        reopened.record_result_if_current_run(
            item_id, requirement_id, run_id="run-current", result_id="stable-result",
            status="completed", summary="conflicting", verification={"status": "passed"}, sources=[],
        )
    assert len(reopened.get_item(item_id)["runs"]) == 1


def test_legacy_sidecar_without_current_run_field_is_readable_and_upgraded_on_begin(tmp_path):
    store, item_id, requirement_id = _fixture(tmp_path)
    item = store.get_item(item_id)
    item["requirements"][0].pop("current_run_id", None)
    # The first write created the legacy-compatible v1 shape; remove the optional field from disk.
    sidecar = _session(tmp_path).session_dir / "work-records.v1.json"
    import json
    data = json.loads(sidecar.read_text(encoding="utf-8"))
    data["items"][0]["requirements"][0].pop("current_run_id", None)
    sidecar.write_text(json.dumps(data), encoding="utf-8")
    reopened = WorkRecordStore(_session(tmp_path))
    assert reopened.get_item(item_id)["requirements"][0]["status"] == "pending"
    started = reopened.begin_requirement_run(item_id, requirement_id, run_id="run-upgrade", expected_current_run_id=None)
    assert started["current_run_id"] == "run-upgrade"


def test_run_cas_is_serialized_across_processes(tmp_path):
    store, item_id, requirement_id = _fixture(tmp_path)
    store.begin_requirement_run(item_id, requirement_id, run_id="run-old", expected_current_run_id=None)
    ctx = multiprocessing.get_context("spawn")
    queue = ctx.Queue()
    process = ctx.Process(target=_cross_process_begin, args=(str(tmp_path), item_id, requirement_id, "run-new", "run-old", queue))
    process.start()
    process.join(15)
    assert process.exitcode == 0
    assert queue.get(timeout=2) == "run-new"
    with pytest.raises(WorkRecordStaleRun):
        _save(store, item_id, requirement_id, "run-old", status="completed")
    assert store.get_item(item_id)["requirements"][0]["current_run_id"] == "run-new"


def test_add_requirement_expected_snapshot_rejects_change_inside_locked_mutation(tmp_path):
    store, item_id, _ = _fixture(tmp_path)
    expected = store._read()
    store.add_requirement(item_id, "concurrent synthetic update", event_id="req-concurrent")
    before = store.get_item(item_id)
    with pytest.raises(WorkRecordConflict, match="snapshot changed"):
        store.add_requirement(
            item_id, "must not be appended", event_id="req-stale-preview",
            expected_snapshot=expected,
        )
    after = store.get_item(item_id)
    assert [r["text"] for r in after["requirements"]] == [r["text"] for r in before["requirements"]]


def test_settlement_adapter_uses_durable_cas_and_persists_only_unknown(tmp_path):
    store, item_id, requirement_id = _fixture(tmp_path)
    session_id = store._session.conversation_id
    store.begin_requirement_run(item_id, requirement_id, run_id="run-settle", expected_current_run_id=None)
    adapter = WorkRecordSettlementAdapter(
        store, BoundExecution(session_id, item_id, requirement_id, "run-settle")
    )
    receipt = ExecutionReceipt(session_id, item_id, requirement_id, "run-settle", 1, "succeeded")
    unverifiable = VerificationReceipt(
        session_id, item_id, requirement_id, "run-settle", "passed", ("user://unverified-string",)
    )
    result = adapter.settle(receipt, unverifiable)
    assert result.status == "recorded"
    assert result.record_status == "unknown"
    item = store.get_item(item_id)
    assert item["requirements"][0]["status"] == "unknown"
    assert item["runs"][0]["status"] == "unknown"
    verification = item["runs"][0]["verification"]
    assert verification["status"] == "not_verified"
    assert verification["evidence_refs"] == []
    assert set(verification) == {"status", "evidence_refs", "host_execution"}
    assert verification["host_execution"] == {"sequence": 1}


def test_adapter_cas_rejects_late_old_run_after_new_run_started(tmp_path):
    store, item_id, requirement_id = _fixture(tmp_path)
    session_id = store._session.conversation_id
    store.begin_requirement_run(item_id, requirement_id, run_id="run-old", expected_current_run_id=None)
    old_adapter = WorkRecordSettlementAdapter(
        store, BoundExecution(session_id, item_id, requirement_id, "run-old")
    )
    store.begin_requirement_run(item_id, requirement_id, run_id="run-new", expected_current_run_id="run-old")
    receipt = ExecutionReceipt(session_id, item_id, requirement_id, "run-old", 1, "succeeded")
    result = old_adapter.settle(receipt)
    assert result.status == "rejected"
    assert result.reason == "stale_run"
    item = store.get_item(item_id)
    assert item["requirements"][0]["current_run_id"] == "run-new"
    assert item["requirements"][0]["status"] == "in_progress"
    assert item["runs"] == []


def test_restarting_same_run_after_result_does_not_reset_requirement(tmp_path):
    store, item_id, requirement_id = _fixture(tmp_path)
    store.begin_requirement_run(item_id, requirement_id, run_id="run-same", expected_current_run_id=None)
    _save(store, item_id, requirement_id, "run-same", status="unknown")
    reopened = WorkRecordStore(_session(tmp_path))
    before = reopened.get_item(item_id)["requirements"][0]
    after = reopened.begin_requirement_run(item_id, requirement_id, run_id="run-same", expected_current_run_id=None)
    assert before["status"] == after["status"] == "unknown"
    assert len(reopened.get_item(item_id)["runs"]) == 1


def test_legacy_unconditional_result_api_is_rejected_once_requirement_uses_run_cas(tmp_path):
    store, item_id, requirement_id = _fixture(tmp_path)
    store.begin_requirement_run(item_id, requirement_id, run_id="run-old", expected_current_run_id=None)
    store.begin_requirement_run(item_id, requirement_id, run_id="run-current", expected_current_run_id="run-old")
    with pytest.raises(WorkRecordStaleRun, match="CAS-managed"):
        store.record_result(
            item_id, requirement_id, run_id="run-old", result_id="late-old-result",
            status="completed", summary="late", verification={"status": "passed"}, sources=[],
        )
    with pytest.raises(WorkRecordStaleRun, match="CAS-managed"):
        store.record_result(
            item_id, requirement_id, run_id="run-current", result_id="bypass-cas",
            status="completed", summary="unconditional", verification={"status": "passed"}, sources=[],
        )
    item = store.get_item(item_id)
    assert item["requirements"][0]["current_run_id"] == "run-current"
    assert item["requirements"][0]["status"] == "in_progress"
    assert item["runs"] == []


def test_unseen_late_old_begin_cannot_replace_run_already_started(tmp_path):
    store, item_id, requirement_id = _fixture(tmp_path)
    store.begin_requirement_run(item_id, requirement_id, run_id="run-new", expected_current_run_id=None)
    with pytest.raises(WorkRecordStaleRun):
        store.begin_requirement_run(item_id, requirement_id, run_id="run-old", expected_current_run_id=None)
    current = store.get_item(item_id)["requirements"][0]
    assert current["current_run_id"] == "run-new"


def test_two_concurrent_initial_begins_with_expected_none_have_one_winner(tmp_path):
    store, item_id, requirement_id = _fixture(tmp_path)
    ctx = multiprocessing.get_context("spawn")
    barrier = ctx.Barrier(2)
    queue = ctx.Queue()
    processes = [
        ctx.Process(target=_cross_process_first_begin,
                    args=(str(tmp_path), item_id, requirement_id, run_id, barrier, queue))
        for run_id in ("run-race-a", "run-race-b")
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(15)
        assert process.exitcode == 0
    results = [queue.get(timeout=2) for _ in processes]
    assert sorted(result[0] for result in results) == ["ok", "stale"]
    current = store.get_item(item_id)["requirements"][0]["current_run_id"]
    assert current in {"run-race-a", "run-race-b"}


def test_process_restart_marks_interrupted_run_unknown_and_late_receipt_cannot_hide_uncertainty(tmp_path):
    store, item_id, requirement_id = _fixture(tmp_path)
    store.begin_requirement_run(item_id, requirement_id, run_id="run-interrupted", expected_current_run_id=None)
    # A separate interpreter models closing and reopening the application; it
    # only marks the durable execution outcome uncertain and never resumes work.
    recovery_probe = r'''
import sys
from pathlib import Path
from paths import AgentPaths
from session import Session, SessionMeta
from work_records import WorkRecordStore
root = Path(sys.argv[1])
paths = AgentPaths(agent_root=root, workspace=root / "workspace", data=root / "data", evolve=root / "evolve")
session_dir = paths.data / "sessions" / "session-cas"
session = Session("session-cas", session_dir, "", SessionMeta(), [], paths)
store = WorkRecordStore(session)
changed = store.mark_interrupted_runs_unknown(event_id_prefix="recovery-one")
print(changed)
'''
    recovered_process = subprocess.run(
        [sys.executable, "-c", recovery_probe, str(tmp_path)], cwd=_CORE,
        env={**os.environ, "PYTHONPATH": str(_CORE), "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True, text=True, encoding="utf-8", timeout=20, check=False,
    )
    assert recovered_process.returncode == 0, recovered_process.stderr
    assert recovered_process.stdout.strip().splitlines()[-1] == "1"
    reopened = WorkRecordStore(_session(tmp_path))
    assert reopened.mark_interrupted_runs_unknown(event_id_prefix="recovery-two", current_owner_id="new-process") == 0
    recovered = reopened.get_item(item_id)
    requirement = recovered["requirements"][0]
    assert requirement["status"] == "unknown"
    assert requirement["current_run_id"] == "run-interrupted"
    with pytest.raises(WorkRecordConflict, match="unknown side effects must be inspected"):
        reopened.set_requirement_control(
            item_id, requirement_id, action="continue", expected_status="unknown",
            event_id="must-inspect-first", idempotency_key="must-inspect-first",
        )
    assert "execution_owner_id" not in requirement
    interruption = [event for event in recovered["history"] if event["kind"] == "requirement_interrupted"]
    assert len(interruption) == 1
    assert interruption[0]["payload"] == {
        "requirement_id": requirement_id,
        "run_id": "run-interrupted",
        "reason": "application_interrupted",
        "outcome": "unknown",
    }

    # A delayed receipt from the old process must not overwrite recovered state.
    with pytest.raises(WorkRecordStaleRun):
        _save(WorkRecordStore(_session(tmp_path)), item_id, requirement_id, "run-interrupted", status="completed")
    final = WorkRecordStore(_session(tmp_path)).get_item(item_id)
    assert final["requirements"][0]["status"] == "unknown"
    assert final["runs"] == []


def test_recovery_preserves_current_process_run_and_rejects_missing_identity(tmp_path):
    store, item_id, requirement_id = _fixture(tmp_path)
    started = store.begin_requirement_run(item_id, requirement_id, run_id="run-live", expected_current_run_id=None)
    owner_id = started["execution_owner_id"]

    assert store.mark_interrupted_runs_unknown(event_id_prefix="recovery", current_owner_id=owner_id) == 0
    with pytest.raises(ValueError, match="recovery identity"):
        store.mark_interrupted_runs_unknown(event_id_prefix=" ", current_owner_id="new-process")
    assert store.get_item(item_id)["requirements"][0]["status"] == "in_progress"


def test_recovered_unknown_run_can_be_retried_only_as_a_new_run(tmp_path):
    store, item_id, requirement_id = _fixture(tmp_path)
    store.begin_requirement_run(item_id, requirement_id, run_id="run-interrupted", expected_current_run_id=None)
    assert store.mark_interrupted_runs_unknown(
        event_id_prefix="recovery", current_owner_id="another-process",
    ) == 1

    same_run = store.begin_requirement_run(
        item_id, requirement_id, run_id="run-interrupted", expected_current_run_id="run-interrupted",
    )
    assert same_run["status"] == "unknown"

    retry = WorkRecordStore(_session(tmp_path)).begin_requirement_run(
        item_id, requirement_id, run_id="run-retry", expected_current_run_id="run-interrupted",
    )
    assert retry["status"] == "in_progress"
    assert retry["current_run_id"] == "run-retry"
    _save(store, item_id, requirement_id, "run-retry", status="completed")

    final = WorkRecordStore(_session(tmp_path)).get_item(item_id)
    requirement = final["requirements"][0]
    assert requirement["status"] == "completed"
    assert requirement["current_run_id"] == "run-retry"
    assert [run["run_id"] for run in final["runs"]] == ["run-retry"]


def test_startup_recovery_does_not_release_an_unstarted_schedule_claim(tmp_path):
    session = _session(tmp_path, project_id="project-cas")
    store = WorkRecordStore(session)
    item = store.create_item(title="synthetic scheduled work", scope={"kind": "project", "project_id": "project-cas"})
    requirement = store.add_requirement(item["item_id"], "synthetic scheduled requirement", event_id="req-claim")
    claim = store.claim_scheduled_requirement(
        "project-cas", item["item_id"], requirement["requirement_id"], expected_revision=2,
        owner_id="synthetic-owner", reservation_id="synthetic-reservation", parent_run_id="synthetic-parent",
    )

    before = store.get_item(item["item_id"])
    assert _recover_interrupted_work_records(session) is False
    after = store.get_item(item["item_id"])
    assert after == before
    assert after["requirements"][0]["schedule_claim"] == claim
    assert after["requirements"][0]["status"] == "pending"


def test_startup_recovery_warns_only_when_persisted_execution_is_unresolved(tmp_path):
    session = _session(tmp_path)
    store = WorkRecordStore(session)
    assert _recover_interrupted_work_records(session) is True

    item = store.create_item(title="synthetic live work", scope={"kind": "independent"})
    requirement = store.add_requirement(item["item_id"], "synthetic live requirement", event_id="req-live")
    store.begin_requirement_run(
        item["item_id"], requirement["requirement_id"], run_id="synthetic-live-run",
        expected_current_run_id=None,
    )

    before = store.get_item(item["item_id"])
    assert _recover_interrupted_work_records(session) is False
    after = store.get_item(item["item_id"])
    assert after == before
    assert after["requirements"][0]["status"] == "in_progress"

