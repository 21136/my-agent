from __future__ import annotations

import json
import sys
from datetime import datetime as RealDateTime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

import work_records
from guard_snapshots import snapshot_payload
from paths import AgentPaths
from session import Session, SessionMeta
from work_records import WorkRecordStore


def _store(root: Path) -> WorkRecordStore:
    session_dir = root / "data" / "sessions" / "collision-session"
    session_dir.mkdir(parents=True)
    paths = AgentPaths(agent_root=root, workspace=root / "workspace", data=root / "data", evolve=root / "evolve")
    return WorkRecordStore(Session(
        conversation_id="collision-session", session_dir=session_dir, goal="",
        meta=SessionMeta(), messages=[], paths=paths,
    ))


def test_same_second_sidecar_mutations_keep_each_prior_snapshot(tmp_path, monkeypatch):
    store = _store(tmp_path / "synthetic-agent")
    fixed = RealDateTime(2026, 10, 30, 12, 34, 56, tzinfo=timezone.utc)

    class FixedDateTime(RealDateTime):
        @classmethod
        def now(cls, _tz=None):
            return fixed

    monkeypatch.setattr(work_records, "_datetime", SimpleNamespace(
        datetime=FixedDateTime, UTC=timezone.utc, timedelta=timedelta,
    ))

    store.create_item(title="synthetic-one", scope={"kind": "independent"})
    store.create_item(title="synthetic-two", scope={"kind": "independent"})
    store.create_item(title="synthetic-three", scope={"kind": "independent"})

    backup_files = sorted((store._agent_root / "data" / ".file-guard").glob("*/*"))
    revisions = [json.loads(path.read_text(encoding="utf-8"))["revision"] for path in backup_files]
    assert revisions == [1, 2]
    assert all(len(path.name) == 16 and path.name.endswith("Z") for path in backup_files)


def test_same_second_more_than_twenty_backups_retain_latest_revisions(tmp_path, monkeypatch):
    store = _store(tmp_path / "synthetic-agent")
    fixed = RealDateTime(2026, 10, 30, 12, 34, 56, tzinfo=timezone.utc)

    class FixedDateTime(RealDateTime):
        @classmethod
        def now(cls, tz=None):
            return fixed

    monkeypatch.setattr(work_records, "_datetime", SimpleNamespace(
        datetime=FixedDateTime, UTC=timezone.utc, timedelta=timedelta,
    ))

    for number in range(1, 31):
        store.create_item(title=f"synthetic-{number}", scope={"kind": "independent"})
        if number > 20:
            backup_files = sorted((store._agent_root / "data" / ".file-guard").glob("*/*"))
            revisions = [json.loads(path.read_text(encoding="utf-8"))["revision"] for path in backup_files]
            assert all(snapshot_payload(path, path.parent) == [path] for path in backup_files)
            assert revisions == list(range(max(1, number - 20), number))


def test_existing_future_legacy_snapshot_remains_and_new_backup_sorts_after_it(tmp_path, monkeypatch):
    store = _store(tmp_path / "synthetic-agent")
    fixed = RealDateTime(2026, 10, 30, 12, 34, 56, tzinfo=timezone.utc)

    class FixedDateTime(RealDateTime):
        @classmethod
        def now(cls, tz=None):
            return fixed

    monkeypatch.setattr(work_records, "_datetime", SimpleNamespace(
        datetime=FixedDateTime, UTC=timezone.utc, timedelta=timedelta,
    ))
    store.create_item(title="synthetic-one", scope={"kind": "independent"})
    store.create_item(title="synthetic-two", scope={"kind": "independent"})
    first = next((store._agent_root / "data" / ".file-guard").glob("*/*"))
    legacy = first.with_name("20300101T000000Z")
    first.rename(legacy)
    old_bytes = legacy.read_bytes()

    store.create_item(title="synthetic-three", scope={"kind": "independent"})

    files = sorted(legacy.parent.iterdir())
    assert [p.name for p in files] == ["20300101T000000Z", "20300101T000001Z"]
    assert legacy.read_bytes() == old_bytes
    assert [json.loads(p.read_text(encoding="utf-8"))["revision"] for p in files] == [1, 2]
    assert all(snapshot_payload(p, p.parent) == [p] for p in files)


def test_unrepresentable_latest_stamp_fails_closed(tmp_path, monkeypatch):
    store = _store(tmp_path / "synthetic-agent")
    store.create_item(title="synthetic-one", scope={"kind": "independent"})
    store.create_item(title="synthetic-two", scope={"kind": "independent"})
    backup = next((store._agent_root / "data" / ".file-guard").glob("*/*"))
    backup.rename(backup.with_name("99991231T235959Z"))
    prior = store._path.read_bytes()

    with pytest.raises(work_records.WorkRecordWriteError, match="later work-record backup timestamp"):
        store.create_item(title="synthetic-three", scope={"kind": "independent"})

    assert store._path.read_bytes() == prior
    assert [p.name for p in backup.parent.iterdir()] == ["99991231T235959Z"]
