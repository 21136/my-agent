from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from work_records_experiment import WorkRecordExperimentController


def test_each_requirement_shows_source_availability_without_claiming_validation_or_location():
    items = [{
        "item_id": "synthetic-item",
        "title": "synthetic source projection",
        "scope": {"kind": "independent"},
        "status": "unknown",
        "requirements": [
            {
                "requirement_id": "req-linked-shape-only",
                "text": "has a syntax-only source reference",
                "status": "unknown",
                "source_ref": {"kind": "requirement", "id": "synthetic-source-id"},
            },
            {
                "requirement_id": "req-legacy-no-source",
                "text": "legacy requirement without a stable source",
                "status": "unknown",
            },
        ],
        "runs": [],
    }]

    rendered = WorkRecordExperimentController._format_recall(items)

    assert "\u6765\u6e90\uff1a\u5f15\u7528\u5c1a\u672a\u6838\u9a8c" in rendered
    assert "\u6765\u6e90\uff1a\u65e0\u7a33\u5b9a\u6765\u6e90\u5173\u8054" in rendered
    assert "\u5f15\u7528\u5df2\u6838\u9a8c" not in rendered
    assert "\u6765\u6e90\u53ef\u5b9a\u4f4d" not in rendered
    assert "synthetic-source-id" not in rendered


def test_legacy_and_cas_in_progress_status_projection_is_preserved_with_source_labels():
    items = [
        {
            "item_id": "legacy-item",
            "title": "legacy item",
            "scope": {"kind": "independent"},
            "status": "in_progress",
            "requirements": [{
                "requirement_id": "legacy-req", "text": "legacy in progress",
                "status": "in_progress",
            }],
            "runs": [],
        },
        {
            "item_id": "cas-item",
            "title": "cas item",
            "scope": {"kind": "independent"},
            "status": "in_progress",
            "requirements": [{
                "requirement_id": "cas-req", "text": "persisted begin",
                "status": "in_progress", "current_run_id": "synthetic-run",
            }],
            "runs": [],
        },
    ]

    rendered = WorkRecordExperimentController._format_recall(items)

    assert "legacy in progress" in rendered and "in_progress" in rendered
    assert "\u5df2\u8bb0\u5f55\u5f00\u59cb\uff0c\u5f53\u524d\u6267\u884c\u72b6\u6001\u5f85\u786e\u8ba4" in rendered
    assert rendered.count("\u6765\u6e90\uff1a\u65e0\u7a33\u5b9a\u6765\u6e90\u5173\u8054") == 2
