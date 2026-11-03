from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from file_guard import is_protected_path
from paths import AgentPaths
from session import create_new
from work_records import WorkRecordStore


@pytest.fixture
def isolated_session(tmp_path):
    """Use a disposable Agent root; never bind a real user session."""
    root = tmp_path / "agent-root"
    root.mkdir()
    paths = AgentPaths(
        agent_root=root,
        workspace=root / "workspace",
        data=root / "data",
        evolve=root / "evolve",
    )
    return create_new(paths, conversation_id="privacy-test-session")


def test_sidecar_and_prewrite_file_guard_backup_preserve_summary_plaintext(isolated_session):
    session = isolated_session
    store = WorkRecordStore(session)
    item = store.create_item(title="Privacy check", scope={"kind": "independent"})
    requirement = store.add_requirement(item["item_id"], "record test", event_id="privacy-req")
    sensitive_summary = "USER_SECRET_CANARY: account ending 4321"
    store.record_result(
        item["item_id"], requirement["requirement_id"],
        run_id="privacy-run", result_id="privacy-result", status="unknown",
        summary=sensitive_summary,
        verification={"status": "not_verified", "summary": "not checked"},
    )

    sidecar = session.session_dir / "work-records.v1.json"
    original_bytes = sidecar.read_bytes()
    payload = json.loads(original_bytes)
    assert payload["items"][0]["runs"][0]["result_summary"] == sensitive_summary
    assert sensitive_summary.encode("utf-8") in original_bytes
    assert is_protected_path(sidecar, session.paths.agent_root)

    # A later write backs up the verified prior sidecar before replacing it.
    store.create_item(title="Trigger pre-write backup", scope={"kind": "independent"})
    backups = list((session.paths.data / ".file-guard").glob("*/*"))
    assert backups  # backup naming is second-granular; do not assume a specific count
    matching_backup = next((backup for backup in backups if backup.read_bytes() == original_bytes), None)
    assert matching_backup is not None
    assert sensitive_summary.encode("utf-8") in matching_backup.read_bytes()


def test_session_guard_excludes_sidecar_but_file_guard_copy_survives_session_tree_removal(isolated_session):
    session = isolated_session
    store = WorkRecordStore(session)
    item = store.create_item(title="Deletion boundary", scope={"kind": "independent"})
    requirement = store.add_requirement(item["item_id"], "persist a prior state", event_id="deletion-req")
    store.record_result(
        item["item_id"], requirement["requirement_id"],
        run_id="deletion-run", result_id="deletion-result", status="unknown",
        summary="guarded sidecar content",
        verification={"status": "not_verified", "summary": "not checked"},
    )
    sidecar = session.session_dir / "work-records.v1.json"
    prior_sidecar_bytes = sidecar.read_bytes()
    assert b"guarded sidecar content" in prior_sidecar_bytes

    # The next mutation creates a .file-guard copy of the prior sidecar.
    store.create_item(title="Create backup", scope={"kind": "independent"})
    sidecar_backups = (session.paths.data / ".file-guard").glob("*/*")
    file_guard_copy = next(
        (backup for backup in sidecar_backups if backup.is_file() and backup.read_bytes() == prior_sidecar_bytes),
        None,
    )
    assert file_guard_copy is not None

    # Session.save calls backup_session_files, whose allowlist is the session
    # files only; a work-record sidecar is not part of .session-guard snapshots.
    session.save()
    session_guard = session.paths.data / ".session-guard" / session.conversation_id
    assert session_guard.is_dir()
    assert not (session_guard / "latest" / sidecar.name).exists()
    dated_snapshots = [p for p in session_guard.iterdir() if p.name != "latest"]
    assert dated_snapshots
    assert all(not (p / sidecar.name).exists() for p in dated_snapshots)

    # This is a fixture-local directory removal to show storage boundaries, not
    # a claim about an automatic/product deletion workflow or real-session safety.
    shutil.rmtree(session.session_dir)

    assert not sidecar.exists()
    assert file_guard_copy.is_file()
    assert file_guard_copy.read_bytes() == prior_sidecar_bytes
    assert session_guard.is_dir()
    assert (session_guard / "latest" / "meta.json").is_file()
