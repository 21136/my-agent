from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

CORE = Path(__file__).resolve().parents[1]
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))

from paths import AgentPaths
from session import Session, SessionMeta
from work_records import WorkRecordStore
from day_journal_summary_sources import DayJournalSummarySourceSelector


def make_session(tmp_path, conversation_id="s-1", project_id="p-1"):
    paths = AgentPaths(tmp_path, tmp_path / "workspace", tmp_path / "data", tmp_path / "evolve")
    for path in (paths.workspace, paths.data, paths.evolve):
        path.mkdir(parents=True, exist_ok=True)
    session_dir = paths.data / "sessions" / conversation_id
    session_dir.mkdir(parents=True, exist_ok=True)
    return Session(conversation_id, session_dir, "synthetic", SessionMeta(project_id=project_id), [], paths)


def make_requirement(session, *, scope_project=None, text="调整登录页布局"):
    store = WorkRecordStore(session)
    project_id = scope_project or session.meta.project_id
    item = store.create_item(title="事项", scope={"kind": "project", "project_id": project_id})
    requirement = store.add_requirement(item["item_id"], text, event_id="requirement-event-1")
    return store, item, requirement


def set_recorded_at(store, event_id="requirement-event-1", at=None):
    path = store._path
    payload = json.loads(path.read_text(encoding="utf-8"))
    event = next(event for item in payload["items"] for event in item["history"] if event["event_id"] == event_id)
    event["at"] = (at or datetime(2026, 10, 29, 3, tzinfo=timezone.utc)).timestamp()
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def select(selector, item, *, project="p-1", day="2026-10-29", event="requirement-event-1"):
    return selector.select(day=day, project_id=project, item_id=item["item_id"], event_id=event)


def test_selects_dated_persisted_requirement_as_recorded_not_completed(tmp_path):
    session = make_session(tmp_path)
    store, item, requirement = make_requirement(session)
    set_recorded_at(store)
    selector = DayJournalSummarySourceSelector(store, session)
    snapshot = select(selector, item)
    assert snapshot.status == "ready"
    assert snapshot.sources == ({"source_id": f"work-record:{item['item_id']}:requirement-event-1",
                                 "text": f"记录了需求（记录时间，不代表原始发生时间、完成或验证）：{requirement['text']}"},)
    assert selector.validate(snapshot)
    detached = snapshot.sources
    detached[0]["text"] = "调用方本地修改"
    assert selector.validate(snapshot)


def test_unknown_or_different_recorded_day_is_insufficient(tmp_path):
    session = make_session(tmp_path)
    store, item, _ = make_requirement(session)
    selector = DayJournalSummarySourceSelector(store, session)
    set_recorded_at(store, at=datetime(2020, 1, 1, 3, tzinfo=timezone.utc))
    assert select(selector, item).status == "insufficient"
    set_recorded_at(store, at=datetime(2026, 10, 28, 3, tzinfo=timezone.utc))
    set_recorded_at(store, at=datetime(2020, 1, 1, 3, tzinfo=timezone.utc))
    assert select(selector, item).status == "insufficient"


def test_cross_project_and_cross_session_evidence_is_rejected(tmp_path):
    session = make_session(tmp_path)
    store, item, _ = make_requirement(session)
    set_recorded_at(store)
    selector = DayJournalSummarySourceSelector(store, session)
    assert select(selector, item, project="other-project").status == "insufficient"
    other_session = make_session(tmp_path, "s-2", "p-1")
    other_store = WorkRecordStore(other_session)
    assert not DayJournalSummarySourceSelector(other_store, other_session).validate(select(selector, item))


def test_changed_persisted_event_invalidates_generation_snapshot(tmp_path):
    session = make_session(tmp_path)
    store, item, _ = make_requirement(session)
    set_recorded_at(store)
    selector = DayJournalSummarySourceSelector(store, session)
    snapshot = select(selector, item)
    assert selector.validate(snapshot)
    set_recorded_at(store, at=datetime(2026, 10, 30, 3, tzinfo=timezone.utc))
    assert not selector.validate(snapshot)


def test_metadata_does_not_expose_requirement_text_or_storage_path(tmp_path):
    session = make_session(tmp_path)
    private_text = "PRIVATE-REQUIREMENT-TEXT"
    store, item, _ = make_requirement(session, text=private_text)
    set_recorded_at(store)
    snapshot = select(DayJournalSummarySourceSelector(store, session), item)
    assert private_text not in repr(snapshot.metadata)
    assert str(session.session_dir) not in repr(snapshot.metadata)



