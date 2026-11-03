from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

CORE = Path(__file__).resolve().parents[1]
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))

from day_journal import DayJournal, JournalConflict
from day_journal_summary_accept import SummaryAcceptanceError, accept_summary_candidate
from day_journal_summary import _candidate_binding_signature


def source_fingerprint(sources):
    normalized = [{"source_id": item["source_id"], "text": item["text"]} for item in sources]
    payload = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def setup(tmp_path):
    journal = DayJournal(tmp_path / "synthetic-journal")
    at = datetime(2026, 10, 29, 8, tzinfo=timezone.utc)
    row = journal.save_day(at, "原有日记摘要", ["project:alpha"], summary_authorized=True)
    candidate = {
        "status": "candidate", "summary": "模型生成的未经核实摘要",
        "sources": ["chat:s1:m7"], "verified": False,
        "notice": "未经核实的模型摘要候选",
        "source_fingerprint": source_fingerprint([{"source_id": "chat:s1:m7", "text": "合成来源内容"}]),
        "context_token": "ctx-1",
    }
    candidate["binding_signature"] = _candidate_binding_signature(candidate)
    snapshot = {
        "date": "2026-10-29", "project_id": "project:alpha", "session_id": "session:1",
        "sources": [{"source_id": "chat:s1:m7", "text": "合成来源内容"}],
        "context_token": "ctx-1",
    }
    return journal, at, row, candidate, snapshot


def accept(journal, at, row, candidate, snapshot, **overrides):
    args = dict(
        candidate=candidate, approval=lambda preview: True, at=at, project_id="project:alpha",
        session_id="session:1", snapshot=snapshot,
        expected_revision=row["revision"], segment_id="summary:accept-1",
        candidate_context_token="ctx-1", current_context_token=lambda: "ctx-1",
        validate_snapshot=lambda supplied: True,
    )
    args.update(overrides)
    return accept_summary_candidate(journal, **args)


def test_accepts_only_as_unverified_append_and_preserves_original_and_evidence(tmp_path):
    journal, at, row, candidate, snapshot = setup(tmp_path)
    result = accept(journal, at, row, candidate, snapshot)
    segment = result["segments"][0]
    assert result["summary"] == "原有日记摘要"
    assert segment["summary"] == candidate["summary"]
    assert segment["summary_metadata"] == {
        "kind": "model_summary_candidate", "verified": False,
        "source_ids": ["chat:s1:m7"],
        "source_fingerprint": candidate["source_fingerprint"],
        "context_fingerprint": hashlib.sha256(b"ctx-1").hexdigest(),
    }
    assert len(segment["summary_metadata"]["source_fingerprint"]) == 64
    assert "source_snapshot" not in segment["summary_metadata"]
    assert journal.read_day("2026-10-29") == result


def test_rejects_without_literal_approval_or_candidate_unverified_marker(tmp_path):
    journal, at, row, candidate, snapshot = setup(tmp_path)
    for approval in (False, 1, "yes", lambda preview: False):
        with pytest.raises(SummaryAcceptanceError):
            accept(journal, at, row, candidate, snapshot, approval=approval)
    candidate["verified"] = True
    with pytest.raises(SummaryAcceptanceError):
        accept(journal, at, row, candidate, snapshot)
    assert journal.read_day("2026-10-29") == row


@pytest.mark.parametrize("change", ["date", "project", "session", "source", "context"])
def test_snapshot_mismatch_fails_closed(tmp_path, change):
    journal, at, row, candidate, snapshot = setup(tmp_path)
    if change == "date": snapshot["date"] = "2026-10-28"
    if change == "project": snapshot["project_id"] = "project:other"
    if change == "session": snapshot["session_id"] = "session:other"
    if change == "source": snapshot["sources"][0]["source_id"] = "chat:other"
    if change == "context": snapshot["context_token"] = "stale"
    with pytest.raises(SummaryAcceptanceError):
        accept(journal, at, row, candidate, snapshot)
    assert journal.read_day("2026-10-29") == row


def test_stale_revision_missing_snapshot_validation_and_cross_project_fail_closed(tmp_path):
    journal, at, row, candidate, snapshot = setup(tmp_path)
    with pytest.raises(JournalConflict):
        accept(journal, at, row, candidate, snapshot, expected_revision=99)
    with pytest.raises(SummaryAcceptanceError):
        accept(journal, at, row, candidate, snapshot, validate_snapshot=lambda supplied: False)
    with pytest.raises(SummaryAcceptanceError):
        accept(journal, at, row, candidate, snapshot, project_id="project:other")
    assert journal.read_day("2026-10-29") == row


def test_same_id_replay_is_idempotent_but_conflicting_content_never_overwrites(tmp_path):
    journal, at, row, candidate, snapshot = setup(tmp_path)
    first = accept(journal, at, row, candidate, snapshot)
    assert accept(journal, at, row, candidate, snapshot, expected_revision=row["revision"]) == first
    changed = {**candidate, "summary": "不同的合成摘要"}
    changed["binding_signature"] = _candidate_binding_signature(changed)
    with pytest.raises(JournalConflict):
        accept(journal, at, row, changed, snapshot, expected_revision=first["revision"])
    assert journal.read_day("2026-10-29") == first


def test_candidate_without_sources_or_unvalidated_snapshot_is_not_saved(tmp_path):
    journal, at, row, candidate, snapshot = setup(tmp_path)
    candidate["sources"] = []
    with pytest.raises(SummaryAcceptanceError):
        accept(journal, at, row, candidate, snapshot)
    assert journal.read_day("2026-10-29") == row


def test_same_source_id_with_changed_text_cannot_be_substituted(tmp_path):
    journal, at, row, candidate, snapshot = setup(tmp_path)
    snapshot["sources"][0]["text"] = "同一个来源ID，但替换成其他原文"
    with pytest.raises(SummaryAcceptanceError):
        accept(journal, at, row, candidate, snapshot)
    assert journal.read_day("2026-10-29") == row


def test_acceptance_requires_confirmation_preview_and_never_copies_excerpts(tmp_path):
    journal, at, row, candidate, snapshot = setup(tmp_path)
    previews = []
    result = accept(journal, at, row, candidate, snapshot,
                    approval=lambda preview: (previews.append(preview) or True))
    assert previews[0]["source_text_persisted"] is False
    assert previews[0]["stored_as_unverified"] is True
    assert "合成来源内容" not in repr(previews[0])
    metadata = result["segments"][0]["summary_metadata"]
    assert "source_snapshot" not in metadata
    assert metadata["source_ids"] == ["chat:s1:m7"]


def test_candidate_context_token_cannot_be_replaced(tmp_path):
    journal, at, row, candidate, snapshot = setup(tmp_path)
    candidate["context_token"] = "ctx-replaced"
    with pytest.raises(SummaryAcceptanceError):
        accept(journal, at, row, candidate, snapshot,
               candidate_context_token="ctx-replaced",
               current_context_token=lambda: "ctx-replaced")
    assert journal.read_day("2026-10-29") == row



def test_mutating_candidate_binding_and_matching_snapshot_is_rejected(tmp_path):
    journal, at, row, candidate, snapshot = setup(tmp_path)
    changed_sources = [{"source_id": "chat:s1:m7", "text": "被替换的来源内容"}]
    candidate["source_fingerprint"] = source_fingerprint(changed_sources)
    snapshot["sources"] = changed_sources
    snapshot["context_token"] = "ctx-replaced"
    candidate["context_token"] = "ctx-replaced"
    with pytest.raises(SummaryAcceptanceError):
        accept(journal, at, row, candidate, snapshot,
               candidate_context_token="ctx-replaced",
               current_context_token=lambda: "ctx-replaced")
    assert journal.read_day("2026-10-29") == row


def test_mutated_save_confirmation_preview_fails_before_append(tmp_path):
    journal, at, row, candidate, snapshot = setup(tmp_path)

    def corrupt_preview(preview):
        preview["source_text_persisted"] = True
        return True

    with pytest.raises(SummaryAcceptanceError):
        accept(journal, at, row, candidate, snapshot, approval=corrupt_preview)
    assert journal.read_day("2026-10-29") == row
