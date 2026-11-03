from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from paths import AgentPaths
from session import Session, SessionMeta
from work_records import WorkRecordScopeError, WorkRecordStore


def test_publish_rechecks_session_scope_after_temp_creation(tmp_path, monkeypatch):
    """Fail closed if the session path no longer validates after its temp is created."""
    root = tmp_path / "agent"
    session_dir = root / "data" / "sessions" / "session-a"
    session_dir.mkdir(parents=True)
    paths = AgentPaths(agent_root=root, workspace=root / "workspace", data=root / "data", evolve=root / "evolve")
    session = Session("session-a", session_dir, "", SessionMeta(), [], paths)
    store = WorkRecordStore(session)
    original = store._validate_scope
    checks = 0

    def fail_after_temp_creation():
        nonlocal checks
        checks += 1
        if checks == 4:
            raise WorkRecordScopeError("simulated session path replacement")
        original()

    monkeypatch.setattr(store, "_validate_scope", fail_after_temp_creation)
    with pytest.raises(WorkRecordScopeError, match="replacement"):
        store.create_item(title="must stay scoped", scope={"kind": "independent"})
    assert not (session_dir / "work-records.v1.json").exists()
    assert not list(session_dir.glob(".work-records.*.tmp"))

