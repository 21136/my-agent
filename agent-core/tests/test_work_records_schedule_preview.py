from __future__ import annotations

import sys
from pathlib import Path

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

from work_records_schedule_preview import build_schedule_preview, pick_schedule_batch


def test_preview_orders_ready_items_and_reports_unmet_or_unseen_dependencies():
    groups = [{
        "kind": "project", "id": "project-alpha", "omitted_item_count": 0,
        "items": [
            {"item_id": "item-a", "priority": 2, "requirements": [
                {"requirement_id": "done", "text": "done", "status": "completed", "depends_on": [], "schedule_claim": None},
                {"requirement_id": "ready-low", "text": "ready-low", "status": "queued", "depends_on": ["done"], "schedule_claim": None},
                {"requirement_id": "ready-high", "text": "ready-high", "status": "pending", "depends_on": [], "schedule_claim": None},
                {"requirement_id": "waiting", "text": "waiting", "status": "queued", "depends_on": ["missing"], "schedule_claim": None},
                {"requirement_id": "blocked", "text": "blocked", "status": "queued", "depends_on": ["ready-low"], "schedule_claim": None},
            ], "omitted_requirement_count": 0},
            {"item_id": "item-b", "priority": 5, "requirements": [
                {"requirement_id": "not-ready", "text": "not-ready", "status": "queued", "depends_on": [], "schedule_claim": None},
            ], "omitted_requirement_count": 0},
        ],
    }]
    before = repr(groups)

    result = build_schedule_preview(groups)

    assert [(r["item_id"], r["requirement_id"]) for r in result["candidates"]] == [
        ("item-b", "not-ready"), ("item-a", "ready-high"), ("item-a", "ready-low"),
    ]
    assert {r["requirement_id"] for r in result["blocked"]} == {"waiting", "blocked"}
    assert "不在已授权预览中" in result["blocked"][1]["reasons"][0]
    assert repr(groups) == before
    assert any("只读预览" in text and "不会启动任务" in text for text in result["limitations"])


def test_preview_keeps_same_item_and_requirement_ids_isolated_per_project():
    groups = [
        {"kind": "project", "id": "project-a", "items": [{
            "item_id": "shared-item", "priority": 2, "requirements": [
                {"requirement_id": "shared-req", "text": "shared-req", "status": "queued", "depends_on": ["dep"], "schedule_claim": None},
                {"requirement_id": "dep", "text": "dep", "status": "completed", "depends_on": [], "schedule_claim": None},
            ],
        }]},
        {"kind": "project", "id": "project-b", "items": [{
            "item_id": "shared-item", "priority": 2, "requirements": [
                {"requirement_id": "shared-req", "text": "shared-req", "status": "queued", "depends_on": ["dep"], "schedule_claim": None},
                {"requirement_id": "dep", "text": "dep", "status": "pending", "depends_on": [], "schedule_claim": None},
            ],
        }]},
    ]

    result = build_schedule_preview(groups)

    assert {(row["project_id"], row["requirement_id"]) for row in result["candidates"]} == {
        ("project-a", "shared-req"), ("project-b", "dep"),
    }
    blocked = next(row for row in result["blocked"] if row["project_id"] == "project-b")
    assert "dep 状态为 pending" in blocked["reasons"][0]


def test_preview_marks_candidates_as_non_authoritative_for_actual_run_start():
    result = build_schedule_preview([{
        "kind": "project", "id": "project-a", "items": [{
            "item_id": "item", "priority": 3, "requirements": [
                {"requirement_id": "r", "text": "r", "status": "queued", "depends_on": [], "schedule_claim": None},
            ],
        }],
    }])

    assert len(result["candidates"]) == 1
    assert any("运行授权" in text and "启动门槛" in text for text in result["limitations"])


def test_preview_treats_invalid_dependency_shapes_as_blocked():
    result = build_schedule_preview([{
        "kind": "project", "id": "project-a", "items": [{
            "item_id": "item", "priority": 3, "requirements": [
                {"requirement_id": "r", "text": "r", "status": "queued", "depends_on": "dep", "schedule_claim": None},
            ],
        }],
    }])

    assert result["candidates"] == []
    assert result["blocked"][0]["reasons"] == ["依赖信息无效"]


def test_preview_returns_unavailable_for_non_list_projection():
    assert build_schedule_preview(None) == {
        "state": "unavailable", "candidates": [], "blocked": [],
        "limitations": ["authorized_projection_unavailable"],
    }


def test_claimed_and_unknown_requirements_are_never_pickable():
    preview = build_schedule_preview([{
        "kind": "project", "id": "project-a", "items": [{
            "item_id": "item", "priority": 3, "requirements": [
                {"requirement_id": "claimed", "text": "claimed", "status": "queued", "depends_on": [],
                 "schedule_claim": {"state": "claimed", "owner_id": "private"}},
                {"requirement_id": "unknown", "text": "unknown", "status": "queued", "depends_on": [],
                 "schedule_claim": {"state": "unknown", "owner_id": "private"}},
                {"requirement_id": "ready", "text": "ready", "status": "queued", "depends_on": [], "schedule_claim": None},
            ],
        }],
    }])

    assert [row["requirement_id"] for row in preview["candidates"]] == ["ready"]
    assert {row["requirement_id"] for row in preview["blocked"]} == {"claimed", "unknown"}
    assert all("owner_id" not in row for row in preview["blocked"])


def test_picker_requires_explicit_opt_in_complete_preview_and_finite_limit():
    preview = build_schedule_preview([{
        "kind": "project", "id": "project-a", "items": [
            {"item_id": "low", "priority": 1, "requirements": [
                {"requirement_id": "r-low", "text": "r-low", "status": "queued", "depends_on": [], "schedule_claim": None}]},
            {"item_id": "high", "priority": 5, "requirements": [
                {"requirement_id": "r-high", "text": "r-high", "status": "pending", "depends_on": [], "schedule_claim": None}]},
            {"item_id": "mid", "priority": 3, "requirements": [
                {"requirement_id": "r-mid", "text": "r-mid", "status": "queued", "depends_on": [], "schedule_claim": None}]},
        ],
    }])

    import pytest
    with pytest.raises(PermissionError):
        pick_schedule_batch(preview, enabled=False)
    assert [row["requirement_id"] for row in pick_schedule_batch(preview, enabled=True, limit=2)] == [
        "r-high", "r-mid",
    ]
    # A continuation requesting only one slot still receives the true queue head.
    assert [row["requirement_id"] for row in pick_schedule_batch(
        preview, enabled=True, limit=1,
    )] == ["r-high"]
    assert [row["requirement_id"] for row in pick_schedule_batch(preview, enabled=True, limit=3)] == [
        "r-high", "r-mid", "r-low",
    ]
    with pytest.raises(ValueError):
        pick_schedule_batch(preview, enabled=True, limit=7)
    malformed = {**preview, "candidates": [{**preview["candidates"][0], "priority": True}]}
    with pytest.raises(ValueError, match="priority"):
        pick_schedule_batch(malformed, enabled=True)
    preview["omissions"] = ["hidden rows"]
    with pytest.raises(ValueError, match="complete authorized"):
        pick_schedule_batch(preview, enabled=True)


def test_preview_never_promotes_candidates_when_projection_is_truncated():
    result = build_schedule_preview([{
        "kind": "project", "id": "project-beta", "omitted_item_count": 2,
        "items": [{"item_id": "i", "priority": 3, "requirements": [
            {"requirement_id": "r", "text": "r", "status": "queued", "depends_on": ["hidden"]},
        ], "omitted_requirement_count": 1}],
    }])
    assert result["candidates"] == []
    assert len(result["blocked"]) == 1
    assert result["omissions"]
    assert any("分页省略" in text for text in result["limitations"])


def test_preview_fails_closed_for_invalid_priority_without_leaking_claim_data():
    result = build_schedule_preview([{
        "kind": "project", "id": "project-a", "items": [{
            "item_id": "item", "priority": True, "requirements": [{
                "requirement_id": "r", "status": "queued", "depends_on": [],
                "schedule_claim": {"state": "unknown", "owner_id": "private-owner",
                                   "reservation_id": "private-reservation"},
            }],
        }],
    }])

    assert result["candidates"] == []
    assert result["blocked"][0]["reasons"] == [
        "优先级无效，无法确认调度顺序",
        "已存在持久调度 claim（可能仍在运行或结果未知），禁止自动重派",
        "要求原文缺失或超出调度表单上限，无法预填启动",
    ]
    assert "private-owner" not in repr(result)
    assert "private-reservation" not in repr(result)


def test_preview_fails_closed_for_missing_claim_or_dependency_fields():
    result = build_schedule_preview([{
        "kind": "project", "id": "project-a", "items": [{
            "item_id": "item", "priority": 3, "requirements": [
                {"requirement_id": "claim-missing", "text": "claim-missing", "status": "queued", "depends_on": []},
                {"requirement_id": "dependencies-missing", "text": "dependencies-missing", "status": "queued", "schedule_claim": None},
            ],
        }],
    }])

    assert result["candidates"] == []
    assert {row["requirement_id"] for row in result["blocked"]} == {
        "claim-missing", "dependencies-missing",
    }
    assert any("claim 状态字段缺失" in reason for row in result["blocked"] for reason in row["reasons"])
    assert any("依赖信息无效" in reason for row in result["blocked"] for reason in row["reasons"])


def test_preview_projects_task_and_suggested_paths_for_workbench_prefill():
    result = build_schedule_preview([{
        "kind": "project", "id": "project-a", "items": [{
            "item_id": "item", "priority": 3, "requirements": [
                {"requirement_id": "r", "text": "Update NOTES.md after reading PROJECT.md", "status": "queued",
                 "depends_on": [], "schedule_claim": None},
            ],
        }],
    }])
    candidate = result["candidates"][0]
    assert candidate["task"] == "Update NOTES.md after reading PROJECT.md"
    assert "PROJECT.md" in candidate["suggested_read_targets"]
    assert candidate["suggested_write_targets"] == ["NOTES.md"]


def test_preview_write_default_falls_back_to_first_file_mention_without_notes():
    result = build_schedule_preview([{
        "kind": "project", "id": "project-a", "items": [{
            "item_id": "item", "priority": 3, "requirements": [
                {"requirement_id": "r", "text": "Adjust README.md for the launch", "status": "queued",
                 "depends_on": [], "schedule_claim": None},
            ],
        }],
    }])
    candidate = result["candidates"][0]
    assert candidate["suggested_read_targets"] == ["README.md"]
    assert candidate["suggested_write_targets"] == ["README.md"]
