from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

from context import CompactResult, append_digest_section
from day_journal import DayJournal
from day_journal_compact import (
    _digest_excerpt,
    refresh_day_journal_after_compact,
    seal_elapsed_day_summaries,
)
from day_journal_directory import build_directory_payload, list_recent_days
from day_journal_events import record_dialogue_segment
from paths import AgentPaths
from session import Session, SessionMeta


def _session(tmp_path: Path, session_id: str, *, project_id: str = "") -> Session:
    data = tmp_path / "data"
    session_dir = data / "sessions" / session_id
    session_dir.mkdir(parents=True)
    meta = SessionMeta(project_id=project_id, active_shell="project" if project_id else "grow")
    (session_dir / "meta.json").write_text(json.dumps(meta.to_dict()), encoding="utf-8")
    paths = AgentPaths(tmp_path, tmp_path / "workspace", data, tmp_path / "evolve")
    return Session(session_id, session_dir, "build feature", meta, [], paths)


def test_compact_refreshes_day_summary_and_project_hint(tmp_path: Path):
    session = _session(tmp_path, "sess-compact", project_id="proj-a")
    journal = DayJournal(tmp_path / "data" / "day-journal")
    record_dialogue_segment(
        session,
        occurred_at=datetime(2026, 11, 5, 10, tzinfo=timezone.utc),
        event_id="restart:one",
        journal=journal,
        paths=session.paths,
        reason="今日重来",
    )
    session.digest_path.parent.mkdir(parents=True, exist_ok=True)
    session.digest_path.write_text("## 压缩 1\nDid some work on API.", encoding="utf-8")
    compact = CompactResult(compacted=True, message="ok", digest_section=1, digested_message_count=3)
    refresh_day_journal_after_compact(session, session.paths, compact)
    row = journal.read_day("2026-11-05")
    assert row is not None
    assert row["summary"] and "压缩" in row["summary"]
    seg = next(s for s in row["segments"] if s["segment_id"] == "restart:one")
    assert seg["summary"] and "完整片段" in seg["summary"]


def test_digest_excerpt_uses_newest_section_as_written_by_compact(tmp_path: Path):
    session = _session(tmp_path, "sess-excerpt")
    append_digest_section(session, "## 目标\noldest section body")
    append_digest_section(session, "## 目标\nnewest section body")
    excerpt = _digest_excerpt(session)
    assert excerpt is not None
    assert excerpt.startswith("# 压缩 2")
    assert "newest section body" in excerpt
    assert "oldest section body" not in excerpt


def test_directory_lists_recent_days(tmp_path: Path):
    root = tmp_path / "data" / "day-journal"
    root.mkdir(parents=True)
    (root / "2026-11-04.json").write_text(
        json.dumps(
            {
                "date": "2026-11-04",
                "summary": None,
                "projects": [],
                "segments": [],
                "revision": 1,
            }
        ),
        encoding="utf-8",
    )
    days = list_recent_days(root)
    assert days == ["2026-11-04"]
    payload = build_directory_payload(root)
    assert payload["type"] == "day_journal.directory"
    assert payload["days"][0]["date"] == "2026-11-04"


def test_seal_elapsed_days_fills_empty_past_summary_without_overwriting(tmp_path: Path):
    journal = DayJournal(tmp_path / "journal")
    past = datetime(2026, 11, 4, 12, tzinfo=timezone.utc)
    today = datetime(2026, 11, 5, 12, tzinfo=timezone.utc)
    journal.save_day(past, None, ["proj-a"])
    journal.save_day(today, "今日摘要", ["proj-a"], summary_authorized=True)
    sealed = seal_elapsed_day_summaries(journal, now=today)
    assert sealed == 1
    assert "按天收尾" in (journal.read_day("2026-11-04") or {})["summary"]
    assert journal.read_day("2026-11-05")["summary"] == "今日摘要"
    assert seal_elapsed_day_summaries(journal, now=today) == 0
