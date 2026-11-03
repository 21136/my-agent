from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_CORE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_CORE))

from multi_project_work_record_bindings import (
    AuthorizedProjectRecordSource,
    ReadOnlyWorkRecordStore,
    _issue_authorized_project_record_source,
    ExistingTaskBinding,
    ConversationWorkRecordBindings,
    WorkRecordBindingConflict,
)
from paths import AgentPaths
from session import Session, SessionMeta
from work_records import WorkRecordStore


def _session(root: Path, conversation_id: str, project_id: str) -> Session:
    paths = AgentPaths(agent_root=root, workspace=root / "workspace", data=root / "data", evolve=root / "evolve")
    directory = paths.data / "sessions" / conversation_id
    directory.mkdir(parents=True, exist_ok=True)
    return Session(conversation_id, directory, "", SessionMeta(project_id=project_id), [], paths)


def _fixture(tmp_path: Path):
    parent = _session(tmp_path, "parent-dialog", "project-a")
    project_a = _session(tmp_path, "project-a-session", "project-a")
    project_b = _session(tmp_path, "project-b-session", "project-b")
    store_a, store_b = WorkRecordStore(project_a), WorkRecordStore(project_b)
    item_a = store_a.create_item(title="A task record", scope={"kind": "project", "project_id": "project-a"})
    req_a = store_a.add_requirement(item_a["item_id"], "A requirement", event_id="req-a")
    store_a.begin_requirement_run(item_a["item_id"], req_a["requirement_id"],
                                  run_id="old-a", expected_current_run_id=None)
    item_b = store_b.create_item(title="B task record", scope={"kind": "project", "project_id": "project-b"})
    req_b = store_b.add_requirement(item_b["item_id"], "B requirement", event_id="req-b")
    store_b.begin_requirement_run(item_b["item_id"], req_b["requirement_id"],
                                  run_id="old-b", expected_current_run_id=None)
    bindings = [
        ExistingTaskBinding("project-a", item_a["item_id"], req_a["requirement_id"],
                            "task-a", "  exact A input\n", "worker-a", "run-a"),
        ExistingTaskBinding("project-b", item_b["item_id"], req_b["requirement_id"],
                            "task-b", "exact B input", "worker-b", "run-b"),
    ]
    sources = {
        "project-a": _issue_authorized_project_record_source("grant-a", parent.conversation_id, "project-a", ReadOnlyWorkRecordStore(project_a)),
        "project-b": _issue_authorized_project_record_source("grant-b", parent.conversation_id, "project-b", ReadOnlyWorkRecordStore(project_b)),
    }
    return parent, sources, bindings


def test_atomic_batch_binds_existing_records_across_projects_under_one_dialog(tmp_path):
    parent, sources, bindings = _fixture(tmp_path)
    namespace = ConversationWorkRecordBindings(parent)
    source_sidecars = [
        tmp_path / "data" / "sessions" / sid / "work-records.v1.json"
        for sid in ("project-a-session", "project-b-session")
    ]
    source_snapshots = [path.read_bytes() for path in source_sidecars]

    result = namespace.bind_existing_tasks(
        batch_id="batch-1", tasks=bindings,
        authorized_sources=sources,
    )

    assert len(result) == 2
    assert {(row["project_id"], row["worker_id"], row["run_id"]) for row in result} == {
        ("project-a", "worker-a", "run-a"), ("project-b", "worker-b", "run-b"),
    }
    assert next(row for row in result if row["task_id"] == "task-a")["task_input"] == "  exact A input\n"
    reopened = ConversationWorkRecordBindings(_session(tmp_path, "parent-dialog", "project-a"))
    assert reopened.list_bindings() == list(result)
    # Binding writes the current dialogue index only; source project records remain read-only.
    assert [path.read_bytes() for path in source_sidecars] == source_snapshots
    assert namespace._path.is_file()
    assert len(sources["project-a"].store.list_items()) == 1
    assert len(sources["project-b"].store.list_items()) == 1
    assert [len(sources[pid].store.get_item(item_id)["runs"]) for pid, item_id in (
        ("project-a", bindings[0].item_id), ("project-b", bindings[1].item_id),
    )] == [0, 0]


def test_batch_rejects_missing_project_authorization_without_partial_write(tmp_path):
    parent, sources, bindings = _fixture(tmp_path)
    namespace = ConversationWorkRecordBindings(parent)

    with pytest.raises(PermissionError):
        namespace.bind_existing_tasks(
            batch_id="batch-1", tasks=bindings,
            authorized_sources={"project-a": sources["project-a"]},
        )

    assert namespace.list_bindings() == []


def test_batch_rejects_ambiguous_worker_or_run_identity_atomically(tmp_path):
    parent, sources, bindings = _fixture(tmp_path)
    namespace = ConversationWorkRecordBindings(parent)
    ambiguous = [bindings[0], ExistingTaskBinding(
        "project-b", bindings[1].item_id, bindings[1].requirement_id,
        "task-b", "second exact input", "worker-a", "run-b",
    )]

    with pytest.raises(ValueError, match="unique"):
        namespace.bind_existing_tasks(
            batch_id="batch-1", tasks=ambiguous,
            authorized_sources=sources,
        )

    assert namespace.list_bindings() == []


def test_batch_rejects_missing_existing_requirement_without_partial_write(tmp_path):
    parent, sources, bindings = _fixture(tmp_path)
    namespace = ConversationWorkRecordBindings(parent)
    invalid = [bindings[0], ExistingTaskBinding(
        "project-b", bindings[1].item_id, "missing-requirement",
        "task-b", "exact B input", "worker-b", "run-b",
    )]

    with pytest.raises(KeyError):
        namespace.bind_existing_tasks(
            batch_id="batch-1", tasks=invalid,
            authorized_sources=sources,
        )

    assert namespace.list_bindings() == []


def test_batch_id_retry_is_idempotent_but_payload_reuse_conflicts(tmp_path):
    parent, sources, bindings = _fixture(tmp_path)
    namespace = ConversationWorkRecordBindings(parent)
    kwargs = {"batch_id": "batch-1", "tasks": bindings, "authorized_sources": sources}

    first = namespace.bind_existing_tasks(**kwargs)
    assert namespace.bind_existing_tasks(**kwargs) == first
    altered = [bindings[0], ExistingTaskBinding(
        "project-b", bindings[1].item_id, bindings[1].requirement_id,
        "task-b", "different input", "worker-b", "run-b",
    )]
    with pytest.raises(WorkRecordBindingConflict):
        namespace.bind_existing_tasks(batch_id="batch-1", tasks=altered, authorized_sources=sources)
    assert namespace.list_bindings() == list(first)


def test_only_matching_host_lifecycle_receipt_is_settled_and_restart_keeps_unknown(tmp_path):
    parent, sources, bindings = _fixture(tmp_path)
    namespace = ConversationWorkRecordBindings(parent)
    bound = namespace.bind_existing_tasks(batch_id="batch-1", tasks=bindings, authorized_sources=sources)
    first = bound[0]

    with pytest.raises(WorkRecordBindingConflict):
        namespace.record_worker_lifecycle(
            binding_id=first["binding_id"], worker_id="worker-b", run_id="run-a",
            sequence=3, status="model_turn_finished", finish_reason="unknown",
        )
    settled = namespace.record_worker_lifecycle(
        binding_id=first["binding_id"], worker_id="worker-a", run_id="run-a",
        sequence=3, status="model_turn_finished", finish_reason="unknown",
    )
    restarted = ConversationWorkRecordBindings(_session(tmp_path, "parent-dialog", "project-a"))

    assert settled["lifecycle"] == {"sequence": 3, "status": "model_turn_finished", "finish_reason": "unknown"}
    assert settled["record_status"] == "unknown"
    assert restarted.get_binding(first["binding_id"]) == settled
    assert restarted.get_binding(bound[1]["binding_id"])["lifecycle"] is None



def test_failed_atomic_replace_keeps_prior_namespace_snapshot(tmp_path, monkeypatch):
    parent, sources, bindings = _fixture(tmp_path)
    namespace = ConversationWorkRecordBindings(parent)
    first = namespace.bind_existing_tasks(batch_id="batch-1", tasks=bindings, authorized_sources=sources)
    second_binding = ExistingTaskBinding(
        "project-a", bindings[0].item_id, bindings[0].requirement_id,
        "task-a2", "second exact input", "worker-a2", "run-a2",
    )

    import multi_project_work_record_bindings as bindings_module
    monkeypatch.setattr(bindings_module, "_replace_parent_from_handle", lambda *_args: (_ for _ in ()).throw(OSError("simulated replace failure")))
    with pytest.raises(Exception, match="atomically published"):
        namespace.bind_existing_tasks(
            batch_id="batch-2", tasks=[second_binding],
            authorized_sources={"project-a": sources["project-a"]},
        )

    monkeypatch.undo()
    reopened = ConversationWorkRecordBindings(_session(tmp_path, "parent-dialog", "project-a"))
    assert reopened.list_bindings() == list(first)


def test_same_project_task_continues_after_restart_with_same_requirement_and_new_worker_run(tmp_path):
    parent, sources, bindings = _fixture(tmp_path)
    first_store = ConversationWorkRecordBindings(parent)
    first = first_store.bind_existing_tasks(
        batch_id="batch-original", tasks=[bindings[0]],
        authorized_sources={"project-a": sources["project-a"]},
    )[0]
    assert first_store.record_worker_lifecycle(
        binding_id=first["binding_id"], worker_id="worker-a", run_id="run-a",
        sequence=5, status="blocked", finish_reason="paused",
    )["lifecycle"]["status"] == "blocked"

    restarted = ConversationWorkRecordBindings(_session(tmp_path, "parent-dialog", "project-a"))
    continuation = ExistingTaskBinding(
        "project-a", bindings[0].item_id, bindings[0].requirement_id,
        "task-a", bindings[0].task_input, "worker-a-continued", "run-a-continued",
    )
    next_binding = restarted.bind_existing_tasks(
        batch_id="batch-continuation", tasks=[continuation],
        authorized_sources={"project-a": sources["project-a"]},
    )[0]

    assert next_binding["binding_id"] != first["binding_id"]
    assert (next_binding["project_id"], next_binding["task_id"], next_binding["item_id"],
            next_binding["requirement_id"]) == (
        first["project_id"], first["task_id"], first["item_id"], first["requirement_id"],
    )
    after_restart = ConversationWorkRecordBindings(_session(tmp_path, "parent-dialog", "project-a"))
    assert after_restart.get_binding(first["binding_id"])["lifecycle"]["status"] == "blocked"
    assert after_restart.get_binding(next_binding["binding_id"])["lifecycle"] is None

    # Retry the same continuation batch after another restart: no third binding is created.
    repeated = after_restart.bind_existing_tasks(
        batch_id="batch-continuation", tasks=[continuation],
        authorized_sources={"project-a": sources["project-a"]},
    )
    assert repeated == (next_binding,)
    assert len(after_restart.list_bindings()) == 2


def test_continuation_cannot_rebind_existing_project_task_to_another_requirement(tmp_path):
    parent, sources, bindings = _fixture(tmp_path)
    namespace = ConversationWorkRecordBindings(parent)
    original = namespace.bind_existing_tasks(
        batch_id="batch-original", tasks=[bindings[0]],
        authorized_sources={"project-a": sources["project-a"]},
    )[0]
    write_store = WorkRecordStore(_session(tmp_path, "project-a-session", "project-a"))
    other_requirement = write_store.add_requirement(
        bindings[0].item_id, "Different requirement", event_id="req-a-other",
    )
    impersonation = ExistingTaskBinding(
        "project-a", bindings[0].item_id, other_requirement["requirement_id"],
        "task-a", bindings[0].task_input, "worker-a-2", "run-a-2",
    )

    with pytest.raises(WorkRecordBindingConflict, match="requirement"):
        namespace.bind_existing_tasks(
            batch_id="batch-forged-continuation", tasks=[impersonation],
            authorized_sources={"project-a": sources["project-a"]},
        )

    assert namespace.list_bindings() == [original]


def test_forged_source_project_scope_is_rejected_before_binding_write(tmp_path):
    parent, sources, bindings = _fixture(tmp_path)
    project_b_session = _session(tmp_path, "project-b-session", "project-b")
    forged = _issue_authorized_project_record_source(
        "forged-grant", parent.conversation_id, "project-a", sources["project-a"].store,
    )
    object.__setattr__(forged, "store", ReadOnlyWorkRecordStore(project_b_session))
    namespace = ConversationWorkRecordBindings(parent)

    with pytest.raises(PermissionError):
        namespace.bind_existing_tasks(
            batch_id="forged-scope", tasks=[bindings[0]], authorized_sources={"project-a": forged},
        )

    assert namespace.list_bindings() == []
    assert sources["project-a"].store.list_items()


def test_same_batch_reauthorization_to_new_source_session_conflicts_instead_of_returning_old_binding(tmp_path):
    parent, sources, bindings = _fixture(tmp_path)
    namespace = ConversationWorkRecordBindings(parent)
    task = [bindings[0]]
    original = namespace.bind_existing_tasks(
        batch_id="stable-batch", tasks=task, authorized_sources={"project-a": sources["project-a"]},
    )

    replacement_session = _session(tmp_path, "project-a-session-new", "project-a")
    old_sidecar = tmp_path / "data" / "sessions" / "project-a-session" / "work-records.v1.json"
    new_sidecar = replacement_session.session_dir / "work-records.v1.json"
    copied = json.loads(old_sidecar.read_text(encoding="utf-8"))
    copied["conversation_id"] = replacement_session.conversation_id
    new_sidecar.write_text(json.dumps(copied), encoding="utf-8")
    replacement_source = _issue_authorized_project_record_source(
        "fresh-grant", parent.conversation_id, "project-a", ReadOnlyWorkRecordStore(replacement_session),
    )

    with pytest.raises(WorkRecordBindingConflict, match="source"):
        namespace.bind_existing_tasks(
            batch_id="stable-batch", tasks=task, authorized_sources={"project-a": replacement_source},
        )

    assert namespace.list_bindings() == list(original)
    assert original[0]["source_session_id"] == "project-a-session"


def test_unissued_source_receipt_with_matching_store_scope_is_rejected(tmp_path):
    parent, sources, bindings = _fixture(tmp_path)
    forged = AuthorizedProjectRecordSource(
        "fabricated-grant", parent.conversation_id, "project-a", sources["project-a"].store,
    )
    namespace = ConversationWorkRecordBindings(parent)

    with pytest.raises(PermissionError, match="authorized"):
        namespace.bind_existing_tasks(
            batch_id="fabricated-receipt", tasks=[bindings[0]], authorized_sources={"project-a": forged},
        )

    assert namespace.list_bindings() == []


def test_linked_existing_read_attempts_are_bound_durable_and_idempotent(tmp_path):
    parent, sources, bindings = _fixture(tmp_path)
    namespace = ConversationWorkRecordBindings(parent)
    row = namespace.bind_existing_tasks(
        batch_id="batch-read-attempts", tasks=bindings[:1],
        authorized_sources={"project-a": sources["project-a"]},
    )[0]
    attempts = [
        {"read_id": 1, "path": "README.md", "attempt": 1,
         "result": "retrying", "error_code": "sharing_violation"},
        {"read_id": 1, "path": "README.md", "attempt": 1,
         "result": "cancelled", "error_code": ""},
    ]
    args = dict(binding_id=row["binding_id"], worker_id="worker-a", run_id="run-a",
                sequence=4, status="cancelled", finish_reason="cancelled",
                read_attempts_available=True, read_attempts=attempts)

    with pytest.raises(WorkRecordBindingConflict):
        namespace.record_worker_lifecycle(**{**args, "worker_id": "worker-b"})
    assert namespace.get_binding(row["binding_id"])["lifecycle"] is None
    first = namespace.record_worker_lifecycle(**args)
    replay = namespace.record_worker_lifecycle(**args)
    reopened = ConversationWorkRecordBindings(_session(tmp_path, "parent-dialog", "project-a"))

    assert first["project_id"] == row["project_id"]
    assert first["requirement_id"] == row["requirement_id"]
    assert first["run_id"] == row["run_id"]
    assert first["read_attempts_available"] is True
    assert first["read_attempts"] == attempts
    assert replay == first
    assert reopened.get_binding(row["binding_id"]) == first
    with pytest.raises(WorkRecordBindingConflict):
        namespace.record_worker_lifecycle(**{**args, "read_attempts": []})


def test_linked_existing_missing_read_attempts_are_explicit_and_malicious_fields_rejected(tmp_path):
    parent, sources, bindings = _fixture(tmp_path)
    namespace = ConversationWorkRecordBindings(parent)
    row = namespace.bind_existing_tasks(
        batch_id="batch-read-missing", tasks=bindings[:1],
        authorized_sources={"project-a": sources["project-a"]},
    )[0]
    base = dict(binding_id=row["binding_id"], worker_id="worker-a", run_id="run-a",
                sequence=4, status="failed", finish_reason="error")

    missing = namespace.record_worker_lifecycle(
        **base, read_attempts_available=False, read_attempts=[],
    )
    assert missing["read_attempts_available"] is False
    assert missing["read_attempts"] == []
    with pytest.raises(ValueError):
        namespace.record_worker_lifecycle(
            **{**base, "binding_id": "nonexistent"}, read_attempts_available=True,
            read_attempts=[{"read_id": 1, "path": "../secret", "attempt": 1,
                           "result": "retrying", "error_code": "sharing_violation"}],
        )

def test_start_receipt_legacy_replay_unknown_and_conflict(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    parent, sources, bindings = _fixture(tmp_path)
    store = ConversationWorkRecordBindings(parent)
    rows = store.bind_existing_tasks(batch_id="batch-legacy", tasks=bindings, authorized_sources=sources)
    first, second = rows
    assert "start_receipt" not in first  # persisted legacy v1 shape
    args = dict(binding_id=first["binding_id"], worker_id=first["worker_id"], run_id=first["run_id"],
                reservation_id="reservation-a", parent_run_id="parent-run", turn_epoch=7,
                project_id=first["project_id"], task_id=first["task_id"])
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: ConversationWorkRecordBindings(parent).record_worker_start(**args), range(2)))
    assert results[0] == results[1]
    assert ConversationWorkRecordBindings(parent).get_binding(first["binding_id"])["start_receipt"] == {
        "reservation_id": "reservation-a", "parent_run_id": "parent-run", "turn_epoch": 7}
    with pytest.raises(WorkRecordBindingConflict):
        store.record_worker_start(**{**args, "reservation_id": "other"})
    with pytest.raises(WorkRecordBindingConflict):
        store.record_worker_lifecycle(binding_id=second["binding_id"], worker_id=second["worker_id"],
            run_id=second["run_id"], sequence=7, status="unknown", finish_reason="unknown")
    terminal = dict(binding_id=first["binding_id"], worker_id=first["worker_id"], run_id=first["run_id"],
                    sequence=7, status="unknown", finish_reason="unknown")
    assert store.record_worker_lifecycle(**terminal) == store.record_worker_lifecycle(**terminal)
    assert store.get_binding(second["binding_id"])["lifecycle"] is None
    with pytest.raises(WorkRecordBindingConflict):
        store.record_worker_lifecycle(**{**terminal, "status": "model_turn_finished"})

def test_start_write_failure_preserves_legacy_binding_without_start(tmp_path, monkeypatch):
    parent, sources, bindings = _fixture(tmp_path)
    namespace = ConversationWorkRecordBindings(parent)
    row = namespace.bind_existing_tasks(batch_id="batch-fail-start", tasks=bindings, authorized_sources=sources)[0]
    before = namespace._path.read_bytes()
    monkeypatch.setattr(namespace, "_publish", lambda *_: (_ for _ in ()).throw(OSError("injected write failure")))
    with pytest.raises(OSError):
        namespace.record_worker_start(binding_id=row["binding_id"], worker_id=row["worker_id"], run_id=row["run_id"],
                                      reservation_id="reserve-fail", parent_run_id="parent-run", turn_epoch=9,
                                      project_id=row["project_id"], task_id=row["task_id"])
    assert namespace._path.read_bytes() == before
    persisted = ConversationWorkRecordBindings(parent).get_binding(row["binding_id"])
    assert "start_receipt" not in persisted and persisted["lifecycle"] is None
