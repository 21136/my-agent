from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

from day_journal import DayJournal
from paths import AgentPaths
from session import Session, SessionMeta
from session_restart import perform_session_restart_today


def _grow_session(tmp_path: Path) -> Session:
    data = tmp_path / "data"
    session_dir = data / "sessions" / "live-grow"
    session_dir.mkdir(parents=True)
    meta = SessionMeta(project_id="", active_shell="grow")
    (session_dir / "meta.json").write_text(json.dumps(meta.to_dict()), encoding="utf-8")
    paths = AgentPaths(tmp_path, tmp_path / "workspace", data, tmp_path / "evolve")
    return Session("live-grow", session_dir, "goal", meta, [{"role": "user", "content": "hi"}], paths)


def test_restart_today_archives_ordinary_segment_and_starts_fresh_session(tmp_path: Path):
    session = _grow_session(tmp_path)
    paths = session.paths
    updated, events, notice = perform_session_restart_today(paths, session, {"request_id": "req-1"})
    assert notice is None
    assert updated.conversation_id != "live-grow"
    assert any(event.get("type") == "session.restart_today.done" for event in events)
    journal = DayJournal(paths.data / "day-journal")
    day_key, _ = journal._instant(datetime.now(timezone.utc))
    row = journal.read_day(day_key)
    assert row is not None
    archived = next(seg for seg in row["segments"] if seg["segment_id"] == "restart:req-1")
    assert archived["scope"] == "ordinary"
    assert archived["source"]["session_id"] == "live-grow"
