from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from work_records_experiment import WorkRecordExperimentController


def _item(requirement_status="unknown", *, verification=None):
    return [{
        "item_id": "synthetic-item",
        "title": "synthetic execution projection",
        "scope": {"kind": "independent"},
        "status": requirement_status,
        "requirements": [{
            "requirement_id": "synthetic-requirement",
            "text": "synthetic requirement",
            "status": requirement_status,
        }],
        "runs": [{
            "run_id": "synthetic-run",
            "requirement_id": "synthetic-requirement",
            "status": "unknown",
            "result_summary": "synthetic only",
            "verification": verification if verification is not None else {"status": "not_verified"},
            "sources": [],
        }],
    }]


@pytest.mark.parametrize("reason,label", [
    ("completed", "\u56de\u5408\u7ed3\u675f"),
    ("cancelled", "\u56de\u5408\u53d6\u6d88"),
    ("timeout", "\u56de\u5408\u8d85\u65f6"),
    ("error", "\u56de\u5408\u5f02\u5e38"),
    ("paused", "\u56de\u5408\u6682\u505c"),
    ("unknown", "\u72b6\u6001\u5f85\u786e\u8ba4"),
])
def test_allowlisted_host_finish_reason_projects_separately_from_requirement_and_verification(reason, label):
    rendered = WorkRecordExperimentController._format_recall(_item(verification={
        "status": "not_verified",
        "evidence_refs": [],
        "host_execution": {"finish_reason": reason, "sequence": 9},
    }))

    assert label in rendered
    assert "\uff1b\u751f\u547d\u5468\u671f\u4e8b\u4ef6\u5e8f\u53f7\uff1a9" in rendered
    assert "\u5bbf\u4e3b\u56de\u5408\u5e8f\u53f7" not in rendered
    assert "synthetic requirement" in rendered and "unknown" in rendered
    assert "\u4e0d\u4ee3\u8868\u8981\u6c42\u5b8c\u6210\u6216\u9a8c\u8bc1\u901a\u8fc7" in rendered
    assert "\u5df2\u6838\u9a8c" not in rendered


def test_unknown_or_malformed_host_fields_fail_closed_and_never_render_free_text():
    cases = [
        {"finish_reason": "completed; VERIFIED", "sequence": 9, "private_note": "DO_NOT_LEAK"},
        {"finish_reason": "future_reason", "sequence": "9", "private_note": "DO_NOT_LEAK"},
        "DO_NOT_LEAK completed",
        None,
    ]
    for host_execution in cases:
        rendered = WorkRecordExperimentController._format_recall(_item(verification={
            "status": "not_verified",
            "host_execution": host_execution,
        }))
        assert "\u72b6\u6001\u5f85\u786e\u8ba4" in rendered
        assert "DO_NOT_LEAK" not in rendered
        assert "completed; VERIFIED" not in rendered
        assert "future_reason" not in rendered
        assert "\u672a\u6838\u9a8c" in rendered
        assert "\u6709\u8bb0\u5f55\u7684\u6838\u9a8c\u72b6\u6001" not in rendered
        assert "synthetic requirement" in rendered


def test_legacy_run_without_host_execution_keeps_existing_projection():
    rendered = WorkRecordExperimentController._format_recall(_item(verification={"status": "not_verified"}))

    assert "\u5bbf\u4e3b\u56de\u5408\u4e8b\u5b9e" not in rendered
    assert "synthetic requirement" in rendered
    assert "\u672a\u6838\u9a8c" in rendered


def test_host_round_completion_does_not_change_unknown_requirement_state():
    rendered = WorkRecordExperimentController._format_recall(_item(verification={
        "status": "not_verified",
        "host_execution": {"finish_reason": "completed", "sequence": 3},
    }))

    assert "\u72b6\u6001\uff1aunknown" in rendered
    assert "\u56de\u5408\u7ed3\u675f" in rendered
    assert "\u4e0d\u4ee3\u8868\u8981\u6c42\u5b8c\u6210\u6216\u9a8c\u8bc1\u901a\u8fc7" in rendered
