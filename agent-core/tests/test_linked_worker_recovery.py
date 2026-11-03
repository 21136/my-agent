from __future__ import annotations

import sys
from pathlib import Path

import pytest

_CORE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_CORE))

from multi_project_work_record_bindings import (
    AuthorizedProjectRecordSource,
    ConversationWorkRecordBindings,
    ExistingTaskBinding,
    ReadOnlyWorkRecordStore,
    WorkRecordBindingConflict,
    _issue_authorized_project_record_source,
)
from paths import AgentPaths
from session import Session, SessionMeta
from work_records import WorkRecordStore


def _bound_task(tmp_path: Path):
    paths = AgentPaths(agent_root=tmp_path, workspace=tmp_path / "workspace",
                       data=tmp_path / "data", evolve=tmp_path / "evolve")
    parent_dir = paths.data / "sessions" / "parent-dialog"
    source_dir = paths.data / "sessions" / "project-session"
    parent_dir.mkdir(parents=True)
    source_dir.mkdir(parents=True)
    parent = Session("parent-dialog", parent_dir, "", SessionMeta(project_id="project-a"), [], paths)
    source = Session("project-session", source_dir, "", SessionMeta(project_id="project-a"), [], paths)
    record_store = WorkRecordStore(source)
    item = record_store.create_item(title="Existing", scope={"kind": "project", "project_id": "project-a"})
    requirement = record_store.add_requirement(item["item_id"], "Exact task", event_id="req")
    task = ExistingTaskBinding("project-a", item["item_id"], requirement["requirement_id"],
                               "stable-task", "Exact task", "worker-first", "run-first")
    auth = _issue_authorized_project_record_source(
        "grant", parent.conversation_id, "project-a", ReadOnlyWorkRecordStore(source),
    )
    return parent, task, {"project-a": auth}


def test_unsettled_linked_start_is_not_rebound_or_reexecuted(tmp_path):
    parent, first_task, sources = _bound_task(tmp_path)
    store = ConversationWorkRecordBindings(parent)
    first = store.bind_existing_tasks(batch_id="batch-first", tasks=[first_task], authorized_sources=sources)[0]
    store.record_worker_start(
        binding_id=first["binding_id"], worker_id=first_task.worker_id, run_id=first_task.run_id,
        reservation_id="reservation-first", parent_run_id="parent-first", turn_epoch=1,
        project_id=first_task.project_id, task_id=first_task.task_id,
    )

    retry = ExistingTaskBinding(
        first_task.project_id, first_task.item_id, first_task.requirement_id,
        first_task.task_id, first_task.task_input, "worker-retry", "run-retry",
    )
    with pytest.raises(WorkRecordBindingConflict, match="unresolved"):
        store.bind_existing_tasks(batch_id="batch-retry", tasks=[retry], authorized_sources=sources)

    persisted = store.list_bindings()
    assert len(persisted) == 1
    assert persisted[0]["start_receipt"]["reservation_id"] == "reservation-first"
    assert persisted[0]["lifecycle"] is None


def test_unknown_linked_lifecycle_is_not_reexecuted(tmp_path):
    parent, first_task, sources = _bound_task(tmp_path)
    store = ConversationWorkRecordBindings(parent)
    first = store.bind_existing_tasks(batch_id="batch-first", tasks=[first_task], authorized_sources=sources)[0]
    store.record_worker_start(
        binding_id=first["binding_id"], worker_id=first_task.worker_id, run_id=first_task.run_id,
        reservation_id="reservation-first", parent_run_id="parent-first", turn_epoch=1,
        project_id=first_task.project_id, task_id=first_task.task_id,
    )
    store.record_worker_lifecycle(
        binding_id=first["binding_id"], worker_id=first_task.worker_id, run_id=first_task.run_id,
        sequence=1, status="unknown", finish_reason="unknown",
    )
    retry = ExistingTaskBinding(
        first_task.project_id, first_task.item_id, first_task.requirement_id,
        first_task.task_id, first_task.task_input, "worker-retry", "run-retry",
    )

    with pytest.raises(WorkRecordBindingConflict, match="unresolved"):
        store.bind_existing_tasks(batch_id="batch-retry", tasks=[retry], authorized_sources=sources)

    assert len(store.list_bindings()) == 1


def _binding_for(task: ExistingTaskBinding, worker_id: str, run_id: str) -> ExistingTaskBinding:
    return ExistingTaskBinding(
        task.project_id, task.item_id, task.requirement_id, task.task_id,
        task.task_input, worker_id, run_id,
    )


def _bind_one(store: ConversationWorkRecordBindings, task: ExistingTaskBinding, sources, batch_id: str):
    return store.bind_existing_tasks(batch_id=batch_id, tasks=[task], authorized_sources=sources)[0]


def _start_one(store: ConversationWorkRecordBindings, row: dict, task: ExistingTaskBinding, suffix: str):
    return store.record_worker_start(
        binding_id=row["binding_id"], worker_id=task.worker_id, run_id=task.run_id,
        reservation_id=f"reservation-{suffix}", parent_run_id=f"parent-{suffix}", turn_epoch=1,
        project_id=task.project_id, task_id=task.task_id,
    )


def test_second_prebound_batch_cannot_start_after_first_batch_starts(tmp_path):
    parent, base_task, sources = _bound_task(tmp_path)
    store = ConversationWorkRecordBindings(parent)
    first_task = _binding_for(base_task, "worker-first", "run-first")
    second_task = _binding_for(base_task, "worker-second", "run-second")
    first = _bind_one(store, first_task, sources, "batch-first")
    second = _bind_one(store, second_task, sources, "batch-second")

    first_receipt = _start_one(store, first, first_task, "first")
    assert _start_one(store, first, first_task, "first") == first_receipt
    assert store.bind_existing_tasks(
        batch_id="batch-first", tasks=[first_task], authorized_sources=sources,
    ) == (first_receipt,)
    with pytest.raises(WorkRecordBindingConflict, match="unresolved"):
        _start_one(store, second, second_task, "second")

    assert store.get_binding(second["binding_id"]).get("start_receipt") is None
    store.record_worker_lifecycle(
        binding_id=first["binding_id"], worker_id=first_task.worker_id, run_id=first_task.run_id,
        sequence=1, status="failed", finish_reason="error",
    )
    assert _start_one(store, second, second_task, "second")["start_receipt"]["reservation_id"] == "reservation-second"


def test_older_unresolved_start_is_not_hidden_by_newer_unstarted_binding(tmp_path):
    parent, base_task, sources = _bound_task(tmp_path)
    store = ConversationWorkRecordBindings(parent)
    first_task = _binding_for(base_task, "worker-first", "run-first")
    second_task = _binding_for(base_task, "worker-second", "run-second")
    third_task = _binding_for(base_task, "worker-third", "run-third")
    first = _bind_one(store, first_task, sources, "batch-first")
    _bind_one(store, second_task, sources, "batch-second")

    _start_one(store, first, first_task, "first")
    with pytest.raises(WorkRecordBindingConflict, match="unresolved"):
        _bind_one(store, third_task, sources, "batch-third")

    assert len(store.list_bindings()) == 2
