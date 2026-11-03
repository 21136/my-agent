from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
import pytest
from paths import AgentPaths
from session import Session, SessionMeta
from work_records import WorkRecordConflict, WorkRecordStaleRun, WorkRecordStore


def fixture(tmp_path, *, project=False):
    paths = AgentPaths(tmp_path, tmp_path / "workspace", tmp_path / "data", tmp_path / "evolve")
    session_id = "abc12345-1234-4abc-8def-1234567890ab"
    session_dir = paths.data / "sessions" / session_id
    session_dir.mkdir(parents=True)
    session = Session(session_id, session_dir, "", SessionMeta(project_id="project-a"), [], paths)
    store = WorkRecordStore(session)
    item = store.create_item(title="Synthetic", scope=({"kind": "project", "project_id": "project-a"} if project else {"kind": "independent"}))
    req = store.add_requirement(item["item_id"], "Original requirement", event_id="original-requirement")
    return session, store, item["item_id"], req["requirement_id"]


def revise(store, item_id, req_id, old="Original requirement", new="Revised requirement", key="revision-1"):
    return store.revise_requirement(item_id, req_id, expected_text=old, new_text=new,
                                    event_id=key, idempotency_key=key, return_commit_status=True)


def test_revision_cas_idempotency_and_preserved_immutable_old_card(tmp_path):
    from work_card_ledger import WorkCardLedger
    from work_record_cards import build_independent_work_record_card
    session, store, item_id, req_id = fixture(tmp_path)
    run_id = str(uuid4())
    store.begin_requirement_run(item_id, req_id, run_id=run_id, expected_current_run_id=None)
    store.record_result_if_current_run(item_id, req_id, run_id=run_id, result_id="old-result", status="completed",
        summary="synthetic previous result", verification={"status":"passed", "evidence_refs":[]}, sources=[])
    snapshot = build_independent_work_record_card(store, item_id)
    ledger = WorkCardLedger(session, store)
    saved = ledger.save_snapshot(str(uuid4()), snapshot)
    assert saved.status == "saved"
    old_card = ledger.get_card(saved.card_id)
    previous = store.get_item(item_id)
    event, committed = revise(store, item_id, req_id)
    assert committed and event["old_text"] == "Original requirement" and event["new_text"] == "Revised requirement"
    after = store.get_item(item_id)
    assert after["runs"] == previous["runs"]
    assert after["history"][:-1] == previous["history"]
    assert after["requirements"][0]["text"] == "Revised requirement"
    assert after["requirements"][0]["status"] == "pending"
    assert after["requirements"][0].get("current_run_id") is None
    assert ledger.get_card(saved.card_id) == old_card
    assert revise(store, item_id, req_id) == (event, False)
    assert store.get_item(item_id) == after
    with pytest.raises(WorkRecordStaleRun):
        store.record_result_if_current_run(item_id, req_id, run_id=run_id, result_id="late", status="completed",
            summary="late", verification={"status":"passed"}, sources=[])
    with pytest.raises(WorkRecordConflict):
        revise(store, item_id, req_id, new="other", key="revision-1")


def test_revision_rejects_running_unknown_and_wrong_expectation(tmp_path):
    _, store, item_id, req_id = fixture(tmp_path)
    with pytest.raises(WorkRecordConflict):
        revise(store, item_id, req_id, old="wrong")
    store.begin_requirement_run(item_id, req_id, run_id="run-1", expected_current_run_id=None)
    before = store.get_item(item_id)
    with pytest.raises(WorkRecordConflict, match="coordination"):
        revise(store, item_id, req_id)
    assert store.get_item(item_id) == before
    store.record_result_if_current_run(item_id, req_id, run_id="run-1", result_id="unknown-1", status="unknown",
                                       summary="unverified", verification={"status":"not_verified"}, sources=[])
    with pytest.raises(WorkRecordConflict, match="coordination"):
        revise(store, item_id, req_id)


def test_concurrent_revision_cas_allows_one_writer(tmp_path):
    session, store, item_id, req_id = fixture(tmp_path)
    def write(n):
        try:
            return revise(WorkRecordStore(session), item_id, req_id, new=f"Revised {n}", key=f"revision-{n}")[1]
        except WorkRecordConflict:
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(write, (1, 2))) == [False, True]
    item = store.get_item(item_id)
    assert len([e for e in item["history"] if e["kind"] == "requirement_revised"]) == 1


def test_revision_rejects_stale_snapshot_and_pending_publication(tmp_path):
    from work_record_settlement import WorkRecordSettlementAdapter, BoundExecution, ExecutionReceipt
    _, store, item_id, req_id = fixture(tmp_path, project=True)
    snapshot = store._read()
    store.add_requirement(item_id, "Another requirement", event_id="additional")
    with pytest.raises(WorkRecordConflict, match="snapshot"):
        store.revise_requirement(item_id, req_id, expected_text="Original requirement", new_text="Revised",
                                 event_id="stale-revision", expected_snapshot=snapshot)
    run = uuid4().hex
    store.begin_requirement_run(item_id, req_id, run_id=run, expected_current_run_id=None)
    binding = BoundExecution(store._session.conversation_id, item_id, req_id, run)
    receipt = ExecutionReceipt(store._session.conversation_id, item_id, req_id, run, 1, "succeeded")
    assert WorkRecordSettlementAdapter(store, binding).settle(receipt, publish_project_update=True).status == "recorded"
    with pytest.raises(WorkRecordConflict, match="coordination"):
        revise(store, item_id, req_id)


def test_revision_rejects_worker_batch_lineage_and_preserves_pending_state(tmp_path):
    from agent import _create_current_dialogue_worker_records
    from project_worker_host import ProjectWorkerHostRequest
    session, _, _, _ = fixture(tmp_path)
    request = ProjectWorkerHostRequest(project_id="project-a", task_id="task-1", task="Batch task",
                                       read_targets=(), write_targets=("out.txt",))
    store, item_id, _, rows, _, _ = _create_current_dialogue_worker_records(
        session, "Track batch", [request], parent_run_id="parent-run", turn_epoch=1, continue_check=lambda: True)
    row = rows[0]
    before = store.get_item(item_id)
    with pytest.raises(WorkRecordConflict, match="coordination"):
        revise(store, item_id, row["requirement_id"], old="Batch task")
    assert store.get_item(item_id) == before

def test_revised_requirement_blocks_unversioned_or_stale_dispatch_under_store_lock(tmp_path):
    _, store, item_id, req_id = fixture(tmp_path)
    # Use an independent record: project records are separately guarded below.
    item = store.create_item(title="Independent", scope={"kind": "independent"})
    req = store.add_requirement(item["item_id"], "Old task", event_id="independent-old")
    store.revise_requirement(item["item_id"], req["requirement_id"], expected_text="Old task", new_text="New task",
                             event_id="independent-revision")
    with pytest.raises(WorkRecordConflict):
        store.begin_requirement_run(item["item_id"], req["requirement_id"], run_id="old-unversioned", expected_current_run_id=None)
    with pytest.raises(WorkRecordConflict):
        store.begin_requirement_run(item["item_id"], req["requirement_id"], run_id="old-task", expected_current_run_id=None,
                                    expected_requirement_text="Old task")
    started = store.begin_requirement_run(item["item_id"], req["requirement_id"], run_id="new-task", expected_current_run_id=None,
                                          expected_requirement_text="New task")
    assert started["status"] == "in_progress" and started["text"] == "New task"


def test_project_record_revision_denied_without_cross_session_dispatch_coordination(tmp_path):
    _, store, item_id, req_id = fixture(tmp_path, project=True)
    before = store.get_item(item_id)
    with pytest.raises(WorkRecordConflict, match="coordination"):
        revise(store, item_id, req_id)
    assert store.get_item(item_id) == before


def test_revision_and_old_dispatch_race_are_serialized_by_current_requirement_cas(tmp_path):
    import threading
    session, store, item_id, req_id = fixture(tmp_path)
    gate = threading.Barrier(2)
    def revise_first():
        gate.wait()
        try:
            return "revised" if revise(WorkRecordStore(session), item_id, req_id)[1] else "replayed"
        except WorkRecordConflict:
            return "revision_rejected"
    def start_old():
        gate.wait()
        try:
            WorkRecordStore(session).begin_requirement_run(item_id, req_id, run_id="reserved-old",
                expected_current_run_id=None, expected_requirement_text="Original requirement")
            return "started_old"
        except WorkRecordConflict:
            return "dispatch_rejected"
    with ThreadPoolExecutor(max_workers=2) as pool:
        one = pool.submit(revise_first); two = pool.submit(start_old)
        outcomes = {one.result(), two.result()}
    assert outcomes in ({"revised", "dispatch_rejected"}, {"revision_rejected", "started_old"})
    requirement = store.get_item(item_id)["requirements"][0]
    if "revised" in outcomes:
        assert requirement["text"] == "Revised requirement" and requirement.get("current_run_id") is None
    else:
        assert requirement["text"] == "Original requirement" and requirement["current_run_id"] == "reserved-old"


def test_cross_session_binding_cannot_silently_revise_project_record_after_reservation(tmp_path):
    from tests.test_multi_project_work_record_bindings import _fixture as binding_fixture
    from multi_project_work_record_bindings import ConversationWorkRecordBindings
    parent, sources, tasks = binding_fixture(tmp_path)
    rows = ConversationWorkRecordBindings(parent).bind_existing_tasks(
        batch_id="revision-race", tasks=tasks, authorized_sources=sources)
    # The exact project source Session is recreated under the same temporary root.
    from tests.test_multi_project_work_record_bindings import _session
    source_store = WorkRecordStore(_session(tmp_path, "project-a-session", "project-a"))
    before = source_store.get_item(tasks[0].item_id)
    with pytest.raises(WorkRecordConflict, match="coordination"):
        source_store.revise_requirement(tasks[0].item_id, tasks[0].requirement_id,
            expected_text="A requirement", new_text="Changed requirement", event_id="cross-session-revision")
    assert source_store.get_item(tasks[0].item_id) == before
    assert rows[0]["requirement_text"] == "A requirement"

def test_distinct_parent_bindings_show_why_cross_session_project_revision_stays_denied(tmp_path):
    from tests.test_multi_project_work_record_bindings import _fixture as binding_fixture, _session
    from multi_project_work_record_bindings import (
        ConversationWorkRecordBindings, ExistingTaskBinding, _issue_authorized_project_record_source,
    )
    parent1, sources, tasks = binding_fixture(tmp_path)
    parent2 = _session(tmp_path, "another-parent", "project-a")
    first = ConversationWorkRecordBindings(parent1).bind_existing_tasks(
        batch_id="parent-one", tasks=[tasks[0]], authorized_sources={"project-a": sources["project-a"]})[0]
    other_source = _issue_authorized_project_record_source(
        "second-grant", parent2.conversation_id, "project-a", sources["project-a"].store)
    other_task = ExistingTaskBinding("project-a", tasks[0].item_id, tasks[0].requirement_id,
                                     "other-task", tasks[0].task_input, "other-worker", "other-run")
    second = ConversationWorkRecordBindings(parent2).bind_existing_tasks(
        batch_id="parent-two", tasks=[other_task], authorized_sources={"project-a": other_source})[0]
    assert (first["source_session_id"], first["item_id"], first["requirement_id"]) == (
        second["source_session_id"], second["item_id"], second["requirement_id"])
    store = WorkRecordStore(_session(tmp_path, "project-a-session", "project-a"))
    before = store.get_item(tasks[0].item_id)
    with pytest.raises(WorkRecordConflict, match="coordination"):
        store.revise_requirement(tasks[0].item_id, tasks[0].requirement_id, expected_text="A requirement",
                                 new_text="Revised", event_id="cross-parent-revision")
    assert store.get_item(tasks[0].item_id) == before
