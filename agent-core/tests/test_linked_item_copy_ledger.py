from dataclasses import replace
from pathlib import Path
import sys
import uuid
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_multi_project_work_record_bindings import _fixture
from multi_project_work_record_bindings import ConversationWorkRecordBindings
from work_records import WorkRecordStore, _digest, _file_lock
from work_card_ledger import WorkCardLedger, build_linked_item_copy_snapshot, linked_item_copy_request_id


def _confirmed(tmp_path):
    parent, sources, tasks = _fixture(tmp_path)
    tasks = [replace(t, source_item_digest=_digest(sources[t.project_id].store.get_item(t.item_id))) for t in tasks]
    bindings = ConversationWorkRecordBindings(parent)
    rows = bindings.bind_existing_tasks(batch_id="batch", tasks=tasks, authorized_sources=sources)
    r = rows[0]
    bindings.record_worker_start(binding_id=r["binding_id"], worker_id=r["worker_id"], run_id=r["run_id"],
        reservation_id="reservation-a", parent_run_id="parent-run", turn_epoch=3, project_id=r["project_id"], task_id=r["task_id"])
    bindings.record_worker_lifecycle(binding_id=r["binding_id"], worker_id=r["worker_id"], run_id=r["run_id"],
        sequence=3, status="model_turn_finished", finish_reason="completed")
    proposal = bindings.prepare_item_copies(binding_ids=[r["binding_id"]], authorized_sources={"project-a": sources["project-a"]})
    saved = bindings.commit_confirmed_item_copies(proposed=proposal, authorized_sources={"project-a": sources["project-a"]}, is_current=lambda: True)[0]
    return parent, bindings, r, saved


def test_confirmed_time_single_item_schema_persisted_and_strict_source(tmp_path):
    parent, bindings, row, copy = _confirmed(tmp_path)
    assert copy["confirmed_at"].endswith("Z")
    ledger = WorkCardLedger(parent, WorkRecordStore(parent))
    snap = build_linked_item_copy_snapshot(parent.conversation_id, copy)
    assert snap.kind == "linked_item_copy" and snap.completeness_scope == "item_requirement"
    assert snap.operation_result == "unknown" and snap.verification == "not_verified"
    assert snap.requirement_status_at_capture == copy["snapshot"]["requirement_status"]
    assert snap.item_status_at_capture == copy["snapshot"]["item_status"]
    assert snap.captured_at == copy["confirmed_at"]
    request = linked_item_copy_request_id(parent.conversation_id, copy["intent"]["copy_id"])
    saved = ledger.save_linked_item_copy_snapshot(request, snap)
    assert saved.status == "saved" and saved.record["snapshot"]["kind"] == "linked_item_copy"
    assert ledger.save_linked_item_copy_snapshot(request, snap).status == "already_saved"
    assert WorkCardLedger(parent, WorkRecordStore(parent)).get_card(saved.card_id) == saved.record
    assert bindings.get_binding(row["binding_id"])["card_copy"] == copy
    for altered in (replace(snap, item_title="forged"), replace(snap, captured_at="2030-01-01T00:00:00Z"),
                    replace(snap, operation_result="completed"), replace(snap, completeness_scope="project"),
                    replace(snap, session_id="foreign-session")):
        assert ledger.save_linked_item_copy_snapshot(request, altered).status == "rejected"
    assert ledger.save_linked_item_copy_snapshot(str(uuid.uuid4()), snap).status == "rejected"


def test_legacy_without_confirmed_time_cannot_build_or_forge_it(tmp_path):
    parent, bindings, row, copy = _confirmed(tmp_path)
    def legacy(data):
        saved = data["batches"][0]["bindings"][0]["card_copy"]
        saved.pop("confirmed_at")
        saved["snapshot"].pop("item_status")
        saved["intent"]["copy_id"] = _digest(saved["snapshot"])
        return None, True
    with _file_lock(bindings._dialog_lock_path, bindings._lock_timeout):
        data = bindings._read()
        legacy(data)
        data["revision"] += 1
        bindings._validate_payload(data)
        bindings._publish(data)
    old = ConversationWorkRecordBindings(parent).get_binding(row["binding_id"])["card_copy"]
    assert "confirmed_at" not in old
    with pytest.raises(ValueError):
        build_linked_item_copy_snapshot(parent.conversation_id, old)


def test_ledger_never_reads_source_and_rejects_foreign_or_forged_copy_before_save(tmp_path, monkeypatch):
    from multi_project_work_record_bindings import ReadOnlyWorkRecordStore
    from test_multi_project_work_record_bindings import _session
    parent, bindings, row, copy = _confirmed(tmp_path)
    snap = build_linked_item_copy_snapshot(parent.conversation_id, copy)
    request = linked_item_copy_request_id(parent.conversation_id, snap.copy_id)
    ledger = WorkCardLedger(parent, WorkRecordStore(parent))
    monkeypatch.setattr(ReadOnlyWorkRecordStore, "_read_items_locked", lambda *_args: (_ for _ in ()).throw(AssertionError("must not read source")))
    for altered in (replace(snap, source_session_id="other-source"), replace(snap, requirement_text="forged"),
                    replace(snap, host_lifecycle={"sequence": 3, "status": "blocked", "finish_reason": "error"}),
                    replace(snap, captured_at="2020-01-01T00:00:00Z")):
        assert ledger.save_linked_item_copy_snapshot(request, altered).status == "rejected"
    other = _session(tmp_path, "other-parent", "project-a")
    other_ledger = WorkCardLedger(other, WorkRecordStore(other))
    assert other_ledger.save_linked_item_copy_snapshot(request, snap).status == "rejected"
    assert ledger.list_cards() == []
    assert ledger.save_linked_item_copy_snapshot(request, snap).status == "saved"
    assert bindings.get_binding(row["binding_id"])["card_copy"] == copy
