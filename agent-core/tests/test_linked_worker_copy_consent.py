from dataclasses import replace
import sys
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_multi_project_work_record_bindings import _fixture
from multi_project_work_record_bindings import ConversationWorkRecordBindings, _reader_for, WorkRecordBindingConflict
from work_records import _digest


def test_confirmed_copy_is_atomic_source_unchanged_and_recoverable(tmp_path):
    parent, sources, tasks = _fixture(tmp_path)
    tasks = [replace(t, source_item_digest=_digest(sources[t.project_id].store.get_item(t.item_id))) for t in tasks]
    binding = ConversationWorkRecordBindings(parent)
    rows = binding.bind_existing_tasks(batch_id="copy-batch", tasks=tasks, authorized_sources=sources)
    for r in rows:
        binding.record_worker_start(binding_id=r["binding_id"], worker_id=r["worker_id"], run_id=r["run_id"],
            reservation_id="reservation-" + r["task_id"], parent_run_id="parent-run", turn_epoch=3,
            project_id=r["project_id"], task_id=r["task_id"])
    r = rows[0]
    binding.record_worker_lifecycle(binding_id=r["binding_id"], worker_id=r["worker_id"], run_id=r["run_id"],
        sequence=3, status="model_turn_finished", finish_reason="completed")
    before = {p: _reader_for(s.store)._path.read_bytes() for p,s in sources.items()}
    proposed = binding.prepare_item_copies(binding_ids=[r["binding_id"]], authorized_sources={"project-a": sources["project-a"]})
    assert proposed[0]["project_id"] == "project-a" and proposed[0]["item_title"] == "A task record"
    assert proposed[0]["requirement_text"] == "A requirement" and proposed[0]["operation_result"] == "unknown"
    assert proposed[0]["verification"] == "not_verified" and "host_start" in proposed[0]
    assert "card_copy" not in binding.get_binding(r["binding_id"])
    binding.commit_confirmed_item_copies(proposed=proposed, authorized_sources={"project-a": sources["project-a"]}, is_current=lambda: True)
    stored = ConversationWorkRecordBindings(parent).get_binding(r["binding_id"])["card_copy"]
    assert stored["snapshot"] == proposed[0] and stored["intent"]["state"] == "pending"
    assert "card_copy" not in binding.get_binding(rows[1]["binding_id"])
    assert binding.commit_confirmed_item_copies(proposed=proposed, authorized_sources={"project-a": sources["project-a"]}, is_current=lambda: True) == [stored]
    assert {p: _reader_for(s.store)._path.read_bytes() for p,s in sources.items()} == before
    assert ConversationWorkRecordBindings(parent).get_binding(r["binding_id"])["card_copy"] == stored


def test_changed_source_and_unconfirmed_or_changed_identity_do_not_persist(tmp_path):
    parent, sources, tasks = _fixture(tmp_path)
    tasks = [replace(t, source_item_digest=_digest(sources[t.project_id].store.get_item(t.item_id))) for t in tasks]
    binding = ConversationWorkRecordBindings(parent)
    r = binding.bind_existing_tasks(batch_id="batch", tasks=tasks, authorized_sources=sources)[0]
    binding.record_worker_start(binding_id=r["binding_id"], worker_id=r["worker_id"], run_id=r["run_id"], reservation_id="res", parent_run_id="parent-run", turn_epoch=1, project_id=r["project_id"], task_id=r["task_id"])
    binding.record_worker_lifecycle(binding_id=r["binding_id"], worker_id=r["worker_id"], run_id=r["run_id"], sequence=1, status="unknown", finish_reason="unknown")
    with pytest.raises(PermissionError):
        binding.prepare_item_copies(binding_ids=[r["binding_id"]], authorized_sources={})
    proposed = binding.prepare_item_copies(binding_ids=[r["binding_id"]], authorized_sources={"project-a": sources["project-a"]})
    with pytest.raises(WorkRecordBindingConflict):
        binding.commit_confirmed_item_copies(proposed=proposed, authorized_sources={"project-a": sources["project-a"]}, is_current=lambda: False)
    assert "card_copy" not in binding.get_binding(r["binding_id"])
    _reader_for(sources["project-a"].store).add_requirement(r["item_id"], "changed", event_id="changed")
    with pytest.raises(WorkRecordBindingConflict):
        binding.commit_confirmed_item_copies(proposed=proposed, authorized_sources={"project-a": sources["project-a"]}, is_current=lambda: True)
    assert "card_copy" not in binding.get_binding(r["binding_id"])


def test_failed_publish_never_exposes_partial_pending_intent(tmp_path, monkeypatch):
    parent, sources, tasks = _fixture(tmp_path)
    tasks = [replace(t, source_item_digest=_digest(sources[t.project_id].store.get_item(t.item_id))) for t in tasks]
    binding = ConversationWorkRecordBindings(parent)
    r = binding.bind_existing_tasks(batch_id="failure", tasks=tasks, authorized_sources=sources)[0]
    binding.record_worker_start(binding_id=r["binding_id"], worker_id=r["worker_id"], run_id=r["run_id"], reservation_id="res", parent_run_id="parent-run", turn_epoch=1, project_id=r["project_id"], task_id=r["task_id"])
    binding.record_worker_lifecycle(binding_id=r["binding_id"], worker_id=r["worker_id"], run_id=r["run_id"], sequence=1, status="unknown", finish_reason="unknown")
    proposal = binding.prepare_item_copies(binding_ids=[r["binding_id"]], authorized_sources={"project-a": sources["project-a"]})
    monkeypatch.setattr(binding, "_publish", lambda _data: (_ for _ in ()).throw(OSError("synthetic failure")))
    with pytest.raises(OSError):
        binding.commit_confirmed_item_copies(proposed=proposal, authorized_sources={"project-a": sources["project-a"]}, is_current=lambda: True)
    assert "card_copy" not in ConversationWorkRecordBindings(parent).get_binding(r["binding_id"])
