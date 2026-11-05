from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

from day_journal_api import dispatch_day_journal_message
from day_journal_events import record_project_boundary
from paths import AgentPaths
from project_session_store import (
    append_segment_pointer,
    attach_project_store_pointers,
    build_project_store_payload,
    list_store_days,
)
from session import Session, SessionMeta


def _session(tmp_path: Path, session_id: str, *, project_id: str) -> Session:
    data = tmp_path / "data"
    session_dir = data / "sessions" / session_id
    session_dir.mkdir(parents=True)
    meta = SessionMeta(project_id=project_id, active_shell="project")
    (session_dir / "meta.json").write_text(json.dumps(meta.to_dict()), encoding="utf-8")
    paths = AgentPaths(tmp_path, tmp_path / "workspace", data, tmp_path / "evolve")
    return Session(session_id, session_dir, "goal", meta, [], paths)


def test_project_store_lists_day_pointers(tmp_path: Path):
    paths = AgentPaths(tmp_path, tmp_path / "workspace", tmp_path / "data", tmp_path / "evolve")
    append_segment_pointer(
        paths,
        project_id="proj-a",
        day_key="2026-11-05",
        segment_id="boundary:one",
        session_id="sess-one",
        occurred_at="2026-11-05T02:00:00+00:00",
    )
    days = list_store_days(paths.data / "project-session-store", "proj-a")
    assert days == ["2026-11-05"]
    payload = build_project_store_payload(paths, "proj-a")
    assert payload["type"] == "day_journal.project_store"
    assert payload["days"][0]["segment_count"] == 1
    assert payload["days"][0]["segments"][0]["session_id"] == "sess-one"


def test_day_journal_api_project_store(tmp_path: Path):
    paths = AgentPaths(tmp_path, tmp_path / "workspace", tmp_path / "data", tmp_path / "evolve")
    append_segment_pointer(
        paths,
        project_id="proj-b",
        day_key="2026-11-04",
        segment_id="boundary:two",
        session_id="sess-two",
        occurred_at="2026-11-04T08:00:00+00:00",
    )
    out = dispatch_day_journal_message(
        paths,
        {"type": "day_journal.project_store", "project_id": "proj-b"},
    )
    assert out["project_id"] == "proj-b"
    assert out["days"][0]["date"] == "2026-11-04"


def test_record_boundary_writes_project_store(tmp_path: Path):
    from day_journal import DayJournal

    session = _session(tmp_path, "sess-boundary", project_id="proj-c")
    journal = DayJournal(tmp_path / "data" / "day-journal")
    fixed = datetime(2026, 11, 5, 12, tzinfo=timezone.utc)
    record_project_boundary(
        session,
        occurred_at=fixed,
        event_id="switch:demo",
        journal=journal,
        paths=session.paths,
    )
    payload = build_project_store_payload(session.paths, "proj-c")
    assert payload["days"][0]["segments"][0]["segment_id"] == "switch:demo"


def test_attach_store_pointers_follows_journal_projects_without_enumerating(tmp_path: Path):
    paths = AgentPaths(tmp_path, tmp_path / "workspace", tmp_path / "data", tmp_path / "evolve")
    append_segment_pointer(
        paths,
        project_id="proj-a",
        day_key="2026-11-05",
        segment_id="boundary:one",
        session_id="sess-one",
        occurred_at="2026-11-05T02:00:00+00:00",
    )
    append_segment_pointer(
        paths,
        project_id="proj-hidden",
        day_key="2026-11-05",
        segment_id="boundary:hidden",
        session_id="sess-hidden",
        occurred_at="2026-11-05T03:00:00+00:00",
    )
    result = attach_project_store_pointers(
        paths,
        {
            "status": "ok",
            "records": [{
                "date": "2026-11-05",
                "summary": "日摘要",
                "projects": ["proj-a"],
                "segments": [],
            }],
            "issues": [],
            "message": "仅返回本地日记中明确记载的信息。",
        },
    )
    store = result["records"][0]["project_store"]
    assert store == [{
        "project_id": "proj-a",
        "date": "2026-11-05",
        "segments": [{
            "segment_id": "boundary:one",
            "session_id": "sess-one",
            "occurred_at": "2026-11-05T02:00:00+00:00",
        }],
    }]
    assert "sess-hidden" not in json.dumps(result)


def test_attach_store_pointers_skips_when_journal_has_no_records(tmp_path: Path):
    paths = AgentPaths(tmp_path, tmp_path / "workspace", tmp_path / "data", tmp_path / "evolve")
    append_segment_pointer(
        paths,
        project_id="proj-a",
        day_key="2026-11-05",
        segment_id="boundary:one",
        session_id="sess-one",
        occurred_at="2026-11-05T02:00:00+00:00",
    )
    result = attach_project_store_pointers(
        paths,
        {"status": "no_record", "records": [], "issues": [], "message": "没有记录"},
    )
    assert "project_store" not in result
    assert result["records"] == []


def test_attach_store_pointers_marks_corrupt_store_partial(tmp_path: Path):
    paths = AgentPaths(tmp_path, tmp_path / "workspace", tmp_path / "data", tmp_path / "evolve")
    bad = paths.data / "project-session-store" / "proj-a"
    bad.mkdir(parents=True)
    (bad / "2026-11-05.json").write_text("{broken", encoding="utf-8")
    result = attach_project_store_pointers(
        paths,
        {
            "status": "ok",
            "records": [{"date": "2026-11-05", "summary": "x", "projects": ["proj-a"], "segments": []}],
            "issues": [],
            "message": "ok",
        },
    )
    assert result["status"] == "partial"
    assert result["issues"] == [{"date": "2026-11-05", "status": "corrupt"}]
    assert result["records"][0]["project_store"] == []
