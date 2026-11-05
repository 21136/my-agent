from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

from day_journal import DayJournal
from day_journal_recall import query_day_journal


def write_day(root: Path, day: str, *, summary="记录摘要", projects=None, segments=None):
    root.mkdir(parents=True, exist_ok=True)
    payload = {
        "date": day,
        "summary": summary,
        "projects": projects if projects is not None else ["proj-1"],
        "segments": segments if segments is not None else [],
        "revision": 1,
    }
    (root / f"{day}.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_exact_day_returns_only_explicit_local_facts_and_stable_coordinate(tmp_path):
    root = tmp_path / "journal"
    write_day(root, "2026-10-28", segments=[{
        "project_id": "proj-1", "date": "2026-10-28", "segment_id": "seg-2",
        "summary": "完成只读接口草案", "source": {"kind": "session", "session_id": "session-9"},
        "occurred_at": "2026-10-28T10:00:00+08:00",
    }])

    result = query_day_journal(root, day="2026-10-28", authorized=True, project_id="proj-1")

    assert result["status"] == "ok"
    assert result["records"] == [{
        "date": "2026-10-28", "summary": "记录摘要", "projects": ["proj-1"],
        "segments": [{"scope": "project", "project_id": "proj-1", "date": "2026-10-28", "segment_id": "seg-2",
                      "summary": "完成只读接口草案", "source": {"kind": "session", "session_id": "session-9"}}],
    }]
    assert "path" not in json.dumps(result).lower()
    assert "diff" not in json.dumps(result).lower()


def test_query_requires_explicit_authorization_and_does_not_touch_disk(tmp_path):
    root = tmp_path / "missing"
    result = query_day_journal(root, day="2026-10-28", authorized=False)
    assert result["status"] == "unauthorized"
    assert not root.exists()


def test_missing_record_is_not_claimed_as_nothing_happened(tmp_path):
    result = query_day_journal(tmp_path / "journal", day="2026-10-28", authorized=True)
    assert result["status"] == "no_record"
    assert result["records"] == []
    assert "未发生" not in result["message"]


def test_empty_summary_is_reported_without_inference(tmp_path):
    root = tmp_path / "journal"
    write_day(root, "2026-10-28", summary=None)
    result = query_day_journal(root, day="2026-10-28", authorized=True)
    assert result["status"] == "summary_missing"
    assert result["records"][0]["summary"] is None


def test_range_requires_bounded_explicit_dates_and_returns_partial_status(tmp_path):
    root = tmp_path / "journal"
    write_day(root, "2026-10-28")
    (root / "2026-10-29.json").write_text("{broken", encoding="utf-8")
    result = query_day_journal(root, start_date="2026-10-28", end_date="2026-10-29", authorized=True)
    assert result["status"] == "partial"
    assert [record["date"] for record in result["records"]] == ["2026-10-28"]
    assert result["issues"] == [{"date": "2026-10-29", "status": "corrupt"}]
    with pytest.raises(ValueError):
        query_day_journal(root, start_date="2026-01-01", end_date="2026-02-15", authorized=True)


def test_oversized_record_and_project_filter_are_explicit(tmp_path):
    root = tmp_path / "journal"
    write_day(root, "2026-10-28", projects=["proj-1", "proj-2"])
    result = query_day_journal(root, day="2026-10-28", project_id="proj-missing", authorized=True)
    assert result["status"] == "no_record"
    (root / "2026-10-28.json").write_text(" " * 4097, encoding="utf-8")
    result = query_day_journal(root, day="2026-10-28", authorized=True, max_record_bytes=4096)
    assert result["status"] == "partial"
    assert result["issues"] == [{"date": "2026-10-28", "status": "oversized"}]


def test_symlink_record_is_not_followed(tmp_path):
    root = tmp_path / "journal"
    root.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    link = root / "2026-10-28.json"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    result = query_day_journal(root, day="2026-10-28", authorized=True)
    assert result["issues"] == [{"date": "2026-10-28", "status": "unsafe_path"}]


def test_native_default_timezone_contract_accepts_shanghai_local_day(tmp_path):
    root = tmp_path / "journal"
    journal = DayJournal(root)
    journal.record_segment(
        __import__("datetime").datetime.fromisoformat("2026-10-27T16:30:00+00:00"),
        "项目摘要",
        segment_id="seg-1",
        source={"kind": "session", "session_id": "session-1"},
        scope="project",
        project_id="proj-1",
        summary_authorized=True,
    )
    result = query_day_journal(root, day="2026-10-28", authorized=True)
    assert result["status"] == "summary_missing"  # native record has no day-level summary
    assert result["records"][0]["segments"][0]["summary"] == "项目摘要"


def test_custom_timezone_controls_occurred_at_date_validation(tmp_path):
    root = tmp_path / "journal"
    write_day(root, "2026-10-28", summary="day")
    payload = json.loads((root / "2026-10-28.json").read_text(encoding="utf-8"))
    payload["segments"] = [{
        "project_id": "proj-1", "date": "2026-10-28", "segment_id": "seg-1",
        "summary": "valid at UTC", "source": {"kind": "session", "session_id": "sess-1"},
        "occurred_at": "2026-10-28T18:30:00+00:00",
    }]
    (root / "2026-10-28.json").write_text(json.dumps(payload), encoding="utf-8")
    assert query_day_journal(root, day="2026-10-28", authorized=True, timezone="UTC")["status"] == "ok"
    result = query_day_journal(root, day="2026-10-28", authorized=True, timezone="Asia/Shanghai")
    assert result["status"] == "partial"
    assert result["records"] == []
    assert result["issues"] == [{"date": "2026-10-28", "status": "corrupt"}]


@pytest.mark.parametrize("defect", ["duplicate_segment", "long_summary", "cross_day_timestamp"])
def test_native_corruption_is_partial_and_never_returned_as_a_fact(tmp_path, defect):
    root = tmp_path / "journal"
    write_day(root, "2026-10-28", summary="valid")
    path = root / "2026-10-28.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    segment = {
        "project_id": "proj-1", "date": "2026-10-28", "segment_id": "seg-1",
        "summary": "valid", "source": {"kind": "session", "session_id": "sess-1"},
        "occurred_at": "2026-10-28T10:00:00+08:00",
    }
    if defect == "duplicate_segment":
        payload["segments"] = [segment, dict(segment)]
    elif defect == "long_summary":
        payload["summary"] = "x" * 2001
    else:
        segment["occurred_at"] = "2026-10-27T23:59:59+08:00"
        payload["segments"] = [segment]
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    result = query_day_journal(root, day="2026-10-28", authorized=True)
    assert result["status"] == "partial"
    assert result["records"] == []
    assert result["issues"] == [{"date": "2026-10-28", "status": "corrupt"}]


def test_windows_junction_root_is_rejected_without_returning_target_record(tmp_path):
    if os.name != "nt":
        pytest.skip("Windows junction behavior")
    target = tmp_path / "target"
    write_day(target, "2026-10-28", summary="must not escape")
    root = tmp_path / "journal-junction"
    completed = subprocess.run(["cmd", "/c", "mklink", "/J", str(root), str(target)],
                               capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        pytest.skip("junction creation unavailable")
    result = query_day_journal(root, day="2026-10-28", authorized=True)
    assert result["status"] == "partial"
    assert result["records"] == []
    assert result["issues"] == [{"date": None, "status": "unsafe_root"}]



def test_explicit_date_query_does_not_scan_other_records(tmp_path):
    root = tmp_path / "journal"
    write_day(root, "2026-10-28", summary="requested")
    (root / "2026-10-29.json").write_text("corrupt unrelated record", encoding="utf-8")
    result = query_day_journal(root, day="2026-10-28", authorized=True)
    assert result["status"] == "ok"
    assert [record["date"] for record in result["records"]] == ["2026-10-28"]
    assert result["issues"] == []


def test_overlong_segment_summary_is_corrupt(tmp_path):
    root = tmp_path / "journal"
    write_day(root, "2026-10-28", summary="valid", segments=[{
        "project_id": "proj-1", "date": "2026-10-28", "segment_id": "seg-1",
        "summary": "x" * 2001, "source": {"kind": "session", "session_id": "sess-1"},
        "occurred_at": "2026-10-28T10:00:00+08:00",
    }])
    result = query_day_journal(root, day="2026-10-28", authorized=True)
    assert result["status"] == "partial"
    assert result["records"] == []
    assert result["issues"] == [{"date": "2026-10-28", "status": "corrupt"}]


def test_native_summary_metadata_is_sanitized_and_explicitly_unverified(tmp_path):
    root = tmp_path / "journal"
    journal = DayJournal(root)
    instant = __import__("datetime").datetime.fromisoformat("2026-10-28T10:00:00+08:00")
    journal.save_day(instant, "日记摘要", ["proj-1"], summary_authorized=True)
    metadata = {
        "kind": "model_summary_candidate", "verified": False,
        "source_ids": ["source-1"], "snapshot_sha256": "a" * 64,
        "source_snapshot": [{"source_id": "source-1", "text": "PRIVATE-SNAPSHOT-CONTENT"}],
    }
    journal.add_segment(instant, "proj-1", "候选摘要", segment_id="seg-1",
                        source={"kind": "session", "session_id": "session-1"},
                        expected_revision=1, summary_authorized=True, summary_metadata=metadata)
    assert journal.read_day("2026-10-28")["segments"][0]["summary_metadata"] == metadata
    result = query_day_journal(root, day="2026-10-28", authorized=True)
    assert result["status"] == "ok"
    segment = result["records"][0]["segments"][0]
    assert segment["summary"] == "候选摘要"
    assert segment["source"] == {"kind": "session", "session_id": "session-1"}
    assert segment["summary_metadata"] == {
        "kind": "model_summary_candidate", "verified": False, "source_ids": ["source-1"]}
    serialized = json.dumps(result, ensure_ascii=False)
    assert "PRIVATE-SNAPSHOT-CONTENT" not in serialized
    assert "source_snapshot" not in serialized
    assert "snapshot_sha256" not in serialized


@pytest.mark.parametrize("defect", ["verified", "duplicate_ids", "empty_snapshot", "bad_hash", "extra_field"])
def test_bad_summary_metadata_is_partial_and_not_returned(tmp_path, defect):
    root = tmp_path / "journal"
    metadata = {"kind": "model_summary_candidate", "verified": False,
                "source_ids": ["source-1"], "snapshot_sha256": "a" * 64,
                "source_snapshot": [{"source_id": "source-1", "text": "SECRET"}]}
    if defect == "verified":
        metadata["verified"] = True
    elif defect == "duplicate_ids":
        metadata["source_ids"] = ["source-1", "source-1"]
    elif defect == "empty_snapshot":
        metadata["source_snapshot"] = []
    elif defect == "bad_hash":
        metadata["snapshot_sha256"] = "bad"
    else:
        metadata["extra"] = "SECRET"
    write_day(root, "2026-10-28", segments=[{
        "project_id": "proj-1", "date": "2026-10-28", "segment_id": "seg-1",
        "summary": "candidate", "source": {"kind": "session", "session_id": "session-1"},
        "occurred_at": "2026-10-28T10:00:00+08:00", "summary_metadata": metadata,
    }])
    result = query_day_journal(root, day="2026-10-28", authorized=True)
    assert result["status"] == "partial"
    assert result["records"] == []
    assert result["issues"] == [{"date": "2026-10-28", "status": "corrupt"}]
    assert "SECRET" not in json.dumps(result)
