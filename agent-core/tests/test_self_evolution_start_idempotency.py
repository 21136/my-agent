from __future__ import annotations

import multiprocessing
import os
import sys
from pathlib import Path

import pytest

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

from self_evolution_store import EvolutionStore, StoreCapacityError, StoreConflict, StoreCorrupt


def _store(path):
    options = {}
    if os.name == "nt":
        target = Path(path)
        directory = target.parent if target.suffix.lower() in {".db", ".sqlite", ".sqlite3"} else target
        directory.mkdir(parents=True, exist_ok=True)
        temp_root = Path(__import__("tempfile").gettempdir()).resolve()
        options["trusted_acl_verifier"] = lambda item: item.parent.resolve().is_relative_to(temp_root)
    return EvolutionStore(path, **options)


def _reserve_worker(db, queue):
    store = _store(db)
    claim = store.reserve_start_request("session-A", "00000000-0000-4000-8000-000000000001", "a" * 64)
    queue.put(claim.status)


def test_start_request_reservation_survives_reopen_and_returns_same_final_result(tmp_path):
    store = _store(tmp_path / "state")
    claim = store.reserve_start_request("session-A", "00000000-0000-4000-8000-000000000001", "a" * 64)
    assert claim.status == "claimed"
    assert claim.claim_token
    result = {"status": "started", "task_id": "task-A", "attempt_id": "attempt-A"}
    store.finish_start_request(claim, result)

    reopened = _store(tmp_path / "state")
    replay = reopened.reserve_start_request("session-A", "00000000-0000-4000-8000-000000000001", "a" * 64)
    assert replay.status == "completed"
    assert replay.result == result
    assert replay.claim_token is None


def test_start_request_identity_is_session_scoped_and_payload_bound(tmp_path):
    store = _store(tmp_path / "state")
    request_id = "00000000-0000-4000-8000-000000000001"
    claim = store.reserve_start_request("session-A", request_id, "a" * 64)
    other_session = store.reserve_start_request("session-B", request_id, "b" * 64)
    assert other_session.status == "rejected"
    assert other_session.claim_token is None
    assert other_session.result == {"status": "denied", "error_code": "start_busy"}
    with pytest.raises(StoreConflict, match="request identity payload mismatch"):
        store.reserve_start_request("session-A", request_id, "b" * 64)
    with pytest.raises(StoreConflict, match="request identity payload mismatch"):
        store.reserve_start_request("session-B", request_id, "a" * 64)
    store.finish_start_request(claim, {"status": "denied", "error_code": "policy_denied"})
    assert store.reserve_start_request("session-B", request_id, "b" * 64) == other_session
    assert store.reserve_start_request("session-B", "00000000-0000-4000-8000-000000000002", "b" * 64).status == "claimed"


def test_pending_start_request_never_yields_second_execution_claim(tmp_path):
    store = _store(tmp_path / "state")
    request_id = "00000000-0000-4000-8000-000000000001"
    original = store.reserve_start_request("session-A", request_id, "a" * 64)
    replay = store.reserve_start_request("session-A", request_id, "a" * 64)
    assert original.status == "claimed"
    assert replay.status == "in_progress"
    assert replay.claim_token is None
    store.mark_start_request_unknown(original, {"status": "unknown", "error_code": "outcome_unconfirmed"})
    after_restart = _store(tmp_path / "state").reserve_start_request("session-A", request_id, "a" * 64)
    assert after_restart.status == "unknown"
    assert after_restart.result == {"status": "unknown", "error_code": "outcome_unconfirmed"}
    assert after_restart.claim_token is None


def test_concurrent_processes_obtain_at_most_one_start_claim(tmp_path):
    db = tmp_path / "state"
    _store(db)
    ctx = multiprocessing.get_context("spawn")
    queue = ctx.Queue()
    workers = [ctx.Process(target=_reserve_worker, args=(db, queue)) for _ in range(2)]
    for worker in workers:
        worker.start()
    results = [queue.get(timeout=15) for _ in workers]
    for worker in workers:
        worker.join(timeout=15)
        assert worker.exitcode == 0
    assert results.count("claimed") == 1
    assert results.count("in_progress") == 1


def test_start_request_corruption_fails_closed_without_replacement(tmp_path):
    import sqlite3

    db = tmp_path / "state"
    store = _store(db)
    store.reserve_start_request("session-A", "00000000-0000-4000-8000-000000000001", "a" * 64)
    with sqlite3.connect(store.path) as conn:
        conn.execute("UPDATE start_requests SET result_json = '{broken' WHERE session_id='session-A'")
    reopened = _store(db)
    with pytest.raises(StoreCorrupt):
        reopened.reserve_start_request("session-A", "00000000-0000-4000-8000-000000000001", "a" * 64)


def test_start_request_capacity_is_hard_fail_closed_without_eviction(tmp_path, monkeypatch):
    import self_evolution_store as store_module

    monkeypatch.setattr(store_module, "_MAX_START_REQUESTS", 1)
    store = _store(tmp_path / "state")
    request_id = "00000000-0000-4000-8000-000000000001"
    first = store.reserve_start_request("session-A", request_id, "a" * 64)
    store.finish_start_request(first, {"status": "denied", "error_code": "policy_denied"})
    with pytest.raises(StoreCapacityError, match="capacity exhausted"):
        store.reserve_start_request("session-B", "00000000-0000-4000-8000-000000000002", "b" * 64)
    assert store.reserve_start_request("session-A", request_id, "a" * 64).result == {
        "status": "denied", "error_code": "policy_denied"}


def test_start_request_finalization_is_idempotent_but_conflicting_result_is_rejected(tmp_path):
    store = _store(tmp_path / "state")
    claim = store.reserve_start_request("session-A", "00000000-0000-4000-8000-000000000001", "a" * 64)
    result = {"status": "started", "task_id": "task-A"}
    store.finish_start_request(claim, result)
    store.finish_start_request(claim, result)
    with pytest.raises(StoreConflict, match="result is immutable"):
        store.finish_start_request(claim, {"status": "started", "task_id": "task-B"})


def test_start_request_rejects_bad_identity_and_unsafe_result(tmp_path):
    store = _store(tmp_path / "state")
    with pytest.raises(ValueError):
        store.reserve_start_request("session-A", "not-a-uuid", "a" * 64)
    with pytest.raises(ValueError):
        store.reserve_start_request("session-A", "00000000-0000-4000-8000-000000000001", "x" * 64)
    claim = store.reserve_start_request("session-A", "00000000-0000-4000-8000-000000000001", "a" * 64)
    with pytest.raises(ValueError):
        store.finish_start_request(claim, {"status": "started", "path": "C:/secret"})


def test_startup_reconciliation_refuses_unresolved_reservations_without_mutating(tmp_path):
    db = tmp_path / "state"
    store = _store(db)
    request_id = "00000000-0000-4000-8000-000000000001"
    claim = store.reserve_start_request("session-A", request_id, "a" * 64)
    assert claim.status == "claimed"

    reopened = _store(db)
    from self_evolution_store import StoreOwnershipError
    with pytest.raises(StoreOwnershipError, match="owner liveness is unconfirmed"):
        reopened.reconcile_startup_writers()
    replay = reopened.reserve_start_request("session-A", request_id, "a" * 64)
    assert replay.status == "in_progress"
    assert replay.result is None
    assert replay.claim_token is None
    store.finish_start_request(claim, {"status": "denied", "error_code": "policy_denied"})
    assert reopened.reconcile_startup_writers() == ()


def _live_host_process(db, root, ready, proceed, output):
    from self_evolution_host import SelfEvolutionHost, SelfEvolutionHostConfig
    from self_evolution_types import EvolutionIdentity, EvolutionSnapshot
    store = _store(db)
    config = SelfEvolutionHostConfig(Path(root) / "stable", Path(root) / "candidates", ("a",), ("a",), {"check": ("check",)})
    host = SelfEvolutionHost(config, integrity_key=b"x" * 32, context_provider=lambda: None,
                             trusted_verifier_resolver=lambda *_: None, store=store)
    claim = store.reserve_start_request("session-A", "00000000-0000-4000-8000-000000000001", "a" * 64,
                                        run_id="run-A", turn_epoch=1)
    ident = EvolutionIdentity("session-A", "task-A", "attempt-A", "run-A", 1)
    store.consume_start_request_claim(claim, session_id="session-A", run_id="run-A", turn_epoch=1)
    store.create_task_with_writer(ident, EvolutionSnapshot(ident, "preparing", 1), "owner-A", start_request_claim=claim)
    output.put(("ready", host._startup_reconciliation_error))
    ready.set()
    if not proceed.wait(25):
        output.put(("timeout",))
        return
    try:
        store.finish_start_request(claim, {"status": "started", "task_id": "task-A"})
        store.compare_and_swap("task-A", 1, EvolutionSnapshot(ident, "observing", 2))
        output.put(("continued", store.read_task("session-A", "task-A").phase))
    except Exception as exc:
        output.put(("failed", type(exc).__name__, str(exc)))


def _second_host_process(db, root, ready, output):
    from self_evolution_host import SelfEvolutionHost, SelfEvolutionHostConfig
    if not ready.wait(25):
        output.put(("timeout",))
        return
    store = _store(db)
    config = SelfEvolutionHostConfig(Path(root) / "stable", Path(root) / "candidates", ("a",), ("a",), {"check": ("check",)})
    host = SelfEvolutionHost(config, integrity_key=b"x" * 32, context_provider=lambda: None,
                             trusted_verifier_resolver=lambda *_: None, store=store)
    output.put(("second", host._startup_reconciliation_error,
                store.read_task("session-A", "task-A").phase,
                store.list_writer_slots()[0]["state"],
                store.reserve_start_request("session-A", "00000000-0000-4000-8000-000000000001", "a" * 64,
                                            run_id="run-A", turn_epoch=1).status))


def test_two_concurrent_real_hosts_do_not_quarantine_live_owner(tmp_path):
    db = tmp_path / "state"
    _store(db)
    ctx = multiprocessing.get_context("spawn")
    ready, proceed, output = ctx.Event(), ctx.Event(), ctx.Queue()
    first = ctx.Process(target=_live_host_process, args=(db, tmp_path, ready, proceed, output))
    second = ctx.Process(target=_second_host_process, args=(db, tmp_path, ready, output))
    first.start()
    second.start()
    try:
        assert output.get(timeout=30) == ("ready", None)
        observed = output.get(timeout=30)
        assert observed == ("second", "startup_reconciliation_unavailable", "preparing", "held", "in_progress")
        assert first.is_alive()
        proceed.set()
        assert output.get(timeout=30) == ("continued", "observing")
    finally:
        proceed.set()
        first.join(timeout=30)
        second.join(timeout=30)
        if first.is_alive():
            first.terminate()
            first.join()
        if second.is_alive():
            second.terminate()
            second.join()
    assert first.exitcode == 0 and second.exitcode == 0


def test_crashed_host_restart_preserves_unknown_owner_and_blocks_replay_and_writer(tmp_path):
    from self_evolution_store import StoreOwnershipError
    from self_evolution_types import EvolutionIdentity, EvolutionSnapshot
    db = tmp_path / "state"
    _store(db)
    ctx = multiprocessing.get_context("spawn")
    ready, proceed, output = ctx.Event(), ctx.Event(), ctx.Queue()
    first = ctx.Process(target=_live_host_process, args=(db, tmp_path, ready, proceed, output))
    first.start()
    try:
        assert output.get(timeout=30) == ("ready", None)
        first.terminate()
        first.join(timeout=30)
        assert not first.is_alive()
        second = ctx.Process(target=_second_host_process, args=(db, tmp_path, ready, output))
        second.start()
        try:
            assert output.get(timeout=30) == ("second", "startup_reconciliation_unavailable", "preparing", "held", "in_progress")
            second.join(timeout=30)
            assert second.exitcode == 0
        finally:
            if second.is_alive():
                second.terminate()
                second.join()
        store = _store(db)
        assert store.reserve_start_request("session-A", "00000000-0000-4000-8000-000000000001", "a" * 64,
                                         run_id="run-A", turn_epoch=1).claim_token is None
        other = EvolutionIdentity("session-B", "task-B", "attempt-B", "run-B", 1)
        with pytest.raises(StoreOwnershipError):
            store.create_task_with_writer(other, EvolutionSnapshot(other, "preparing", 1), "owner-B")
        assert store.read_task("session-B", "task-B") is None
        assert store.list_writer_slots()[0]["owner_id"] == "owner-A"
    finally:
        if first.is_alive():
            first.terminate()
            first.join()


def test_unresolved_foreign_start_reservation_blocks_new_writer_without_evicting_owner(tmp_path):
    from self_evolution_store import StoreOwnershipError
    from self_evolution_types import EvolutionIdentity, EvolutionSnapshot
    store = _store(tmp_path / "state")
    first = store.reserve_start_request("session-A", "00000000-0000-4000-8000-000000000001", "a" * 64)
    second = store.reserve_start_request("session-B", "00000000-0000-4000-8000-000000000002", "b" * 64)
    ident = EvolutionIdentity("session-B", "task-B", "attempt-B", "run-B", 1)
    with pytest.raises(StoreOwnershipError):
        store.create_task_with_writer(ident, EvolutionSnapshot(ident, "preparing", 1), "owner-B", start_request_claim=second)
    assert store.read_task("session-B", "task-B") is None
    assert store.list_writer_slots() == ()
    assert second.status == "rejected" and second.claim_token is None
    store.finish_start_request(first, {"status": "denied", "error_code": "not_started"})
    assert store.reserve_start_request("session-B", "00000000-0000-4000-8000-000000000002", "b" * 64) == second
    new_claim = store.reserve_start_request("session-B", "00000000-0000-4000-8000-000000000003", "b" * 64,
                                            run_id="run-B", turn_epoch=1)
    store.consume_start_request_claim(new_claim, session_id="session-B", run_id="run-B", turn_epoch=1)
    store.create_task_with_writer(ident, EvolutionSnapshot(ident, "preparing", 1), "owner-B", start_request_claim=new_claim)
    assert store.list_writer_slots()[0]["owner_id"] == "owner-B"


def test_unresolved_task_without_writer_blocks_new_writer_even_if_startup_gate_is_skipped(tmp_path):
    from self_evolution_store import StoreOwnershipError
    from self_evolution_types import EvolutionIdentity, EvolutionSnapshot
    store = _store(tmp_path / "state")
    old = EvolutionIdentity("session-A", "task-A", "attempt-A", "run-A", 1)
    store.create_task(old, EvolutionSnapshot(old, "preparing", 1))
    new = EvolutionIdentity("session-B", "task-B", "attempt-B", "run-B", 1)
    with pytest.raises(StoreOwnershipError):
        store.create_task_with_writer(new, EvolutionSnapshot(new, "preparing", 1), "owner-B")
    assert store.list_writer_slots() == ()
    assert store.read_task("session-B", "task-B") is None
    assert store.read_task("session-A", "task-A").phase == "preparing"


def _claim_then_wait_for_crash(db, ready, wait):
    store = _store(db)
    claim = store.reserve_start_request("session-A", "00000000-0000-4000-8000-000000000001", "a" * 64)
    assert claim.status == "claimed"
    ready.set()
    wait.wait(30)


def test_crash_before_writer_claim_blocks_distinct_new_writer(tmp_path):
    from self_evolution_store import StoreOwnershipError
    from self_evolution_types import EvolutionIdentity, EvolutionSnapshot
    db = tmp_path / "state"
    _store(db)
    ctx = multiprocessing.get_context("spawn")
    ready, wait = ctx.Event(), ctx.Event()
    process = ctx.Process(target=_claim_then_wait_for_crash, args=(db, ready, wait))
    process.start()
    try:
        assert ready.wait(30)
        process.terminate()
        process.join(timeout=30)
        assert not process.is_alive()
        store = _store(db)
        with pytest.raises(StoreOwnershipError):
            store.reconcile_startup_writers()
        replay = store.reserve_start_request("session-A", "00000000-0000-4000-8000-000000000001", "a" * 64)
        assert replay.status == "in_progress" and replay.claim_token is None
        second = store.reserve_start_request("session-B", "00000000-0000-4000-8000-000000000002", "b" * 64)
        assert second.status == "rejected" and second.claim_token is None
        ident = EvolutionIdentity("session-B", "task-B", "attempt-B", "run-B", 1)
        with pytest.raises(StoreOwnershipError):
            store.create_task_with_writer(ident, EvolutionSnapshot(ident, "preparing", 1), "owner-B", start_request_claim=second)
        assert store.list_writer_slots() == ()
        assert store.read_task("session-B", "task-B") is None
    finally:
        if process.is_alive():
            process.terminate()
            process.join()


def _competing_start_process(db, suffix, barrier, output):
    from self_evolution_types import EvolutionIdentity, EvolutionSnapshot
    store = _store(db)
    session = "session-" + suffix
    request = "00000000-0000-4000-8000-00000000000" + suffix
    barrier.wait(timeout=20)
    run_id = "run-" + suffix
    claim = store.reserve_start_request(session, request, "a" * 64, run_id=run_id, turn_epoch=1)
    output.put(("reserved", suffix, claim.status, claim.result, bool(claim.claim_token)))
    barrier.wait(timeout=20)
    if claim.status == "claimed":
        ident = EvolutionIdentity(session, "task-" + suffix, "attempt-" + suffix, "run-" + suffix, 1)
        try:
            store.consume_start_request_claim(claim, session_id=session, run_id=ident.run_id, turn_epoch=1)
            store.create_task_with_writer(ident, EvolutionSnapshot(ident, "preparing", 1), "owner-" + suffix, start_request_claim=claim)
            output.put(("writer", suffix, "created"))
        except Exception as exc:
            output.put(("writer", suffix, type(exc).__name__, str(exc)))


def test_distinct_sessions_compete_atomically_before_external_side_effects(tmp_path):
    db = tmp_path / "state"
    _store(db)
    ctx = multiprocessing.get_context("spawn")
    barrier, output = ctx.Barrier(2), ctx.Queue()
    workers = [ctx.Process(target=_competing_start_process, args=(db, suffix, barrier, output)) for suffix in ("1", "2")]
    for worker in workers:
        worker.start()
    try:
        first, second = output.get(timeout=30), output.get(timeout=30)
        reserved = [first, second]
        assert [row[2] for row in reserved].count("claimed") == 1, reserved
        assert [row[2] for row in reserved].count("rejected") == 1, reserved
        loser = next(row for row in reserved if row[2] == "rejected")
        assert loser[3:] == ({"status": "denied", "error_code": "start_busy"}, False)
        assert output.get(timeout=30)[2:] == ("created",)
        for worker in workers:
            worker.join(timeout=30)
            assert worker.exitcode == 0
        store = _store(db)
        replay = store.reserve_start_request("session-" + loser[1], "00000000-0000-4000-8000-00000000000" + loser[1], "a" * 64,
                                                  run_id="run-" + loser[1], turn_epoch=1)
        assert (replay.status, replay.result, replay.claim_token) == ("rejected", loser[3], None)
        assert len(store.list_writer_slots()) == 1
    finally:
        for worker in workers:
            if worker.is_alive():
                worker.terminate()
                worker.join()


def test_active_writer_blocks_new_reservation_after_start_result_is_completed(tmp_path):
    from self_evolution_types import EvolutionIdentity, EvolutionSnapshot
    store = _store(tmp_path / "state")
    request = "00000000-0000-4000-8000-000000000001"
    claim = store.reserve_start_request("session-A", request, "a" * 64, run_id="run-A", turn_epoch=1)
    ident = EvolutionIdentity("session-A", "task-A", "attempt-A", "run-A", 1)
    store.consume_start_request_claim(claim, session_id="session-A", run_id="run-A", turn_epoch=1)
    store.create_task_with_writer(ident, EvolutionSnapshot(ident, "preparing", 1), "owner-A", start_request_claim=claim)
    store.finish_start_request(claim, {"status": "started", "task_id": "task-A"})
    other = store.reserve_start_request("session-B", "00000000-0000-4000-8000-000000000002", "b" * 64)
    assert other.status == "rejected" and other.claim_token is None
    assert store.reserve_start_request("session-A", request, "a" * 64,
                                        run_id="run-A", turn_epoch=1).status == "completed"
    assert store.list_writer_slots()[0]["state"] == "held"


def test_unknown_start_blocks_new_reservation_but_its_original_result_replays(tmp_path):
    store = _store(tmp_path / "state")
    request = "00000000-0000-4000-8000-000000000001"
    claim = store.reserve_start_request("session-A", request, "a" * 64)
    result = {"status": "unknown", "error_code": "worker_start_unconfirmed"}
    store.mark_start_request_unknown(claim, result)
    other = _store(tmp_path / "state").reserve_start_request("session-B", "00000000-0000-4000-8000-000000000002", "b" * 64)
    assert (other.status, other.claim_token, other.result) == ("rejected", None, {"status": "denied", "error_code": "start_busy"})
    replay = store.reserve_start_request("session-A", request, "a" * 64)
    assert (replay.status, replay.result, replay.claim_token) == ("unknown", result, None)


def test_corrupt_pending_reservation_blocks_other_session_without_inserting_rejection(tmp_path):
    import sqlite3
    store = _store(tmp_path / "state")
    store.reserve_start_request("session-A", "00000000-0000-4000-8000-000000000001", "a" * 64)
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE start_requests SET result_json='broken'")
    with pytest.raises(StoreCorrupt):
        store.reserve_start_request("session-B", "00000000-0000-4000-8000-000000000002", "b" * 64)
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT count(*) FROM start_requests").fetchone()[0] == 1


def test_active_or_unknown_task_without_writer_blocks_reservation(tmp_path):
    from self_evolution_types import EvolutionIdentity, EvolutionSnapshot
    store = _store(tmp_path / "state")
    ident = EvolutionIdentity("session-A", "task-A", "attempt-A", "run-A", 1)
    store.create_task(ident, EvolutionSnapshot(ident, "preparing", 1))
    rejected = store.reserve_start_request("session-B", "00000000-0000-4000-8000-000000000002", "b" * 64)
    assert rejected.status == "rejected" and rejected.claim_token is None
    store.mark_task_unknown("task-A", "unconfirmed_owner")
    again = store.reserve_start_request("session-B", "00000000-0000-4000-8000-000000000003", "b" * 64)
    assert again.status == "rejected" and again.claim_token is None
