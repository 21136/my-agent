from __future__ import annotations

import sys
from pathlib import Path

import pytest

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

from agent import Agent, TurnResult
from paths import AgentPaths
from session import Session, SessionMeta
from work_records import WorkRecordScopeError, WorkRecordStore
from work_records_experiment import WorkRecordExperimentController
from work_records_luna_recall import WorkRecordsLunaRecall
from work_records_overview import render_work_records_overview


class NoCallModel:
    def __init__(self):
        self.calls = []

    def chat(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        raise AssertionError("overview must not call any model")


def make_session(root: Path, conversation_id: str, project_id: str) -> Session:
    paths = AgentPaths(
        agent_root=root,
        workspace=root / "workspace",
        data=root / "data",
        evolve=root / "evolve",
    )
    session_dir = paths.data / "sessions" / conversation_id
    session_dir.mkdir(parents=True, exist_ok=True)
    session = Session(
        conversation_id=conversation_id,
        session_dir=session_dir,
        goal="synthetic overview wiring test",
        meta=SessionMeta(project_id=project_id),
        messages=[],
        paths=paths,
    )
    session.save()
    return session


def add_requirement(store: WorkRecordStore, item_id: str, index: int, status: str) -> str:
    requirement = store.add_requirement(
        item_id,
        f"synthetic requirement {index} status {status}",
        event_id=f"overview-req-{item_id}-{index}",
    )
    if status == "pending":
        return requirement["requirement_id"]
    if status == "in_progress":
        store.begin_requirement_run(
            item_id,
            requirement["requirement_id"],
            run_id=f"overview-run-{item_id}-{index}",
            expected_current_run_id=None,
        )
        return requirement["requirement_id"]
    store.record_result(
        item_id,
        requirement["requirement_id"],
        run_id=f"overview-run-{item_id}-{index}",
        result_id=f"overview-result-{item_id}-{index}",
        status=status,
        summary="synthetic fixture only",
        verification={"status": "not_verified"},
    )
    return requirement["requirement_id"]


def make_recall_agent(session: Session, store: WorkRecordStore, model: NoCallModel) -> Agent:
    agent = Agent.create(
        session,
        llm=model,
        confirm_fn=lambda *_args: "n",
        enable_work_record_recall=True,
    )
    controller = WorkRecordExperimentController(
        agent=agent,
        store=store,
        recognizer=lambda *_args: {"action": "none"},
        semantic_matcher=WorkRecordsLunaRecall(model),
        semantic_excerpt_policy=lambda title, requirements: "\n".join((title, *requirements)),
    )
    agent.work_record_recall_controller = controller
    return agent


def test_real_current_session_overview_is_bounded_read_only_and_controller_bound(tmp_path, monkeypatch):
    root = tmp_path / "agent-root"
    root.mkdir()
    session = make_session(root, "overview-current", "synthetic-project-1")
    store = WorkRecordStore(session)

    project_items = [
        store.create_item(
            title=f"Synthetic project matter {index}",
            scope={"kind": "project", "project_id": "synthetic-project-1"},
        )
        for index in range(6)
    ]
    for index, status in enumerate((
        "blocked", "unknown", "in_progress", "stopped", "pending", "completed", "completed",
    )):
        add_requirement(store, project_items[0]["item_id"], index, status)
    for index, item in enumerate(project_items[1:], start=1):
        add_requirement(store, item["item_id"], index, "pending")

    independent = store.create_item(
        title="Synthetic independent matter",
        scope={"kind": "independent"},
    )
    add_requirement(store, independent["item_id"], 99, "completed")

    sidecar_path = session.session_dir / "work-records.v1.json"
    before_bytes = sidecar_path.read_bytes()
    before_items = store.list_items()

    # A different project binding cannot safely read a sidecar containing this project's item.
    other_binding = Session(
        conversation_id=session.conversation_id,
        session_dir=session.session_dir,
        goal=session.goal,
        meta=SessionMeta(project_id="synthetic-project-2"),
        messages=[],
        paths=session.paths,
    )
    with pytest.raises(WorkRecordScopeError):
        WorkRecordStore(other_binding).list_items()
    assert sidecar_path.read_bytes() == before_bytes

    model = NoCallModel()
    agent = make_recall_agent(session, store, model)
    executor_calls = []
    monkeypatch.setattr(
        agent.executor,
        "run",
        lambda *args, **kwargs: executor_calls.append((args, kwargs)),
    )

    result = agent.run_turn("\u8bf7\u7ed9\u6211\u5f53\u524d\u5de5\u4f5c\u6574\u4f53\u6982\u51b5")
    reply = result.assistant_text

    assert result.finish_reason == "work_record_overview"
    assert result.turn_intent == "recall"
    assert result.tool_rounds == result.total_tool_rounds == 0
    assert model.calls == []
    assert executor_calls == []
    assert "1 \u4e2a\u9879\u76ee\u7ec4\u3001 1 \u4e2a\u72ec\u7acb\u4e8b\u9879\u7ec4" not in reply
    assert "1 \u4e2a\u9879\u76ee\u7ec4\u30011 \u4e2a\u72ec\u7acb\u4e8b\u9879\u7ec4" in reply
    assert "7 \u4e2a\u4e8b\u9879\u300113 \u9879\u8981\u6c42" in reply
    assert "\u53d7\u963b" in reply
    assert "\u7ed3\u679c\u5f85\u786e\u8ba4" in reply
    assert "\u6267\u884c\u72b6\u6001\u5f85\u786e\u8ba4" in reply
    assert "\u5df2\u505c\u6b62" in reply
    assert "\u5f85\u5f00\u59cb" in reply
    assert "\u5df2\u8bb0\u5f55\u5b8c\u6210\uff08\u4e0d\u4ee3\u8868\u9a8c\u8bc1\u901a\u8fc7\uff09" in reply
    assert "\u8fd8\u6709 1 \u9879\u8981\u6c42\u672a\u5c55\u5f00" in reply
    assert "\u8fd8\u6709 1 \u4e2a\u4e8b\u9879\u672a\u5c55\u5f00" in reply
    assert "\u540e\u7eed\u5bf9\u8bdd\u6a21\u578b\u53ef\u80fd\u8bfb\u53d6" in reply

    assert len(session.messages) == 2
    assert session.messages[0] == {"role": "user", "content": "\u8bf7\u7ed9\u6211\u5f53\u524d\u5de5\u4f5c\u6574\u4f53\u6982\u51b5"}
    assert session.messages[1] == {"role": "assistant", "content": reply}
    assert store.list_items() == before_items
    assert sidecar_path.read_bytes() == before_bytes

    foreign_session = make_session(root, "overview-foreign", "synthetic-project-foreign")
    foreign_store = WorkRecordStore(foreign_session)
    foreign_model = NoCallModel()
    foreign_agent = make_recall_agent(foreign_session, foreign_store, foreign_model)
    foreign_agent.work_record_recall_controller = agent.work_record_recall_controller
    fallback_calls = []
    foreign_agent._run_turn_without_work_record_recording = lambda text, **kwargs: (
        fallback_calls.append(text) or TurnResult(
            assistant_text="normal foreign-session path",
            tool_rounds=0,
            finish_reason="completed",
        )
    )
    foreign_agent._run_current_work_overview = lambda *_args: pytest.fail(
        "a controller bound to another Session must not route overview"
    )
    foreign_result = foreign_agent.run_turn("\u5f53\u524d\u5de5\u4f5c\u6574\u4f53\u6982\u51b5")
    assert foreign_result.assistant_text == "normal foreign-session path"
    assert fallback_calls == ["\u5f53\u524d\u5de5\u4f5c\u6574\u4f53\u6982\u51b5"]
    assert foreign_model.calls == []
    assert store.list_items() == before_items


def test_cross_project_sidecar_scope_refuses_second_project_binding(tmp_path):
    root = tmp_path / "agent-root"
    root.mkdir()
    session = make_session(root, "overview-project-binding", "synthetic-project-a")
    store = WorkRecordStore(session)
    item = store.create_item(
        title="Synthetic project A",
        scope={"kind": "project", "project_id": "synthetic-project-a"},
    )
    add_requirement(store, item["item_id"], 1, "pending")
    before_bytes = (session.session_dir / "work-records.v1.json").read_bytes()

    other_project_binding = Session(
        conversation_id=session.conversation_id,
        session_dir=session.session_dir,
        goal=session.goal,
        meta=SessionMeta(project_id="synthetic-project-b"),
        messages=[],
        paths=session.paths,
    )
    with pytest.raises(WorkRecordScopeError):
        WorkRecordStore(other_project_binding).create_item(
            title="Synthetic project B",
            scope={"kind": "project", "project_id": "synthetic-project-b"},
        )
    assert (session.session_dir / "work-records.v1.json").read_bytes() == before_bytes

    rendered = render_work_records_overview(session)
    assert "1 \u4e2a\u9879\u76ee\u7ec4" in rendered
    assert "Synthetic project B" not in rendered
    assert "\u4e0d\u4ee3\u8868\u76f8\u5173\u5de5\u4f5c\u6ca1\u6709\u53d1\u751f" in rendered