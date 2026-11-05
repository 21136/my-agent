from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

from day_journal import DayJournal
from day_journal_compact import refresh_mechanical_day_summary
from day_journal_project_summary import evaluate_summary_readiness
from paths import AgentPaths
from session import Session, SessionMeta
from work_records import WorkRecordStore


def _session(tmp_path: Path, conversation_id: str, *, project_id: str = "proj-a") -> Session:
    root = tmp_path / "root"
    session_dir = root / "data" / "sessions" / conversation_id
    session_dir.mkdir(parents=True)
    paths = AgentPaths(root, root / "workspace", root / "data", root / "evolve")
    session = Session(conversation_id, session_dir, "goal text", SessionMeta(project_id=project_id), [], paths)
    session.digest_path.write_text("## 压缩 1\nhello digest tail", encoding="utf-8")
    return session


def test_evaluate_summary_readiness_blocked_without_day_or_boundary(tmp_path: Path):
    session = _session(tmp_path, "sess-1")
    journal = DayJournal(tmp_path / "root" / "data" / "day-journal")
    blocked = evaluate_summary_readiness(session, journal)
    assert blocked["ready"] is False

    at = datetime.now(timezone.utc)
    journal.save_day(at, None, ["proj-a"])
    still_blocked = evaluate_summary_readiness(session, journal)
    assert still_blocked["ready"] is False


def test_evaluate_summary_readiness_ready_with_boundary_and_source(tmp_path: Path):
    session = _session(tmp_path, "sess-2")
    journal = DayJournal(tmp_path / "root" / "data" / "day-journal")
    at = datetime.now(timezone.utc)
    journal.save_day(at, None, ["proj-a"])
    journal.record_segment(
        at,
        None,
        segment_id="boundary:sess-2",
        source={"kind": "session", "session_id": "sess-2"},
        scope="project",
        project_id="proj-a",
    )
    store = WorkRecordStore(session)
    item = store.create_item(title="item", scope={"kind": "project", "project_id": "proj-a"})
    store.add_requirement(item["item_id"], "req text", event_id="ev-1")
    ready = evaluate_summary_readiness(session, journal)
    assert ready["ready"] is True
    assert ready["source_count"] == 1


def test_refresh_mechanical_day_summary_uses_digest_when_empty(tmp_path: Path):
    session = _session(tmp_path, "sess-3")
    paths = session.paths
    result = refresh_mechanical_day_summary(session, paths)
    assert result["updated"] is True
    journal = DayJournal(paths.data / "day-journal")
    day_key, _ = journal._instant(datetime.now(timezone.utc))
    row = journal.read_day(day_key)
    assert row and row.get("summary") and "digest" in row["summary"]

    again = refresh_mechanical_day_summary(session, paths)
    assert again["updated"] is False
