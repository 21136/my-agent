from __future__ import annotations

import json
import multiprocessing
import sys
import threading
from pathlib import Path

CORE = Path(__file__).resolve().parents[1]
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))

from paths import AgentPaths
from session import Session, SessionMeta
from work_records import WorkRecordStore, _digest, _file_lock


def _locked_competing_writer(lock_path: str, records_path: str, acquired, proceed):
    lock = Path(lock_path)
    records = Path(records_path)
    with _file_lock(lock, 4.0):
        acquired.set()
        if not proceed.wait(4.0):
            raise RuntimeError("test writer timed out")
        payload = json.loads(records.read_text(encoding="utf-8"))
        item = payload["items"][0]
        requirement = item["requirements"][0]
        requirement["text"] = "跨进程竞争修改"
        event = next(row for row in item["history"] if row["event_id"] == "race-event")
        event["payload"]["text"] = requirement["text"]
        event["digest"] = _digest({"kind": "requirement_added", "text": requirement["text"],
                                   "source_ref": requirement.get("source_ref")})
        payload["revision"] += 1
        records.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _store(tmp_path):
    paths = AgentPaths(tmp_path, tmp_path / "workspace", tmp_path / "data", tmp_path / "evolve")
    for folder in (paths.workspace, paths.data, paths.evolve, paths.data / "sessions" / "race-session"):
        folder.mkdir(parents=True, exist_ok=True)
    session = Session("race-session", paths.data / "sessions" / "race-session", "synthetic",
                      SessionMeta(project_id="race-project"), [], paths)
    store = WorkRecordStore(session, lock_timeout=4.0)
    item = store.create_item(title="race", scope={"kind": "project", "project_id": "race-project"})
    store.add_requirement(item["item_id"], "原始", event_id="race-event")
    return store


def test_cross_process_source_change_blocks_then_final_snapshot_rejects_and_releases_lock(tmp_path):
    store = _store(tmp_path)
    ctx = multiprocessing.get_context("spawn")
    acquired, proceed = ctx.Event(), ctx.Event()
    process = ctx.Process(target=_locked_competing_writer,
                          args=(str(store._lock_path), str(store._path), acquired, proceed))
    process.start()
    try:
        assert acquired.wait(5.0), "writer process did not acquire the cross-process lock"
        result = {}
        finished = threading.Event()

        def final_check():
            def reject_if_changed(rows):
                if rows[0]["requirements"][0]["text"] != "原始":
                    raise ValueError("final snapshot changed")
                return rows
            try:
                store.with_locked_items(reject_if_changed)
            except ValueError as exc:
                result["rejected"] = str(exc)
            finally:
                finished.set()

        thread = threading.Thread(target=final_check, daemon=True)
        thread.start()
        assert not finished.wait(0.2), "final validation bypassed the competing process lock"
        proceed.set()
        thread.join(5.0)
        assert finished.is_set(), "final validation deadlocked after competing writer released lock"
        process.join(5.0)
        assert process.exitcode == 0
        assert result["rejected"] == "final snapshot changed"
        # A fresh operation proves the lock was released after callback rejection.
        assert store.list_items()[0]["requirements"][0]["text"] == "跨进程竞争修改"
    finally:
        proceed.set()
        if process.is_alive():
            process.terminate()
            process.join(2.0)


def test_locked_items_releases_os_lock_when_callback_raises(tmp_path):
    store = _store(tmp_path)

    def fail(_items):
        raise RuntimeError("synthetic validation refusal")

    try:
        store.with_locked_items(fail)
    except RuntimeError as exc:
        assert str(exc) == "synthetic validation refusal"
    else:
        raise AssertionError("callback exception was swallowed")
    assert store.list_items()[0]["requirements"][0]["text"] == "原始"

