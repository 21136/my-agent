from __future__ import annotations

import multiprocessing as mp
import os
import queue as queue_module
import time
import traceback
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
import pytest

# Spawned children import this module by name. Keep the repository core importable
# without referring to any non-temporary user project or session.
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from paths import AgentPaths
from session import Session, SessionMeta
from work_card_ledger import WorkCardLedger
from work_record_cards import (
    IndependentWorkRecordCardsSnapshot,
    ProjectWorkRecordCardsSnapshot,
    build_independent_work_record_card,
    build_project_work_record_cards,
)
from work_records import WorkRecordStore


_SESSION_ID = "20261030-abcdef01"
_PROJECT_ID = "synthetic-ledger-project"


class _FixedCardClock(datetime):
    @classmethod
    def now(cls, tz=None):
        value = cls(2026, 10, 30, 12, 0, 0, tzinfo=UTC)
        return value if tz is None else value.astimezone(tz)


def _open_synthetic_session(root_text: str, *, lock_timeout: float = 5.0):
    root = Path(root_text)
    paths = AgentPaths(
        agent_root=root,
        workspace=root / "workspace",
        data=root / "data",
        evolve=root / "evolve",
    )
    session_dir = paths.data / "sessions" / _SESSION_ID
    session = Session(
        conversation_id=_SESSION_ID,
        session_dir=session_dir,
        goal="synthetic only",
        meta=SessionMeta(project_id=_PROJECT_ID),
        messages=[],
        paths=paths,
    )
    store = WorkRecordStore(session, lock_timeout=lock_timeout)
    return session, store


def _worker_save(
    output_queue,
    start_gate,
    root_text: str,
    request_id: str,
    snapshot: ProjectWorkRecordCardsSnapshot | IndependentWorkRecordCardsSnapshot,
    conflict_title: bool = False,
    lock_timeout: float = 5.0,
) -> None:
    try:
        if not start_gate.wait(20):
            output_queue.put({"kind": "worker_error", "error": "start gate timed out"})
            return
        if isinstance(snapshot, IndependentWorkRecordCardsSnapshot):
            import work_record_cards as cards_module
            cards_module.datetime = _FixedCardClock
        session, store = _open_synthetic_session(root_text, lock_timeout=lock_timeout)
        ledger = WorkCardLedger(session, store)
        candidate = snapshot
        if conflict_title:
            if isinstance(snapshot, ProjectWorkRecordCardsSnapshot):
                changed_card = replace(snapshot.cards[0], title=snapshot.cards[0].title + " changed")
                candidate = replace(snapshot, cards=(changed_card, *snapshot.cards[1:]))
            else:
                changed_card = replace(snapshot.card, title=snapshot.card.title + " changed")
                candidate = replace(snapshot, card=changed_card)
        result = ledger.save_snapshot(request_id, candidate)
        output_queue.put({
            "kind": "save",
            "request_id": request_id,
            "status": result.status,
            "card_id": result.card_id,
            "reason": result.reason,
        })
    except BaseException:
        output_queue.put({"kind": "worker_error", "error": traceback.format_exc()})


def _worker_ack(output_queue, start_gate, root_text: str, card_id: str, *, lock_timeout: float = 5.0) -> None:
    try:
        if not start_gate.wait(20):
            output_queue.put({"kind": "worker_error", "error": "start gate timed out"})
            return
        session, store = _open_synthetic_session(root_text, lock_timeout=lock_timeout)
        result = WorkCardLedger(session, store).acknowledge_viewed(card_id)
        output_queue.put({
            "kind": "ack",
            "status": result.status,
            "card_id": result.card_id,
            "viewed_at": result.viewed_at,
        })
    except BaseException:
        output_queue.put({"kind": "worker_error", "error": traceback.format_exc()})


def _worker_save_prebuilt(output_queue, root_text: str, request_id: str, snapshot: ProjectWorkRecordCardsSnapshot,
                          lock_timeout: float) -> None:
    try:
        session, store = _open_synthetic_session(root_text, lock_timeout=lock_timeout)
        result = WorkCardLedger(session, store).save_snapshot(request_id, snapshot)
        output_queue.put({
            "kind": "save",
            "request_id": request_id,
            "status": result.status,
            "card_id": result.card_id,
            "reason": result.reason,
        })
    except BaseException:
        output_queue.put({"kind": "worker_error", "error": traceback.format_exc()})


def _run_spawn_group(worker, jobs: list[tuple[Any, ...]]) -> list[dict[str, Any]]:
    context = mp.get_context("spawn")
    output_queue = context.Queue()
    start_gate = context.Event()
    processes = [
        context.Process(target=worker, args=(output_queue, start_gate, *job))
        for job in jobs
    ]
    try:
        for process in processes:
            process.start()
        start_gate.set()
        results = []
        deadline = time.monotonic() + 45
        while len(results) < len(processes) and time.monotonic() < deadline:
            try:
                results.append(output_queue.get(timeout=0.25))
            except queue_module.Empty:
                if all(not process.is_alive() for process in processes):
                    break
        for process in processes:
            process.join(2)
        stuck = [process for process in processes if process.is_alive()]
        for process in stuck:
            process.terminate()
            process.join(5)
        assert not stuck, "spawn worker did not exit"
        assert len(results) == len(processes), (
            f"received {len(results)} worker results for {len(processes)} processes; "
            f"exitcodes={[process.exitcode for process in processes]}"
        )
        assert [process.exitcode for process in processes] == [0] * len(processes)
        errors = [result for result in results if result.get("kind") == "worker_error"]
        assert errors == [], "spawn worker failed: " + repr(errors)
        return results
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(5)
        output_queue.close()
        output_queue.join_thread()


def _make_synthetic_ledger_fixture(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    paths = AgentPaths(
        agent_root=root,
        workspace=root / "workspace",
        data=root / "data",
        evolve=root / "evolve",
    )
    session_dir = paths.data / "sessions" / _SESSION_ID
    session_dir.mkdir(parents=True)
    session = Session(
        conversation_id=_SESSION_ID,
        session_dir=session_dir,
        goal="synthetic only",
        meta=SessionMeta(project_id=_PROJECT_ID),
        messages=[],
        paths=paths,
    )
    store = WorkRecordStore(session, lock_timeout=5.0)
    project_item = store.create_item(
        title="synthetic project card",
        scope={"kind": "project", "project_id": _PROJECT_ID},
    )
    store.add_requirement(project_item["item_id"], "synthetic project requirement", event_id="project-req")
    independent_item = store.create_item(title="synthetic independent card", scope={"kind": "independent"})
    store.add_requirement(independent_item["item_id"], "synthetic independent requirement", event_id="independent-req")
    return session, store, project_item["item_id"], independent_item["item_id"]


def _request_id(number: int) -> str:
    return f"00000000-0000-4000-8000-{number:012x}"


def test_spawn_processes_serialize_project_and_independent_saves_duplicates_and_ack(tmp_path, monkeypatch):
    if os.name != "nt":
        pytest.skip("this stress test targets native Windows spawn and OS byte-range locks")

    monkeypatch.setattr("work_record_cards.datetime", _FixedCardClock)
    session, store, _project_item_id, independent_item_id = _make_synthetic_ledger_fixture(tmp_path / "ledger-home")
    project_snapshot = build_project_work_record_cards(store, _PROJECT_ID)
    independent_snapshot = build_independent_work_record_card(store, independent_item_id)
    ledger = WorkCardLedger(session, store)

    baseline_request = _request_id(1)
    baseline = ledger.save_snapshot(baseline_request, project_snapshot)
    assert baseline.status == "saved" and baseline.card_id

    distinct_jobs = [
        (str(tmp_path / "ledger-home"), _request_id(2), project_snapshot),
        (str(tmp_path / "ledger-home"), _request_id(3), project_snapshot),
        (str(tmp_path / "ledger-home"), _request_id(4), independent_snapshot),
        (str(tmp_path / "ledger-home"), _request_id(5), independent_snapshot),
    ]
    distinct_results = _run_spawn_group(_worker_save, distinct_jobs)
    assert all(result["status"] == "saved" for result in distinct_results)

    # Reopen through new Session/Store/Ledger objects: all distinct request IDs persist.
    reopened_session, reopened_store = _open_synthetic_session(str(tmp_path / "ledger-home"))
    reopened = WorkCardLedger(reopened_session, reopened_store)
    records = reopened.list_cards()
    expected_requests = {_request_id(n) for n in range(1, 6)}
    assert {record["request_id"] for record in records} == expected_requests
    assert len(records) == len(expected_requests)
    assert sum(record["snapshot"].get("kind") == "independent" for record in records) == 2

    duplicates = [
        (str(tmp_path / "ledger-home"), baseline_request, project_snapshot, False, 5.0),
        (str(tmp_path / "ledger-home"), baseline_request, project_snapshot, False, 5.0),
        (str(tmp_path / "ledger-home"), baseline_request, project_snapshot, False, 5.0),
        (str(tmp_path / "ledger-home"), baseline_request, project_snapshot, True, 5.0),
    ]
    duplicate_results = _run_spawn_group(_worker_save, duplicates)
    same_results = [result for result in duplicate_results if not result.get("reason") and result["status"] == "already_saved"]
    conflict_results = [result for result in duplicate_results if result["status"] == "conflict"]
    assert len(same_results) == 3
    assert all(result["card_id"] == baseline.card_id for result in same_results + conflict_results)
    assert len(conflict_results) == 1

    records_after_duplicates = reopened.list_cards()
    assert len(records_after_duplicates) == len(expected_requests)
    assert {record["request_id"] for record in records_after_duplicates} == expected_requests
    stored_baseline = reopened.get_by_request_id(baseline_request)
    assert stored_baseline["snapshot"]["cards"][0]["title"] == "synthetic project card"
    acknowledgements = [
        (str(tmp_path / "ledger-home"), baseline.card_id)
        for _ in range(5)
    ]
    ack_results = _run_spawn_group(_worker_ack, acknowledgements)
    assert sum(result["status"] == "acknowledged" for result in ack_results) == 1
    assert sum(result["status"] == "already_acknowledged" for result in ack_results) == 4
    viewed_times = {result["viewed_at"] for result in ack_results}
    assert len(viewed_times) == 1 and None not in viewed_times
    reopened_after_ack = WorkCardLedger(reopened_session, reopened_store)
    assert reopened_after_ack.get_card(baseline.card_id)["viewed_at"] == next(iter(viewed_times))


def test_lock_timeout_is_reported_and_does_not_overwrite_existing_history(tmp_path):
    if os.name != "nt":
        pytest.skip("native Windows byte-range lock timeout validation requires Windows")

    session, store, _, _ = _make_synthetic_ledger_fixture(tmp_path / "timeout-home")
    snapshot = build_project_work_record_cards(store, _PROJECT_ID)
    ledger = WorkCardLedger(session, store)
    saved = ledger.save_snapshot(_request_id(10), snapshot)
    assert saved.status == "saved"
    acknowledged = ledger.acknowledge_viewed(saved.card_id)
    assert acknowledged.status == "acknowledged"
    historical_record = ledger.get_by_request_id(_request_id(10))
    context = mp.get_context("spawn")
    queue = context.Queue()
    child = context.Process(
        target=_worker_save_prebuilt,
        args=(queue, str(tmp_path / "timeout-home"), _request_id(11), snapshot, 0.15),
    )

    def hold_real_session_lock(_items):
        child.start()
        result = queue.get(timeout=20)
        child.join(10)
        assert not child.is_alive()
        assert child.exitcode == 0
        assert result["kind"] == "save"
        assert result["status"] == "failed"
        assert result["reason"] == "ledger_write_failed_no_retry"
        return None

    try:
        store.with_locked_items(hold_real_session_lock)
    finally:
        if child.is_alive():
            child.terminate()
            child.join(5)
        queue.close()
        queue.join_thread()

    after_timeout = WorkCardLedger(session, store)
    cards = after_timeout.list_cards()
    assert len(cards) == 1
    assert cards[0] == historical_record
    assert after_timeout.get_by_request_id(_request_id(11)) is None
