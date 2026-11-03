from __future__ import annotations

import multiprocessing
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from paths import AgentPaths
from session import Session, SessionMeta
from work_records import WorkRecordConflict, WorkRecordStaleRun, WorkRecordStore
from work_record_settlement import BoundExecution, ExecutionReceipt, VerificationReceipt, WorkRecordSettlementAdapter


def _session(root: Path, conversation_id: str = "session-cas") -> Session:
    paths = AgentPaths(agent_root=root, workspace=root / "workspace", data=root / "data", evolve=root / "evolve")
    session_dir = paths.data / "sessions" / conversation_id
    session_dir.mkdir(parents=True, exist_ok=True)
    return Session(conversation_id=conversation_id, session_dir=session_dir, goal="", meta=SessionMeta(), messages=[], paths=paths)


def _cross_process_begin(root: str, item_id: str, requirement_id: str, run_id: str, expected: str | None, queue) -> None:
    store = WorkRecordStore(_session(Path(root)))
    queue.put(store.begin_requirement_run(item_id, requirement_id, run_id=run_id, expected_current_run_id=expected)["current_run_id"])


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


def _save(store, item_id, requirement_id, run_id, *, result_id=None, status="unknown"):
    return store.record_result_if_current_run(
        item_id, requirement_id, run_id=run_id, result_id=result_id or f"result-{run_id}",
        status=status, summary="synthetic structured receipt only", verification={"status": "not_verified"}, sources=[],
    )


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

