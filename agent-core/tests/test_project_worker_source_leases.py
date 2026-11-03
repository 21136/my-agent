from __future__ import annotations
import multiprocessing
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pytest
from paths import AgentPaths
from session import Session, SessionMeta
from work_records import WorkRecordConflict, WorkRecordStore, WorkRecordWriteError


def make_source(root: Path):
    paths = AgentPaths(root, root / "workspace", root / "data", root / "evolve")
    session_id = "20261106-source001"
    directory = paths.data / "sessions" / session_id
    directory.mkdir(parents=True, exist_ok=True)
    return Session(session_id, directory, "", SessionMeta(project_id="project-a"), [], paths)


def fixture(tmp_path):
    source = make_source(tmp_path)
    store = WorkRecordStore(source)
    item = store.create_item(title="Example", scope={"kind": "project", "project_id": "project-a"})
    requirement = store.add_requirement(item["item_id"], "Original task", event_id="original")
    return source, store, item["item_id"], requirement["requirement_id"]


def reserve(ledger, item_id, req_id, *, parent="parent-one", reservation="reservation-one"):
    return ledger.reserve(item_id=item_id, requirement_id=req_id, expected_text="Original task",
                          parent_session_id=parent, parent_run_id="parent-run", turn_epoch=3,
                          reservation_id=reservation, worker_id="worker-one", worker_run_id="worker-run")


def test_same_source_different_parent_exclusive_and_idempotent(tmp_path):
    from project_worker_source_leases import SourceWorkerLeaseLedger
    source, store, item_id, req_id = fixture(tmp_path)
    before = store._path.read_bytes()
    ledger = SourceWorkerLeaseLedger(source)
    first = reserve(ledger, item_id, req_id)
    assert reserve(SourceWorkerLeaseLedger(make_source(tmp_path)), item_id, req_id) == first
    with pytest.raises(WorkRecordConflict):
        reserve(ledger, item_id, req_id, parent="parent-two", reservation="reservation-two")
    assert store._path.read_bytes() == before


def test_start_and_cancel_are_fenced_and_stale_text_rejected(tmp_path):
    from project_worker_source_leases import SourceWorkerLeaseLedger
    source, store, item_id, req_id = fixture(tmp_path)
    ledger = SourceWorkerLeaseLedger(source)
    original = reserve(ledger, item_id, req_id)
    with pytest.raises(WorkRecordConflict):
        ledger.start(original, expected_text="Different task")
    assert ledger.get(original["reservation_id"])["state"] == "reserved"
    assert ledger.cancel_unstarted(original)["state"] == "cancelled"
    assert ledger.cancel_unstarted(original)["state"] == "cancelled"
    with pytest.raises(WorkRecordConflict):
        ledger.start(original, expected_text="Original task")
    second = reserve(ledger, item_id, req_id, parent="parent-two", reservation="reservation-two")
    assert ledger.start(second, expected_text="Original task")["state"] == "started"
    with pytest.raises(WorkRecordConflict): ledger.start(second, expected_text="Original task")
    with pytest.raises(WorkRecordConflict): ledger.cancel_unstarted(second)
    assert store.get_item(item_id)["requirements"][0]["text"] == "Original task"


def test_started_survives_reopen_until_matched_trusted_terminal(tmp_path):
    from project_worker_source_leases import SourceWorkerLeaseLedger
    source, _, item_id, req_id = fixture(tmp_path)
    ledger = SourceWorkerLeaseLedger(source)
    initial = reserve(ledger, item_id, req_id)
    ledger.start(initial, expected_text="Original task")
    reopened = SourceWorkerLeaseLedger(make_source(tmp_path))
    with pytest.raises(WorkRecordConflict):
        reserve(reopened, item_id, req_id, parent="other", reservation="other-reservation")
    with pytest.raises(WorkRecordConflict):
        reopened.finish(initial, terminal_status="unknown", trusted_terminal_check=lambda *_: True)
    with pytest.raises(WorkRecordConflict):
        reopened.finish(initial, terminal_status="finished", trusted_terminal_check=lambda *_: False)
    assert reopened.get(initial["reservation_id"])["state"] == "started"
    done = reopened.finish(initial, terminal_status="finished", trusted_terminal_check=lambda *_: True)
    assert done["state"] == "finished"
    assert reopened.finish(initial, terminal_status="finished", trusted_terminal_check=lambda *_: True) == done
    assert reserve(reopened, item_id, req_id, parent="other", reservation="other-reservation")["state"] == "reserved"


def _process_race(root, item_id, req_id, parent, ready, start, output):
    from project_worker_source_leases import SourceWorkerLeaseLedger
    source = make_source(Path(root)); ready.put(parent); start.wait(10)
    try:
        reserve(SourceWorkerLeaseLedger(source, lock_timeout=8), item_id, req_id,
                parent=parent, reservation="reservation-" + parent)
        output.put("reserved")
    except WorkRecordConflict:
        output.put("conflict")


def test_cross_process_parent_reservations_are_exclusive(tmp_path):
    source, _, item_id, req_id = fixture(tmp_path)
    ctx = multiprocessing.get_context("spawn")
    ready = ctx.Queue(); output = ctx.Queue(); start = ctx.Event()
    processes = [ctx.Process(target=_process_race, args=(str(tmp_path), item_id, req_id, p, ready, start, output))
                 for p in ("parent-a", "parent-b")]
    for process in processes: process.start()
    try:
        assert {ready.get(timeout=10), ready.get(timeout=10)} == {"parent-a", "parent-b"}
        start.set()
        assert sorted([output.get(timeout=10), output.get(timeout=10)]) == ["conflict", "reserved"]
    finally:
        start.set()
        for process in processes: process.join(10)
    assert all(process.exitcode == 0 for process in processes)


def test_source_text_change_after_reservation_rejects_start_and_retains_reservation(tmp_path):
    from project_worker_source_leases import SourceWorkerLeaseLedger
    source, store, item_id, req_id = fixture(tmp_path)
    ledger = SourceWorkerLeaseLedger(source)
    lease = reserve(ledger, item_id, req_id)
    # Simulate an independently changed legacy record; production revisions remain disabled.
    def change(data):
        data["items"][0]["requirements"][0]["text"] = "Changed task"
        return None, True
    store._mutate(change)
    with pytest.raises(WorkRecordConflict): ledger.start(lease, expected_text="Original task")
    assert ledger.get(lease["reservation_id"])["state"] == "reserved"


def _start_and_crash(root, item_id, req_id):
    import os
    from project_worker_source_leases import SourceWorkerLeaseLedger
    ledger = SourceWorkerLeaseLedger(make_source(Path(root)))
    lease = reserve(ledger, item_id, req_id)
    ledger.start(lease, expected_text="Original task")
    os._exit(0)  # no cleanup or terminal receipt


def test_crashed_process_never_releases_started_lease(tmp_path):
    from project_worker_source_leases import SourceWorkerLeaseLedger
    source, _, item_id, req_id = fixture(tmp_path)
    ctx = multiprocessing.get_context("spawn")
    child = ctx.Process(target=_start_and_crash, args=(str(tmp_path), item_id, req_id))
    child.start(); child.join(10)
    assert child.exitcode == 0
    ledger = SourceWorkerLeaseLedger(source)
    assert ledger.get("reservation-one")["state"] == "started"
    with pytest.raises(WorkRecordConflict):
        reserve(ledger, item_id, req_id, parent="other-parent", reservation="other-reservation")


def test_lease_write_failure_and_wrong_identity_fail_closed(tmp_path, monkeypatch):
    from project_worker_source_leases import SourceWorkerLeaseLedger
    import project_worker_source_leases as module
    source, store, item_id, req_id = fixture(tmp_path)
    ledger = SourceWorkerLeaseLedger(source)
    original_bytes = store._path.read_bytes()
    with monkeypatch.context() as inject:
        inject.setattr(module.os, "replace", lambda *_: (_ for _ in ()).throw(OSError("synthetic storage failure")))
        with pytest.raises(WorkRecordWriteError): reserve(ledger, item_id, req_id)
    assert ledger.get("reservation-one") is None
    assert store._path.read_bytes() == original_bytes
    lease = reserve(ledger, item_id, req_id)
    ledger.start(lease, expected_text="Original task")
    forged = {**lease, "parent_session_id": "other-parent"}
    with pytest.raises(WorkRecordConflict):
        ledger.finish(forged, terminal_status="finished", trusted_terminal_check=lambda *_: True)
    assert ledger.get("reservation-one")["state"] == "started"


@pytest.mark.parametrize("status", ["in_progress", "unknown"])
def test_live_or_unknown_current_run_blocks_reserve_and_start(tmp_path, status):
    from project_worker_source_leases import SourceWorkerLeaseLedger
    source, store, item_id, req_id = fixture(tmp_path)
    ledger = SourceWorkerLeaseLedger(source)
    lease = reserve(ledger, item_id, req_id)
    store.begin_requirement_run(item_id, req_id, run_id="current-run", expected_current_run_id=None)
    if status == "unknown":
        store.record_result_if_current_run(item_id, req_id, run_id="current-run", result_id="unknown-result", status="unknown",
            summary="unknown", verification={"status": "not_verified"}, sources=[])
    with pytest.raises(WorkRecordConflict): ledger.start(lease, expected_text="Original task")
    with pytest.raises(WorkRecordConflict): reserve(ledger, item_id, req_id)
    assert ledger.cancel_unstarted(lease)["state"] == "cancelled"
    with pytest.raises(WorkRecordConflict):
        reserve(ledger, item_id, req_id, reservation="new-reservation")


def test_known_terminal_source_run_may_reserve_and_start_continuation(tmp_path):
    from project_worker_source_leases import SourceWorkerLeaseLedger
    source, store, item_id, req_id = fixture(tmp_path)
    store.begin_requirement_run(item_id, req_id, run_id="prior-run", expected_current_run_id=None)
    store.record_result_if_current_run(item_id, req_id, run_id="prior-run", result_id="prior-result", status="blocked",
        summary="blocked", verification={"status": "not_verified"}, sources=[])
    ledger = SourceWorkerLeaseLedger(source)
    assert ledger.start(reserve(ledger, item_id, req_id), expected_text="Original task")["state"] == "started"


def test_terminal_callback_can_reenter_ledger_without_lock_held(tmp_path):
    from project_worker_source_leases import SourceWorkerLeaseLedger
    source, _, item_id, req_id = fixture(tmp_path)
    ledger = SourceWorkerLeaseLedger(source, lock_timeout=0.1)
    lease = reserve(ledger, item_id, req_id)
    ledger.start(lease, expected_text="Original task")
    def verifier(row, status):
        assert ledger.get(row["reservation_id"])["state"] == "started"
        return status == "finished"
    assert ledger.finish(lease, terminal_status="finished", trusted_terminal_check=verifier)["state"] == "finished"


def test_terminal_callback_state_change_is_rechecked_after_callback(tmp_path):
    from project_worker_source_leases import SourceWorkerLeaseLedger
    source, _, item_id, req_id = fixture(tmp_path)
    ledger = SourceWorkerLeaseLedger(source, lock_timeout=0.1)
    lease = reserve(ledger, item_id, req_id)
    ledger.start(lease, expected_text="Original task")
    def raced(row, status):
        assert ledger.finish(lease, terminal_status="blocked", trusted_terminal_check=lambda *_: True)["state"] == "blocked"
        return True
    with pytest.raises(WorkRecordConflict):
        ledger.finish(lease, terminal_status="finished", trusted_terminal_check=raced)
    assert ledger.get(lease["reservation_id"])["state"] == "blocked"


def test_malformed_unhashable_lease_state_is_corrupt(tmp_path):
    import json
    from project_worker_source_leases import SourceWorkerLeaseLedger
    from work_records import WorkRecordCorrupt
    source, _, item_id, req_id = fixture(tmp_path)
    ledger = SourceWorkerLeaseLedger(source)
    reserve(ledger, item_id, req_id)
    data = json.loads(ledger._path.read_text(encoding="utf-8"))
    data["leases"][0]["state"] = []
    ledger._path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(WorkRecordCorrupt): ledger.get("reservation-one")


def _reserve_then_crash(root, item_id, req_id):
    import os
    from project_worker_source_leases import SourceWorkerLeaseLedger
    reserve(SourceWorkerLeaseLedger(make_source(Path(root))), item_id, req_id)
    os._exit(0)


def test_find_unresolved_after_reservation_crash_and_parent_isolation(tmp_path):
    from project_worker_source_leases import SourceWorkerLeaseLedger
    source, _, item_id, req_id = fixture(tmp_path)
    ctx = multiprocessing.get_context("spawn")
    child = ctx.Process(target=_reserve_then_crash, args=(str(tmp_path), item_id, req_id))
    child.start(); child.join(10)
    assert child.exitcode == 0
    ledger = SourceWorkerLeaseLedger(source)
    def query(parent="parent-one", run="parent-run", epoch=3):
        return ledger._find_unresolved_for_parent(parent_session_id=parent, parent_run_id=run, turn_epoch=epoch, limit=10)
    page = query()
    assert [row["reservation_id"] for row in page["leases"]] == ["reservation-one"]
    assert page["leases"][0]["state"] == "reserved" and page["next_cursor"] is None
    assert "Original task" not in repr(page)
    assert query(parent="parent-two")["leases"] == []
    assert query(run="other-run")["leases"] == []
    assert query(epoch=4)["leases"] == []
    assert ledger.get("reservation-one")["state"] == "reserved"
    ledger.cancel_unstarted(ledger.get("reservation-one"))
    assert query()["leases"] == []


def test_unresolved_paging_bounded_identity_and_revision_fenced(tmp_path):
    from project_worker_source_leases import SourceWorkerLeaseLedger
    source, _, item_id, req_id = fixture(tmp_path)
    ledger = SourceWorkerLeaseLedger(source)
    base = reserve(ledger, item_id, req_id)
    # Synthetic, schema-valid rows represent many independently reserved requirements.
    data = ledger._read()
    for i in range(125):
        data["leases"].append({**base, "item_id": f"item-{i}", "requirement_id": f"req-{i}",
                               "reservation_id": f"reservation-{i:03}", "worker_id": f"worker-{i}",
                               "worker_run_id": f"run-{i}"})
    data["revision"] += 1
    ledger._publish(data)
    args = dict(parent_session_id="parent-one", parent_run_id="parent-run", turn_epoch=3, limit=20)
    rows = []; cursor = None
    while True:
        page = ledger._find_unresolved_for_parent(**args, cursor=cursor)
        assert len(page["leases"]) <= 20
        rows.extend(page["leases"])
        cursor = page["next_cursor"]
        if cursor is None: break
    assert len(rows) == 126 and len({row["reservation_id"] for row in rows}) == 126
    page = ledger._find_unresolved_for_parent(**args)
    with pytest.raises(ValueError):
        ledger._find_unresolved_for_parent(**{**args, "turn_epoch": 4}, cursor=page["next_cursor"])
    with pytest.raises(ValueError): ledger._find_unresolved_for_parent(**{**args, "limit": 101})
    with pytest.raises(ValueError): ledger._find_unresolved_for_parent(**{**args, "turn_epoch": True})
    with pytest.raises(ValueError): ledger._find_unresolved_for_parent(**{**args, "parent_session_id": "../other"})
    ledger.cancel_unstarted(base)
    with pytest.raises(WorkRecordConflict):
        ledger._find_unresolved_for_parent(**args, cursor=page["next_cursor"])


def test_recovery_discovery_includes_started_but_not_terminal(tmp_path):
    from project_worker_source_leases import SourceWorkerLeaseLedger
    source, _, item_id, req_id = fixture(tmp_path)
    ledger = SourceWorkerLeaseLedger(source)
    lease = reserve(ledger, item_id, req_id)
    ledger.start(lease, expected_text="Original task")
    args = dict(parent_session_id="parent-one", parent_run_id="parent-run", turn_epoch=3)
    assert ledger._find_unresolved_for_parent(**args)["leases"][0]["state"] == "started"
    ledger.finish(lease, terminal_status="blocked", trusted_terminal_check=lambda *_: True)
    assert ledger._find_unresolved_for_parent(**args)["leases"] == []
