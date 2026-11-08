from __future__ import annotations

import sys
from pathlib import Path

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

from project_worker_host import ProjectWorkerHostRequest
from work_records_schedule_preview import build_schedule_preview


def test_schedule_candidate_cannot_be_promoted_to_worker_request_without_fresh_host_inputs():
    """A read-only candidate is identity/priority only, not execution authorization."""
    preview = build_schedule_preview([{
        "kind": "project",
        "id": "synthetic-project",
        "items": [{
            "item_id": "synthetic-item",
            "priority": 3,
            "requirements": [{
                "requirement_id": "synthetic-requirement",
                "status": "queued",
                "depends_on": [],
                "schedule_claim": None,
            }],
        }],
    }])
    candidate = preview["candidates"][0]

    assert set(candidate) == {"project_id", "item_id", "requirement_id", "priority"}
    required_execution_fields = {
        "project_id", "task_id", "task", "read_targets", "write_targets",
    }
    assert not required_execution_fields <= candidate.keys()
    assert "不会启动任务" in preview["limitations"][0]
    assert "运行授权" in preview["limitations"][2]
    assert ProjectWorkerHostRequest.__dataclass_fields__.keys() >= required_execution_fields


def test_truncated_or_unavailable_preview_never_supplies_dispatch_authority():
    unavailable = build_schedule_preview(None)
    truncated = build_schedule_preview([{
        "kind": "project",
        "id": "synthetic-project",
        "omitted_item_count": 1,
        "items": [{
            "item_id": "synthetic-item",
            "priority": 3,
            "requirements": [{
                "requirement_id": "synthetic-requirement",
                "status": "queued",
                "depends_on": [],
                "schedule_claim": None,
            }],
        }],
    }])

    assert unavailable["candidates"] == []
    assert truncated["omissions"]
    # Truncation blocks promotion to a runnable candidate, even for visible rows.
    assert truncated["candidates"] == []
    assert len(truncated["blocked"]) == 1
    assert any("省略" in text for text in truncated["limitations"])
