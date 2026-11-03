from __future__ import annotations

import hashlib
import json
import os
import sys
from dataclasses import replace
from pathlib import Path

import pytest

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

from paths import AgentPaths
from session import Session, SessionMeta
from work_record_cards import SourceReference, build_project_work_record_cards, build_independent_work_record_card
from work_card_ledger import (
    LEDGER_FILENAME,
    WorkCardLedger,
    WorkCardLedgerCorrupt,
    WorkCardLedgerError,
)
from work_records import WorkRecordStore


REQUEST_ID = "b10e50a4-f902-4b10-a404-f430821f1d88"
OTHER_REQUEST_ID = "c7efbe2a-f689-4c7a-98df-a5a2a6858d00"


def make_session(tmp_path: Path, conversation_id="20261101-aaaaaaaa"):
    root = tmp_path / "agent-root"
    root.mkdir(parents=True, exist_ok=True)
    paths = AgentPaths(
        agent_root=root,
        workspace=root / "workspace",
        data=root / "data",
        evolve=root / "evolve",
    )
    session_dir = paths.data / "sessions" / conversation_id
    session_dir.mkdir(parents=True)
    session = Session(
        conversation_id=conversation_id,
        session_dir=session_dir,
        goal="synthetic card ledger fixture",
        meta=SessionMeta(project_id="synthetic-project"),
        messages=[],
        paths=paths,
    )
    session.save()
    store = WorkRecordStore(session)
    item = store.create_item(
        title="Synthetic task",
        scope={"kind": "project", "project_id": "synthetic-project"},
        source_refs=[{"kind": "project", "id": "project-source"}],
    )
    requirement = store.add_requirement(
        item["item_id"],
        "Synthetic requirement",
        event_id="ledger-requirement-event",
        source_ref={"kind": "requirement", "id": "requirement-source"},
    )
    for suffix, summary in (("old-1", "older stored summary"), ("old-2", "latest stored summary")):
        store.record_result(
            item["item_id"], requirement["requirement_id"],
            run_id=f"run-{suffix}", result_id=f"result-{suffix}", status="unknown",
            summary=summary,
            verification={"status": "not_verified", "tool_output": "RAW_TOOL_BODY_DO_NOT_COPY"},
            sources=[{"kind": "result", "id": f"{suffix}-source"}],
        )
    store.begin_requirement_run(
        item["item_id"], requirement["requirement_id"],
        run_id="run-current", expected_current_run_id=None,
    )
    snapshot = build_project_work_record_cards(store, "synthetic-project")
    assert snapshot.completeness == "complete"
    assert snapshot.history_complete is False
    assert snapshot.omitted_result_count == 1
    current = snapshot.cards[0].requirements[0]
    assert current.current_run_id == "run-current"
    assert current.latest_result_is_current is False
    return session, store, snapshot


def _reopen(session):
    reopened = Session(
        conversation_id=session.conversation_id,
        session_dir=session.session_dir,
        goal=session.goal,
        meta=SessionMeta(project_id=session.meta.project_id),
        messages=[],
        paths=session.paths,
    )
    return reopened, WorkRecordStore(reopened)


def test_persist_reopen_idempotency_and_preserve_current_run_history_fields(tmp_path):
    session, store, snapshot = make_session(tmp_path)
    sidecar_before = store._path.read_bytes()
    messages_before = list(session.messages)
    ledger = WorkCardLedger(session, store)

    saved = ledger.save_snapshot(REQUEST_ID, snapshot)
    assert saved.status == "saved"
    assert saved.card_id
    record = ledger.get_card(saved.card_id)
    assert record is not None
    stored_snapshot = record["snapshot"]
    requirement = stored_snapshot["cards"][0]["requirements"][0]
    assert stored_snapshot["completeness"] == "complete"
    assert stored_snapshot["history_complete"] is False
    assert stored_snapshot["omitted_result_count"] == 1
    assert requirement["current_run_id"] == "run-current"
    assert requirement["latest_result_is_current"] is False
    assert requirement["latest_result"]["run_id"] == "run-old-2"
    assert "RAW_TOOL_BODY_DO_NOT_COPY" not in json.dumps(record, ensure_ascii=False)
    assert store._path.read_bytes() == sidecar_before
    assert session.messages == messages_before  # source messages and ledger are separate writes

    reopened, reopened_store = _reopen(session)
    reopened_ledger = WorkCardLedger(reopened, reopened_store)
    reopened_record = reopened_ledger.get_card(saved.card_id)
    assert reopened_record == record
    assert reopened_ledger.get_by_request_id(REQUEST_ID) == record

    ledger_bytes = (session.session_dir / LEDGER_FILENAME).read_bytes()
    replay = reopened_ledger.save_snapshot(REQUEST_ID, snapshot)
    assert replay.status == "already_saved"
    assert replay.card_id == saved.card_id
    assert replay.record == record
    assert (session.session_dir / LEDGER_FILENAME).read_bytes() == ledger_bytes

    second = reopened_ledger.save_snapshot(OTHER_REQUEST_ID, snapshot)
    assert second.status == "saved"
    assert second.card_id != saved.card_id
    assert reopened_ledger.get_card(saved.card_id) == record
    assert len(reopened_ledger.list_cards()) == 2


def test_same_request_id_with_different_snapshot_facts_conflicts_without_overwrite(tmp_path):
    session, store, snapshot = make_session(tmp_path)
    ledger = WorkCardLedger(session, store)
    saved = ledger.save_snapshot(REQUEST_ID, snapshot)
    before = (session.session_dir / LEDGER_FILENAME).read_bytes()
    changed = replace(snapshot, captured_at="2099-01-01T00:00:00Z")

    conflict = ledger.save_snapshot(REQUEST_ID, changed)

    assert conflict.status == "conflict"
    assert conflict.card_id == saved.card_id
    assert ledger.get_card(saved.card_id) == saved.record
    assert (session.session_dir / LEDGER_FILENAME).read_bytes() == before



def test_project_snapshot_facts_must_match_locked_persisted_source(tmp_path):
    session, store, snapshot = make_session(tmp_path)
    ledger = WorkCardLedger(session, store)
    card = snapshot.cards[0]
    requirement = card.requirements[0]
    forged_result = replace(requirement.latest_result, summary="FORGED_RESULT_NOT_IN_SOURCE")
    forged_requirement = replace(requirement, latest_result=forged_result)
    forged_card = replace(card, requirements=(forged_requirement,))
    forged_snapshot = replace(snapshot, cards=(forged_card,))

    result = ledger.save_snapshot(REQUEST_ID, forged_snapshot)

    assert result.status == "rejected"
    assert not (session.session_dir / LEDGER_FILENAME).exists()


def test_project_snapshot_is_rejected_when_persisted_requirement_history_changes(tmp_path):
    session, store, snapshot = make_session(tmp_path)
    ledger = WorkCardLedger(session, store)
    item = store.list_items()[0]
    store.add_requirement(item["item_id"], "A later persisted requirement", event_id="later-project-req")

    result = ledger.save_snapshot(REQUEST_ID, snapshot)

    assert result.status == "rejected"
    assert not (session.session_dir / LEDGER_FILENAME).exists()

def test_invalid_incomplete_wrong_session_and_link_snapshot_facts_are_rejected(tmp_path):
    session, store, snapshot = make_session(tmp_path)
    ledger = WorkCardLedger(session, store)
    ledger_path = session.session_dir / LEDGER_FILENAME

    incomplete = replace(snapshot, completeness="incomplete", incomplete_reason="test_incomplete")
    wrong_session = replace(snapshot, session_id="other-session")
    bad_ref = replace(
        snapshot.cards[0].source_refs[0],
        id="../outside",
    )
    bad_link = replace(snapshot, cards=(replace(snapshot.cards[0], source_refs=(bad_ref,)),))
    for index, candidate in enumerate((incomplete, wrong_session, bad_link), start=1):
        result = ledger.save_snapshot(f"d0e50a4-f902-4b10-a404-f430821f1d8{index}", candidate)
        assert result.status == "rejected"
    assert not ledger_path.exists()
    with pytest.raises(WorkCardLedgerError):
        WorkCardLedger(session, WorkRecordStore(_reopen(session)[0]))


def test_unknown_and_foreign_session_ack_reject_and_repeated_ack_keeps_first_time(tmp_path):
    session, store, snapshot = make_session(tmp_path)
    ledger = WorkCardLedger(session, store)
    saved = ledger.save_snapshot(REQUEST_ID, snapshot)
    assert ledger.get_card(saved.card_id)["viewed_at"] is None
    assert ledger.acknowledge_viewed("work-card-" + "0" * 64).status == "rejected"

    first_ack = ledger.acknowledge_viewed(saved.card_id)
    assert first_ack.status == "acknowledged"
    assert first_ack.viewed_at and first_ack.viewed_at.endswith("Z")
    repeated = ledger.acknowledge_viewed(saved.card_id)
    assert repeated.status == "already_acknowledged"
    assert repeated.viewed_at == first_ack.viewed_at
    assert ledger.get_card(saved.card_id)["viewed_at"] == first_ack.viewed_at

    foreign_session, foreign_store, _ = make_session(tmp_path, "20261101-bbbbbbbb")
    foreign_ledger = WorkCardLedger(foreign_session, foreign_store)
    assert foreign_ledger.get_card(saved.card_id) is None
    assert foreign_ledger.acknowledge_viewed(saved.card_id).status == "rejected"
    with pytest.raises(WorkCardLedgerError):
        WorkCardLedger(session, foreign_store)


def test_write_failure_is_reported_once_and_corrupt_ledger_is_not_overwritten(tmp_path, monkeypatch):
    session, store, snapshot = make_session(tmp_path)
    ledger = WorkCardLedger(session, store)
    replace_calls = []
    real_replace = os.replace

    def fail_replace(*args, **kwargs):
        replace_calls.append((args, kwargs))
        raise OSError("synthetic atomic replace failure")

    monkeypatch.setattr("work_card_ledger.os.replace", fail_replace)
    failed = ledger.save_snapshot(REQUEST_ID, snapshot)
    assert failed.status in {"failed", "uncertain"}
    assert len(replace_calls) == 1
    assert not (session.session_dir / LEDGER_FILENAME).exists()

    monkeypatch.setattr("work_card_ledger.os.replace", real_replace)
    saved = ledger.save_snapshot(REQUEST_ID, snapshot)
    assert saved.status == "saved"
    ledger_path = session.session_dir / LEDGER_FILENAME
    ledger_path.write_text("{corrupt", encoding="utf-8")
    corrupt_bytes = ledger_path.read_bytes()
    with pytest.raises(WorkCardLedgerCorrupt):
        ledger.get_card(saved.card_id)
    rejected = ledger.save_snapshot(OTHER_REQUEST_ID, snapshot)
    assert rejected.status == "corrupt"
    assert ledger_path.read_bytes() == corrupt_bytes


def test_ledger_symlink_target_and_arbitrary_path_override_are_rejected(tmp_path):
    session, store, snapshot = make_session(tmp_path)
    outside = tmp_path / "outside-ledger.json"
    outside.write_text("outside sentinel", encoding="utf-8")
    ledger_path = session.session_dir / LEDGER_FILENAME
    try:
        ledger_path.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")

    ledger = WorkCardLedger(session, store)
    with pytest.raises(WorkCardLedgerCorrupt):
        ledger.get_card("work-card-" + "0" * 64)
    rejected = ledger.save_snapshot(REQUEST_ID, snapshot)
    assert rejected.status == "corrupt"
    assert outside.read_text(encoding="utf-8") == "outside sentinel"
    with pytest.raises(TypeError):
        WorkCardLedger(session, store, outside)
def _create_ledger_test_junction(link: Path, target: Path, tmp_path: Path) -> None:
    import os
    import subprocess

    if os.name != "nt":
        pytest.skip("native Windows junction validation requires Windows")
    root = tmp_path.resolve(strict=True)
    target.mkdir(parents=True, exist_ok=True)
    link.parent.mkdir(parents=True, exist_ok=True)
    assert target.resolve(strict=True).is_relative_to(root)
    assert link.parent.resolve(strict=True).is_relative_to(root)
    result = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        pytest.skip("native Windows junction creation unavailable")
    if not (callable(getattr(link, "is_junction", None)) and link.is_junction()):
        os.rmdir(link)
        pytest.skip("pathlib does not recognize created junction")


def test_session_directory_junction_escape_is_rejected_by_bound_ledger(tmp_path):
    import os
    from work_records import WorkRecordScopeError

    session, store, snapshot = make_session(tmp_path)
    ledger = WorkCardLedger(session, store)
    saved = ledger.save_snapshot(REQUEST_ID, snapshot)
    assert saved.status == "saved"

    link = session.session_dir
    target = link.with_name(link.name + "-junction-target")
    root = tmp_path.resolve(strict=True)
    assert target.resolve(strict=False).is_relative_to(root)
    link.rename(target)
    _create_ledger_test_junction(link, target, tmp_path)
    try:
        with pytest.raises(WorkRecordScopeError):
            ledger.get_card(saved.card_id)
    finally:
        if callable(getattr(link, "is_junction", None)) and link.is_junction():
            os.rmdir(link)

def make_independent_session(tmp_path, bound, conversation_id="20261101-cccccccc"):
    root = tmp_path / "independent-root"
    paths = AgentPaths(agent_root=root, workspace=root / "workspace", data=root / "data", evolve=root / "evolve")
    session_dir = paths.data / "sessions" / conversation_id
    session_dir.mkdir(parents=True)
    session = Session(conversation_id, session_dir, "synthetic", SessionMeta(project_id="synthetic-project" if bound else ""), [], paths)
    session.save()
    store = WorkRecordStore(session)
    item = store.create_item(title="Synthetic résumé", scope={"kind": "independent"})
    req = store.add_requirement(item["item_id"], "Synthetic résumé requirement", event_id="independent-req")
    for n in range(2):
        store.record_result(item["item_id"], req["requirement_id"], run_id=f"old-run-{n}", result_id=f"old-result-{n}",
                            status="unknown", summary=f"synthetic old {n}", verification={"status": "not_verified", "body": "PRIVATE"})
    store.begin_requirement_run(item["item_id"], req["requirement_id"], run_id="current-run", expected_current_run_id=None)
    return session, store, build_independent_work_record_card(store, item["item_id"]), item, req


@pytest.mark.parametrize("bound", [False, True])
def test_independent_saved_restarted_ack_and_old_result_distinction(tmp_path, bound):
    session, store, snap, item, req = make_independent_session(tmp_path, bound)
    ledger = WorkCardLedger(session, store)
    before = store._path.read_bytes()
    result = ledger.save_snapshot(REQUEST_ID, snap)
    assert result.status == "saved"
    saved = result.record["snapshot"]
    assert saved["kind"] == "independent" and saved["project_id"] is None and saved["item_id"] == item["item_id"]
    r = saved["card"]["requirements"][0]
    assert r["current_run_id"] == "current-run" and r["latest_result_is_current"] is False
    assert r["latest_result"]["verification_status"] == "not_verified" and "PRIVATE" not in json.dumps(saved)
    assert saved["history_complete"] is False and saved["omitted_result_count"] == 1
    assert store._path.read_bytes() == before
    ack = ledger.acknowledge_viewed(result.card_id)
    assert ack.status == "acknowledged"
    reopened, reopened_store = _reopen(session)
    new_ledger = WorkCardLedger(reopened, reopened_store)
    assert new_ledger.acknowledge_viewed(result.card_id).status == "already_acknowledged"
    assert new_ledger.get_card(result.card_id)["viewed_at"] == ack.viewed_at
    assert new_ledger.save_snapshot(REQUEST_ID, snap).status == "already_saved"


def test_independent_replay_conflicts_and_scope_spoof_rejected(tmp_path):
    session, store, snap, item, req = make_independent_session(tmp_path, True)
    ledger = WorkCardLedger(session, store)
    project = store.create_item(title="Synthetic project", scope={"kind": "project", "project_id": "synthetic-project"})
    saved = ledger.save_snapshot(REQUEST_ID, snap)
    assert saved.status == "saved"
    assert ledger.save_snapshot(REQUEST_ID, replace(snap, captured_at="2099-01-01T00:00:00Z")).status == "conflict"
    forged = replace(snap, item_id=project["item_id"], card=replace(snap.card, item_id=project["item_id"]))
    assert ledger.save_snapshot(OTHER_REQUEST_ID, forged).status == "rejected"
    assert ledger.save_snapshot(OTHER_REQUEST_ID, replace(snap, session_id="20261101-dddddddd")).status == "rejected"
    assert ledger.save_snapshot(OTHER_REQUEST_ID, replace(snap, card=replace(snap.card, title="FORGED_CLIENT_BODY"))).status == "rejected"
    assert len(ledger.list_cards()) == 1


def test_independent_stale_or_corrupt_result_facts_rejected(tmp_path):
    session, store, snap, item, req = make_independent_session(tmp_path, False)
    ledger = WorkCardLedger(session, store)
    wrong = replace(snap.card.requirements[0], latest_result_is_current=True)
    assert ledger.save_snapshot(REQUEST_ID, replace(snap, card=replace(snap.card, requirements=(wrong,)))).status == "rejected"
    bogus = replace(snap.card.requirements[0].latest_result, verification_status="passed")
    altered = replace(snap.card.requirements[0], latest_result=bogus)
    assert ledger.save_snapshot(REQUEST_ID, replace(snap, card=replace(snap.card, requirements=(altered,)))).status == "rejected"
    store.add_requirement(item["item_id"], "new requirement", event_id="independent-new-req")
    assert ledger.save_snapshot(REQUEST_ID, snap).status == "rejected"
    assert not (session.session_dir / LEDGER_FILENAME).exists()


@pytest.mark.parametrize("bound", [False, True])
def test_independent_binding_change_and_foreign_ack_rejected(tmp_path, bound):
    session, store, snap, item, req = make_independent_session(tmp_path, bound)
    ledger = WorkCardLedger(session, store)
    saved = ledger.save_snapshot(REQUEST_ID, snap)
    assert saved.status == "saved"
    session.meta.project_id = "" if bound else "synthetic-project"
    assert ledger.acknowledge_viewed(saved.card_id).status == "rejected"
    assert ledger.save_snapshot(OTHER_REQUEST_ID, snap).status == "rejected"


def test_project_and_independent_share_ledger_with_scope_and_request_id_conflict(tmp_path):
    session, store, project_snapshot = make_session(tmp_path)
    independent = store.create_item(title="Separate", scope={"kind": "independent"})
    store.add_requirement(independent["item_id"], "Separate requirement", event_id="separate-req")
    snapshot = build_independent_work_record_card(store, independent["item_id"])
    ledger = WorkCardLedger(session, store)
    project = ledger.save_snapshot(REQUEST_ID, project_snapshot)
    assert project.status == "saved"
    assert ledger.save_snapshot(REQUEST_ID, snapshot).status == "conflict"
    single = ledger.save_snapshot(OTHER_REQUEST_ID, snapshot)
    assert single.status == "saved" and single.card_id != project.card_id
    reopened, reopened_store = _reopen(session)
    cards = WorkCardLedger(reopened, reopened_store).list_cards()
    assert len(cards) == 2 and cards[0]["snapshot"]["project_id"] == "synthetic-project"
    assert cards[1]["snapshot"]["project_id"] is None


def test_foreign_session_independent_snapshot_and_ack_cannot_cross(tmp_path):
    first, first_store, first_snapshot, _, _ = make_independent_session(tmp_path / "first", False)
    second, second_store, second_snapshot, _, _ = make_independent_session(tmp_path / "second", False, "20261101-dddddddd")
    # Distinct Session IDs and roots prevent reuse of another Session's snapshot or ACK.
    first_ledger = WorkCardLedger(first, first_store)
    second_ledger = WorkCardLedger(second, second_store)
    saved = first_ledger.save_snapshot(REQUEST_ID, first_snapshot)
    assert saved.status == "saved"
    assert second_ledger.save_snapshot(OTHER_REQUEST_ID, first_snapshot).status == "rejected"
    assert second_ledger.acknowledge_viewed(saved.card_id).status == "rejected"
    assert second_ledger.list_cards() == []


def test_independent_incomplete_or_invalid_counts_never_saved(tmp_path):
    session, store, snap, _, _ = make_independent_session(tmp_path, False)
    ledger = WorkCardLedger(session, store)
    cases = [replace(snap, completeness="incomplete", incomplete_reason="current_result_missing"),
             replace(snap, history_complete=True), replace(snap, omitted_result_count=0),
             replace(snap, item_count=2), replace(snap, project_id="synthetic-project"),
             replace(snap, card=replace(snap.card, project_id="synthetic-project"))]
    for candidate in cases:
        assert ledger.save_snapshot(REQUEST_ID, candidate).status == "rejected"
    assert not (session.session_dir / LEDGER_FILENAME).exists()


def test_unbound_rejects_project_snapshot_even_if_project_id_forged_empty(tmp_path):
    unbound, store, _, _, _ = make_independent_session(tmp_path / "u", False)
    _, _, project = make_session(tmp_path / "p")
    ledger = WorkCardLedger(unbound, store)
    assert ledger.save_snapshot(REQUEST_ID, project).status == "rejected"
    assert ledger.save_snapshot(REQUEST_ID, replace(project, session_id=unbound.conversation_id, project_id="")).status == "rejected"


def test_independent_serialized_client_body_is_not_a_trusted_snapshot(tmp_path):
    from dataclasses import asdict
    session, store, snap, _, _ = make_independent_session(tmp_path, False)
    ledger = WorkCardLedger(session, store)
    assert ledger.save_snapshot(REQUEST_ID, asdict(snap)).status == "rejected"
    assert not (session.session_dir / LEDGER_FILENAME).exists()
