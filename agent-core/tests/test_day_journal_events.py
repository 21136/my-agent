from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

from day_journal import DayJournal, JournalConflict
from day_journal_events import record_project_boundary
from paths import AgentPaths
from session import Session, SessionMeta


def current_session(tmp_path: Path, project_id: str = "project:alpha") -> Session:
    data = tmp_path / "data"
    session_dir = data / "sessions" / "session-live-1"
    session_dir.mkdir(parents=True)
    meta = SessionMeta(project_id=project_id)
    (session_dir / "meta.json").write_text(json.dumps(meta.to_dict()), encoding="utf-8")
    paths = AgentPaths(tmp_path, tmp_path / "workspace", data, tmp_path / "evolve")
    return Session("session-live-1", session_dir, "", meta, [], paths)


def test_records_current_session_boundary_with_explicit_local_date(tmp_path):
    session = current_session(tmp_path)
    journal = DayJournal(tmp_path / "journal")
    at = datetime(2026, 10, 28, 17, 30, tzinfo=timezone.utc)

    row = record_project_boundary(session, occurred_at=at, event_id="boundary:enter-1", journal=journal)

    assert row["date"] == "2026-10-29"
    assert row["projects"] == ["project:alpha"]
    assert row["segments"] == [{
        "project_id": "project:alpha", "date": "2026-10-29",
        "segment_id": "boundary:enter-1", "summary": None,
        "source": {"kind": "session", "session_id": "session-live-1"},
        "occurred_at": at.isoformat(),
    }]


def test_replay_is_idempotent_and_conflict_fails_closed(tmp_path):
    session = current_session(tmp_path)
    journal = DayJournal(tmp_path / "journal")
    at = datetime(2026, 10, 29, 2, tzinfo=timezone.utc)
    first = record_project_boundary(session, occurred_at=at, event_id="boundary:one", journal=journal)
    assert record_project_boundary(session, occurred_at=at, event_id="boundary:one", journal=journal) == first
    with pytest.raises(JournalConflict):
        record_project_boundary(session, occurred_at=at.replace(hour=3), event_id="boundary:one", journal=journal)


def test_project_must_match_persisted_current_session_meta_and_source_identity(tmp_path):
    session = current_session(tmp_path)
    journal = DayJournal(tmp_path / "journal")
    session.meta.project_id = "project:forged"
    with pytest.raises(ValueError, match="persisted"):
        record_project_boundary(session, occurred_at=datetime.now(timezone.utc), event_id="boundary:x", journal=journal)
    assert list((tmp_path / "journal").glob("*.json")) == []


def test_timezone_naive_and_unbound_session_are_rejected(tmp_path):
    session = current_session(tmp_path, project_id="")
    journal = DayJournal(tmp_path / "journal")
    with pytest.raises(ValueError):
        record_project_boundary(session, occurred_at=datetime(2026, 10, 29), event_id="boundary:x", journal=journal)
    with pytest.raises(ValueError, match="bound"):
        record_project_boundary(session, occurred_at=datetime.now(timezone.utc), event_id="boundary:x", journal=journal)


def test_cross_day_events_use_independent_day_locks_and_records(tmp_path):
    session = current_session(tmp_path)
    journal = DayJournal(tmp_path / "journal")
    a = record_project_boundary(session, occurred_at=datetime(2026, 10, 29, 15, tzinfo=timezone.utc), event_id="boundary:a", journal=journal)
    b = record_project_boundary(session, occurred_at=datetime(2026, 10, 30, 15, tzinfo=timezone.utc), event_id="boundary:b", journal=journal)
    assert a["date"] == "2026-10-29"
    assert b["date"] == "2026-10-30"


def test_concurrent_distinct_boundaries_on_one_day_are_preserved(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    session = current_session(tmp_path)
    journal = DayJournal(tmp_path / "journal")
    at = datetime(2026, 10, 29, 12, tzinfo=timezone.utc)
    def append(index: int):
        return record_project_boundary(session, occurred_at=at, event_id=f"boundary-{index}", journal=journal)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(append, range(8)))
    row = journal.read_day("2026-10-29")
    assert {item["segment_id"] for item in row["segments"]} == {f"boundary-{i}" for i in range(8)}


def test_concurrent_replay_is_idempotent_and_same_event_with_different_payload_conflicts(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    session = current_session(tmp_path)
    journal = DayJournal(tmp_path / "journal")
    at = datetime(2026, 10, 29, 12, tzinfo=timezone.utc)
    def append(_):
        return record_project_boundary(session, occurred_at=at, event_id="boundary-replay", journal=journal)
    with ThreadPoolExecutor(max_workers=8) as pool:
        rows = list(pool.map(append, range(8)))
    assert all(row == rows[0] for row in rows)
    assert len(rows[0]["segments"]) == 1
    with pytest.raises(JournalConflict):
        record_project_boundary(session, occurred_at=at.replace(hour=13), event_id="boundary-replay", journal=journal)


@pytest.mark.parametrize("seed_day", [False, True])
def test_boundary_write_failure_leaves_no_partial_day_or_event(tmp_path, monkeypatch, seed_day):
    session = current_session(tmp_path)
    journal = DayJournal(tmp_path / "journal")
    at = datetime(2026, 10, 29, 12, tzinfo=timezone.utc)
    before = None
    if seed_day:
        before = journal.save_day(at, "authorized summary", ["project:alpha"], summary_authorized=True)
    real_replace = __import__("os").replace
    def fail_event_replace(source, destination):
        if "boundary-write-failure" in Path(source).read_text(encoding="utf-8"):
            raise OSError("synthetic replace failure")
        return real_replace(source, destination)
    monkeypatch.setattr("day_journal.os.replace", fail_event_replace)

    with pytest.raises(OSError, match="synthetic replace failure"):
        record_project_boundary(session, occurred_at=at, event_id="boundary-write-failure", journal=journal)

    if seed_day:
        assert journal.read_day("2026-10-29") == before
    else:
        assert journal.read_day("2026-10-29") is None

def test_existing_authorized_day_summary_is_preserved_when_recording_boundary(tmp_path):
    session = current_session(tmp_path)
    journal = DayJournal(tmp_path / "journal")
    at = datetime(2026, 10, 29, 12, tzinfo=timezone.utc)
    seeded = journal.save_day(at, "caller-authorized summary", [], summary_authorized=True)

    row = record_project_boundary(session, occurred_at=at, event_id="boundary-preserve-summary", journal=journal)

    assert row["summary"] == "caller-authorized summary"
    assert row["revision"] == seeded["revision"] + 1

@pytest.mark.parametrize("field,value", [
    ("conversation_id", "../outside"),
    ("project_id", "../outside"),
    ("event_id", "../outside"),
])
def test_untrusted_ids_rejected_before_session_path_or_meta_read(tmp_path, field, value, monkeypatch):
    session = current_session(tmp_path)
    if field == "conversation_id":
        session.conversation_id = value
    elif field == "project_id":
        session.meta.project_id = value
        # Make metadata disagree too; ID validation must happen before reading it.
    journal = DayJournal(tmp_path / "journal")
    if field == "event_id":
        event_id = value
    else:
        event_id = "boundary-valid"
    import day_journal_events
    def unexpected_meta_read(*_args, **_kwargs):
        raise AssertionError("meta.json must not be read before identity validation")
    monkeypatch.setattr(day_journal_events, "_read_meta", unexpected_meta_read)

    with pytest.raises(ValueError):
        record_project_boundary(session, occurred_at=datetime.now(timezone.utc), event_id=event_id, journal=journal)
    assert not (tmp_path.parent / "outside" / "meta.json").exists()


def test_symlinked_sessions_root_is_rejected_before_meta_read(tmp_path, monkeypatch):
    session = current_session(tmp_path)
    sessions_root = tmp_path / "data" / "sessions"
    moved_root = tmp_path / "data" / "sessions-real"
    sessions_root.rename(moved_root)
    try:
        sessions_root.symlink_to(moved_root, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable")
    import day_journal_events
    monkeypatch.setattr(day_journal_events, "_read_meta", lambda *_a, **_k: pytest.fail("meta read through linked root"))
    with pytest.raises(ValueError, match="sessions root"):
        record_project_boundary(session, occurred_at=datetime.now(timezone.utc), event_id="boundary-valid", journal=DayJournal(tmp_path / "journal"))


def test_symlinked_session_directory_is_rejected_before_meta_read(tmp_path, monkeypatch):
    session = current_session(tmp_path)
    link = session.session_dir.parent / "session-linked"
    try:
        link.symlink_to(session.session_dir, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    session.conversation_id = "session-linked"
    session.session_dir = link
    import day_journal_events
    monkeypatch.setattr(day_journal_events, "_read_meta", lambda *_a, **_k: pytest.fail("meta read through linked session"))
    with pytest.raises(ValueError, match="session directory"):
        record_project_boundary(session, occurred_at=datetime.now(timezone.utc), event_id="boundary-valid", journal=DayJournal(tmp_path / "journal"))



def test_symlinked_meta_is_rejected_before_metadata_parser(tmp_path, monkeypatch):
    session = current_session(tmp_path)
    meta = session.session_dir / "meta.json"
    target = tmp_path / "other-meta.json"
    target.write_text(meta.read_text(encoding="utf-8"), encoding="utf-8")
    meta.unlink()
    try:
        meta.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable")
    import day_journal_events
    monkeypatch.setattr(day_journal_events, "_read_meta", lambda *_a, **_k: pytest.fail("meta parser called on linked file"))
    with pytest.raises(ValueError, match="metadata"):
        record_project_boundary(session, occurred_at=datetime.now(timezone.utc), event_id="boundary-valid", journal=DayJournal(tmp_path / "journal"))
