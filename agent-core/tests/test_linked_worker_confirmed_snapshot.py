from dataclasses import replace
import sys
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_multi_project_work_record_bindings import _fixture
from multi_project_work_record_bindings import ConversationWorkRecordBindings, WorkRecordBindingConflict, _reader_for
from work_records import _digest


def test_binding_persists_confirmed_item_digests_and_rejects_changes_or_missing_auth(tmp_path):
    parent, sources, tasks = _fixture(tmp_path)
    snapshots = {t.project_id: _digest(sources[t.project_id].store.get_item(t.item_id)) for t in tasks}
    tasks = [replace(t, source_item_digest=snapshots[t.project_id]) for t in tasks]
    bindings = ConversationWorkRecordBindings(parent)
    source_bytes = {p: _reader_for(s.store)._path.read_bytes() for p, s in sources.items()}
    with pytest.raises(PermissionError):
        bindings.bind_existing_tasks(batch_id="batch", tasks=tasks, authorized_sources={"project-a": sources["project-a"]})
    assert bindings.list_bindings() == []
    rows = bindings.bind_existing_tasks(batch_id="batch", tasks=tasks, authorized_sources=sources)
    assert {row["project_id"]: row["source_item_digest"] for row in rows} == snapshots
    assert ConversationWorkRecordBindings(parent).list_bindings() == list(rows)
    assert bindings.bind_existing_tasks(batch_id="batch", tasks=tasks, authorized_sources=sources) == rows
    assert {p: _reader_for(s.store)._path.read_bytes() for p,s in sources.items()} == source_bytes
    a, b = rows
    for row in rows:
        bindings.record_worker_start(binding_id=row["binding_id"], worker_id=row["worker_id"], run_id=row["run_id"],
            reservation_id="reservation-" + row["task_id"], parent_run_id="parent-run", turn_epoch=3,
            project_id=row["project_id"], task_id=row["task_id"])
    bindings.record_worker_lifecycle(binding_id=a["binding_id"], worker_id=a["worker_id"], run_id=a["run_id"],
        sequence=3, status="model_turn_finished", finish_reason="completed")
    view = bindings.project_authorized_worker_status(binding_id=a["binding_id"], source=sources["project-a"])
    assert view["source_snapshot_verified"] is True and view["operation_result"] == "unknown"
    assert view["verification"] == "not_verified"
    assert ConversationWorkRecordBindings(parent).project_authorized_worker_status(
        binding_id=a["binding_id"], source=sources["project-a"]) == view
    missing_lifecycle = bindings.project_authorized_worker_status(binding_id=b["binding_id"], source=sources["project-b"])
    assert missing_lifecycle["host_status"] == "unknown"
    assert missing_lifecycle["finish_reason"] == "unknown"
    assert {p: _reader_for(s.store)._path.read_bytes() for p,s in sources.items()} == source_bytes
    source = _reader_for(sources["project-a"].store)
    source.add_requirement(tasks[0].item_id, "new requirement", event_id="source-change")
    assert bindings.project_authorized_worker_status(binding_id=a["binding_id"], source=sources["project-a"]) is None
    with pytest.raises(WorkRecordBindingConflict):
        bindings.bind_existing_tasks(batch_id="batch", tasks=tasks, authorized_sources=sources)
    with pytest.raises(WorkRecordBindingConflict):
        bindings.bind_existing_tasks(batch_id="other", tasks=tasks, authorized_sources=sources)
    assert len(bindings.list_bindings()) == 2


def test_legacy_binding_does_not_gain_a_fabricated_snapshot(tmp_path):
    parent, sources, tasks = _fixture(tmp_path)
    bindings = ConversationWorkRecordBindings(parent)
    rows = bindings.bind_existing_tasks(batch_id="legacy", tasks=tasks, authorized_sources=sources)
    assert all("source_item_digest" not in row for row in rows)
    assert ConversationWorkRecordBindings(parent).list_bindings() == list(rows)
