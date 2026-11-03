from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from work_records_experiment import WorkRecordExperimentController


def _item(requirements, runs):
    return [{
        "item_id": "item-synthetic",
        "title": "Synthetic item",
        "scope": {"kind": "independent"},
        "status": "unknown",
        "requirements": requirements,
        "runs": runs,
    }]


def test_run_rows_match_exact_requirement_id_not_requirement_order():
    req_a = {"requirement_id": "req-a", "text": "Requirement Alpha", "status": "unknown"}
    req_b = {"requirement_id": "req-b", "text": "Requirement Beta", "status": "blocked"}
    runs = [
        {
            "run_id": "private-run-id-a",
            "requirement_id": "req-b",
            "status": "blocked",
            "result_summary": "PRIVATE SUMMARY A",
            "verification": {"status": "not_verified", "host_execution": {"sequence": 21, "finish_reason": "cancelled"}},
        },
        {
            "run_id": "private-run-id-b",
            "requirement_id": "req-a",
            "status": "unknown",
            "result_summary": "PRIVATE SUMMARY B",
            "verification": {"status": "not_verified", "host_execution": {"sequence": 22, "finish_reason": "error"}},
        },
    ]

    rendered = WorkRecordExperimentController._format_recall(_item([req_a, req_b], runs))
    run_lines = [line for line in rendered.splitlines() if "\u56de\u5408\u7ed3\u679c" in line]

    assert len(run_lines) == 2
    assert "Requirement Beta" in run_lines[0]
    assert "Requirement Alpha" in run_lines[1]
    assert "\u5173\u8054\u8981\u6c42" in run_lines[0]
    assert "\u5bbf\u4e3b\u56de\u5408\u4e8b\u5b9e" in rendered
    assert "\u672a\u6838\u9a8c" in rendered
    assert "PRIVATE SUMMARY" not in rendered
    assert "private-run-id" not in rendered


def test_missing_duplicate_or_foreign_requirement_reference_is_not_guessed_or_leaked():
    requirements = [
        {"requirement_id": "dup-id", "text": "First duplicate requirement", "status": "unknown"},
        {"requirement_id": "dup-id", "text": "Second duplicate requirement", "status": "unknown"},
        {"requirement_id": "local-id", "text": "Local unrelated requirement", "status": "unknown"},
    ]
    runs = [
        {"status": "unknown", "result_summary": "SECRET A", "verification": {"status": "not_verified"}},
        {"requirement_id": "SECRET-DAMAGED-ID", "status": "unknown", "result_summary": "SECRET B", "verification": {"status": "not_verified"}},
        {"requirement_id": "dup-id", "status": "unknown", "result_summary": "SECRET C", "verification": {"status": "not_verified"}},
    ]

    rendered = WorkRecordExperimentController._format_recall(_item(requirements, runs))
    run_lines = [line for line in rendered.splitlines() if "\u56de\u5408\u7ed3\u679c" in line]

    assert len(run_lines) == 3
    assert all("\u65e0\u6cd5\u5173\u8054\u5230\u672c\u4e8b\u9879\u5185\u7684\u8981\u6c42" in line for line in run_lines)
    assert "SECRET-DAMAGED-ID" not in rendered
    assert "SECRET A" not in rendered and "SECRET B" not in rendered and "SECRET C" not in rendered
    assert "First duplicate requirement" not in run_lines[2]
    assert "Second duplicate requirement" not in run_lines[2]


def test_associated_long_requirement_text_is_explicitly_truncated():
    requirement_text = "r" * 200
    requirements = [{"requirement_id": "req-long", "text": requirement_text, "status": "unknown"}]
    runs = [{"requirement_id": "req-long", "status": "unknown", "verification": {"status": "not_verified"}}]

    rendered = WorkRecordExperimentController._format_recall(_item(requirements, runs))
    run_line = next(line for line in rendered.splitlines() if "\u56de\u5408\u7ed3\u679c" in line)

    assert "\u5173\u8054\u8981\u6c42" in run_line
    assert "\u6587\u672c\u622a\u65ad\uff0c\u53e6\u6709 80 \u5b57" in run_line
    assert len(run_line) < 400


def test_more_than_ten_runs_report_exact_older_run_omission_count():
    requirement = {"requirement_id": "req-many", "text": "Many-run requirement", "status": "unknown"}
    runs = [
        {
            "requirement_id": "req-many", "status": "unknown",
            "verification": {"status": "not_verified", "host_execution": {"sequence": n, "finish_reason": "unknown"}},
        }
        for n in range(1, 13)
    ]

    rendered = WorkRecordExperimentController._format_recall(_item([requirement], runs))

    assert "\u8fd8\u6709 2 \u6761\u66f4\u65e9\u56de\u5408\u8bb0\u5f55\u672a\u5c55\u5f00" in rendered
    assert rendered.count("\u56de\u5408\u7ed3\u679c") == 10
    assert "\u751f\u547d\u5468\u671f\u4e8b\u4ef6\u5e8f\u53f7\uff1a12" in rendered
    assert "\u751f\u547d\u5468\u671f\u4e8b\u4ef6\u5e8f\u53f7\uff1a1\uff08" not in rendered
