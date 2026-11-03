from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from paths import AgentPaths
from session import Session, SessionMeta
from work_records import WorkRecordStore, WorkRecordWriteError


def _store(root: Path) -> WorkRecordStore:
    conversation_id = "synthetic-privacy-test"
    session_dir = root / "data" / "sessions" / conversation_id
    session_dir.mkdir(parents=True)
    paths = AgentPaths(
        agent_root=root,
        workspace=root / "workspace",
        data=root / "data",
        evolve=root / "evolve",
    )
    session = Session(
        conversation_id=conversation_id,
        session_dir=session_dir,
        goal="",
        meta=SessionMeta(),
        messages=[],
        paths=paths,
    )
    return WorkRecordStore(session)


def test_unsupported_source_metadata_is_rejected_but_free_summary_is_not_redacted(tmp_path):
    """Source refs are strict metadata; free text has no guaranteed redaction."""
    store = _store(tmp_path / "isolated-agent")
    secret = "SYNTHETIC_SECRET_DO_NOT_USE_REAL_CREDENTIALS_7f3c"
    original = "SYNTHETIC_ORIGINAL_TEXT: private draft paragraph"
    synthetic_path = str(tmp_path / "fixture-only" / "private-notes.txt")
    with pytest.raises(ValueError):
        store.create_item(
            title="Synthetic privacy boundary",
            scope={"kind": "independent"},
            source_refs=[{"kind": "file", "id": "fixture", "path": synthetic_path, "label": secret, "raw": original}],
        )

    item = store.create_item(title="Free text boundary", scope={"kind": "independent"})
    requirement = store.add_requirement(item["item_id"], "synthetic requirement", event_id="synthetic-privacy-requirement")
    summary = f"Summary includes {secret}; quoted original: {original}; path: {synthetic_path}"
    store.record_result(
        item["item_id"], requirement["requirement_id"], run_id="synthetic-run", result_id="synthetic-result",
        status="unknown", summary=summary, verification={"status": "not_verified", "summary": "synthetic only"},
    )
    raw = store._path.read_bytes()
    decoded = json.loads(raw)
    saved = decoded["items"][0]["runs"][0]["result_summary"]
    assert saved == summary  # deliberately not a redaction guarantee
    assert secret in saved and original in saved and synthetic_path in saved


def test_serialized_sidecar_size_is_bounded_but_not_sensitive_content(tmp_path):
    store = _store(tmp_path / "isolated-agent")
    item = store.create_item(title="Small seed", scope={"kind": "independent"})
    requirement = store.add_requirement(item["item_id"], "small requirement", event_id="size-boundary")
    baseline = store._path.read_bytes()
    # A single oversized synthetic summary exceeds the 4 MiB serialized-sidecar
    # ceiling; this is a storage-size guard, not a secret/content detector.
    with pytest.raises(WorkRecordWriteError, match="size/history limit"):
        store.record_result(
            item["item_id"], requirement["requirement_id"],
            run_id="oversized-run", result_id="oversized-result", status="unknown",
            summary="SYNTHETIC_PADDING_" + "x" * (4 * 1024 * 1024),
            verification={"status": "not_verified", "summary": "synthetic"},
        )
    assert store._path.read_bytes() == baseline


