from __future__ import annotations
import sys
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_multi_project_work_record_bindings import _fixture
from multi_project_work_record_bindings import ConversationWorkRecordBindings, AuthorizedProjectRecordSource, _reader_for, _issue_authorized_project_record_source
from work_records import WorkRecordScopeError


def test_projection_without_matching_host_start_stays_unknown_and_content_free(tmp_path):
    parent, sources, tasks = _fixture(tmp_path)
    store = ConversationWorkRecordBindings(parent)
    rows = store.bind_existing_tasks(batch_id="batch-no-host-start", tasks=tasks, authorized_sources=sources)
    row = rows[0]

    # Lifecycle data without its matching host start receipt is not evidence of liveness.
    store.record_worker_lifecycle(binding_id=row["binding_id"], worker_id=row["worker_id"],
        run_id=row["run_id"], sequence=1, status="blocked", finish_reason="paused")
    view = store.project_authorized_worker_status(binding_id=row["binding_id"], source=sources["project-a"])

    assert view == {"project_id": "project-a", "binding_id": row["binding_id"],
        "worker_run_id": row["run_id"], "host_status": "unknown", "finish_reason": "unknown",
        "cancellation_status": "unknown", "operation_result": "unknown",
        "verification": "not_verified", "source_snapshot_verified": False}
    assert not ({"title", "requirements", "text", "source_session_id", "host_start"} & view.keys())


def test_projection_is_project_scoped_authorized_replayable_and_does_not_write_sources(tmp_path):
    parent, sources, tasks = _fixture(tmp_path)
    store = ConversationWorkRecordBindings(parent)
    rows = store.bind_existing_tasks(batch_id="batch-projection", tasks=tasks, authorized_sources=sources)
    # A valid read authorization and task binding without an actual host start
    # receipt cannot establish liveness or authorize a cancellation attempt.
    unstarted = store.project_authorized_worker_status(binding_id=rows[0]["binding_id"], source=sources["project-a"])
    assert unstarted["worker_run_id"] == rows[0]["run_id"]
    assert unstarted["host_status"] == "unknown"
    assert unstarted["cancellation_status"] == "unknown"
    source_paths = [sources[p].store.lock_path.parent / "work-records.v1.json" for p in ("project-a", "project-b")]
    before = [p.read_bytes() for p in source_paths]
    a, b = rows
    def start(row):
        store.record_worker_start(binding_id=row["binding_id"], worker_id=row["worker_id"], run_id=row["run_id"],
            reservation_id="reservation-" + row["task_id"], parent_run_id="parent-run", turn_epoch=3,
            project_id=row["project_id"], task_id=row["task_id"])
    start(a); start(b)
    store.record_worker_lifecycle(binding_id=a["binding_id"], worker_id=a["worker_id"], run_id=a["run_id"],
        sequence=3, status="model_turn_finished", finish_reason="completed")
    view = store.project_authorized_worker_status(binding_id=a["binding_id"], source=sources["project-a"])
    assert view == {"project_id": "project-a", "binding_id": a["binding_id"],
        "worker_run_id": a["run_id"], "host_status": "model_turn_finished",
        "finish_reason": "completed", "cancellation_status": "unknown",
        "operation_result": "unknown", "verification": "not_verified",
        "source_snapshot_verified": False}
    missing_lifecycle = store.project_authorized_worker_status(binding_id=b["binding_id"], source=sources["project-b"])
    assert missing_lifecycle["host_status"] == "unknown"
    assert missing_lifecycle["finish_reason"] == "unknown"
    second_grant = _issue_authorized_project_record_source(sources["project-a"].authorization_id,
        parent.conversation_id, "project-a", sources["project-a"].store)
    assert store.project_authorized_worker_status(binding_id=a["binding_id"], source=second_grant) == view
    reopened = ConversationWorkRecordBindings(parent)
    assert reopened.project_authorized_worker_status(binding_id=a["binding_id"], source=sources["project-a"]) == view
    assert [p.read_bytes() for p in source_paths] == before
    with pytest.raises(PermissionError):
        store.project_authorized_worker_status(binding_id=a["binding_id"], source=sources["project-b"])
    forged = AuthorizedProjectRecordSource(sources["project-a"].authorization_id, parent.conversation_id,
                                          "project-a", sources["project-a"].store)
    with pytest.raises(PermissionError):
        store.project_authorized_worker_status(binding_id=a["binding_id"], source=forged)
    source_store = _reader_for(sources["project-a"].store)
    def change_requirement(data):
        data["items"][0]["requirements"][0]["text"] = "changed source"
        return None, True
    source_store._mutate(change_requirement)
    assert store.project_authorized_worker_status(binding_id=a["binding_id"], source=sources["project-a"]) is None
    # Changing the parent scope invalidates the parent binding.
    parent.meta.project_id = "wrong"
    with pytest.raises(WorkRecordScopeError):
        store.project_authorized_worker_status(binding_id=a["binding_id"], source=sources["project-a"])
