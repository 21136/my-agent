from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from paths import AgentPaths
from session import Session, SessionMeta
from work_records import WorkRecordCorrupt, WorkRecordScopeError, WorkRecordStore


def _session(session_dir: Path, conversation_id: str, project_id: str = "") -> Session:
    root = session_dir.parents[2]
    session_dir.mkdir(parents=True, exist_ok=True)
    paths = AgentPaths(agent_root=root, workspace=root / "workspace", data=root / "data", evolve=root / "evolve")
    return Session(
        conversation_id=conversation_id,
        session_dir=session_dir,
        goal="",
        meta=SessionMeta(project_id=project_id),
        messages=[],
        paths=paths,
    )


def test_project_scope_cannot_be_rebound_after_store_authorization(tmp_path):
    """Mutating the retained Session must not silently widen an existing store's authority."""
    session = _session(tmp_path / "data/sessions/session-a", "session-a", "project-original")
    store = WorkRecordStore(session)

    session.meta.project_id = "project-attacker"

    with pytest.raises(WorkRecordScopeError):
        store.create_item(
            title="cross-project write",
            scope={"kind": "project", "project_id": "project-attacker"},
        )
    with pytest.raises(WorkRecordScopeError):
        store.list_items()


def test_malformed_items_are_rejected_without_rewriting_user_data(tmp_path):
    """A valid envelope around invalid item records must fail closed and stay byte-for-byte intact."""
    session_dir = tmp_path / "data/sessions/session-a"
    session = _session(session_dir, "session-a")
    path = session_dir / "work-records.v1.json"
    original = json.dumps(
        {
            "schema_version": 1,
            "conversation_id": "session-a",
            "revision": 4,
            "items": ["user-owned opaque content"],
        },
        ensure_ascii=False,
    ).encode("utf-8")
    path.write_bytes(original)
    store = WorkRecordStore(session)

    with pytest.raises(WorkRecordCorrupt):
        store.create_item(title="must not replace damaged data", scope={"kind": "independent"})
    assert path.read_bytes() == original



def test_lock_hardlink_is_rejected_without_touching_external_file(tmp_path):
    """A lock alias must not lock or mutate a file outside the session directory."""
    import os
    from work_records import WorkRecordLockUnavailable
    session_dir = tmp_path / "data/sessions/session-a"
    session = _session(session_dir, "session-a")
    external = tmp_path / "external.lock"
    external.write_bytes(b"external-owner-data")
    lock_path = session_dir / ".work-records.lock"
    try:
        os.link(external, lock_path)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"hardlinks unavailable on test filesystem: {exc}")

    before = external.read_bytes()
    with pytest.raises(WorkRecordLockUnavailable, match="link|hardlink|alias"):
        WorkRecordStore(session).create_item(title="must not lock alias", scope={"kind": "independent"})
    assert external.read_bytes() == before


def test_sidecar_hardlink_is_rejected_without_replacing_external_file(tmp_path):
    import os
    session_dir = tmp_path / "data/sessions/session-a"
    session = _session(session_dir, "session-a")
    external = tmp_path / "external.json"
    external.write_text("external-content", encoding="utf-8")
    sidecar = session_dir / "work-records.v1.json"
    try:
        os.link(external, sidecar)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"hardlinks unavailable on test filesystem: {exc}")
    before = external.read_bytes()

    with pytest.raises(WorkRecordCorrupt, match="linked|hardlink|alias"):
        WorkRecordStore(session).list_items()
    assert external.read_bytes() == before
