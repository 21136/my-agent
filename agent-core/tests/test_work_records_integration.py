from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from paths import AgentPaths
from session import create_new
from work_records import WorkRecordBindingChanged, WorkRecordScopeError, WorkRecordStore


@pytest.fixture
def isolated_session(tmp_path):
    """Create a normal Session under a disposable, isolated agent root."""
    root = tmp_path / "agent-root"
    root.mkdir()
    paths = AgentPaths(
        agent_root=root,
        workspace=root / "workspace",
        data=root / "data",
        evolve=root / "evolve",
    )
    session = create_new(paths, conversation_id="integration-session")
    return session


def test_work_items_record_order_evidence_truth_and_session_scoped_followup(isolated_session):
    # The project binding comes from this Session, not a caller-supplied project lookup.
    isolated_session.meta.project_id = "project-demo"
    store = WorkRecordStore(isolated_session)
    evidence = {"kind": "project", "id": "project-demo"}

    project = store.create_item(
        title="项目：整理登录页",
        scope={"kind": "project", "project_id": "project-demo"},
        source_refs=[evidence],
    )
    project_requirement = store.add_requirement(
        project["item_id"], "检查登录页布局", event_id="project-request",
        source_ref=evidence,
    )
    project_result = store.record_result(
        project["item_id"], project_requirement["requirement_id"],
        run_id="project-run", result_id="project-result", status="completed",
        summary="已调整布局；此集成测试仅验证记录写入，不代表真实页面验收。",
        verification={"status": "not_verified", "summary": "未运行页面或真实用户验收"},
        sources=[evidence],
    )

    resume = store.create_item(
        title="独立事项：更新简历",
        scope={"kind": "independent"},
    )
    resume_requirement = store.add_requirement(
        resume["item_id"], "补充最近项目经历", event_id="resume-request",
    )
    # Model an operation exception as an unknown outcome, not a success.
    try:
        raise RuntimeError("simulated local operation exception")
    except RuntimeError as exc:
        resume_result = store.record_result(
            resume["item_id"], resume_requirement["requirement_id"],
            run_id="resume-run", result_id="resume-result", status="unknown",
            summary=f"操作异常，未确认是否完成：{exc}",
            verification={"status": "not_verified", "summary": "异常后未做结果核验"},
        )

    assert project_result["sources"] == [evidence]
    assert project_result["verification"]["status"] == "not_verified"
    assert resume_result["status"] == "unknown"
    assert "未确认是否完成" in resume_result["result_summary"]
    assert resume_result["verification"]["status"] == "not_verified"

    # A controlled follow-up query only sees this Session's records.
    assert [item["item_id"] for item in store.search("登录页")] == [project["item_id"]]
    assert [item["item_id"] for item in store.search("简历")] == [resume["item_id"]]
    persisted_project = store.get_item(project["item_id"])
    assert persisted_project["source_refs"] == [evidence]
    assert persisted_project["runs"] == [project_result]
    assert persisted_project["history"][-1]["event_id"] == "project-result"

    other_session = create_new(isolated_session.paths, conversation_id="other-session")
    other_store = WorkRecordStore(other_session)
    assert other_store.search("") == []
    with pytest.raises(KeyError):
        other_store.record_result(
            project["item_id"], project_requirement["requirement_id"],
            run_id="cross-session", result_id="cross-session-result",
            status="completed", summary="不应写入", verification={"status": "passed"},
        )


def test_session_project_binding_cannot_be_rebound_to_query_other_project(isolated_session):
    isolated_session.meta.project_id = "project-original"
    store = WorkRecordStore(isolated_session)
    store.create_item(
        title="原项目事项", scope={"kind": "project", "project_id": "project-original"},
    )

    isolated_session.meta.project_id = "project-other"
    with pytest.raises(WorkRecordBindingChanged):
        store.search("原项目事项")
    with pytest.raises(WorkRecordScopeError):
        store.create_item(
            title="跨项目伪装", scope={"kind": "project", "project_id": "project-other"},
        )

    # Reconstructing a store under the changed binding cannot read the old project data.
    rebound_store = WorkRecordStore(isolated_session)
    with pytest.raises(WorkRecordScopeError):
        rebound_store.list_items()

