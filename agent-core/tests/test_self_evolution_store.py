from __future__ import annotations

import multiprocessing
import os
import sys
from dataclasses import replace
from pathlib import Path

import pytest

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

from self_evolution_store import (
    EvolutionStore,
    StoreConflict,
    StoreCorrupt,
    StoreOwnershipError,
    StoreError,
)
from self_evolution_types import EvolutionEvent, EvolutionIdentity, EvolutionSnapshot, VerificationEvidence


def identity(session="session-A", task="task-A", attempt="attempt-A", run="run-A", epoch=1):
    return EvolutionIdentity(session, task, attempt, run, epoch)


def snapshot(i=None, phase="preparing", rev=1, **kwargs):
    return EvolutionSnapshot(i or identity(), phase, rev, **kwargs)


def _store(path):
    options = {}
    if os.name == "nt":
        target = Path(path)
        directory = target.parent if target.suffix.lower() in {".db", ".sqlite", ".sqlite3"} else target
        directory.mkdir(parents=True, exist_ok=True)
        temp_root = Path(__import__("tempfile").gettempdir()).resolve()
        # Test-only path-scope callback: resolve the stable parent, not a
        # transient SQLite sidecar that can disappear during Path.resolve().
        options["trusted_acl_verifier"] = lambda item: item.parent.resolve().is_relative_to(temp_root)
    return EvolutionStore(path, **options)


def _claim_worker(db, owner, queue):
    try:
        _store(db).claim_writer(owner, "task-A", "attempt-A")
        queue.put((owner, "claimed"))
    except StoreOwnershipError:
        queue.put((owner, "denied"))


def test_create_read_are_bound_to_exact_session_and_cas_rejects_stale_revision(tmp_path):
    store = _store(tmp_path / "state")
    store.create_task(identity(), snapshot())
    updated = store.compare_and_swap("task-A", 1, snapshot(phase="observing", rev=2))
    assert updated.snapshot_revision == 2
    with pytest.raises(StoreConflict):
        store.compare_and_swap("task-A", 1, snapshot(phase="failed", rev=2))
    assert store.read_task("session-A", "task-A").phase == "observing"
    assert store.read_task("session-B", "task-A") is None


def test_transition_table_rejects_illegal_transition_without_mutating(tmp_path):
    store = _store(tmp_path / "state")
    store.create_task(identity(), snapshot())
    with pytest.raises(StoreConflict):
        store.compare_and_swap("task-A", 1, snapshot(phase="merged", rev=2))
    assert store.read_task("session-A", "task-A").phase == "preparing"


def test_new_attempt_rejects_late_events_from_previous_attempt(tmp_path):
    store = _store(tmp_path / "state")
    store.create_task(identity(), snapshot())
    store.compare_and_swap("task-A", 1, snapshot(phase="observing", rev=2))
    store.compare_and_swap("task-A", 2, snapshot(phase="editing", rev=3))
    store.compare_and_swap("task-A", 3, snapshot(phase="cancelling", rev=4))
    store.compare_and_swap("task-A", 4, snapshot(phase="cancelled", rev=5))
    next_id = identity(attempt="attempt-B", run="run-B")
    next_snapshot = snapshot(next_id, phase="preparing", rev=6)
    store.begin_attempt("task-A", 5, next_id, next_snapshot)
    with pytest.raises(StoreConflict):
        store.accept_sequence(identity(), 1)
    event = EvolutionEvent(next_id, 1, "preparing", "phase_changed", (), 6)
    assert store.append_event(event) is True
    assert store.accept_sequence(next_id, 1) is False


def test_unknown_recovery_retains_writer_ownership_and_blocks_reassignment(tmp_path):
    db = tmp_path / "state"
    store = _store(db)
    store.create_task(identity(), snapshot())
    store.compare_and_swap("task-A", 1, snapshot(phase="observing", rev=2))
    store.compare_and_swap("task-A", 2, snapshot(phase="editing", rev=3))
    store.claim_writer("owner-dead", "task-A", "attempt-A")
    store.close()
    recovered = _store(db)
    assert recovered.mark_orphaned_unknown("owner-dead") == ("task-A",)
    assert recovered.read_task("session-A", "task-A").phase == "unknown"
    with pytest.raises(StoreOwnershipError):
        recovered.claim_writer("owner-new", "task-A", "attempt-A")
    with pytest.raises(StoreOwnershipError):
        recovered.release_writer("owner-dead", stop_confirmed=False)
    with pytest.raises(StoreOwnershipError, match="unknown writer ownership"):
        recovered.release_writer("owner-dead", stop_confirmed=True)
    assert recovered.list_writer_slots()[0]["state"] == "unknown"
    with pytest.raises(StoreOwnershipError):
        recovered.claim_writer("owner-new", "task-A", "attempt-A")


def test_two_processes_can_claim_at_most_one_persistent_writer(tmp_path):
    db = tmp_path / "state"
    _store(db).create_task(identity(), snapshot())
    ctx = multiprocessing.get_context("spawn")
    queue = ctx.Queue()
    workers = [ctx.Process(target=_claim_worker, args=(db, f"owner-{n}", queue)) for n in range(2)]
    for worker in workers:
        worker.start()
    results = [queue.get(timeout=10) for _ in workers]
    for worker in workers:
        worker.join(timeout=10)
        assert worker.exitcode == 0
    assert [result for _, result in results].count("claimed") == 1
    assert [result for _, result in results].count("denied") == 1


def test_corrupt_database_record_never_becomes_a_writable_missing_task(tmp_path):
    db = tmp_path / "state"
    store = _store(db)
    store.create_task(identity(), snapshot())
    store.close()
    import sqlite3
    with sqlite3.connect(db / "self-evolution.sqlite3") as conn:
        conn.execute("UPDATE tasks SET payload = '{broken' WHERE task_id = 'task-A'")
    store = _store(db)
    with pytest.raises(StoreCorrupt):
        store.read_task("session-A", "task-A")
    with pytest.raises(StoreCorrupt):
        store.create_task(identity(), snapshot())


def test_unknown_owner_does_not_mutate_unrelated_tasks(tmp_path):
    store = _store(tmp_path / "state")
    store.create_task(identity(), snapshot())
    store.create_task(identity(session="session-B", task="task-B"), snapshot(identity(session="session-B", task="task-B")))
    store.claim_writer("owner-A", "task-A", "attempt-A")
    assert store.mark_orphaned_unknown("owner-A") == ("task-A",)
    assert store.read_task("session-B", "task-B").phase == "preparing"





def test_events_are_identity_bound_deduplicated_and_bounded(tmp_path):
    store = _store(tmp_path / "state")
    store.create_task(identity(), snapshot())
    for sequence in range(1, 270):
        event = EvolutionEvent(identity(), sequence, "preparing", "phase_changed", ("task_created",), 1)
        assert store.append_event(event) is True
    assert store.append_event(EvolutionEvent(identity(), 269, "preparing", "phase_changed", (), 1)) is False
    current = store.read_task("session-A", "task-A")
    assert current.snapshot_revision == 1
    events = store.read_events("session-A", "task-A", "attempt-A")
    assert len(events) == 256
    assert events[0]["sequence"] == 14
    with pytest.raises(StoreConflict):
        store.append_event(EvolutionEvent(identity(session="session-B"), 270, "preparing", "phase_changed", (), 1))



def test_commit_transition_atomically_persists_snapshot_event_and_watermark(tmp_path):
    import sqlite3

    store = _store(tmp_path / "state")
    store.create_task(identity(), snapshot())
    event = EvolutionEvent(identity(), 1, "observing", "phase_changed", ("observation_recorded",), 2)
    changed = snapshot(phase="observing", rev=2)
    with sqlite3.connect(store.path) as conn:
        conn.execute("""CREATE TRIGGER reject_atomic_event BEFORE INSERT ON events
                       BEGIN SELECT RAISE(ABORT, 'event unavailable'); END""")

    with pytest.raises(sqlite3.IntegrityError, match="event unavailable"):
        store.commit_transition(identity(), 1, changed, event)

    current = store.read_task("session-A", "task-A")
    assert (current.phase, current.snapshot_revision) == ("preparing", 1)
    assert store.read_events("session-A", "task-A", "attempt-A") == ()
    with sqlite3.connect(store.path) as conn:
        assert conn.execute("SELECT sequence FROM event_watermarks WHERE task_id='task-A'").fetchone()[0] == 0
        conn.execute("DROP TRIGGER reject_atomic_event")

    assert store.commit_transition(identity(), 1, changed, event) == changed
    assert store.read_task("session-A", "task-A") == changed
    assert store.read_events("session-A", "task-A", "attempt-A")[0]["sequence"] == 1



def test_task_creation_and_writer_claim_are_one_durable_operation(tmp_path):
    store = _store(tmp_path / "state")
    created = store.create_task_with_writer(identity(), snapshot(), "owner-A")

    assert created == snapshot()
    with pytest.raises(StoreOwnershipError):
        store.claim_writer("owner-B", "task-A", "attempt-A")


def test_atomic_create_persists_accepted_event_and_allows_next_sequence(tmp_path):
    store = _store(tmp_path / "state")
    created = store.create_task_with_writer(identity(), snapshot(), "owner-A")

    assert created == snapshot()
    events = store.read_events("session-A", "task-A", "attempt-A")
    assert len(events) == 1
    assert events[0] == {
        "session_id": "session-A", "task_id": "task-A", "attempt_id": "attempt-A",
        "run_id": "run-A", "turn_epoch": 1, "sequence": 1, "phase": "preparing",
        "kind": "phase_changed", "fact_codes": ["task_accepted"],
        "snapshot_revision": 1, "evidence_refs": [],
    }
    next_snapshot = snapshot(phase="observing", rev=2)
    next_event = EvolutionEvent(identity(), 2, "observing", "phase_changed", (), 2)
    assert store.commit_transition(identity(), 1, next_snapshot, next_event) == next_snapshot
    assert [event["sequence"] for event in store.read_events("session-A", "task-A", "attempt-A")] == [1, 2]


def test_atomic_create_event_failure_rolls_back_task_writer_and_watermark(tmp_path):
    import sqlite3

    store = _store(tmp_path / "state")
    with store._connection() as db:
        db.execute("""CREATE TRIGGER reject_initial_event BEFORE INSERT ON events
                      BEGIN SELECT RAISE(ABORT, 'initial event unavailable'); END""")

    with pytest.raises(StoreCorrupt, match="initial event"):
        store.create_task_with_writer(identity(), snapshot(), "owner-A")

    assert store.read_task("session-A", "task-A") is None
    assert store.read_events("session-A", "task-A", "attempt-A") == ()
    with store._connection() as db:
        assert db.execute("SELECT COUNT(*) FROM writer_slot").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM event_watermarks").fetchone()[0] == 0
        db.execute("DROP TRIGGER reject_initial_event")

    store.create_task_with_writer(identity(), snapshot(), "owner-A")
    assert store.read_events("session-A", "task-A", "attempt-A")[0]["sequence"] == 1


def test_atomic_create_writer_conflict_leaves_no_partial_task_or_event(tmp_path):
    store = _store(tmp_path / "state")
    store.create_task_with_writer(identity(), snapshot(), "owner-A")
    other = identity(session="session-B", task="task-B", attempt="attempt-B", run="run-B")

    with pytest.raises(StoreOwnershipError):
        store.create_task_with_writer(other, snapshot(other), "owner-B")

    assert store.read_task("session-B", "task-B") is None
    assert store.read_events("session-B", "task-B", "attempt-B") == ()
    assert store.read_events("session-A", "task-A", "attempt-A")[0]["fact_codes"] == ["task_accepted"]


def test_transition_can_atomically_quarantine_writer_for_reconciliation(tmp_path):
    import sqlite3

    store = _store(tmp_path / "state")
    store.create_task(identity(), snapshot())
    store.compare_and_swap("task-A", 1, snapshot(phase="observing", rev=2))
    store.claim_writer("owner-A", "task-A", "attempt-A")

    changed = snapshot(phase="unknown", rev=3, blocker_code="verification_requires_reconciliation",
                       error_code="verification_requires_reconciliation", allowed_next_actions=("get_snapshot",))
    event = EvolutionEvent(identity(), 1, "unknown", "task_terminal", ("unknown",), 3)
    committed = store.commit_transition(identity(), 2, changed, event, quarantine_owner_id="owner-A")

    assert committed.phase == "unknown"
    assert store.read_events("session-A", "task-A", "attempt-A")[0]["sequence"] == 1
    conn = sqlite3.connect(store.path)
    try:
        assert conn.execute("SELECT state FROM writer_slot WHERE slot=1").fetchone()[0] == "unknown"
    finally:
        conn.close()


def test_quarantine_event_failure_rolls_back_state_and_writer_quarantine(tmp_path):
    import sqlite3

    store = _store(tmp_path / "state")
    store.create_task(identity(), snapshot())
    store.claim_writer("owner-A", "task-A", "attempt-A")
    with sqlite3.connect(store.path) as conn:
        conn.execute("""CREATE TRIGGER reject_quarantine_event BEFORE INSERT ON events
                       BEGIN SELECT RAISE(ABORT, 'event unavailable'); END""")
    changed = snapshot(phase="unknown", rev=2, blocker_code="verification_requires_reconciliation",
                       error_code="verification_requires_reconciliation", allowed_next_actions=("get_snapshot",))
    event = EvolutionEvent(identity(), 1, "unknown", "task_terminal", ("unknown",), 2)

    with pytest.raises(sqlite3.IntegrityError, match="event unavailable"):
        store.commit_transition(identity(), 1, changed, event, quarantine_owner_id="owner-A")

    assert store.read_task("session-A", "task-A").phase == "preparing"
    with sqlite3.connect(store.path) as conn:
        assert conn.execute("SELECT state FROM writer_slot WHERE slot=1").fetchone()[0] == "held"


def test_transition_and_writer_release_roll_back_together_on_event_failure(tmp_path):
    import sqlite3

    store = _store(tmp_path / "state")
    store.create_task(identity(), snapshot())
    for revision, phase in ((2, "observing"), (3, "editing"), (4, "checking")):
        store.compare_and_swap("task-A", revision - 1, snapshot(phase=phase, rev=revision))
    store.claim_writer("owner-A", "task-A", "attempt-A")
    with sqlite3.connect(store.path) as conn:
        conn.execute("""CREATE TRIGGER reject_final_event BEFORE INSERT ON events
                       BEGIN SELECT RAISE(ABORT, 'event unavailable'); END""")

    final = snapshot(phase="awaiting_review", rev=5)
    event = EvolutionEvent(identity(), 1, "awaiting_review", "phase_changed", (), 5)
    with pytest.raises(sqlite3.IntegrityError, match="event unavailable"):
        store.commit_transition(identity(), 4, final, event,
                                release_owner_id="owner-A", stop_confirmed=True)

    assert store.read_task("session-A", "task-A").phase == "checking"
    with pytest.raises(StoreOwnershipError):
        store.claim_writer("owner-B", "task-A", "attempt-A")
    assert store.read_events("session-A", "task-A", "attempt-A") == ()


def test_commit_transition_rejects_unbound_or_stale_event_without_mutation(tmp_path):
    store = _store(tmp_path / "state")
    store.create_task(identity(), snapshot())
    changed = snapshot(phase="observing", rev=2)

    invalid_events = (
        EvolutionEvent(identity(session="foreign-session"), 1, "observing", "phase_changed", (), 2),
        EvolutionEvent(identity(), 2, "observing", "phase_changed", (), 2),
        EvolutionEvent(identity(), 1, "editing", "phase_changed", (), 2),
        EvolutionEvent(identity(), 1, "observing", "phase_changed", (), 3),
    )
    for event in invalid_events:
        with pytest.raises(StoreConflict):
            store.commit_transition(identity(), 1, changed, event)
        current = store.read_task("session-A", "task-A")
        assert (current.phase, current.snapshot_revision) == ("preparing", 1)
        assert store.read_events("session-A", "task-A", "attempt-A") == ()


def test_concurrent_commit_transition_accepts_only_one_expected_revision(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    store = _store(tmp_path / "state")
    store.create_task(identity(), snapshot())

    def advance(phase):
        changed = snapshot(phase=phase, rev=2)
        event = EvolutionEvent(identity(), 1, phase, "phase_changed", (), 2)
        try:
            store.commit_transition(identity(), 1, changed, event)
            return "committed"
        except StoreConflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(advance, ("observing", "blocked")))

    assert sorted(results) == ["committed", "conflict"]
    current = store.read_task("session-A", "task-A")
    events = store.read_events("session-A", "task-A", "attempt-A")
    assert current.snapshot_revision == 2
    assert len(events) == 1
    assert (events[0]["phase"], events[0]["snapshot_revision"]) == (current.phase, 2)
    assert events[0]["sequence"] == 1


def _move_to_review(store, *, digest="a" * 64, evidence=()):
    store.create_task(identity(), snapshot(candidate_digest=digest, evidence=evidence))
    phases = ("observing", "editing", "checking", "awaiting_review")
    revision = 1
    for phase in phases:
        revision += 1
        store.compare_and_swap("task-A", revision - 1, snapshot(phase=phase, rev=revision, candidate_digest=digest, evidence=evidence))
    return revision


def test_awaiting_review_can_begin_new_attempt_only_after_writer_is_released(tmp_path):
    store = _store(tmp_path / "state")
    revision = _move_to_review(store)
    store.claim_writer("owner-review", "task-A", "attempt-A")
    with pytest.raises(StoreOwnershipError):
        store.begin_attempt("task-A", revision, identity(attempt="attempt-B", run="run-B"),
                            snapshot(identity(attempt="attempt-B", run="run-B"), phase="preparing", rev=revision + 1))
    store.release_writer("owner-review", stop_confirmed=True)
    other_identity = identity(session="session-B", task="task-B", attempt="attempt-X", run="run-X")
    store.create_task(other_identity, snapshot(other_identity))
    store.claim_writer("owner-other", "task-B", "attempt-X")
    next_id = identity(attempt="attempt-B", run="run-B")
    with pytest.raises(StoreOwnershipError):
        store.begin_attempt("task-A", revision, next_id, snapshot(next_id, phase="preparing", rev=revision + 1))
    store.release_writer("owner-other", stop_confirmed=True)
    started = store.begin_attempt("task-A", revision, next_id, snapshot(next_id, phase="preparing", rev=revision + 1, candidate_digest="b" * 64))
    assert started.identity == next_id
    assert store.read_task("session-A", "task-A").phase == "preparing"


def test_merged_task_cannot_start_another_attempt(tmp_path):
    store = _store(tmp_path / "state")
    revision = _move_to_review(store)
    store.compare_and_swap("task-A", revision, snapshot(phase="merged", rev=revision + 1, candidate_digest="a" * 64))
    next_id = identity(attempt="attempt-B", run="run-B")
    with pytest.raises(StoreConflict):
        store.begin_attempt("task-A", revision + 1, next_id, snapshot(next_id, phase="preparing", rev=revision + 2))


def test_candidate_digest_change_rejects_old_passed_evidence_on_cas(tmp_path):
    store = _store(tmp_path / "state")
    evidence = (VerificationEvidence("lint", "a" * 64, "passed", 0, "stopped", "evidence-1"),)
    store.create_task(identity(), snapshot(candidate_digest="a" * 64, evidence=evidence))
    with pytest.raises(StoreConflict):
        store.compare_and_swap("task-A", 1, snapshot(rev=2, candidate_digest="b" * 64, evidence=evidence))
    assert store.read_task("session-A", "task-A").candidate_digest == "a" * 64


def test_candidate_digest_change_rejects_old_passed_evidence_on_new_attempt(tmp_path):
    store = _store(tmp_path / "state")
    evidence = (VerificationEvidence("lint", "a" * 64, "passed", 0, "stopped", "evidence-1"),)
    revision = _move_to_review(store, evidence=evidence)
    next_id = identity(attempt="attempt-B", run="run-B")
    stale_evidence = snapshot(next_id, phase="preparing", rev=revision + 1, candidate_digest="b" * 64, evidence=evidence)
    with pytest.raises(StoreConflict):
        store.begin_attempt("task-A", revision, next_id, stale_evidence)


def test_operation_connection_is_closed_even_when_operation_raises(tmp_path, monkeypatch):
    import sqlite3

    class TrackingConnection:
        def __init__(self, connection):
            self.connection = connection
            self.closed = False

        def __getattr__(self, name):
            return getattr(self.connection, name)

        def close(self):
            self.closed = True
            self.connection.close()

    store = _store(tmp_path / "state")
    tracked = TrackingConnection(sqlite3.connect(store.path, isolation_level=None))
    monkeypatch.setattr(store, "_connect", lambda: tracked)
    with pytest.raises(RuntimeError, match="operation failed"):
        with store._connection():
            raise RuntimeError("operation failed")
    assert tracked.closed is True


def test_orphan_reconciliation_is_idempotent_under_concurrent_audit_writes(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    store = _store(tmp_path / "state")
    store.create_task(identity(), snapshot())
    store.append_event(EvolutionEvent(identity(), 1, "preparing", "phase_changed", (), 1))
    store.claim_writer("owner-dead", "task-A", "attempt-A")

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(store.mark_orphaned_unknown, ("owner-dead", "owner-dead")))

    assert results == [("task-A",), ("task-A",)]
    current = store.read_task("session-A", "task-A")
    events = store.read_events("session-A", "task-A", "attempt-A")
    assert current.phase == "unknown"
    assert current.snapshot_revision == 2
    assert [event["sequence"] for event in events] == [1, 2]
    assert events[-1]["phase"] == current.phase
    assert events[-1]["snapshot_revision"] == current.snapshot_revision
    assert store.accept_sequence(identity(), 2) is False
    with pytest.raises(StoreConflict, match="persisted event"):
        store.accept_sequence(identity(), 3)


def test_candidate_digest_change_on_new_attempt_invalidates_old_summary(tmp_path):
    store = _store(tmp_path / "state")
    store.create_task(identity(), snapshot(candidate_digest="digest-A", change_summary="summary for A"))
    store.compare_and_swap("task-A", 1, snapshot(phase="observing", rev=2, candidate_digest="digest-A", change_summary="summary for A"))
    store.compare_and_swap("task-A", 2, snapshot(phase="editing", rev=3, candidate_digest="digest-A", change_summary="summary for A"))
    store.compare_and_swap("task-A", 3, snapshot(phase="cancelling", rev=4, candidate_digest="digest-A", change_summary="summary for A"))
    store.compare_and_swap("task-A", 4, snapshot(phase="cancelled", rev=5, candidate_digest="digest-A", change_summary="summary for A"))
    next_identity = identity(attempt="attempt-B", run="run-B")

    updated = store.begin_attempt("task-A", 5, next_identity, snapshot(
        next_identity, phase="preparing", rev=6, candidate_digest="digest-B", change_summary="summary for A"))

    assert updated.change_summary is None
    assert store.read_task("session-A", "task-A").change_summary is None


def test_orphan_audit_failure_rolls_back_snapshot_event_watermark_and_owner_state(tmp_path):
    import sqlite3

    db_path = tmp_path / "state"
    store = _store(db_path)
    store.create_task(identity(), snapshot())
    store.append_event(EvolutionEvent(identity(), 1, "preparing", "phase_changed", (), 1))
    store.claim_writer("owner-dead", "task-A", "attempt-A")
    with sqlite3.connect(store.path) as conn:
        conn.execute("""CREATE TRIGGER reject_unknown_audit BEFORE INSERT ON events
                       WHEN json_extract(NEW.payload, '$.phase') = 'unknown'
                       BEGIN SELECT RAISE(ABORT, 'audit unavailable'); END""")

    with pytest.raises(sqlite3.IntegrityError, match="audit unavailable"):
        store.mark_orphaned_unknown("owner-dead")

    current = store.read_task("session-A", "task-A")
    assert (current.phase, current.snapshot_revision) == ("preparing", 1)
    assert [event["sequence"] for event in store.read_events("session-A", "task-A", "attempt-A")] == [1]
    with sqlite3.connect(store.path) as conn:
        assert conn.execute("SELECT sequence FROM event_watermarks WHERE task_id='task-A'").fetchone()[0] == 1
        assert conn.execute("SELECT state FROM writer_slot WHERE slot=1").fetchone()[0] == "held"
        conn.execute("DROP TRIGGER reject_unknown_audit")

    assert store.mark_orphaned_unknown("owner-dead") == ("task-A",)
    events = store.read_events("session-A", "task-A", "attempt-A")
    assert [event["sequence"] for event in events] == [1, 2]
    assert events[-1]["phase"] == "unknown"



def test_event_watermark_cannot_advance_without_its_persisted_event(tmp_path):
    store = _store(tmp_path / "state")
    store.create_task(identity(), snapshot())

    with pytest.raises(StoreConflict, match="persisted event"):
        store.accept_sequence(identity(), 1)

    assert store.read_events("session-A", "task-A", "attempt-A") == ()
    page = store.read_event_page("session-A", "task-A", "attempt-A")
    assert page.watermark_sequence == 0
    assert page.has_gap is False


def test_event_page_reports_bounded_history_and_cursor_gaps(tmp_path):
    store = _store(tmp_path / "state")
    store.create_task(identity(), snapshot())
    for sequence in range(1, 271):
        event = EvolutionEvent(identity(), sequence, "preparing", "phase_changed", (), 1)
        assert store.append_event(event) is True

    initial = store.read_event_page("session-A", "task-A", "attempt-A")
    assert len(initial.events) == 256
    assert initial.earliest_sequence == 15
    assert initial.latest_sequence == 270
    assert initial.watermark_sequence == 270
    assert initial.history_truncated is True
    assert initial.has_gap is True

    resumed = store.read_event_page("session-A", "task-A", "attempt-A", after_sequence=14)
    assert resumed.has_gap is False
    assert resumed.events[0]["sequence"] == 15
    ahead = store.read_event_page("session-A", "task-A", "attempt-A", after_sequence=500)
    assert ahead.events == ()
    assert ahead.has_gap is True


def test_event_page_detects_watermark_without_corresponding_persisted_event(tmp_path):
    import sqlite3

    store = _store(tmp_path / "state")
    store.create_task(identity(), snapshot())
    store.append_event(EvolutionEvent(identity(), 1, "preparing", "phase_changed", (), 1))
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE event_watermarks SET sequence=2 WHERE task_id='task-A'")

    page = store.read_event_page("session-A", "task-A", "attempt-A")
    assert page.watermark_sequence == 2
    assert page.latest_sequence == 1
    assert page.has_gap is True


def test_empty_retained_event_table_reports_truncated_history(tmp_path):
    import sqlite3

    store = _store(tmp_path / "state")
    store.create_task(identity(), snapshot())
    store.append_event(EvolutionEvent(identity(), 1, "preparing", "phase_changed", (), 1))
    with sqlite3.connect(store.path) as db:
        db.execute("DELETE FROM events WHERE task_id='task-A'")

    page = store.read_event_page("session-A", "task-A", "attempt-A")
    assert page.events == ()
    assert page.watermark_sequence == 1
    assert page.history_truncated is True
    assert page.has_gap is True


def test_unknown_writer_cannot_be_released_by_stale_owner_assertion(tmp_path):
    store = _store(tmp_path / "state")
    store.create_task(identity(), snapshot())
    store.claim_writer("owner-A", "task-A", "attempt-A")
    store.mark_task_unknown("task-A", "process_state_unconfirmed")

    with pytest.raises(StoreOwnershipError, match="unknown writer ownership"):
        store.release_writer("owner-A", stop_confirmed=True)
    assert store.list_writer_slots()[0]["state"] == "unknown"


def test_startup_reconciliation_refuses_unresolved_legacy_writer_without_mutating(tmp_path):
    db = tmp_path / "state"
    store = _store(db)
    store.create_task(identity(), snapshot())
    store.claim_writer("legacy-owner", "task-A", "attempt-A")
    store.close()

    recovered = _store(db)
    assert recovered.list_writer_slots() == ({
        "owner_id": "legacy-owner", "task_id": "task-A",
        "attempt_id": "attempt-A", "state": "held",
    },)
    with pytest.raises(StoreOwnershipError, match="owner liveness is unconfirmed"):
        recovered.reconcile_startup_writers()
    assert recovered.read_task("session-A", "task-A").phase == "preparing"
    assert recovered.list_writer_slots()[0]["state"] == "held"
    with pytest.raises(StoreOwnershipError):
        recovered.claim_writer("new-owner", "task-A", "attempt-A")
    with pytest.raises(StoreOwnershipError):
        recovered.release_writer("legacy-owner", stop_confirmed=False)


def test_startup_reconciliation_refuses_active_task_without_writer_without_mutating(tmp_path):
    store = _store(tmp_path / "state")
    store.create_task(identity(), snapshot())

    with pytest.raises(StoreOwnershipError, match="owner liveness is unconfirmed"):
        store.reconcile_startup_writers()
    assert store.read_task("session-A", "task-A").phase == "preparing"
    assert store.list_writer_slots() == ()


def test_windows_storage_without_verified_acl_is_disabled(tmp_path):
    from self_evolution_store import _require_private_storage_acl

    with pytest.raises(StoreError, match="ACL"):
        _require_private_storage_acl(tmp_path, platform="nt", verifier=None, expected_type="directory")
    with pytest.raises(StoreError, match="ACL"):
        _require_private_storage_acl(tmp_path, platform="nt", verifier=lambda _path: False, expected_type="directory")
    _require_private_storage_acl(tmp_path, platform="nt", verifier=lambda _path: True, expected_type="directory")



def test_legacy_writer_schema_is_not_migrated_while_an_owner_may_be_live(tmp_path):
    import sqlite3

    db = tmp_path / "legacy.sqlite3"
    store = _store(db)
    store.create_task(identity(), snapshot())
    store.close()
    with sqlite3.connect(db) as conn:
        conn.execute("DROP TABLE writer_slot")
        conn.execute("""CREATE TABLE writer_slot (
            slot INTEGER PRIMARY KEY CHECK(slot=1), owner_id TEXT NOT NULL,
            task_id TEXT NOT NULL, attempt_id TEXT NOT NULL)""")
        conn.execute("INSERT INTO writer_slot VALUES (1, 'legacy-owner', 'task-A', 'attempt-A')")

    with pytest.raises(StoreOwnershipError, match="legacy writer schema"):
        _store(db)
    with sqlite3.connect(db) as conn:
        assert [row[1] for row in conn.execute("PRAGMA table_info(writer_slot)")] == [
            "slot", "owner_id", "task_id", "attempt_id"]
        assert conn.execute("SELECT * FROM writer_slot").fetchall() == [
            (1, "legacy-owner", "task-A", "attempt-A")]



def test_windows_store_constructor_stays_disabled_without_production_acl_verifier(tmp_path):
    if os.name != "nt":
        pytest.skip("production ACL gate is platform-specific")
    database_root = tmp_path / "must-not-open"
    with pytest.raises(StoreError, match="Windows storage disabled"):
        EvolutionStore(database_root)
    assert not database_root.exists()


def test_windows_acl_callback_cannot_override_failed_native_audit(tmp_path, monkeypatch):
    import self_evolution_windows_acl
    from self_evolution_store import _require_private_storage_acl

    monkeypatch.setattr(
        self_evolution_windows_acl,
        "audit_private_path",
        lambda _path, **_kwargs: (_ for _ in ()).throw(self_evolution_windows_acl.WindowsAclError("denied")),
    )
    with pytest.raises(StoreError, match="native private ACL audit failed"):
        _require_private_storage_acl(tmp_path, platform="nt", verifier=lambda _path: True, expected_type="directory")


@pytest.mark.skipif(os.name != "nt", reason="Windows ACL gate")
@pytest.mark.parametrize("suffix", ["-wal", "-shm"])
@pytest.mark.parametrize("operation", ["initialize", "reconnect"])
def test_unsafe_existing_sidecar_is_denied_before_sqlite_connect(tmp_path, monkeypatch, suffix, operation):
    import self_evolution_store as store_module
    import self_evolution_windows_acl as acl

    root = tmp_path / "state"
    root.mkdir()
    db = root / "self-evolution.sqlite3"
    store = _store(root) if operation == "reconnect" else None
    sidecar = Path(f"{db}{suffix}")
    sidecar.write_bytes(b"untrusted")
    original_audit = acl.audit_private_path
    observed = []

    def audit(path, **kwargs):
        observed.append(Path(path))
        if Path(path) == sidecar:
            raise acl.WindowsAclError("unsafe sidecar")
        return original_audit(path, **kwargs)

    connects = []
    monkeypatch.setattr(acl, "audit_private_path", audit)
    monkeypatch.setattr(store_module.sqlite3, "connect", lambda *a, **kw: connects.append(a))
    with pytest.raises(StoreError, match="native private ACL audit failed"):
        if store is None:
            EvolutionStore(root, trusted_acl_verifier=lambda _path: True)
        else:
            store._connect()
    assert sidecar in observed
    assert connects == []


@pytest.mark.skipif(os.name != "nt", reason="requires real Windows ACLs")
@pytest.mark.parametrize("suffix", ["-wal", "-shm"])
def test_real_unsafe_sidecar_acl_blocks_store_before_sqlite_open(tmp_path, monkeypatch, suffix):
    import shutil
    import subprocess
    import self_evolution_store as store_module

    root = tmp_path / "state"
    root.mkdir()
    sidecar = Path(f"{root / 'self-evolution.sqlite3'}{suffix}")
    sidecar.write_bytes(b"untrusted")
    icacls = shutil.which("icacls.exe")
    assert icacls, "icacls.exe is required for the real ACL test"
    result = subprocess.run([icacls, str(sidecar), "/grant", "*S-1-1-0:(M)"],
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    connects = []
    monkeypatch.setattr(store_module.sqlite3, "connect", lambda *args, **kwargs: connects.append(args))
    with pytest.raises(StoreError, match="native private ACL audit failed") as error:
        EvolutionStore(root, trusted_acl_verifier=lambda _path: True)
    assert str(sidecar) in str(error.value)
    assert connects == []


@pytest.mark.skipif(os.name != "nt", reason="requires real Windows handle types")
@pytest.mark.parametrize("suffix", ["", "-wal", "-shm"])
def test_directory_in_sqlite_file_slot_is_rejected_before_connect(tmp_path, monkeypatch, suffix):
    import self_evolution_store as store_module

    root = tmp_path / "state"
    root.mkdir()
    target = Path(f"{root / 'self-evolution.sqlite3'}{suffix}")
    target.mkdir()
    connects = []
    monkeypatch.setattr(store_module.sqlite3, "connect", lambda *args, **kwargs: connects.append(args))
    with pytest.raises(StoreError, match="native private ACL audit failed") as error:
        EvolutionStore(root, trusted_acl_verifier=lambda _path: True)
    assert "type" in str(error.value.__cause__)
    assert connects == []


@pytest.mark.skipif(os.name != "nt", reason="Windows ACL gate")
@pytest.mark.parametrize("suffix", ["-wal", "-shm"])
def test_disappearing_sidecar_restarts_full_audit_and_never_skips_unsafe_replacement(tmp_path, monkeypatch, suffix):
    import self_evolution_store as store_module
    import self_evolution_windows_acl as acl

    root = tmp_path / "state"
    root.mkdir()
    sidecar = Path(f"{root / 'self-evolution.sqlite3'}{suffix}")
    sidecar.write_bytes(b"data")
    real = acl.audit_private_path
    checked = []
    def audit(path, **kwargs):
        checked.append(Path(path))
        if Path(path) == sidecar:
            if checked.count(sidecar) == 1:
                raise acl.WindowsAclError("temporarily gone", winerror=2)
            raise acl.WindowsAclError("unsafe replacement")
        return real(path, **kwargs)
    monkeypatch.setattr(acl, "audit_private_path", audit)
    connects = []
    monkeypatch.setattr(store_module.sqlite3, "connect", lambda *a, **kw: connects.append(a))
    with pytest.raises(StoreError, match="native private ACL audit failed"):
        EvolutionStore(root, trusted_acl_verifier=lambda _: True)
    assert checked.count(root) >= 2
    assert checked.count(sidecar) == 2
    assert not connects


@pytest.mark.skipif(os.name != "nt", reason="Windows ACL gate")
def test_missing_parent_is_rejected_without_creating_it_or_opening_sqlite(tmp_path, monkeypatch):
    import self_evolution_store as store_module
    root = tmp_path / "missing" / "state"
    connects = []
    monkeypatch.setattr(store_module.sqlite3, "connect", lambda *a, **kw: connects.append(a))
    with pytest.raises(StoreError, match="Windows storage disabled"):
        EvolutionStore(root, trusted_acl_verifier=lambda _: True)
    assert not (tmp_path / "missing").exists()
    assert not connects


@pytest.mark.skipif(os.name != "nt", reason="Windows ACL gate")
@pytest.mark.parametrize("winerror", [2, 5, 32, 33])
def test_persistent_transient_sidecar_error_exhausts_retries_before_connect(tmp_path, monkeypatch, winerror):
    import self_evolution_store as store_module
    import self_evolution_windows_acl as acl
    root = tmp_path / "state"
    root.mkdir()
    sidecar = root / "self-evolution.sqlite3-shm"
    sidecar.write_bytes(b"data")
    real = acl.audit_private_path
    attempts = []
    def audit(path, **kwargs):
        if Path(path) == sidecar:
            attempts.append(path)
            raise acl.WindowsAclError("blocked", winerror=winerror)
        return real(path, **kwargs)
    monkeypatch.setattr(acl, "audit_private_path", audit)
    monkeypatch.setattr(store_module.time, "sleep", lambda _: None)
    connects = []
    monkeypatch.setattr(store_module.sqlite3, "connect", lambda *a, **kw: connects.append(a))
    with pytest.raises(StoreError, match="native private ACL audit failed"):
        EvolutionStore(root, trusted_acl_verifier=lambda _: True)
    assert len(attempts) == 30
    assert not connects


def _bound_start_claim(store, *, session="session-A", run="run-A", epoch=1, request="00000000-0000-4000-8000-000000000001"):
    return store.reserve_start_request(session, request, "a" * 64, run_id=run, turn_epoch=epoch)


def test_start_claim_consume_atomically_binds_request_session_run_and_epoch(tmp_path):
    store = _store(tmp_path / "state")
    claim = _bound_start_claim(store)

    store.consume_start_request_claim(
        claim, session_id="session-A", run_id="run-A", turn_epoch=1,
    )

    # A second caller cannot turn the already-consumed reservation into another
    # candidate-creation authorization, even with the original claim object.
    with pytest.raises(StoreOwnershipError):
        store.consume_start_request_claim(
            claim, session_id="session-A", run_id="run-A", turn_epoch=1,
        )
    replay = store.reserve_start_request(
        "session-A", claim.request_id, "a" * 64, run_id="run-A", turn_epoch=1,
    )
    assert replay.status == "in_progress" and replay.claim_token is None


@pytest.mark.parametrize("binding", [
    {"session_id": "session-B", "run_id": "run-A", "turn_epoch": 1},
    {"session_id": "session-A", "run_id": "run-B", "turn_epoch": 1},
    {"session_id": "session-A", "run_id": "run-A", "turn_epoch": 2},
])
def test_start_claim_consume_rejects_wrong_authenticated_context_without_consuming(tmp_path, binding):
    store = _store(tmp_path / "state")
    claim = _bound_start_claim(store)

    with pytest.raises(StoreOwnershipError):
        store.consume_start_request_claim(claim, **binding)

    # A wrong context cannot burn a valid claim; its true owner can still consume it.
    store.consume_start_request_claim(
        claim, session_id="session-A", run_id="run-A", turn_epoch=1,
    )


def test_start_claim_consume_rejects_forged_unknown_and_replayed_claims(tmp_path):
    store = _store(tmp_path / "state")
    claim = _bound_start_claim(store)
    from dataclasses import replace

    for forged in (
        replace(claim, claim_token="forged-token-that-is-long-enough-to-look-valid"),
        replace(claim, request_digest="b" * 64),
        replace(claim, status="in_progress"),
    ):
        with pytest.raises(StoreOwnershipError):
            store.consume_start_request_claim(
                forged, session_id="session-A", run_id="run-A", turn_epoch=1,
            )

    store.mark_start_request_unknown(claim, {"status": "unknown", "error_code": "uncertain"})
    with pytest.raises(StoreOwnershipError):
        store.consume_start_request_claim(
            claim, session_id="session-A", run_id="run-A", turn_epoch=1,
        )


def _consume_start_claim_worker(db, claim, barrier, outcomes):
    store = _store(db)
    barrier.wait(timeout=10)
    try:
        store.consume_start_request_claim(
            claim, session_id="session-A", run_id="run-A", turn_epoch=1,
        )
        outcomes.append("consumed")
    except StoreOwnershipError:
        outcomes.append("denied")


def test_two_store_instances_can_consume_one_start_claim_exactly_once(tmp_path):
    import threading

    db = tmp_path / "state"
    claim = _bound_start_claim(_store(db))
    barrier = threading.Barrier(2)
    outcomes = []
    threads = [threading.Thread(target=_consume_start_claim_worker,
                                args=(db, claim, barrier, outcomes)) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)
        assert not thread.is_alive()

    assert sorted(outcomes) == ["consumed", "denied"]


def test_consumed_start_claim_can_only_create_writer_for_its_reserved_epoch(tmp_path):
    store = _store(tmp_path / "state")
    claim = _bound_start_claim(store)
    store.consume_start_request_claim(
        claim, session_id="session-A", run_id="run-A", turn_epoch=1,
    )
    wrong = identity(run="run-B", epoch=2)

    with pytest.raises(StoreOwnershipError, match="different task epoch"):
        store.create_task_with_writer(
            wrong, snapshot(wrong), "owner-A", start_request_claim=claim,
        )
    assert store.read_task("session-A", "task-A") is None
    assert store.list_writer_slots() == ()

    store.create_task_with_writer(
        identity(), snapshot(), "owner-A", start_request_claim=claim,
    )
    assert store.read_task("session-A", "task-A").phase == "preparing"
    assert store.read_events("session-A", "task-A", "attempt-A")[0]["fact_codes"] == ["task_accepted"]


def test_legacy_unbound_start_claim_cannot_authorize_pre_side_effect_dispatch(tmp_path):
    store = _store(tmp_path / "state")
    claim = store.reserve_start_request("session-A", "00000000-0000-4000-8000-000000000001", "a" * 64)

    with pytest.raises(StoreOwnershipError):
        store.consume_start_request_claim(
            claim, session_id="session-A", run_id="run-A", turn_epoch=1,
        )


def test_legacy_unbound_start_claim_cannot_create_task_or_writer(tmp_path):
    store = _store(tmp_path / "state")
    claim = store.reserve_start_request(
        "session-A", "00000000-0000-4000-8000-000000000001", "a" * 64,
    )

    with pytest.raises(StoreOwnershipError):
        store.create_task_with_writer(
            identity(), snapshot(), "owner-A", start_request_claim=claim,
        )

    assert store.read_task("session-A", "task-A") is None
    assert store.list_writer_slots() == ()


def test_legacy_unbound_claim_blocks_compat_create_without_claim_until_finalized(tmp_path):
    store = _store(tmp_path / "state")
    claim = store.reserve_start_request(
        "session-A", "00000000-0000-4000-8000-000000000001", "a" * 64,
    )

    with pytest.raises(StoreOwnershipError):
        store.create_task_with_writer(identity(), snapshot(), "owner-A")
    assert store.read_task("session-A", "task-A") is None
    assert store.list_writer_slots() == ()

    store.finish_start_request(claim, {"status": "denied", "error_code": "policy_denied"})
    store.create_task_with_writer(identity(), snapshot(), "owner-A")
    assert store.read_task("session-A", "task-A").phase == "preparing"


def test_bound_start_claim_cannot_create_writer_until_pre_side_effect_consume(tmp_path):
    store = _store(tmp_path / "state")
    claim = _bound_start_claim(store)

    with pytest.raises(StoreOwnershipError):
        store.create_task_with_writer(
            identity(), snapshot(), "owner-A", start_request_claim=claim,
        )

    assert store.read_task("session-A", "task-A") is None
    assert store.list_writer_slots() == ()
    store.consume_start_request_claim(
        claim, session_id="session-A", run_id="run-A", turn_epoch=1,
    )
    store.create_task_with_writer(
        identity(), snapshot(), "owner-A", start_request_claim=claim,
    )


def test_store_reuses_native_private_acl_verifier_for_database_lifecycle(tmp_path):
    import os
    import pytest
    if os.name != "nt":
        pytest.skip("requires real Windows ACL proof")
    from self_evolution_windows_acl import create_private_storage_acl_verifier

    root = tmp_path / "private-store"
    root.mkdir()
    verifier = create_private_storage_acl_verifier(root)
    store = EvolutionStore(root, trusted_acl_verifier=verifier)

    claim = store.reserve_start_request("session-A", "00000000-0000-4000-8000-000000000001", "a" * 64,
                                        run_id="run-A", turn_epoch=1)
    assert claim.status == "claimed"
    root_proof = store.audit_private_storage_path(root)
    database_proof = store.audit_private_storage_path(store.path)
    assert root_proof.expected_type == "directory"
    assert database_proof.expected_type == "file"
    assert database_proof.root_identity == root_proof.root_identity
    store.close()
    reopened = EvolutionStore(root, trusted_acl_verifier=verifier)
    replay = reopened.reserve_start_request(
        "session-A", "00000000-0000-4000-8000-000000000001", "a" * 64,
        run_id="run-A", turn_epoch=1,
    )
    assert replay.status == "in_progress"


def test_native_acl_verifier_audits_live_sqlite_wal_and_shm_then_reopens(tmp_path):
    import os
    import sqlite3
    import pytest
    if os.name != "nt":
        pytest.skip("requires real Windows DPAPI/ACL behavior")
    from self_evolution_windows_acl import create_private_storage_acl_verifier

    root = tmp_path / "sqlite-lifecycle"
    root.mkdir()
    verifier = create_private_storage_acl_verifier(root)
    store = EvolutionStore(root, trusted_acl_verifier=verifier)
    writer = sqlite3.connect(store.path)
    probe = None
    try:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("CREATE TABLE IF NOT EXISTS lifecycle_probe(value TEXT NOT NULL)")
        writer.execute("INSERT INTO lifecycle_probe(value) VALUES ('present')")
        writer.commit()
        sidecars = [Path(f"{store.path}-wal"), Path(f"{store.path}-shm")]
        assert all(path.is_file() for path in sidecars)
        assert all(store.audit_private_storage_path(path).expected_type == "file" for path in sidecars)
        probe = store._connect()
        assert probe.execute("SELECT value FROM lifecycle_probe").fetchone()[0] == "present"
    finally:
        if probe is not None:
            probe.close()
        writer.close()
    reopened = EvolutionStore(root, trusted_acl_verifier=verifier)
    with reopened._connection() as db:
        assert db.execute("SELECT value FROM lifecycle_probe").fetchone()[0] == "present"


def test_same_store_serializes_connection_lifetimes_during_native_acl_audits(tmp_path, monkeypatch):
    """A second operation must not open/close WAL handles during the first audit window."""
    import threading
    import time

    store = _store(tmp_path / "state")
    original_connect = store._connect
    first_connection_open = threading.Event()
    release_first = threading.Event()
    second_attempting = threading.Event()
    connect_calls = []

    def observed_connect():
        connect_calls.append(threading.get_ident())
        if len(connect_calls) == 1:
            connection = original_connect()
            first_connection_open.set()
            return connection
        return original_connect()

    monkeypatch.setattr(store, "_connect", observed_connect)

    def first_operation():
        with store._connection():
            assert first_connection_open.wait(timeout=5)
            assert release_first.wait(timeout=5)

    def second_operation():
        second_attempting.set()
        with store._connection():
            pass

    first = threading.Thread(target=first_operation)
    second = threading.Thread(target=second_operation)
    first.start()
    assert first_connection_open.wait(timeout=5)
    second.start()
    assert second_attempting.wait(timeout=5)
    time.sleep(0.1)
    assert len(connect_calls) == 1, "concurrent connection can change WAL/SHM lifetime during ACL audit"
    release_first.set()
    first.join(timeout=5)
    second.join(timeout=5)
    assert not first.is_alive() and not second.is_alive()
    assert len(connect_calls) == 2
