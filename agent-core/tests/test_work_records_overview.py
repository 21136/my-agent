from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from paths import AgentPaths
from session import Session, SessionMeta
from work_records import WorkRecordStore
from work_records_overview import render_work_records_overview


def _store(root: Path, *, session_id="session-overview", project_id="project-overview"):
    paths = AgentPaths(
        agent_root=root,
        workspace=root / "workspace",
        data=root / "data",
        evolve=root / "evolve",
    )
    session_dir = paths.data / "sessions" / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    session = Session(
        conversation_id=session_id,
        session_dir=session_dir,
        goal="synthetic overview tests",
        meta=SessionMeta(project_id=project_id),
        messages=[],
        paths=paths,
    )
    return session, WorkRecordStore(session)


def _item(store, title, scope, requirements, *, statuses=None):
    row = store.create_item(title=title, scope=scope)
    statuses = statuses or ["pending"] * len(requirements)
    for index, (text, status) in enumerate(zip(requirements, statuses)):
        req = store.add_requirement(
            row["item_id"], text, event_id=f"event-{row['item_id']}-{index}",
        )
        if status == "in_progress":
            store.begin_requirement_run(
                row["item_id"], req["requirement_id"],
                run_id=f"run-{row['item_id']}-{index}", expected_current_run_id=None,
            )
        elif status != "pending":
            run_id = f"run-{row['item_id']}-{index}"
            store.begin_requirement_run(
                row["item_id"], req["requirement_id"],
                run_id=run_id, expected_current_run_id=None,
            )
            store.record_result_if_current_run(
                row["item_id"], req["requirement_id"], run_id=run_id,
                result_id=f"result-{row['item_id']}-{index}", status=status,
                summary="synthetic fixture only",
                verification={"status": "fixture-only"}, sources=[],
            )
    return row


def test_groups_four_or_five_project_items_and_keeps_independent_items_separate_and_read_only(tmp_path):
    root = tmp_path / "agent-root"
    root.mkdir()
    session, store = _store(root)
    project_items = [
        _item(store, f"project-item-{n}", {"kind": "project", "project_id": "project-overview"},
              [f"project-{n}-requirement-a", f"project-{n}-requirement-b"])
        for n in range(5)
    ]
    independent_a = _item(store, "resume-a", {"kind": "independent"}, ["resume-a-requirement"])
    independent_b = _item(store, "resume-b", {"kind": "independent"}, ["resume-b-requirement"])
    before = store._path.read_bytes()

    overview = render_work_records_overview(session)

    assert "1 个项目组" in overview
    assert "2 个独立事项组" in overview
    assert "5 个事项" in overview
    assert "project-overview" in overview
    assert "project-0-requirement-a" in overview
    assert "project-4-requirement-b" in overview
    assert "resume-a" in overview and "resume-b" in overview
    assert independent_a["item_id"] != independent_b["item_id"]
    assert "工作记录和历史卡片不修改" in overview
    assert "读取可能创建 .work-records.lock" in overview
    assert "已读并收起" not in overview
    assert store._path.read_bytes() == before

    # A different Session in the same temporary root is not searched or merged.
    other_session, other_store = _store(root, session_id="other-session", project_id="other-project")
    _item(other_store, "foreign-secret-marker", {"kind": "independent"}, ["foreign-requirement"])
    assert "foreign-secret-marker" not in render_work_records_overview(session)


def test_priority_order_and_exact_group_omission_counts(tmp_path):
    root = tmp_path / "agent-root"
    root.mkdir()
    _session, store = _store(root)
    statuses = ["completed", "stopped", "in_progress", "unknown", "blocked", "completed", "completed"]
    for index, status in enumerate(statuses):
        _item(store, f"independent-{index}-{status}", {"kind": "independent"}, [f"req-{index}"], statuses=[status])

    overview = render_work_records_overview(store._session)

    blocked_pos = overview.index("independent-4-blocked")
    unknown_pos = overview.index("independent-3-unknown")
    in_progress_pos = overview.index("independent-2-in_progress")
    assert blocked_pos < unknown_pos < in_progress_pos
    assert "执行状态待确认" in overview
    assert "还有 1 组未展开，包含 1 个事项" in overview
    assert "共 7 组" in overview
    assert "全部已记录要求状态计数：受阻 1、结果待确认 1、执行状态待确认 1、已停止 1、已记录完成 3" in overview
    assert "independent-0-completed" in overview  # completion is lower priority, not dropped first


def test_item_and_requirement_limits_report_exact_omissions(tmp_path):
    root = tmp_path / "agent-root"
    root.mkdir()
    session, store = _store(root)
    for item_index in range(8):
        _item(
            store,
            f"many-project-item-{item_index}",
            {"kind": "project", "project_id": "project-overview"},
            [f"item-{item_index}-requirement-{req_index}" for req_index in range(9)],
        )

    overview = render_work_records_overview(session)

    assert "还有 3 个事项未展开" in overview
    assert "还有 3 项要求未展开" in overview
    assert "many-project-item-0" in overview
    assert "many-project-item-7" not in overview
    assert "item-0-requirement-8" not in overview


def test_empty_sidecar_does_not_claim_work_never_happened(tmp_path):
    root = tmp_path / "agent-root"
    root.mkdir()
    session, _store_obj = _store(root)

    store_obj = WorkRecordStore(session)
    assert not store_obj._path.exists()

    overview = render_work_records_overview(session)

    assert "不代表相关工作没有发生" in overview
    assert "没有已记录事项" in overview
    assert "读取可能创建 .work-records.lock" in overview
    assert not store_obj._path.exists()  # lock acquisition may create only the lock file
    assert store_obj._lock_path.exists()


def test_text_fields_are_clipped_with_exact_character_omission_counts(tmp_path):
    root = tmp_path / "agent-root"
    root.mkdir()
    long_project_id = "p" * 200
    session, store = _store(root, project_id=long_project_id)
    _item(
        store, "t" * 100, {"kind": "project", "project_id": long_project_id},
        ["r" * 200],
    )

    overview = render_work_records_overview(session)

    assert len(overview) < 2000
    assert "另有 120 字" in overview
    assert "另有 20 字" in overview
    assert "另有 80 字" in overview



def test_mixed_persisted_requirement_statuses_show_counts_without_false_execution_claims(tmp_path):
    root = tmp_path / "agent-root"
    root.mkdir()
    session, store = _store(root)
    _item(store, "synthetic mixed", {"kind": "project", "project_id": "project-overview"},
          ["blocked", "unknown", "running marker", "stopped", "pending", "completed", "completed second"],
          statuses=["blocked", "unknown", "in_progress", "stopped", "pending", "completed", "completed"])
    before = store._path.read_bytes()

    overview = render_work_records_overview(session)

    assert "关注：有要求受阻" in overview
    assert "状态计数：受阻 1、结果待确认 1、执行状态待确认 1、已停止 1、待开始 1、已记录完成 2" in overview
    assert "不代表验证通过" in overview
    assert "运行中" not in overview
    assert "还有 1 项要求未展开" in overview
    assert store._path.read_bytes() == before


def test_group_counts_include_requirements_from_omitted_items_and_details():
    from work_records_overview import _format_overview
    items = []
    for n in range(6):
        statuses = ["blocked"] + ["pending"] * 6 if n == 0 else ["completed"]
        items.append({"item_id": f"item-{n}", "title": f"item-{n}",
                      "scope": {"kind": "project", "project_id": "synthetic"},
                      "requirements": [{"requirement_id": f"r-{n}-{i}", "text": f"req-{n}-{i}", "status": status}
                                       for i, status in enumerate(statuses)]})

    overview = _format_overview(items)

    assert "状态计数：受阻 1、待开始 6、已记录完成 5" in overview
    assert "还有 1 个事项未展开，包含 1 项要求未展开" in overview
    assert "还有 1 项要求未展开" in overview
    assert "item-5" not in overview



def _worker_batch_item(store, *, project_ids):
    """Create writer-shaped explicit task markers for projection tests."""
    batch_digest = "a" * 64
    original = store.create_item_with_requirement(
        title="多项目任务批次", scope={"kind": "independent"},
        requirement="原始请求：每个项目分别完成多项要求",
        event_id=f"worker-batch-original-{batch_digest}",
        idempotency_key=f"worker-batch-original:{batch_digest}",
    )
    item = original[0] if isinstance(original, tuple) else original
    for index, project_id in enumerate(project_ids):
        text = f"{project_id}-task-{index}"
        store.add_requirement(
            item["item_id"], text, event_id=f"worker-batch-task-{batch_digest}-{index}",
            source_ref={"kind": "project", "id": project_id},
            idempotency_key=f"worker-batch-task:{batch_digest}:{index}",
        )
    return item


def test_explicit_worker_batch_original_request_is_trace_only_and_not_counted(tmp_path):
    root = tmp_path / "agent-root"
    root.mkdir()
    session, store = _store(root)
    projects = [f"project-{index}" for index in range(5)]
    item = _worker_batch_item(store, project_ids=[project for project in projects for _ in range(2)])
    before = store._path.read_bytes()

    overview = render_work_records_overview(session)

    assert "5 个项目组" in overview
    assert "0 个独立事项组" in overview
    assert "1 个事项、10 项要求" in overview
    assert "原始请求（仅追溯，不计入事项/要求）：原始请求：每个项目分别完成多项要求" in overview
    assert "事项：多项目任务批次 · 原始/未标记要求" not in overview
    for project in projects:
        assert f"项目 {project}（关联 1 个事项、2 项要求" in overview
        assert f"{project}-task-" in overview
    assert "工作记录和历史卡片不修改" in overview
    assert store._path.read_bytes() == before
    persisted = store.get_item(item["item_id"])
    assert persisted["item_id"] == item["item_id"]
    assert any(req["text"] == "原始请求：每个项目分别完成多项要求" for req in persisted["requirements"])


def test_ordinary_project_source_ref_and_task_marker_without_batch_original_stay_independent(tmp_path):
    root = tmp_path / "agent-root"
    root.mkdir()
    session, store = _store(root)
    item = store.create_item(title="普通独立事项", scope={"kind": "independent"})
    store.add_requirement(
        item["item_id"], "ordinary-source-reference",
        event_id=f"worker-batch-task:{'c' * 64}-0",
        source_ref={"kind": "project", "id": "not-a-worker-batch"},
        idempotency_key=f"worker-batch-task:{'c' * 64}:0",
    )

    overview = render_work_records_overview(session)

    assert "0 个项目组" in overview
    assert "1 个独立事项组" in overview
    assert "项目 not-a-worker-batch" not in overview
    assert "ordinary-source-reference" in overview


def test_unmarked_independent_requirement_in_batch_remains_counted_and_visible(tmp_path):
    root = tmp_path / "agent-root"
    root.mkdir()
    session, store = _store(root)
    item = _worker_batch_item(store, project_ids=["project-a", "project-b"])
    store.add_requirement(
        item["item_id"], "真正独立的后续事项", event_id="genuine-independent-requirement",
        idempotency_key="genuine-independent-key",
    )

    overview = render_work_records_overview(session)

    assert "2 个项目组" in overview
    assert "1 个独立事项组" in overview
    assert "1 个事项、3 项要求" in overview
    assert "原始请求（仅追溯，不计入事项/要求）：原始请求：每个项目分别完成多项要求" in overview
    assert "真正独立的后续事项" in overview
    assert "事项：多项目任务批次 · 原始/未标记要求" in overview
