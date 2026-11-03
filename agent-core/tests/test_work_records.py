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


def test_project_and_independent_items_keep_distinct_identity_and_scope(tmp_path):
    store = WorkRecordStore(_session(tmp_path / "data/sessions/session-a", "session-a", "p-1"))
    project = store.create_item(title="登录页", scope={"kind": "project", "project_id": "p-1"})
    independent = store.create_item(title="简历摘要", scope={"kind": "independent"})
    assert project["item_id"] != independent["item_id"]
    assert project["scope"] == {"kind": "project", "project_id": "p-1"}
    assert independent["scope"] == {"kind": "independent", "object_ref": None}
    assert store.get_item(project["item_id"])["scope"]["kind"] == "project"


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
