from __future__ import annotations

import json
import multiprocessing
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from paths import AgentPaths
from session import Session, SessionMeta
from work_records import WorkRecordConflict, WorkRecordCorrupt, WorkRecordScopeError, WorkRecordStore, WorkRecordWriteError


def _session(session_dir: Path, conversation_id: str, project_id: str = "") -> Session:
    root = session_dir.parents[2]
    session_dir.mkdir(parents=True, exist_ok=True)
    paths = AgentPaths(agent_root=root, workspace=root / "workspace", data=root / "data", evolve=root / "evolve")
    assert session_dir == paths.data / "sessions" / conversation_id
    return Session(conversation_id=conversation_id, session_dir=session_dir, goal="", meta=SessionMeta(project_id=project_id), messages=[], paths=paths)


def _create_item(session_dir: str, conversation_id: str, title: str, start, queue) -> None:
    try:
        store = WorkRecordStore(_session(Path(session_dir), conversation_id), lock_timeout=5)
        start.wait(5)
        queue.put(store.create_item(title=title, scope={"kind": "independent"})["item_id"])
    except BaseException as exc:
        queue.put(("error", repr(exc)))


def _schedule_claim_process(session_dir: str, conversation_id: str, project_id: str,
                            item_id: str, requirement_id: str, revision: int,
                            owner_id: str, reservation_id: str, start, queue) -> None:
    try:
        store = WorkRecordStore(_session(Path(session_dir), conversation_id, project_id), lock_timeout=5)
        start.wait(5)
        claim = store.claim_scheduled_requirement(
            project_id, item_id, requirement_id, expected_revision=revision,
            owner_id=owner_id, reservation_id=reservation_id, parent_run_id="parent-run",
        )
        queue.put(("ok", claim["owner_id"]))
    except WorkRecordConflict:
        queue.put(("conflict", owner_id))
    except BaseException as exc:
        queue.put(("error", repr(exc)))


def _copy_requirement_process(session_dir: str, conversation_id: str, item_id: str,
                              requirement_id: str, revision: int, start, queue) -> None:
    try:
        store = WorkRecordStore(_session(Path(session_dir), conversation_id), lock_timeout=5)
        start.wait(5)
        copied = store.copy_requirement_to_new_item(
            item_id, requirement_id, expected_revision=revision,
            event_id="concurrent-copy-event", idempotency_key="concurrent-copy-key",
        )
        queue.put(("ok", copied["item_id"]))
    except BaseException as exc:
        queue.put(("error", repr(exc)))


def test_persistent_schedule_claim_is_idempotent_and_blocks_a_different_owner(tmp_path):
    session = _session(tmp_path / "data/sessions/session-a", "session-a", "p-1")
    store = WorkRecordStore(session)
    item = store.create_item(title="scheduled", scope={"kind": "project", "project_id": "p-1"})
    requirement = store.add_requirement(item["item_id"], "synthetic requirement", event_id="schedule-req")
    revision = store.with_locked_data(lambda data: data["revision"])
    kwargs = dict(expected_revision=revision, owner_id="owner-1", reservation_id="reservation-1",
                  parent_run_id="parent-run")
    with pytest.raises(WorkRecordConflict, match="revision changed"):
        store.claim_scheduled_requirement("p-1", item["item_id"], requirement["requirement_id"],
            **{**kwargs, "expected_revision": revision + 1})
    with pytest.raises(WorkRecordScopeError, match="does not match"):
        store.claim_scheduled_requirement("other-project", item["item_id"], requirement["requirement_id"],
            **kwargs)
    first = store.claim_scheduled_requirement("p-1", item["item_id"], requirement["requirement_id"], **kwargs)
    reconstructed = WorkRecordStore(_session(session.session_dir, "session-a", "p-1"))
    assert reconstructed.get_item(item["item_id"])["requirements"][0]["schedule_claim"] == first
    assert store.claim_scheduled_requirement("p-1", item["item_id"], requirement["requirement_id"], **kwargs) == first
    with pytest.raises(WorkRecordConflict, match="persistent claim"):
        store.claim_scheduled_requirement("p-1", item["item_id"], requirement["requirement_id"],
            expected_revision=revision, owner_id="owner-2", reservation_id="reservation-2",
            parent_run_id="parent-run")
    unknown = store.mark_schedule_claim_unknown(
        item["item_id"], requirement["requirement_id"], owner_id="owner-1",
        reservation_id="reservation-1", parent_run_id="parent-run",
    )
    assert unknown["state"] == "unknown"
    with pytest.raises(WorkRecordConflict, match="persistent claim"):
        store.claim_scheduled_requirement("p-1", item["item_id"], requirement["requirement_id"],
            expected_revision=revision, owner_id="owner-1", reservation_id="reservation-1",
            parent_run_id="parent-run")
    with pytest.raises(WorkRecordConflict, match="persistent schedule claim"):
        store.begin_requirement_run(item["item_id"], requirement["requirement_id"],
            run_id="bypass-run", expected_current_run_id=None,
            expected_requirement_text="synthetic requirement")
    with pytest.raises(WorkRecordConflict, match="persistent schedule claim"):
        store.revise_requirement(item["item_id"], requirement["requirement_id"],
            expected_text="synthetic requirement", new_text="changed requirement",
            event_id="claimed-revision", idempotency_key="claimed-revision-key", allow_project_scope=True)
    assert store.get_item(item["item_id"])["requirements"][0]["text"] == "synthetic requirement"


def test_persistent_schedule_claim_is_exclusive_across_processes(tmp_path):
    context = multiprocessing.get_context("spawn")
    session_dir = tmp_path / "data/sessions/session-claim"
    store = WorkRecordStore(_session(session_dir, "session-claim", "p-claim"), lock_timeout=5)
    item = store.create_item(title="claim race", scope={"kind": "project", "project_id": "p-claim"})
    requirement = store.add_requirement(item["item_id"], "synthetic race", event_id="claim-race-req")
    revision = store.with_locked_data(lambda data: data["revision"])
    start, queue = context.Event(), context.Queue()
    workers = [context.Process(target=_schedule_claim_process, args=(
        str(session_dir), "session-claim", "p-claim", item["item_id"], requirement["requirement_id"],
        revision, f"owner-{index}", f"reservation-{index}", start, queue,
    )) for index in range(2)]
    for worker in workers:
        worker.start()
    start.set()
    results = [queue.get(timeout=10) for _ in workers]
    for worker in workers:
        worker.join(10)
        assert worker.exitcode == 0
    assert sorted(result[0] for result in results) == ["conflict", "ok"]


def test_project_and_independent_items_keep_distinct_identity_and_scope(tmp_path):
    store = WorkRecordStore(_session(tmp_path / "data/sessions/session-a", "session-a", "p-1"))
    project = store.create_item(title="登录页", scope={"kind": "project", "project_id": "p-1"})
    independent = store.create_item(title="简历摘要", scope={"kind": "independent"})
    assert project["item_id"] != independent["item_id"]
    assert project["scope"] == {"kind": "project", "project_id": "p-1"}
    assert independent["scope"] == {"kind": "independent", "object_ref": None}
    assert store.get_item(project["item_id"])["scope"]["kind"] == "project"


def test_cross_item_dependencies_require_exact_scope_cycle_check_and_gate_start(tmp_path):
    store = WorkRecordStore(_session(tmp_path / "data/sessions/session-a", "session-a", "p-1"))
    prerequisite_item = store.create_item(title="prerequisite", scope={"kind": "project", "project_id": "p-1"})
    dependent_item = store.create_item(title="dependent", scope={"kind": "project", "project_id": "p-1"})
    wrong_scope = store.create_item(title="wrong scope", scope={"kind": "independent"})
    prerequisite = store.add_requirement(prerequisite_item["item_id"], "finish prerequisite", event_id="cross-req-a")
    dependent = store.add_requirement(dependent_item["item_id"], "dependent work", event_id="cross-req-b")
    wrong = store.add_requirement(wrong_scope["item_id"], "wrong scope", event_id="cross-req-c")
    ref = {"item_id": prerequisite_item["item_id"], "requirement_id": prerequisite["requirement_id"]}
    with pytest.raises(WorkRecordConflict, match="exactly matching scope"):
        store.set_requirement_control(dependent_item["item_id"], dependent["requirement_id"],
            action="set_dependencies", expected_status="pending", event_id="cross-wrong-scope",
            idempotency_key="cross-wrong-scope-key", depends_on_refs=[
                {"item_id": wrong_scope["item_id"], "requirement_id": wrong["requirement_id"]}])
    store.set_requirement_control(dependent_item["item_id"], dependent["requirement_id"],
        action="set_dependencies", expected_status="pending", event_id="cross-add-edge",
        idempotency_key="cross-add-edge-key", depends_on_refs=[ref])
    with pytest.raises(WorkRecordConflict, match="cycle"):
        store.set_requirement_control(prerequisite_item["item_id"], prerequisite["requirement_id"],
            action="set_dependencies", expected_status="pending", event_id="cross-cycle",
            idempotency_key="cross-cycle-key", depends_on_refs=[
                {"item_id": dependent_item["item_id"], "requirement_id": dependent["requirement_id"]}])
    with pytest.raises(WorkRecordConflict, match="not all completed"):
        store.begin_requirement_run(dependent_item["item_id"], dependent["requirement_id"],
            run_id="blocked-cross-run", expected_current_run_id=None)
    audit = store.get_item(dependent_item["item_id"])["history"][-1]
    assert audit["kind"] == "requirement_control"
    assert audit["payload"]["depends_on_refs"] == [ref]


def test_mixed_legacy_and_cross_item_edges_cannot_create_cycle(tmp_path):
    store = WorkRecordStore(_session(tmp_path / "data/sessions/session-a", "session-a", "p-1"))
    first_item = store.create_item(title="first", scope={"kind": "project", "project_id": "p-1"})
    second_item = store.create_item(title="second", scope={"kind": "project", "project_id": "p-1"})
    first = store.add_requirement(first_item["item_id"], "first", event_id="mixed-first")
    second = store.add_requirement(second_item["item_id"], "second", event_id="mixed-second")

    # Existing same-item dependency followed by a cross-item edge back into it.
    store.add_requirement(first_item["item_id"], "first follow-up", event_id="mixed-followup")
    followup = store.get_item(first_item["item_id"])["requirements"][1]
    store.set_requirement_control(
        first_item["item_id"], followup["requirement_id"], action="set_dependencies",
        expected_status="pending", event_id="mixed-legacy-edge", idempotency_key="mixed-legacy-key",
        depends_on=[first["requirement_id"]],
    )
    store.set_requirement_control(
        second_item["item_id"], second["requirement_id"], action="set_dependencies",
        expected_status="pending", event_id="mixed-cross-edge", idempotency_key="mixed-cross-key",
        depends_on_refs=[{"item_id": first_item["item_id"], "requirement_id": followup["requirement_id"]}],
    )

    with pytest.raises(WorkRecordConflict, match="cycle"):
        store.set_requirement_control(
            first_item["item_id"], first["requirement_id"], action="set_dependencies",
            expected_status="pending", event_id="mixed-closing-edge", idempotency_key="mixed-closing-key",
            depends_on_refs=[{"item_id": second_item["item_id"], "requirement_id": second["requirement_id"]}],
        )
    assert store.get_item(first_item["item_id"])["requirements"][0].get("depends_on_refs", []) == []


def test_cross_item_dependency_control_replay_is_idempotent_and_payload_bound(tmp_path):
    store = WorkRecordStore(_session(tmp_path / "data/sessions/session-a", "session-a", "p-1"))
    upstream_item = store.create_item(title="upstream", scope={"kind": "project", "project_id": "p-1"})
    downstream_item = store.create_item(title="downstream", scope={"kind": "project", "project_id": "p-1"})
    upstream = store.add_requirement(upstream_item["item_id"], "upstream", event_id="idem-upstream")
    downstream = store.add_requirement(downstream_item["item_id"], "downstream", event_id="idem-downstream")
    ref = {"item_id": upstream_item["item_id"], "requirement_id": upstream["requirement_id"]}
    args = dict(action="set_dependencies", expected_status="pending", event_id="idem-edge",
                idempotency_key="idem-edge-key", depends_on_refs=[ref])

    first = store.set_requirement_control(downstream_item["item_id"], downstream["requirement_id"], **args)
    revision = store.with_locked_data(lambda data: data["revision"])
    assert store.set_requirement_control(downstream_item["item_id"], downstream["requirement_id"], **args) == first
    assert store.with_locked_data(lambda data: data["revision"]) == revision
    with pytest.raises(WorkRecordConflict, match="different operation"):
        store.set_requirement_control(
            downstream_item["item_id"], downstream["requirement_id"], **{
                **args, "depends_on_refs": [],
            },
        )


def test_concurrent_opposite_dependency_updates_cannot_publish_a_cycle(tmp_path):
    import threading

    session_dir = tmp_path / "data/sessions/session-a"
    session = _session(session_dir, "session-a", "p-1")
    store = WorkRecordStore(session)
    first_item = store.create_item(title="first", scope={"kind": "project", "project_id": "p-1"})
    second_item = store.create_item(title="second", scope={"kind": "project", "project_id": "p-1"})
    first = store.add_requirement(first_item["item_id"], "first", event_id="race-first")
    second = store.add_requirement(second_item["item_id"], "second", event_id="race-second")
    start = threading.Barrier(2)
    outcomes = []
    outcome_lock = threading.Lock()

    def attach(source_item, source_req, target_item, target_req, suffix):
        local_store = WorkRecordStore(session)
        start.wait(timeout=5)
        try:
            local_store.set_requirement_control(
                source_item["item_id"], source_req["requirement_id"], action="set_dependencies",
                expected_status="pending", event_id=f"race-{suffix}", idempotency_key=f"race-{suffix}-key",
                depends_on_refs=[{"item_id": target_item["item_id"], "requirement_id": target_req["requirement_id"]}],
            )
            outcome = "committed"
        except WorkRecordConflict:
            outcome = "cycle-rejected"
        with outcome_lock:
            outcomes.append(outcome)

    threads = [
        threading.Thread(target=attach, args=(first_item, first, second_item, second, "forward")),
        threading.Thread(target=attach, args=(second_item, second, first_item, first, "reverse")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
        assert not thread.is_alive()

    assert sorted(outcomes) == ["committed", "cycle-rejected"]
    saved = store.list_items()
    assert sum(bool(req.get("depends_on_refs")) for item in saved for req in item["requirements"]) == 1


def test_corrupt_cross_item_dependency_scope_mismatch_fails_closed(tmp_path):
    session_dir = tmp_path / "data/sessions/session-a"
    session = _session(session_dir, "session-a", "p-1")
    store = WorkRecordStore(session)
    upstream = store.create_item(title="upstream", scope={"kind": "project", "project_id": "p-1"})
    downstream = store.create_item(title="downstream", scope={"kind": "project", "project_id": "p-1"})
    source = store.add_requirement(upstream["item_id"], "source", event_id="scope-source")
    target = store.add_requirement(downstream["item_id"], "target", event_id="scope-target")
    store.set_requirement_control(
        downstream["item_id"], target["requirement_id"], action="set_dependencies",
        expected_status="pending", event_id="scope-edge", idempotency_key="scope-edge-key",
        depends_on_refs=[{"item_id": upstream["item_id"], "requirement_id": source["requirement_id"]}],
    )
    path = session_dir / "work-records.v1.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["items"][0]["scope"] = {"kind": "independent", "object_ref": None}
    corrupted_bytes = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
    path.write_bytes(corrupted_bytes)

    with pytest.raises(WorkRecordScopeError, match="exactly matching scope"):
        store.list_items()
    assert path.read_bytes() == corrupted_bytes


def test_corrupt_cross_item_dependency_fails_closed_on_read_and_startup_recovery(tmp_path):
    session_dir = tmp_path / "data/sessions/session-a"
    session = _session(session_dir, "session-a", "p-1")
    store = WorkRecordStore(session)
    upstream = store.create_item(title="upstream", scope={"kind": "project", "project_id": "p-1"})
    downstream = store.create_item(title="downstream", scope={"kind": "project", "project_id": "p-1"})
    source = store.add_requirement(upstream["item_id"], "source", event_id="corrupt-source")
    target = store.add_requirement(downstream["item_id"], "target", event_id="corrupt-target")
    running = store.add_requirement(upstream["item_id"], "running", event_id="corrupt-running")
    store.begin_requirement_run(upstream["item_id"], running["requirement_id"],
                                run_id="startup-must-not-recover", expected_current_run_id=None)
    store.set_requirement_control(
        downstream["item_id"], target["requirement_id"], action="set_dependencies",
        expected_status="pending", event_id="corrupt-edge", idempotency_key="corrupt-edge-key",
        depends_on_refs=[{"item_id": upstream["item_id"], "requirement_id": source["requirement_id"]}],
    )
    path = session_dir / "work-records.v1.json"
    original = json.loads(path.read_text(encoding="utf-8"))
    broken = json.loads(json.dumps(original))
    broken["items"][1]["requirements"][0]["depends_on_refs"][0]["requirement_id"] = "missing"
    corrupted_bytes = (json.dumps(broken, ensure_ascii=False) + "\n").encode("utf-8")
    path.write_bytes(corrupted_bytes)

    with pytest.raises(WorkRecordCorrupt, match="missing requirement"):
        store.list_items()
    with pytest.raises(WorkRecordCorrupt, match="missing requirement"):
        store.mark_interrupted_runs_unknown(event_id_prefix="conservative-startup", current_owner_id="old-process")
    assert path.read_bytes() == corrupted_bytes


def test_copy_requirement_to_new_item_is_pending_audited_and_preserves_source(tmp_path):
    store = WorkRecordStore(_session(tmp_path / "data/sessions/session-a", "session-a"))
    source = store.create_item(title="original", scope={"kind": "independent"})
    requirement = store.add_requirement(source["item_id"], "original request", event_id="source-req")
    store.add_requirement(source["item_id"], "dependent follow-up", event_id="follow-up-req")
    store.set_requirement_control(
        source["item_id"], store.get_item(source["item_id"])["requirements"][1]["requirement_id"],
        action="set_dependencies", expected_status="pending", event_id="follow-up-dependency",
        idempotency_key="follow-up-dependency-key", depends_on=[requirement["requirement_id"]],
    )
    before = store.with_locked_data(lambda data: data)

    copied = store.copy_requirement_to_new_item(
        source["item_id"], requirement["requirement_id"],
        expected_revision=before["revision"], event_id="copy-event", idempotency_key="copy-key",
    )

    assert copied["item_id"] != source["item_id"]
    assert copied["scope"] == {"kind": "independent", "object_ref": None}
    assert copied["requirements"] == [{
        "requirement_id": copied["requirements"][0]["requirement_id"],
        "text": "original request", "status": "pending",
    }]
    assert copied["runs"] == []
    event = copied["history"][-1]
    assert event["kind"] == "requirement_copied_to_new_item"
    assert event["payload"]["source_session_id"] == "session-a"
    assert event["payload"]["source_item_id"] == source["item_id"]
    assert event["payload"]["source_requirement_id"] == requirement["requirement_id"]
    assert len(event["payload"]["source_text_digest"]) == 64
    assert store.with_locked_data(lambda data: data)["items"][0] == before["items"][0]

    replay = store.copy_requirement_to_new_item(
        source["item_id"], requirement["requirement_id"],
        expected_revision=before["revision"], event_id="copy-event", idempotency_key="copy-key",
    )
    assert replay == copied
    assert len(store.list_items()) == 2


def test_concurrent_copy_replays_publish_exactly_one_new_item(tmp_path):
    session_dir = tmp_path / "data/sessions/session-a"
    store = WorkRecordStore(_session(session_dir, "session-a"))
    source = store.create_item(title="original", scope={"kind": "independent"})
    requirement = store.add_requirement(source["item_id"], "original request", event_id="source-req")
    revision = store.with_locked_data(lambda data: data["revision"])
    ctx = multiprocessing.get_context("spawn")
    start, queue = ctx.Barrier(2), ctx.Queue()
    processes = [ctx.Process(
        target=_copy_requirement_process,
        args=(str(session_dir), "session-a", source["item_id"], requirement["requirement_id"], revision, start, queue),
    ) for _ in range(2)]
    for process in processes:
        process.start()
    for process in processes:
        process.join(15)
        assert process.exitcode == 0
    results = [queue.get(timeout=2) for _ in processes]

    assert [result[0] for result in results] == ["ok", "ok"]
    assert results[0][1] == results[1][1]
    assert len(store.list_items()) == 2


def test_copy_requirement_idempotency_key_and_event_id_cannot_alias_other_requests(tmp_path):
    store = WorkRecordStore(_session(tmp_path / "data/sessions/session-a", "session-a"))
    first = store.create_item(title="first", scope={"kind": "independent"})
    first_req = store.add_requirement(first["item_id"], "first request", event_id="first-req")
    second = store.create_item(title="second", scope={"kind": "independent"})
    second_req = store.add_requirement(second["item_id"], "second request", event_id="second-req")

    def copy(source, req, *, event_id, key):
        return store.copy_requirement_to_new_item(
            source["item_id"], req["requirement_id"],
            expected_revision=store.with_locked_data(lambda data: data["revision"]),
            event_id=event_id, idempotency_key=key,
        )

    copy(first, first_req, event_id="copy-event-one", key="copy-key-one")
    with pytest.raises(WorkRecordConflict):
        copy(second, second_req, event_id="copy-event-two", key="copy-key-one")
    with pytest.raises(WorkRecordConflict):
        copy(second, second_req, event_id="copy-event-one", key="copy-key-two")
    assert len(store.list_items()) == 3


def test_copy_requirement_replay_requires_same_event_and_source_identity(tmp_path):
    store = WorkRecordStore(_session(tmp_path / "data/sessions/session-a", "session-a"))
    source = store.create_item(title="original", scope={"kind": "independent"})
    requirement = store.add_requirement(source["item_id"], "original request", event_id="source-req")
    revision = store.with_locked_data(lambda data: data["revision"])
    copied = store.copy_requirement_to_new_item(
        source["item_id"], requirement["requirement_id"], expected_revision=revision,
        event_id="copy-event", idempotency_key="copy-key",
    )

    with pytest.raises(WorkRecordConflict):
        store.copy_requirement_to_new_item(
            source["item_id"], requirement["requirement_id"], expected_revision=revision,
            event_id="different-event", idempotency_key="copy-key",
        )
    assert len(store.list_items()) == 2
    assert store.get_item(copied["item_id"])["requirements"][0]["text"] == "original request"


def test_copy_requirement_revision_race_conflicts_without_copy(tmp_path):
    store = WorkRecordStore(_session(tmp_path / "data/sessions/session-a", "session-a"))
    source = store.create_item(title="original", scope={"kind": "independent"})
    requirement = store.add_requirement(source["item_id"], "original request", event_id="source-req")
    revision = store.with_locked_data(lambda data: data["revision"])
    store.add_requirement(source["item_id"], "concurrent update", event_id="concurrent-req")

    with pytest.raises(WorkRecordConflict):
        store.copy_requirement_to_new_item(
            source["item_id"], requirement["requirement_id"], expected_revision=revision,
            event_id="copy-race", idempotency_key="copy-race-key",
        )
    assert len(store.list_items()) == 1


def test_failed_copy_publish_leaves_source_and_revision_unchanged(tmp_path, monkeypatch):
    session_dir = tmp_path / "data/sessions/session-a"
    store = WorkRecordStore(_session(session_dir, "session-a"))
    source = store.create_item(title="original", scope={"kind": "independent"})
    requirement = store.add_requirement(source["item_id"], "original request", event_id="source-req")
    before_bytes = (session_dir / "work-records.v1.json").read_bytes()
    import work_records

    def fail_replace(self, target):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(work_records.os, "replace", fail_replace)
    with pytest.raises(WorkRecordWriteError):
        store.copy_requirement_to_new_item(
            source["item_id"], requirement["requirement_id"],
            expected_revision=store.with_locked_data(lambda data: data["revision"]),
            event_id="copy-failed", idempotency_key="copy-failed-key",
        )

    assert (session_dir / "work-records.v1.json").read_bytes() == before_bytes
    assert len(store.list_items()) == 1


def test_copy_requirement_rejects_wrong_scope_and_invalid_revision_types(tmp_path):
    store = WorkRecordStore(_session(tmp_path / "data/sessions/session-a", "session-a", "project-a"))
    project = store.create_item(title="project", scope={"kind": "project", "project_id": "project-a"})
    requirement = store.add_requirement(project["item_id"], "project request", event_id="project-req")
    revision = store.with_locked_data(lambda data: data["revision"])
    with pytest.raises(WorkRecordConflict):
        store.copy_requirement_to_new_item(
            project["item_id"], requirement["requirement_id"], expected_revision=revision,
            event_id="copy-project", idempotency_key="copy-project-key",
        )
    with pytest.raises(ValueError):
        store.copy_requirement_to_new_item(
            project["item_id"], requirement["requirement_id"], expected_revision=True,
            event_id="copy-bool", idempotency_key="copy-bool-key",
        )
    assert len(store.list_items()) == 1


def test_result_idempotency_and_conflicting_replay_preserve_history(tmp_path):
    store = WorkRecordStore(_session(tmp_path / "data/sessions/session-a", "session-a"))
    item = store.create_item(title="事项", scope={"kind": "independent"})
    req = store.add_requirement(item["item_id"], "只改摘要", event_id="req-event")
    assert store.add_requirement(item["item_id"], "只改摘要", event_id="req-event") == req
    kwargs = dict(run_id="run-1", result_id="result-1", status="completed", summary="已完成", verification={"status": "not_recorded", "summary": ""})
    first = store.record_result(item["item_id"], req["requirement_id"], **kwargs)
    assert store.record_result(item["item_id"], req["requirement_id"], **kwargs) == first
    with pytest.raises(WorkRecordConflict):
        store.record_result(item["item_id"], req["requirement_id"], **{**kwargs, "summary": "不同"})
    loaded = store.get_item(item["item_id"])
    assert len(loaded["runs"]) == 1
    assert [event["event_id"] for event in loaded["history"]][-2:] == ["req-event", "result-1"]


def test_search_and_pending_requirement_are_current_session_only(tmp_path):
    store = WorkRecordStore(_session(tmp_path / "data/sessions/session-a", "session-a", "p-1"))
    project = store.create_item(title="登录页", scope={"kind": "project", "project_id": "p-1"})
    store.add_requirement(project["item_id"], "调整登录页", event_id="req-1")
    resume = store.create_item(title="简历", scope={"kind": "independent"})
    store.add_requirement(resume["item_id"], "精简摘要", event_id="req-2")
    assert {item["item_id"] for item in store.search("登录")} == {project["item_id"]}
    assert store.get_item(resume["item_id"])["requirements"][0]["status"] == "pending"


def test_persistent_queue_dependencies_priority_and_stop_continue_controls(tmp_path):
    store = WorkRecordStore(_session(tmp_path / "data/sessions/session-controls", "session-controls"))
    item = store.create_item(title="受依赖约束的事项", scope={"kind": "independent"})
    first = store.add_requirement(item["item_id"], "先完成基础修改", event_id="control-req-first")
    second = store.add_requirement(item["item_id"], "再执行验收", event_id="control-req-second")

    store.set_requirement_control(
        item["item_id"], second["requirement_id"], action="set_dependencies", expected_status="pending",
        event_id="dependencies-second", idempotency_key="dependencies-second-key",
        depends_on=[first["requirement_id"]],
    )
    store.set_requirement_control(
        item["item_id"], second["requirement_id"], action="queue", expected_status="pending",
        event_id="queue-second-early", idempotency_key="queue-second-early-key",
    )
    assert store.get_item(item["item_id"])["requirements"][1]["status"] == "queued"
    with pytest.raises(WorkRecordConflict, match="dependencies are not all completed"):
        store.begin_requirement_run(item["item_id"], second["requirement_id"],
                                    run_id="second-early", expected_current_run_id=None)
    with pytest.raises(WorkRecordConflict, match="dependency cycle"):
        store.set_requirement_control(
            item["item_id"], first["requirement_id"], action="set_dependencies", expected_status="pending",
            event_id="cycle-first", idempotency_key="cycle-first-key",
            depends_on=[second["requirement_id"]],
        )
    store.record_result(item["item_id"], first["requirement_id"], run_id="first-run",
                        result_id="first-result", status="completed", summary="base done",
                        verification={"status": "not_recorded", "summary": ""})
    assert store.get_item(item["item_id"])["requirements"][1]["status"] == "queued"
    store.begin_requirement_run(item["item_id"], second["requirement_id"],
                                run_id="second-after-dependency", expected_current_run_id=None)
    assert store.get_item(item["item_id"])["requirements"][1]["status"] == "in_progress"
    # Settle the second run safely before exercising direct queue stop/continue on another requirement.
    store.record_result_if_current_run(
        item["item_id"], second["requirement_id"], run_id="second-after-dependency",
        result_id="second-result", status="completed", summary="verification done",
        verification={"status": "not_recorded"},
    )

    third = store.add_requirement(item["item_id"], "额外待执行步骤", event_id="control-req-third")
    store.set_requirement_control(
        item["item_id"], third["requirement_id"], action="stop", expected_status="pending",
        event_id="stop-third", idempotency_key="stop-third-key",
    )
    assert store.get_item(item["item_id"])["requirements"][2]["status"] == "stopped"
    store.set_requirement_control(
        item["item_id"], third["requirement_id"], action="continue", expected_status="stopped",
        event_id="continue-third", idempotency_key="continue-third-key",
    )
    assert store.get_item(item["item_id"])["requirements"][2]["status"] == "pending"
    store.set_item_priority(item["item_id"], 5, event_id="priority-1", idempotency_key="priority-key")
    loaded = store.get_item(item["item_id"])
    assert loaded["priority"] == 5
    assert loaded["requirements"][1]["depends_on"] == [first["requirement_id"]]
    assert [event["kind"] for event in loaded["history"]][-3:] == [
        "requirement_control", "requirement_control", "item_priority_changed",
    ]


def test_requirement_controls_refuse_running_or_unknown_side_effects(tmp_path):
    store = WorkRecordStore(_session(tmp_path / "data/sessions/session-control-guard", "session-control-guard"))
    item = store.create_item(title="运行中的事项", scope={"kind": "independent"})
    req = store.add_requirement(item["item_id"], "修改文件", event_id="guard-req")
    store.begin_requirement_run(item["item_id"], req["requirement_id"], run_id="guard-run",
                                expected_current_run_id=None)
    with pytest.raises(WorkRecordConflict, match="host cancellation"):
        store.set_requirement_control(
            item["item_id"], req["requirement_id"], action="stop", expected_status="in_progress",
            event_id="guard-stop", idempotency_key="guard-stop-key",
        )
    store.mark_interrupted_runs_unknown(event_id_prefix="boot-1", current_owner_id="previous-process")
    assert store.get_item(item["item_id"])["requirements"][0]["status"] == "unknown"
    with pytest.raises(WorkRecordConflict, match="must be inspected"):
        store.set_requirement_control(
            item["item_id"], req["requirement_id"], action="continue", expected_status="unknown",
            event_id="guard-continue", idempotency_key="guard-continue-key",
        )
    item_after = store.get_item(item["item_id"])
    assert any(row["kind"] == "requirement_interrupted" for row in item_after["history"])


def test_restart_recovery_preserves_current_process_runs_and_marks_other_owners_unknown(tmp_path):
    from work_records import _WORK_RECORD_PROCESS_INSTANCE_ID

    store = WorkRecordStore(_session(tmp_path / "data/sessions/session-recovery", "session-recovery"))
    item = store.create_item(title="recovery", scope={"kind": "independent"})
    current = store.add_requirement(item["item_id"], "current process", event_id="recovery-current-req")
    stale = store.add_requirement(item["item_id"], "previous process", event_id="recovery-stale-req")
    store.begin_requirement_run(item["item_id"], current["requirement_id"], run_id="current-process-run",
                                expected_current_run_id=None)
    store.begin_requirement_run(item["item_id"], stale["requirement_id"], run_id="prior-process-run",
                                expected_current_run_id=None)

    changed = store.mark_interrupted_runs_unknown(
        event_id_prefix="restart-identity", current_owner_id=_WORK_RECORD_PROCESS_INSTANCE_ID,
    )
    assert changed == 0
    after_same_process = store.get_item(item["item_id"])
    assert all(row["status"] == "in_progress" for row in after_same_process["requirements"])

    # A simulated new process uses another owner token. Recovery only records
    # uncertainty; it never resumes or retries the stale side effect.
    store._mutate(lambda data: (
        next(row for row in data["items"][0]["requirements"]
             if row["requirement_id"] == stale["requirement_id"]).__setitem__(
                 "execution_owner_id", "previous-process-instance"), True))
    changed = store.mark_interrupted_runs_unknown(
        event_id_prefix="restart-identity-2", current_owner_id=_WORK_RECORD_PROCESS_INSTANCE_ID,
    )
    assert changed == 1
    after_restart = store.get_item(item["item_id"])
    states = {row["requirement_id"]: row["status"] for row in after_restart["requirements"]}
    assert states[current["requirement_id"]] == "in_progress"
    assert states[stale["requirement_id"]] == "unknown"
    assert any(row["kind"] == "requirement_interrupted" for row in after_restart["history"])


def test_corrupt_or_unknown_version_is_never_replaced(tmp_path):
    session_dir = tmp_path / "data/sessions/session-a"
    session_dir.mkdir(parents=True)
    path = session_dir / "work-records.v1.json"
    path.write_bytes(b"{broken")
    store = WorkRecordStore(_session(session_dir, "session-a"))
    with pytest.raises(WorkRecordCorrupt):
        store.create_item(title="不应覆盖", scope={"kind": "independent"})
    assert path.read_bytes() == b"{broken"
    path.write_text(json.dumps({"schema_version": 99}), encoding="utf-8")
    with pytest.raises(WorkRecordCorrupt):
        store.list_items()
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == 99


def test_failed_replace_keeps_previous_snapshot(tmp_path, monkeypatch):
    session_dir = tmp_path / "data/sessions/session-a"
    store = WorkRecordStore(_session(session_dir, "session-a"))
    store.create_item(title="已有", scope={"kind": "independent"})
    path = session_dir / "work-records.v1.json"
    before = path.read_bytes()
    def fail_replace(self, target):
        raise OSError("simulated replace failure")
    import work_records
    monkeypatch.setattr(work_records.os, "replace", fail_replace)
    with pytest.raises(WorkRecordWriteError):
        store.create_item(title="新事项", scope={"kind": "independent"})
    assert path.read_bytes() == before


def test_two_process_writers_do_not_lose_updates(tmp_path):
    session_dir = tmp_path / "data/sessions/session-a"
    WorkRecordStore(_session(session_dir, "session-a")).list_items()
    ctx = multiprocessing.get_context("spawn")
    start, queue = ctx.Event(), ctx.Queue()
    processes = [ctx.Process(target=_create_item, args=(str(session_dir), "session-a", title, start, queue)) for title in ("甲", "乙")]
    for process in processes: process.start()
    start.set()
    results = [queue.get(timeout=15) for _ in processes]
    for process in processes:
        process.join(15)
        assert process.exitcode == 0
    assert all(not (isinstance(result, tuple) and result[0] == "error") for result in results), results
    assert {item["title"] for item in WorkRecordStore(_session(session_dir, "session-a")).list_items()} == {"甲", "乙"}


def test_store_rejects_non_session_and_junction_session_path(tmp_path):
    with pytest.raises(TypeError):
        WorkRecordStore(tmp_path / "outside", "arbitrary")
    session_dir = tmp_path / "data/sessions/session-a"
    session_dir.parent.mkdir(parents=True)
    target = tmp_path / "elsewhere"
    target.mkdir()
    try:
        session_dir.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("Windows symlink privilege unavailable; canonical scope checks remain covered")
    root = tmp_path
    paths = AgentPaths(agent_root=root, workspace=root / "workspace", data=root / "data", evolve=root / "evolve")
    forged = Session(conversation_id="session-a", session_dir=session_dir, goal="", meta=SessionMeta(), messages=[], paths=paths)
    with pytest.raises(WorkRecordScopeError):
        WorkRecordStore(forged)


def test_session_scope_and_project_binding_are_enforced(tmp_path):
    session_dir = tmp_path / "data/sessions/session-a"
    session = _session(session_dir, "session-a", "project-1")
    store = WorkRecordStore(session)
    with pytest.raises(Exception):
        store.create_item(title="错项目", scope={"kind": "project", "project_id": "project-2"})
    with pytest.raises(Exception):
        WorkRecordStore(_session(session_dir, "different-session", "project-1"))


def _completed_item(store):
    item = store.create_item(title="已完成", scope={"kind": "independent"})
    requirement = store.add_requirement(item["item_id"], "要求一", event_id="req-complete")
    store.record_result(
        item["item_id"], requirement["requirement_id"], run_id="run-done", result_id="result-done",
        status="completed", summary="完成", verification={"status": "passed", "summary": "已验证"},
    )
    return store, item, requirement


def test_adding_requirement_reopens_completed_item(tmp_path):
    store, item, _ = _completed_item(WorkRecordStore(_session(tmp_path / "data/sessions/session-a", "session-a")))
    store.add_requirement(item["item_id"], "新增要求", event_id="req-new")
    assert store.get_item(item["item_id"])["status"] == "active"


@pytest.mark.parametrize("result_status", ["blocked", "stopped"])
def test_blocked_or_stopped_result_reopens_completed_item(tmp_path, result_status):
    store, item, requirement = _completed_item(WorkRecordStore(_session(tmp_path / "data/sessions/session-a", "session-a")))
    store.record_result(
        item["item_id"], requirement["requirement_id"], run_id=f"run-{result_status}", result_id=f"result-{result_status}",
        status=result_status, summary="状态变化", verification={"status": "not_recorded", "summary": ""},
    )
    assert store.get_item(item["item_id"])["status"] == result_status


@pytest.mark.parametrize("result_status", ["in_progress", "unknown"])
def test_nonterminal_results_keep_exact_requirement_and_item_status(tmp_path, result_status):
    store, item, requirement = _completed_item(WorkRecordStore(_session(tmp_path / "data/sessions/session-a", "session-a")))
    store.record_result(
        item["item_id"], requirement["requirement_id"], run_id=f"run-{result_status}", result_id=f"result-{result_status}",
        status=result_status, summary="状态未定", verification={"status": "not_recorded", "summary": ""},
    )
    loaded = store.get_item(item["item_id"])
    assert loaded["requirements"][0]["status"] == result_status
    assert loaded["status"] == result_status


@pytest.mark.parametrize("mutate", [
    lambda payload: payload["items"].__setitem__(0, None),
    lambda payload: payload["items"][0].__setitem__("requirements", [None]),
    lambda payload: payload["items"][0].__setitem__("history", [None]),
    lambda payload: payload["items"][0].__setitem__("runs", [{"result_id": "bad"}]),
    lambda payload: payload["items"][0].__setitem__("status", []),
    lambda payload: payload["items"][0]["requirements"][0].__setitem__("status", []),
])
def test_nested_corruption_blocks_writes_without_replacing_snapshot(tmp_path, mutate):
    session_dir = tmp_path / "data/sessions/session-a"
    store = WorkRecordStore(_session(session_dir, "session-a"))
    item = store.create_item(title="基准", scope={"kind": "independent"})
    store.add_requirement(item["item_id"], "结构校验要求", event_id="corruption-fixture-req")
    payload = json.loads((session_dir / "work-records.v1.json").read_text(encoding="utf-8"))
    mutate(payload)
    original = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
    path = session_dir / "work-records.v1.json"
    path.write_bytes(original)
    with pytest.raises(WorkRecordCorrupt):
        store.create_item(title="不能覆盖", scope={"kind": "independent"})
    assert path.read_bytes() == original


def test_session_project_rebinding_hides_records_and_blocks_all_writes(tmp_path):
    session = _session(tmp_path / "data/sessions/session-a", "session-a", "project-original")
    store = WorkRecordStore(session)
    store.create_item(title="original project item", scope={"kind": "project", "project_id": "project-original"})
    session.meta.project_id = "project-attacker"

    with pytest.raises(WorkRecordScopeError):
        store.list_items()
    with pytest.raises(WorkRecordScopeError):
        store.search("original")
    with pytest.raises(WorkRecordScopeError):
        store.get_item("any-id")
    with pytest.raises(WorkRecordScopeError):
        store.create_item(title="cross-project item", scope={"kind": "project", "project_id": "project-attacker"})
    with pytest.raises(WorkRecordScopeError):
        store.create_item(title="independent bypass", scope={"kind": "independent"})


def test_reconstructed_store_rejects_sidecar_from_different_project(tmp_path):
    session_dir = tmp_path / "data/sessions/session-a"
    session = _session(session_dir, "session-a", "project-original")
    store = WorkRecordStore(session)
    store.create_item(title="original project record", scope={"kind": "project", "project_id": "project-original"})

    session.meta.project_id = "project-switched"
    switched_store = WorkRecordStore(session)
    with pytest.raises(WorkRecordScopeError):
        switched_store.list_items()
    with pytest.raises(WorkRecordScopeError):
        switched_store.create_item(title="must not append", scope={"kind": "independent"})

    unbound = _session(session_dir, "session-a")
    with pytest.raises(WorkRecordScopeError):
        WorkRecordStore(unbound).list_items()


def test_unbound_session_can_read_and_write_independent_only_sidecar(tmp_path):
    session = _session(tmp_path / "data/sessions/session-a", "session-a")
    store = WorkRecordStore(session)
    item = store.create_item(title="independent note", scope={"kind": "independent"})
    assert store.list_items()[0]["item_id"] == item["item_id"]


def test_temp_anchor_with_unverified_junction_ancestor_is_rejected(tmp_path, monkeypatch):
    """A process-selected temp directory is not by itself a trusted authority."""
    import os
    import subprocess
    import work_records

    if os.name != "nt":
        pytest.skip("native Windows junction validation requires Windows")
    target = tmp_path / "temp-parent-target"
    target.mkdir()
    alias = tmp_path / "temp-parent-alias"
    result = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(alias), str(target)],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        pytest.skip("Windows junction creation is unavailable")
    try:
        if not getattr(alias, "is_junction", lambda: False)():
            pytest.skip("junction cannot be verified on this Python")
        temp_anchor = alias / "system-temp"
        temp_anchor.mkdir()
        monkeypatch.setattr(work_records.tempfile, "gettempdir", lambda: str(temp_anchor))
        session_dir = temp_anchor / "agent" / "data" / "sessions" / "session-a"
        with pytest.raises(WorkRecordScopeError, match="link or reparse point"):
            WorkRecordStore(_session(session_dir, "session-a"))
    finally:
        if getattr(alias, "is_junction", lambda: False)():
            os.rmdir(alias)


@pytest.mark.parametrize("operation", ["read", "write"])
def test_replaced_session_junction_rejected_before_creating_lock(tmp_path, operation):
    import os
    import subprocess

    if os.name != "nt":
        pytest.skip("native Windows junction validation requires Windows")
    session_dir = tmp_path / "agent" / "data" / "sessions" / "session-a"
    store = WorkRecordStore(_session(session_dir, "session-a"))
    saved = session_dir.with_name("session-a-original")
    target = session_dir.with_name("session-a-target")
    temp_root = tmp_path.resolve(strict=True)
    assert session_dir.resolve(strict=True).is_relative_to(temp_root)
    assert saved.parent.resolve(strict=True).is_relative_to(temp_root)
    assert not saved.exists()
    target.mkdir()
    assert target.resolve(strict=True).is_relative_to(temp_root)
    session_dir.rename(saved)
    result = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(session_dir), str(target)],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        pytest.skip("Windows junction creation is unavailable")
    try:
        if not getattr(session_dir, "is_junction", lambda: False)():
            pytest.skip("junction cannot be verified on this Python")
        with pytest.raises(WorkRecordScopeError):
            if operation == "read":
                store.list_items()
            else:
                store.create_item(title="must not write", scope={"kind": "independent"})
        assert not (target / ".work-records.lock").exists()
    finally:
        if getattr(session_dir, "is_junction", lambda: False)():
            os.rmdir(session_dir)


def test_non_temp_root_checks_ancestors_up_to_filesystem_anchor(tmp_path, monkeypatch):
    import os
    import subprocess
    import work_records

    if os.name != "nt":
        pytest.skip("native Windows junction validation requires Windows")
    configured_temp = tmp_path / "separate-configured-temp"
    configured_temp.mkdir()
    target = tmp_path / "agent-parent-target"
    (target / "agent" / "data" / "sessions" / "session-a").mkdir(parents=True)
    alias = tmp_path / "agent-parent-alias"
    result = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(alias), str(target)],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        pytest.skip("Windows junction creation is unavailable")
    try:
        if not getattr(alias, "is_junction", lambda: False)():
            pytest.skip("junction cannot be verified on this Python")
        monkeypatch.setattr(work_records.tempfile, "gettempdir", lambda: str(configured_temp))
        session = _session(alias / "agent" / "data" / "sessions" / "session-a", "session-a")
        with pytest.raises(WorkRecordScopeError, match="link or reparse point"):
            WorkRecordStore(session)
    finally:
        if getattr(alias, "is_junction", lambda: False)():
            os.rmdir(alias)

@pytest.mark.parametrize("source", [
    {"kind": "project", "id": "project-a", "path": "C:/private/file.txt"},
    {"kind": "filesystem_path", "id": "C:/private/file.txt"},
    {"kind": "unknown", "id": "source-a"},
    {"kind": "project", "id": "../outside"},
    {"kind": "project", "id": "secret with spaces"},
])
def test_source_reference_schema_rejects_paths_unknown_fields_types_and_ids(tmp_path, source):
    store = WorkRecordStore(_session(tmp_path / "data/sessions/session-a", "session-a", "project-a"))
    with pytest.raises(ValueError):
        store.create_item(title="invalid reference", scope={"kind": "independent"}, source_refs=[source])
    assert store.list_items() == []


def test_existing_nonconforming_source_reference_is_preserved_and_blocks_read_write(tmp_path):
    session_dir = tmp_path / "data/sessions/session-a"
    session = _session(session_dir, "session-a")
    path = session_dir / "work-records.v1.json"
    payload = {
        "schema_version": 1, "conversation_id": "session-a", "revision": 1,
        "items": [{"item_id": "item-a", "scope": {"kind": "independent", "object_ref": None},
                   "title": "preserve", "status": "active", "requirements": [], "runs": [], "history": [],
                   "source_refs": [{"kind": "file", "path": "C:/private/old.txt"}]}],
    }
    original = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    path.write_bytes(original)
    store = WorkRecordStore(session)
    with pytest.raises(WorkRecordCorrupt):
        store.list_items()
    with pytest.raises(WorkRecordCorrupt):
        store.create_item(title="must not clean", scope={"kind": "independent"})
    assert path.read_bytes() == original


def test_requirement_and_result_references_use_same_strict_contract(tmp_path):
    store = WorkRecordStore(_session(tmp_path / "data/sessions/session-a", "session-a"))
    item = store.create_item(title="empty references are allowed", scope={"kind": "independent"}, source_refs=[])
    req = store.add_requirement(item["item_id"], "requirement", event_id="ref-contract")
    with pytest.raises(ValueError):
        store.add_requirement(item["item_id"], "bad ref", event_id="bad-ref", source_ref={"kind": "result", "id": "r1", "excerpt": "raw text"})
    with pytest.raises(ValueError):
        store.record_result(
            item["item_id"], req["requirement_id"], run_id="run", result_id="result", status="unknown",
            summary="not redacted", verification={"status": "not_verified"},
            sources=[{"kind": "result", "id": "r1", "path": "workspace/private.txt"}],
        )
    assert len(store.list_items()) == 1
    assert store.get_item(item["item_id"])["runs"] == []

def test_create_item_with_requirement_publishes_one_revision(tmp_path, monkeypatch):
    store = WorkRecordStore(_session(tmp_path / "data/sessions/session-a", "session-a"))
    publishes = []
    original_publish = store._publish
    def capture_publish(data, **kwargs):
        publishes.append(data["revision"])
        return original_publish(data, **kwargs)
    monkeypatch.setattr(store, "_publish", capture_publish)

    item = store.create_item_with_requirement(
        title="synthetic title", scope={"kind": "independent"},
        requirement="synthetic requirement", event_id="synthetic-event",
    )

    assert len(publishes) == 1
    assert item["title"] == "synthetic title"
    loaded = store.get_item(item["item_id"])
    assert [row["text"] for row in loaded["requirements"]] == ["synthetic requirement"]
    assert [event["kind"] for event in loaded["history"]] == ["created", "requirement_added"]

def test_atomic_create_rejects_changed_expected_snapshot(tmp_path):
    session = _session(tmp_path / "data/sessions/session-a", "session-a")
    store = WorkRecordStore(session)
    expected = store._read()
    store.create_item(title="concurrent item", scope={"kind": "independent"})

    with pytest.raises(WorkRecordConflict):
        store.create_item_with_requirement(
            title="stale candidate", scope={"kind": "independent"},
            requirement="stale requirement", event_id="stale-event", expected_snapshot=expected,
        )

    assert [item["title"] for item in store.list_items()] == ["concurrent item"]
