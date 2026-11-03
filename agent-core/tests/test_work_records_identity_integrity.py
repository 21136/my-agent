from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

from paths import AgentPaths
from session import Session, SessionMeta
from work_records import WorkRecordCorrupt, WorkRecordStore, _digest


def make_store(tmp_path):
    root = tmp_path / "synthetic-root"
    directory = root / "data" / "sessions" / "synthetic"
    directory.mkdir(parents=True)
    paths = AgentPaths(root, root / "workspace", root / "data", root / "evolve")
    return WorkRecordStore(Session("synthetic", directory, "", SessionMeta(), [], paths))


def payload():
    run = {"run_id": "run-1", "requirement_id": "req-1", "result_id": "result-1",
           "status": "unknown", "result_summary": "synthetic result", "verification": {}, "sources": []}
    return {"schema_version": 1, "conversation_id": "synthetic", "revision": 4, "items": [{
        "item_id": "item-1", "title": "synthetic first", "scope": {"kind": "independent", "object_ref": None},
        "status": "unknown", "requirements": [{"requirement_id": "req-1", "text": "synthetic requirement", "status": "unknown"}],
        "runs": [run], "history": [{"event_id": "result-1", "kind": "result_recorded", "payload": {},
                                     "digest": _digest(run), "at": 1.0}],
    }]}


def corrupt(kind):
    data = payload()
    item = data["items"][0]
    if kind == "duplicate_item":
        duplicate = copy.deepcopy(item)
        duplicate["title"] = "synthetic second"
        data["items"].append(duplicate)
    elif kind == "duplicate_requirement":
        duplicate = copy.deepcopy(item["requirements"][0])
        duplicate["text"] = "synthetic second requirement"
        item["requirements"].append(duplicate)
    elif kind == "duplicate_result":
        duplicate = copy.deepcopy(item["runs"][0])
        duplicate["result_summary"] = "synthetic wrong result"
        item["runs"].insert(0, duplicate)
    elif kind == "duplicate_event":
        duplicate = copy.deepcopy(item["history"][0])
        duplicate["digest"] = "synthetic wrong digest"
        item["history"].insert(0, duplicate)
    elif kind == "orphan_result":
        item["runs"][0]["requirement_id"] = "missing-requirement"
    return data


def write_fixture(store, data):
    raw = (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    store._path.write_bytes(raw)
    return raw


@pytest.mark.parametrize("kind", ["duplicate_item", "duplicate_requirement", "duplicate_result", "duplicate_event", "orphan_result"])
def test_corrupt_identity_rejected_on_read_and_mutation_preserving_bytes(tmp_path, kind):
    store = make_store(tmp_path)
    original = write_fixture(store, corrupt(kind))
    with pytest.raises(WorkRecordCorrupt):
        store.list_items()
    assert store._path.read_bytes() == original
    with pytest.raises(WorkRecordCorrupt):
        store.create_item(title="synthetic attempted mutation", scope={"kind": "independent"})
    assert store._path.read_bytes() == original
    with pytest.raises(WorkRecordCorrupt):
        if kind == "duplicate_item":
            store.add_requirement("item-1", "synthetic added", event_id="new-event", idempotency_key="new-key")
        elif kind == "duplicate_requirement":
            store.begin_requirement_run("item-1", "req-1", run_id="new-run", expected_current_run_id=None)
        else:
            store.record_result("item-1", "req-1", run_id="run-1", result_id="result-1",
                                status="unknown", summary="synthetic result", verification={})
    assert store._path.read_bytes() == original


def test_legacy_id_scope_and_repeated_run_id_are_accepted(tmp_path):
    store = make_store(tmp_path)
    data = payload()
    item = data["items"][0]
    next_run = copy.deepcopy(item["runs"][0])
    next_run["result_id"] = "result-2"
    item["runs"].append(next_run)
    second = copy.deepcopy(item)
    second["item_id"] = "item-2"
    data["items"].append(second)
    original = write_fixture(store, data)
    assert len(store.list_items()) == 2
    assert store._path.read_bytes() == original
