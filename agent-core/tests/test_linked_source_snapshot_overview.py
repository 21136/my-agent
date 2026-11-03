from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from multi_project_work_record_bindings import (
    ConversationWorkRecordBindings,
    ReadOnlyWorkRecordStore,
    _issue_authorized_project_record_source,
    _reader_for,
)
from server import WsBridge
from test_multi_project_work_record_bindings import _fixture
from work_card_ledger import WorkCardLedger
from work_records import WorkRecordCorrupt, WorkRecordStore, _digest, _file_lock
import server
from work_records_overview_api import (
    _bounded_group,
    _decode_cursor,
    _detail_cursor,
    _linked_source_snapshot_groups,
    query_work_records_overview,
)


def _confirmed_project_copies(root: Path, *, terminal_source: bool = False):
    parent, sources, tasks = _fixture(root)
    if terminal_source:
        for task in tasks:
            source = _reader_for(sources[task.project_id].store)
            item = source.get_item(task.item_id)
            requirement = next(req for req in item["requirements"] if req["requirement_id"] == task.requirement_id)
            source.record_result_if_current_run(
                task.item_id, task.requirement_id, run_id=requirement["current_run_id"],
                result_id=f"synthetic-terminal-{task.task_id}", status="completed",
                summary="synthetic captured terminal state", verification={"status": "not_verified"},
            )
    else:
        source_a = _reader_for(sources["project-a"].store)
        first_item = source_a.get_item(tasks[0].item_id)
        for index in range(6):
            source_a.add_requirement(tasks[0].item_id, f"Synthetic A requirement {index + 2}",
                                     event_id=f"overview-a-extra-{index}")
        second_item = source_a.create_item(title="Synthetic A second item",
                                           scope={"kind": "project", "project_id": "project-a"})
        source_a.add_requirement(second_item["item_id"], "Synthetic A2 requirement", event_id="overview-a2-req")
        assert first_item is not None
    tasks = [replace_task_digest(task, sources) for task in tasks]
    bindings = ConversationWorkRecordBindings(parent)
    rows = bindings.bind_existing_tasks(batch_id="overview-linked-copy", tasks=tasks, authorized_sources=sources)
    parent_run_id = str(uuid4())
    for row in rows:
        bindings.record_worker_start(binding_id=row["binding_id"], worker_id=row["worker_id"],
            run_id=row["run_id"], reservation_id=f"reservation-{row['task_id']}",
            parent_run_id=parent_run_id, turn_epoch=2, project_id=row["project_id"], task_id=row["task_id"])
        bindings.record_worker_lifecycle(binding_id=row["binding_id"], worker_id=row["worker_id"],
            run_id=row["run_id"], sequence=2, status="model_turn_finished", finish_reason="completed")
    for project_id in ("project-a", "project-b"):
        row = next(item for item in rows if item["project_id"] == project_id)
        args = {"project_id": project_id, "binding_ids": [row["binding_id"]], "source": sources[project_id]}
        preview = bindings.prepare_project_session_copy(**args)
        bindings.prepare_project_session_copy(**args, expected=preview, commit=True, is_current=lambda: True)
    store = WorkRecordStore(parent)
    events: list[dict] = []
    bridge = WsBridge(emit=events.append, paths=parent.paths)
    repl = SimpleNamespace(session=parent, agent=SimpleNamespace(session=parent))
    assert server._resume_linked_project_session_copies(repl, bridge, parent, store, emit_event=True) == 2
    return parent, store, bindings, rows, sources, events, bridge, repl


def replace_task_digest(task, sources):
    from dataclasses import replace
    return replace(task, source_item_digest=_digest(sources[task.project_id].store.get_item(task.item_id)))


def _refresh_a(parent, bindings, rows, sources):
    source_a = _reader_for(sources["project-a"].store)
    refreshed_req = source_a.add_requirement(rows[0]["item_id"], "Synthetic refreshed A requirement", event_id="overview-a-refresh")
    source_a.begin_requirement_run(rows[0]["item_id"], refreshed_req["requirement_id"],
                                   run_id=str(uuid4()), expected_current_run_id=None)
    current_req = next(req for req in source_a.get_item(rows[0]["item_id"])["requirements"]
                       if req["requirement_id"] == refreshed_req["requirement_id"])
    source_a.record_result_if_current_run(rows[0]["item_id"], refreshed_req["requirement_id"],
        run_id=current_req["current_run_id"], result_id="synthetic-refreshed-a-completed",
        status="completed", summary="synthetic capture complete", verification={"status": "not_verified"})
    source_session = _reader_for(sources["project-a"].store)._session
    fresh = _issue_authorized_project_record_source(
        "fresh-overview-project-a", parent.conversation_id, "project-a", ReadOnlyWorkRecordStore(source_session),
    )
    anchor = next(row for row in rows if row["project_id"] == "project-a")
    args = {"project_id": "project-a", "binding_ids": [anchor["binding_id"]], "source": fresh,
            "refresh_id": str(uuid4()), "refresh_run_id": str(uuid4()), "refresh_turn_epoch": 7}
    preview = bindings.prepare_project_session_refresh(**args)
    return bindings.prepare_project_session_refresh(**args, expected=preview,
        is_current=lambda *_args: True, commit=True)


def _parent_snapshot_groups(parent, store, bindings):
    with _file_lock(store._lock_path, store._lock_timeout):
        binding_data = bindings._read()
        ledger_data = WorkCardLedger(parent, store)._ledger_store._read()
    return _linked_source_snapshot_groups(parent.conversation_id, binding_data, ledger_data)


def test_overview_projects_latest_parent_source_snapshot_bounded_and_pagable(tmp_path, monkeypatch):
    parent, store, bindings, rows, sources, _events, _bridge, _repl = _confirmed_project_copies(tmp_path)
    old_a = next(card for card in WorkCardLedger(parent, store).list_cards()
                 if card["snapshot"].get("kind") == "linked_project_session_copy"
                 and card["snapshot"]["source"]["project_id"] == "project-a")
    refresh = _refresh_a(parent, bindings, rows, sources)
    refresh_events: list[dict] = []
    refresh_bridge = WsBridge(emit=refresh_events.append, paths=parent.paths)
    refresh_repl = SimpleNamespace(session=parent, agent=SimpleNamespace(session=parent))
    assert server._resume_linked_project_session_refreshes(refresh_repl, refresh_bridge, parent, store,
        binding_ids=[refresh["binding_id"]], emit_event=True) == 1
    current_cards = WorkCardLedger(parent, store).list_cards()
    latest_a = next(card for card in current_cards if card["snapshot"].get("kind") == "linked_project_session_copy"
                    and card["snapshot"]["copy_id"] == refresh["copy"]["intent"]["copy_id"])
    assert old_a["card_id"] != latest_a["card_id"]

    source_paths = [_reader_for(sources[pid].store)._path for pid in sources]
    source_bytes = {path: path.read_bytes() for path in source_paths}
    monkeypatch.setattr(ReadOnlyWorkRecordStore, "_read_items_locked",
                        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("source sidecar read")))
    monkeypatch.setattr(ReadOnlyWorkRecordStore, "list_items",
                        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("source list")))
    monkeypatch.setattr(ReadOnlyWorkRecordStore, "get_item",
                        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("source get")))
    original_read = WorkRecordStore._read
    monkeypatch.setattr(WorkRecordStore, "_read", lambda self, *a, **k: (
        (_ for _ in ()).throw(AssertionError("non-parent WorkRecordStore read"))
        if self._conversation_id != parent.conversation_id else original_read(self, *a, **k)))

    groups = _parent_snapshot_groups(parent, store, bindings)
    assert len(groups) == 2, "old revisions coalesce to one bounded attention group per project"
    group_a_raw = next(group for group in groups if group["source_snapshot"]["project_id"] == "project-a")
    group_a = _bounded_group(group_a_raw, "synthetic-parent-scope", "synthetic-snapshot")
    assert group_a["kind"] == "linked_source_snapshot"
    assert group_a["visibility"] == "visible" and group_a["status"] == "unknown"
    assert group_a["attention_status"] == "unknown"
    assert group_a["visibility_timer_status"] == "unknown"
    assert group_a["source_snapshot"] == {
        "scope": "authorized_source_session_only", "mapping_claim": "not_asserted",
        "project_id": "project-a", "source_session_id": "project-a-session",
        "source_revision": latest_a["snapshot"]["source"]["source_revision"],
        "captured_at": latest_a["snapshot"]["captured_at"], "copy_id": latest_a["snapshot"]["copy_id"],
        "intent_state": "reference_queued", "card_id": latest_a["card_id"], "viewed_at": None,
        "superseded_snapshot_count": 1, "superseded_unviewed_count": 1,
    }
    assert group_a["item_count"] == 2 and group_a["requirement_count"] == 9
    assert group_a["items_page"]["total"] == 2
    assert group_a["items"][0]["requirements_page"]["total"] == 8
    assert len(group_a["items"][0]["requirements"]) == 5
    assert all(req["status"] == "unknown" and req["status_at_capture"] in
               {"pending", "in_progress", "unknown", "blocked", "stopped", "completed"}
               for item in group_a["items"] for req in item["requirements"])
    assert all(not item["current_results"] for item in group_a["items"])
    assert all(fact["operation_result"] == "unknown" and fact["verification"] == "not_verified"
               for item in group_a["items"] for req in item["requirements"] for fact in req["worker_facts"])

    detail_cursor = group_a["items"][0]["requirements_page"]["next_cursor"]
    assert detail_cursor
    decoded = _decode_cursor(detail_cursor)
    assert decoded is not None and decoded["group_kind"] == "linked_source_snapshot"
    assert {path: path.read_bytes() for path in source_paths} == source_bytes

    live_route = query_work_records_overview(parent, page_size=10)
    live_groups = [group for group in live_route["groups"] if group["kind"] == "linked_source_snapshot"]
    assert len(live_groups) == 2
    assert all(group["visibility_timer_status"] == "not_viewed" for group in live_groups)


def test_pending_and_corrupt_parent_snapshot_are_not_silently_treated_as_no_records(tmp_path):
    parent, store, bindings, rows, _sources, _events, _bridge, _repl = _confirmed_project_copies(tmp_path)
    anchor = next(row for row in rows if row["project_id"] == "project-a")
    with _file_lock(bindings._dialog_lock_path, bindings._lock_timeout):
        data = bindings._read()
        copy = json.loads(json.dumps(next(r for b in data["batches"] for r in b["bindings"]
                                          if r["binding_id"] == anchor["binding_id"])["project_session_copy"]))
        copy["intent"]["state"] = "pending"
        data.setdefault("project_refreshes", []).append({"refresh_id": str(uuid4()), "run_id": str(uuid4()),
            "turn_epoch": 8, "binding_id": anchor["binding_id"], "authorization_id": "fresh-pending-auth", "copy": copy})
        data["revision"] += 1
        bindings._validate_payload(data)
        bindings._publish(data)
    groups = _parent_snapshot_groups(parent, store, bindings)
    project_a = next(group for group in groups if group["source_snapshot"]["project_id"] == "project-a")
    assert project_a["source_snapshot"]["intent_state"] == "pending"
    assert project_a["attention_status"] == "unknown"

    damaged = bindings._read()
    damaged["batches"][0]["bindings"][0]["project_session_copy"]["snapshot"]["mapping_claim"] = "current"
    with _file_lock(bindings._dialog_lock_path, bindings._lock_timeout):
        bindings._path.write_text(json.dumps(damaged, ensure_ascii=False), encoding="utf-8")
        with pytest.raises(WorkRecordCorrupt):
            bad_binding_data = bindings._read()
            _linked_source_snapshot_groups(parent.conversation_id, bad_binding_data,
                WorkCardLedger(parent, store)._ledger_store._read())


def test_legacy_viewed_terminal_capture_without_elapsed_evidence_stays_visible(tmp_path):
    parent, store, bindings, _rows, _sources, _events, _bridge, _repl = _confirmed_project_copies(
        tmp_path, terminal_source=True,
    )
    ledger = WorkCardLedger(parent, store)
    cards = [row for row in ledger.list_cards()
             if row["snapshot"].get("kind") == "linked_project_session_copy"]
    assert len(cards) == 2
    assert all(all(req["status"] == "completed" for item in row["snapshot"]["source"]["items"]
                   for req in item["requirements"]) for row in cards)
    assert all(ledger.acknowledge_viewed(row["card_id"]).status in {"acknowledged", "already_acknowledged"}
               for row in cards)
    groups = _parent_snapshot_groups(parent, store, bindings)
    assert len(groups) == 2, "viewed_at alone is not elapsed-time evidence or a verified outcome"
    assert all(group["attention_status"] == "unknown" and group["visibility_timer_status"] == "unknown"
               for group in groups)
    assert all(group["source_snapshot"]["viewed_at"] is not None for group in groups)
    assert all(group["attention_reason"] == "view_not_confirmed" for group in groups)
    live = query_work_records_overview(parent, page_size=10)
    live_linked = [group for group in live["groups"] if group["kind"] == "linked_source_snapshot"]
    assert len(live_linked) == 2
    assert all(group["visibility_timer_status"] == "not_viewed" for group in live_linked)


def test_linked_snapshot_view_evidence_collapses_completed_capture_only_after_30_minutes(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from work_card_presentations import WorkCardPresentations
    from linked_snapshot_view_evidence import LinkedSnapshotViewEvidence
    import work_record_visibility_clock as clock
    from work_record_visibility_clock import ElapsedClockSample

    parent, store, _bindings, _rows, _sources, _events, _bridge, _repl = _confirmed_project_copies(
        tmp_path, terminal_source=True,
    )
    ledger = WorkCardLedger(parent, store)
    cards = [row for row in ledger.list_cards() if row["snapshot"].get("kind") == "linked_project_session_copy"]
    presentations = WorkCardPresentations(parent, store, ledger)
    evidence = LinkedSnapshotViewEvidence(parent, store, ledger)
    now = {"seconds": 100}
    def sample():
        wall = datetime(2026, 10, 31, tzinfo=timezone.utc) + timedelta(seconds=now["seconds"])
        return ElapsedClockSample("synthetic-boot", now["seconds"] * 1_000_000_000,
                                  wall.isoformat().replace("+00:00", "Z"))
    monkeypatch.setattr(clock, "sample_elapsed_clock", sample)
    for card in cards:
        ref = next(message for message in parent.messages if message.get("role") == "work_card_presentation"
                   and message.get("card_id") == card["card_id"])
        assert presentations.acknowledge(ref["presentation_id"], card["card_id"], explicit_user_action=True).status == "acknowledged"
        assert evidence.record_view(ref["presentation_id"], card["card_id"], explicit_user_action=True).status == "acknowledged"
    now["seconds"] += 29 * 60
    before = query_work_records_overview(parent, page_size=10)
    linked_before = [group for group in before["groups"] if group["kind"] == "linked_source_snapshot"]
    assert len(linked_before) == 2
    assert all(group["visibility_timer_status"] == "waiting" and group["status"] == "unknown"
               for group in linked_before)
    assert before["visibility_recheck_after_ms"] == 60 * 1000
    now["seconds"] += 60
    after = query_work_records_overview(parent, page_size=10)
    assert after["state"] == "collapsed"
    assert after["groups"] == []
    assert after["visibility_counts"]["collapsed_groups"] == 2


def test_overview_cursor_survives_monotonic_checkpoint_but_expires_on_maturity(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from work_card_presentations import WorkCardPresentations
    from linked_snapshot_view_evidence import LinkedSnapshotViewEvidence
    import work_record_visibility_clock as clock
    from work_record_visibility_clock import ElapsedClockSample

    parent, store, _bindings, _rows, _sources, _events, _bridge, _repl = _confirmed_project_copies(
        tmp_path, terminal_source=True,
    )
    ledger = WorkCardLedger(parent, store)
    cards = [row for row in ledger.list_cards() if row["snapshot"].get("kind") == "linked_project_session_copy"]
    presentations = WorkCardPresentations(parent, store, ledger)
    evidence = LinkedSnapshotViewEvidence(parent, store, ledger)
    now = {"seconds": 100}
    def sample():
        wall = datetime(2026, 10, 31, tzinfo=timezone.utc) + timedelta(seconds=now["seconds"])
        return ElapsedClockSample("synthetic-boot", now["seconds"] * 1_000_000_000,
                                  wall.isoformat().replace("+00:00", "Z"))
    monkeypatch.setattr(clock, "sample_elapsed_clock", sample)
    card_a = next(card for card in cards if card["snapshot"]["source"]["project_id"] == "project-a")
    ref_a = next(message for message in parent.messages if message.get("role") == "work_card_presentation"
                 and message.get("card_id") == card_a["card_id"])
    presentations.acknowledge(ref_a["presentation_id"], card_a["card_id"], explicit_user_action=True)
    assert evidence.record_view(ref_a["presentation_id"], card_a["card_id"], explicit_user_action=True).status == "acknowledged"

    first = query_work_records_overview(parent, page_size=1)
    assert first["state"] == "visible" and first["page"]["has_more"]
    cursor = first["page"]["next_cursor"]
    first_snapshot = first["snapshot_id"]

    # Crossing the 60s evidence checkpoint persists monotonic accrual only;
    # visible group semantics are unchanged, so this cursor must remain usable.
    now["seconds"] += 61
    next_page = query_work_records_overview(parent, cursor=cursor, page_size=1)
    assert next_page["state"] == "visible", next_page
    assert next_page["snapshot_id"] == first_snapshot
    assert next_page["page"]["offset"] == 1
    assert next_page["groups"][0]["source_snapshot"]["project_id"] == "project-b"

    # Once the exact view evidence matures, the visible projection changes and
    # the old cursor must expire instead of mixing snapshots.
    now["seconds"] = 100 + 31 * 60
    matured = query_work_records_overview(parent, cursor=cursor, page_size=1)
    assert matured["state"] == "expired"
    assert matured["groups"] == []


def test_explicit_view_action_changes_overview_fingerprint_and_expires_old_cursor(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    from work_card_presentations import WorkCardPresentations
    from linked_snapshot_view_evidence import LinkedSnapshotViewEvidence
    import work_record_visibility_clock as clock
    from work_record_visibility_clock import ElapsedClockSample

    parent, store, _bindings, _rows, _sources, _events, _bridge, _repl = _confirmed_project_copies(
        tmp_path, terminal_source=True,
    )
    ledger = WorkCardLedger(parent, store)
    card = next(row for row in ledger.list_cards()
                if row["snapshot"].get("kind") == "linked_project_session_copy"
                and row["snapshot"]["source"]["project_id"] == "project-a")
    ref = next(message for message in parent.messages if message.get("role") == "work_card_presentation"
               and message.get("card_id") == card["card_id"])
    now = {"seconds": 100}
    def sample():
        wall = datetime(2026, 10, 31, tzinfo=timezone.utc)
        return ElapsedClockSample("synthetic-boot", now["seconds"] * 1_000_000_000,
                                  wall.isoformat().replace("+00:00", "Z"))
    monkeypatch.setattr(clock, "sample_elapsed_clock", sample)
    first = query_work_records_overview(parent, page_size=1)
    cursor = first["page"]["next_cursor"]
    assert cursor
    presentations = WorkCardPresentations(parent, store, ledger)
    evidence = LinkedSnapshotViewEvidence(parent, store, ledger)
    assert presentations.acknowledge(ref["presentation_id"], card["card_id"], explicit_user_action=True).status == "acknowledged"
    assert evidence.record_view(ref["presentation_id"], card["card_id"], explicit_user_action=True).status == "acknowledged"
    changed = query_work_records_overview(parent, cursor=cursor, page_size=1)
    assert changed["state"] == "expired"
    assert changed["groups"] == []


def test_linked_snapshot_captured_active_or_unviewed_never_collapses_after_30_minutes(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from work_card_presentations import WorkCardPresentations
    from linked_snapshot_view_evidence import LinkedSnapshotViewEvidence
    import work_record_visibility_clock as clock
    from work_record_visibility_clock import ElapsedClockSample

    parent, store, _bindings, _rows, _sources, _events, _bridge, _repl = _confirmed_project_copies(tmp_path)
    ledger = WorkCardLedger(parent, store)
    cards = [row for row in ledger.list_cards() if row["snapshot"].get("kind") == "linked_project_session_copy"]
    presentations = WorkCardPresentations(parent, store, ledger)
    evidence = LinkedSnapshotViewEvidence(parent, store, ledger)
    now = {"seconds": 100}
    def sample():
        wall = datetime(2026, 10, 31, tzinfo=timezone.utc) + timedelta(seconds=now["seconds"])
        return ElapsedClockSample("synthetic-boot", now["seconds"] * 1_000_000_000,
                                  wall.isoformat().replace("+00:00", "Z"))
    monkeypatch.setattr(clock, "sample_elapsed_clock", sample)
    # Explicitly view only the first source card. Leave the second without evidence.
    ref = next(message for message in parent.messages if message.get("role") == "work_card_presentation"
               and message.get("card_id") == cards[0]["card_id"])
    presentations.acknowledge(ref["presentation_id"], cards[0]["card_id"], explicit_user_action=True)
    assert evidence.record_view(ref["presentation_id"], cards[0]["card_id"], explicit_user_action=True).status == "acknowledged"
    now["seconds"] += 31 * 60
    result = query_work_records_overview(parent, page_size=10)
    linked = [group for group in result["groups"] if group["kind"] == "linked_source_snapshot"]
    assert len(linked) == 2
    assert all(group["visibility"] == "visible" for group in linked)
    assert any(group["attention_reason"] == "captured_requirement_unresolved" for group in linked)
    assert any(group["visibility_timer_status"] == "not_viewed" for group in linked)


def test_latest_completed_viewed_snapshot_is_not_pinned_by_superseded_nonterminal_capture(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from linked_snapshot_view_evidence import LinkedSnapshotViewEvidence
    from work_card_presentations import WorkCardPresentations
    import work_record_visibility_clock as clock
    from work_record_visibility_clock import ElapsedClockSample

    parent, store, bindings, rows, sources, _events, _bridge, _repl = _confirmed_project_copies(tmp_path)
    source_a = _reader_for(sources["project-a"].store)
    # Make all A requirements complete in the refreshed capture, while its old
    # snapshot below intentionally retains nonterminal capture-time status.
    for item in source_a.list_items():
        if item.get("scope") != {"kind": "project", "project_id": "project-a"}:
            continue
        for requirement in item["requirements"]:
            run_id = requirement.get("current_run_id")
            if not run_id:
                run_id = str(uuid4())
                source_a.begin_requirement_run(item["item_id"], requirement["requirement_id"],
                                               run_id=run_id, expected_current_run_id=None)
            source_a.record_result_if_current_run(item["item_id"], requirement["requirement_id"],
                run_id=run_id, result_id=f"synthetic-complete-{requirement['requirement_id']}",
                status="completed", summary="synthetic completed capture", verification={"status": "not_verified"})

    refreshed = _refresh_a(parent, bindings, rows, sources)
    refresh_bridge = WsBridge(emit=lambda _event: None, paths=parent.paths)
    refresh_repl = SimpleNamespace(session=parent, agent=SimpleNamespace(session=parent))
    assert server._resume_linked_project_session_refreshes(refresh_repl, refresh_bridge, parent, store,
        binding_ids=[refreshed["binding_id"]], emit_event=True) == 1
    ledger = WorkCardLedger(parent, store)
    a_cards = [row for row in ledger.list_cards()
               if row["snapshot"].get("kind") == "linked_project_session_copy"
               and row["snapshot"]["source"]["project_id"] == "project-a"]
    assert len(a_cards) == 2
    assert not all(req["status"] == "completed" for item in a_cards[0]["snapshot"]["source"]["items"]
                   for req in item["requirements"])
    assert all(req["status"] == "completed" for item in a_cards[1]["snapshot"]["source"]["items"]
               for req in item["requirements"])

    now = {"seconds": 100}
    def sample():
        wall = datetime(2026, 10, 31, tzinfo=timezone.utc) + timedelta(seconds=now["seconds"])
        return ElapsedClockSample("synthetic-boot", now["seconds"] * 1_000_000_000,
                                  wall.isoformat().replace("+00:00", "Z"))
    monkeypatch.setattr(clock, "sample_elapsed_clock", sample)
    presentations = WorkCardPresentations(parent, store, ledger)
    evidence = LinkedSnapshotViewEvidence(parent, store, ledger)
    for card in a_cards:
        ref = next(message for message in parent.messages if message.get("role") == "work_card_presentation"
                   and message.get("card_id") == card["card_id"])
        assert presentations.acknowledge(ref["presentation_id"], card["card_id"],
                                         explicit_user_action=True).status == "acknowledged"
        assert evidence.record_view(ref["presentation_id"], card["card_id"],
                                    explicit_user_action=True).status == "acknowledged"
    now["seconds"] += 31 * 60

    overview = query_work_records_overview(parent, page_size=20)
    linked = [group for group in overview["groups"] if group["kind"] == "linked_source_snapshot"]
    assert len(linked) == 1
    assert linked[0]["source_snapshot"]["project_id"] == "project-b"
    assert overview["visibility_counts"]["collapsed_groups"] == 1
    assert overview["visibility_counts"]["visible_groups"] == 1


def test_linked_snapshot_clock_anomaly_and_legacy_ack_keep_attention_unknown(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from work_card_presentations import WorkCardPresentations
    from linked_snapshot_view_evidence import LinkedSnapshotViewEvidence
    import work_record_visibility_clock as clock
    from work_record_visibility_clock import ElapsedClockSample

    parent, store, _bindings, _rows, _sources, _events, _bridge, _repl = _confirmed_project_copies(
        tmp_path, terminal_source=True,
    )
    ledger = WorkCardLedger(parent, store)
    cards = [row for row in ledger.list_cards() if row["snapshot"].get("kind") == "linked_project_session_copy"]
    presentations = WorkCardPresentations(parent, store, ledger)
    evidence = LinkedSnapshotViewEvidence(parent, store, ledger)
    now = {"seconds": 100, "wall": 100}
    def sample():
        wall = datetime(2026, 10, 31, tzinfo=timezone.utc) + timedelta(seconds=now["wall"])
        return ElapsedClockSample("synthetic-boot", now["seconds"] * 1_000_000_000,
                                  wall.isoformat().replace("+00:00", "Z"))
    monkeypatch.setattr(clock, "sample_elapsed_clock", sample)
    ref = next(message for message in parent.messages if message.get("role") == "work_card_presentation"
               and message.get("card_id") == cards[0]["card_id"])
    presentations.acknowledge(ref["presentation_id"], cards[0]["card_id"], explicit_user_action=True)
    assert evidence.record_view(ref["presentation_id"], cards[0]["card_id"], explicit_user_action=True).status == "acknowledged"
    now["seconds"] += 31 * 60
    now["wall"] += 10
    result = query_work_records_overview(parent, page_size=10)
    linked = [group for group in result["groups"] if group["kind"] == "linked_source_snapshot"]
    assert len(linked) == 2 and all(group["visibility"] == "visible" for group in linked)
    assert all(group["attention_status"] == "unknown" for group in linked)
    assert all(group["visibility_timer_status"] in {"waiting", "not_viewed", "unavailable"} for group in linked)
