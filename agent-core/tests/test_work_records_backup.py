from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from paths import AgentPaths
from session import Session, SessionMeta
from work_records import WorkRecordStore, WorkRecordWriteError


def _store(tmp_path: Path) -> WorkRecordStore:
    root = tmp_path / "agent"
    session_dir = root / "data" / "sessions" / "session-a"
    session_dir.mkdir(parents=True)
    paths = AgentPaths(agent_root=root, workspace=root / "workspace", data=root / "data", evolve=root / "evolve")
    return WorkRecordStore(Session(
        conversation_id="session-a", session_dir=session_dir, goal="",
        meta=SessionMeta(), messages=[], paths=paths,
    ))


def _backup_files(root: Path) -> list[Path]:
    return sorted((root / "data" / ".file-guard").glob("*/*"))


def test_mutation_keeps_previous_valid_snapshot_in_guard_layout(tmp_path):
    store = _store(tmp_path)
    store.create_item(title="first", scope={"kind": "independent"})
    sidecar = store._path
    prior = sidecar.read_bytes()

    store.create_item(title="second", scope={"kind": "independent"})

    backups = _backup_files(tmp_path / "agent")
    assert len(backups) == 1
    assert backups[0].read_bytes() == prior
    assert json.loads(backups[0].read_text(encoding="utf-8"))["revision"] == 1


def test_backup_publish_failure_fails_closed_before_replacing_sidecar(tmp_path, monkeypatch):
    import work_records

    store = _store(tmp_path)
    store.create_item(title="first", scope={"kind": "independent"})
    sidecar = store._path
    prior = sidecar.read_bytes()
    real_replace = work_records.os.replace

    def reject_backup(source, target):
        if ".file-guard" in Path(target).parts:
            raise OSError("isolated backup failure")
        return real_replace(source, target)

    monkeypatch.setattr(work_records.os, "replace", reject_backup)
    with pytest.raises(WorkRecordWriteError, match="backup"):
        store.create_item(title="second", scope={"kind": "independent"})

    assert sidecar.read_bytes() == prior
    assert store._read()["revision"] == 1


def test_backup_uses_existing_twenty_snapshot_retention(tmp_path):
    store = _store(tmp_path)
    store.create_item(title="first", scope={"kind": "independent"})
    directory = next((tmp_path / "agent" / "data" / ".file-guard").glob("*"), None)
    if directory is None:
        # The first publish has no prior snapshot, so establish the matching guard key.
        import hashlib
        key = hashlib.sha256(store._path.resolve().as_posix().encode("utf-8")).hexdigest()[:16]
        directory = tmp_path / "agent" / "data" / ".file-guard" / key
        directory.mkdir(parents=True)
    for day in range(1, 22):
        (directory / f"202001{day:02d}T000000Z").write_bytes(b"old snapshot")

    store.create_item(title="second", scope={"kind": "independent"})

    snapshots = sorted(path for path in directory.iterdir() if len(path.name) == 16 and path.name.endswith("Z"))
    assert len(snapshots) == 20
    assert not (directory / "20200101T000000Z").exists()
    assert any(path.read_bytes() != b"old snapshot" for path in snapshots)
