from __future__ import annotations

import errno
import json
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from datetime import datetime, timezone
from pathlib import Path

import pytest

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

import day_journal
from day_journal import DayJournal, JournalConflict, JournalCorrupt, JournalLockTimeout, JournalPathError


def make_journal(tmp_path: Path) -> DayJournal:
    return DayJournal(tmp_path / "synthetic-journal", timezone="Asia/Shanghai")



def save_day(journal, at, summary, projects, **kwargs):
    return journal.save_day(at, summary, projects, summary_authorized=summary is not None, **kwargs)


def add_segment(journal, at, project_id, summary, **kwargs):
    return journal.add_segment(at, project_id, summary, summary_authorized=summary is not None, **kwargs)


def test_explicit_zoned_time_and_day_directory_plus_project_fragment(tmp_path):
    journal = make_journal(tmp_path)
    moment = datetime(2026, 10, 29, 9, 30, tzinfo=timezone.utc)
    first = save_day(journal, moment, "synthetic daily summary", ["project:alpha"])
    row = add_segment(journal, 
        moment, project_id="project:alpha", summary="synthetic work summary",
        segment_id="segment:stable-1", source={"kind": "session", "session_id": "session:stable-7"},
        expected_revision=first["revision"],
    )

    assert row["date"] == "2026-10-29"
    assert row["summary"] == "synthetic daily summary"
    assert row["projects"] == ["project:alpha"]
    assert row["segments"] == [{
        "scope": "project",
        "project_id": "project:alpha", "date": "2026-10-29",
        "segment_id": "segment:stable-1", "summary": "synthetic work summary",
        "source": {"kind": "session", "session_id": "session:stable-7"},
        "occurred_at": "2026-10-29T09:30:00+00:00",
    }]
    assert journal.read_day("2026-10-29") == row


def test_missing_daily_or_segment_summary_is_allowed_but_timestamp_must_be_aware(tmp_path):
    journal = make_journal(tmp_path)
    moment = datetime(2026, 10, 29, 10, tzinfo=timezone.utc)
    row = save_day(journal, moment, None, ["p1"])
    row = add_segment(journal, 
        moment, "p1", None, segment_id="seg-1",
        source={"kind": "session", "session_id": "session:1"}, expected_revision=row["revision"],
    )
    assert row["summary"] is None
    assert row["segments"][0]["summary"] is None
    with pytest.raises(ValueError):
        journal.save_day(moment, "unapproved synthetic summary", ["p1"])
    with pytest.raises(ValueError):
        save_day(journal, datetime(2026, 10, 29, 10), "synthetic", ["p1"])


def test_timezone_conversion_uses_store_zone_and_idempotent_replay(tmp_path):
    journal = make_journal(tmp_path)
    moment = datetime(2026, 10, 28, 17, 0, tzinfo=timezone.utc)
    row = save_day(journal, moment, "summary", ["p1"])
    a = add_segment(journal, moment, "p1", "fragment", segment_id="seg-1", source={"kind": "session", "session_id": "session:1"}, expected_revision=row["revision"])
    replay = add_segment(journal, moment, "p1", "fragment", segment_id="seg-1", source={"kind": "session", "session_id": "session:1"}, expected_revision=1)
    assert replay == a
    assert a["date"] == "2026-10-29"
    assert a["revision"] == 2


def test_conflicting_revision_or_reused_segment_id_never_overwrites(tmp_path):
    journal = make_journal(tmp_path)
    moment = datetime(2026, 10, 29, 1, tzinfo=timezone.utc)
    row = save_day(journal, moment, "summary", ["p1"])
    add_segment(journal, moment, "p1", "fragment", segment_id="seg-1", source={"kind": "session", "session_id": "session:1"}, expected_revision=row["revision"])
    with pytest.raises(JournalConflict):
        save_day(journal, moment, "new summary", ["p1"], expected_revision=1)
    with pytest.raises(JournalConflict):
        add_segment(journal, moment, "p1", "different", segment_id="seg-1", source={"kind": "session", "session_id": "session:1"}, expected_revision=2)
    assert journal.read_day("2026-10-29")["summary"] == "summary"


def test_concurrent_same_segment_is_single_idempotent_record(tmp_path):
    journal = make_journal(tmp_path)
    moment = datetime(2026, 10, 29, 1, tzinfo=timezone.utc)
    initial = save_day(journal, moment, "summary", ["p1"])
    def append(_):
        return add_segment(journal, moment, "p1", "fragment", segment_id="seg-one", source={"kind": "session", "session_id": "session:1"}, expected_revision=initial["revision"])
    with ThreadPoolExecutor(max_workers=8) as pool:
        rows = list(pool.map(append, range(8)))
    assert all(row == rows[0] for row in rows)
    assert len(rows[0]["segments"]) == 1


def test_project_identity_and_real_source_coordinate_are_structurally_required(tmp_path):
    journal = make_journal(tmp_path)
    moment = datetime(2026, 10, 29, 1, tzinfo=timezone.utc)
    with pytest.raises(ValueError):
        save_day(journal, moment, "summary", ["Project Alpha"])
    row = save_day(journal, moment, "summary", ["project:alpha"])
    bad_sources = [None, {}, {"kind": "session", "session_id": "../outside"}, {"kind": "url", "url": "https://example.invalid"}]
    for source in bad_sources:
        with pytest.raises(ValueError):
            add_segment(journal, moment, "project:alpha", "summary", segment_id="seg-x", source=source, expected_revision=row["revision"])
    with pytest.raises(ValueError):
        add_segment(journal, moment, "project:other", "summary", segment_id="seg-x", source={"kind": "session", "session_id": "session:1"}, expected_revision=row["revision"])


def test_corrupt_data_fails_closed_and_explicit_delete_requires_revision(tmp_path):
    journal = make_journal(tmp_path)
    moment = datetime(2026, 10, 29, 1, tzinfo=timezone.utc)
    save_day(journal, moment, "summary", ["p1"])
    file = tmp_path / "synthetic-journal" / "2026-10-29.json"
    file.write_text("{broken", encoding="utf-8")
    with pytest.raises(JournalCorrupt):
        journal.read_day("2026-10-29")
    with pytest.raises(JournalCorrupt):
        save_day(journal, moment, "replacement", ["p1"])
    file.write_text(json.dumps({"date": "2026-10-29", "summary": "ok", "projects": ["p1"], "segments": [], "revision": 1}), encoding="utf-8")
    with pytest.raises(JournalConflict):
        journal.delete_day("2026-10-29", expected_revision=0)
    assert journal.delete_day("2026-10-29", expected_revision=1) is True
    assert journal.read_day("2026-10-29") is None



def test_save_day_rejects_removing_project_referenced_by_segment_and_preserves_record(tmp_path):
    journal = make_journal(tmp_path)
    moment = datetime(2026, 10, 29, 1, tzinfo=timezone.utc)
    row = save_day(journal, moment, "summary", ["p1", "p2"])
    row = add_segment(journal, moment, "p1", None, segment_id="seg-1", source={"kind": "session", "session_id": "session:1"}, expected_revision=row["revision"])

    with pytest.raises(JournalConflict, match="referenced"):
        save_day(journal, moment, "updated summary", ["p2"], expected_revision=row["revision"])

    assert journal.read_day("2026-10-29") == row


def test_read_rejects_segment_timestamp_whose_local_date_differs_from_day_key(tmp_path):
    journal = make_journal(tmp_path)
    moment = datetime(2026, 10, 29, 1, tzinfo=timezone.utc)
    row = save_day(journal, moment, None, ["p1"])
    add_segment(journal, moment, "p1", None, segment_id="seg-1", source={"kind": "session", "session_id": "session:1"}, expected_revision=row["revision"])
    path = tmp_path / "synthetic-journal" / "2026-10-29.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["segments"][0]["occurred_at"] = "2026-10-28T15:00:00+00:00"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(JournalCorrupt):
        journal.read_day("2026-10-29")


def test_windows_lock_wait_is_bounded_and_only_contention_is_retried(monkeypatch):
    class BusyMsvcrt:
        LK_NBLCK = 1

        @staticmethod
        def locking(_fd, _mode, _count):
            raise OSError(errno.EACCES, "lock busy")

    class BrokenMsvcrt:
        LK_NBLCK = 1

        @staticmethod
        def locking(_fd, _mode, _count):
            raise OSError(errno.EINVAL, "invalid lock request")

    monkeypatch.setattr(day_journal, "_WINDOWS_LOCK_TIMEOUT_SECONDS", 0.02)
    with tempfile.TemporaryFile() as handle:
        with pytest.raises(JournalLockTimeout):
            day_journal._acquire_windows_lock(handle.fileno(), BusyMsvcrt)
        with pytest.raises(OSError) as error:
            day_journal._acquire_windows_lock(handle.fileno(), BrokenMsvcrt)
        assert error.value.errno == errno.EINVAL


def test_date_traversal_and_symlink_escape_are_rejected(tmp_path):
    journal = make_journal(tmp_path)
    with pytest.raises(ValueError):
        journal.read_day("../outside")
    root = tmp_path / "synthetic-journal"
    root.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    link = root / "2026-10-29.json"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable in this environment")
    with pytest.raises(JournalPathError):
        journal.read_day("2026-10-29")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows CRT region-lock behavior")
def test_windows_concurrent_first_lock_does_not_write_lock_file(tmp_path, monkeypatch):
    root = tmp_path / "synthetic-journal"
    lock_path = root / ".2026-10-29.lock"
    workers = 12
    start = Barrier(workers)
    write_calls = []

    def fail_if_lock_initialization_writes(fd, data):
        # The old zero-byte initialization raced before msvcrt.locking and
        # could fail with WinError 5 when another worker had opened the file.
        write_calls.append((fd, data))
        raise PermissionError(13, "simulated transient Windows sharing violation")

    monkeypatch.setattr(day_journal.os, "write", fail_if_lock_initialization_writes)

    def read_on_fresh_instance(_):
        start.wait(timeout=5)
        return DayJournal(root).read_day("2026-10-29")

    with ThreadPoolExecutor(max_workers=workers) as pool:
        rows = list(pool.map(read_on_fresh_instance, range(workers)))

    assert rows == [None] * workers
    assert write_calls == []
    assert lock_path.exists()
    assert lock_path.stat().st_size == 0
