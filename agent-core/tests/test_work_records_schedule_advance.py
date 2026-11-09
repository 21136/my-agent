from __future__ import annotations

import json
import sys
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest

_CORE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_CORE))

from multi_project_work_record_authorization import authorize_project_record_sources
from paths import AgentPaths
from project_worker_host import ProjectWorkerHost
from session import Session, SessionMeta
from work_records import WorkRecordStore
from work_records_schedule_advance import (
    build_run_project_workers_arguments,
    schedule_advance_limit,
    validate_schedule_dispatch,
)


def _fixture(tmp_path):
    paths = AgentPaths(agent_root=tmp_path, workspace=tmp_path / "workspace", data=tmp_path / "data", evolve=tmp_path / "evolve")
    paths.workspace.mkdir(parents=True)
    paths.data.mkdir(parents=True)
    paths.evolve.mkdir(parents=True)
    (paths.data / "sessions").mkdir(parents=True)
    parent_id, source_id = "20261109-aaaaaaaa", "20261109-bbbbbbbb"
    parent_dir = paths.data / "sessions" / parent_id
    source_dir = paths.data / "sessions" / source_id
    parent_dir.mkdir()
    source_dir.mkdir()
    (source_dir / "meta.json").write_text(json.dumps({"project_id": "project-alpha"}), encoding="utf-8")
    (paths.data / "state.json").write_text(json.dumps({"project_sessions": {"project-alpha": source_id}}), encoding="utf-8")
    parent = Session(parent_id, parent_dir, "", SessionMeta(), [], paths)
    source_session = Session(source_id, source_dir, "", SessionMeta(project_id="project-alpha"), [], paths)
    store = WorkRecordStore(source_session)
    item = store.create_item(title="queued", scope={"kind": "project", "project_id": "project-alpha"})
    req = store.add_requirement(item["item_id"], "write the agreed file", event_id="queued-req")
    store.set_requirement_control(
        item["item_id"], req["requirement_id"], action="queue", expected_status="pending",
        event_id="queue-1", idempotency_key="queue-1",
    )
    source = authorize_project_record_sources(
        parent, ["project-alpha"], confirm_fn=lambda *_: "y", cancel_event=Event(),
    )["project-alpha"]
    return parent, source, item, req


def test_schedule_advance_respects_two_project_host_cap(tmp_path):
    parent, source, item, req = _fixture(tmp_path)
    host = ProjectWorkerHost(lambda: SimpleNamespace())
    assert schedule_advance_limit(host, parent.conversation_id, requested=2) == 2
    host._reservations["r1"] = (SimpleNamespace(conversation_id=parent.conversation_id, project_id="p-other"), True)
    assert schedule_advance_limit(host, parent.conversation_id, requested=2) == 1


def test_validate_schedule_dispatch_aligns_worker_to_authorized_candidate(tmp_path):
    parent, source, item, req = _fixture(tmp_path)
    workers = [{
        "project_id": "project-alpha",
        "item_id": item["item_id"],
        "requirement_id": req["requirement_id"],
        "priority": 3,
        "task": req["text"],
        "read_targets": ["README.md"],
        "write_targets": ["RESULT.md"],
    }]
    normalized = validate_schedule_dispatch({"project-alpha": source}, workers)
    tool_args = build_run_project_workers_arguments(normalized)
    assert tool_args["workers"][0]["task"] == req["text"]
    assert tool_args["schedule_candidates"][0]["requirement_id"] == req["requirement_id"]


def test_validate_schedule_dispatch_rejects_task_text_drift(tmp_path):
    parent, source, item, req = _fixture(tmp_path)
    workers = [{
        "project_id": "project-alpha",
        "item_id": item["item_id"],
        "requirement_id": req["requirement_id"],
        "priority": 3,
        "task": "different task",
        "read_targets": [],
        "write_targets": ["RESULT.md"],
    }]
    with pytest.raises(PermissionError):
        validate_schedule_dispatch({"project-alpha": source}, workers)
