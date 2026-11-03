from __future__ import annotations

import sys
import threading
from pathlib import Path
from typing import Any

import pytest

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

from agent import Agent
from paths import AgentPaths
from project_switch import record_project_session
from server import WsBridge
from session import Session, SessionMeta, create_new
from work_records import WorkRecordStore
from work_records_experiment import WorkRecordExperimentController


class NoCallModel:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def chat(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append((args, kwargs))
        raise AssertionError("multi-project overview must not call the model")


def _make_fixture(root: Path, project_count: int = 5) -> tuple[Agent, Session, list[Session], NoCallModel]:
    paths = AgentPaths(
        agent_root=root,
        workspace=root / "workspace",
        data=root / "data",
        evolve=root / "evolve",
    )
    for directory in (paths.workspace, paths.data, paths.evolve):
        directory.mkdir(parents=True, exist_ok=True)
    (paths.evolve / "_index.core.toml").write_text("", encoding="utf-8")

    # Parent is the one visible conversation; it deliberately has no project binding.
    current = create_new(paths, conversation_id="20261030-00000001")
    current.meta.project_id = ""
    current.save()

    project_ids = [f"synthetic-project-{index}" for index in range(1, project_count + 1)]
    project_sessions: list[Session] = []
    for index, project_id in enumerate(project_ids, start=2):
        project_root = paths.workspace / project_id
        project_root.mkdir(parents=True)
        (project_root / "PROJECT.md").write_text(f"# {project_id}\n", encoding="utf-8")
        session_id = f"20261030-{index:08x}"
        session = Session(
            conversation_id=session_id,
            session_dir=paths.data / "sessions" / session_id,
            goal=f"synthetic goal for {project_id}",
            meta=SessionMeta(
                active_shell="project",
                project_id=project_id,
                project_root=f"workspace/{project_id}",
            ),
            messages=[],
            paths=paths,
        )
        session.session_dir.mkdir(parents=True)
        session.save()
        store = WorkRecordStore(session)
        item = store.create_item(
            title=f"{project_id} recorded item",
            scope={"kind": "project", "project_id": project_id},
        )
        for requirement_index in range(3):
            store.add_requirement(
                item["item_id"],
                f"{project_id} requirement {requirement_index + 1}",
                event_id=f"{project_id}-requirement-{requirement_index + 1}",
            )
        record_project_session(paths, project_id, session.conversation_id)
        project_sessions.append(session)

    model = NoCallModel()
    agent = Agent.create(current, llm=model, enable_work_record_recall=True)
    parent_store = WorkRecordStore(current)
    agent.work_record_recall_controller = WorkRecordExperimentController(
        agent=agent,
        store=parent_store,
        recognizer=lambda *_args: {"action": "none"},
    )
    agent.enable_work_record_recall = True
    return agent, current, project_sessions, model


def _run_with_real_bridge_confirmation(
    agent: Agent,
    *,
    choice: str,
) -> tuple[Any, list[dict[str, Any]]]:
    events: list[dict[str, Any]] = []
    confirm_requested = threading.Event()

    def emit(event: dict[str, Any]) -> None:
        events.append(event)
        if event.get("type") == "confirm.request":
            confirm_requested.set()

    bridge = WsBridge(emit=emit, paths=agent.session.paths, confirm_timeout=5.0)
    agent.executor.confirm_fn = bridge.confirm_fn
    agent.executor.on_event = lambda event_type, payload: emit({"type": event_type, **payload})
    outcome: dict[str, Any] = {}
    failures: list[BaseException] = []

    def run_turn() -> None:
        try:
            outcome["result"] = agent.run_turn("\u8bf7\u7ed9\u6211\u6240\u6709\u9879\u76ee\u7684\u5de5\u4f5c\u6982\u51b5")
        except BaseException as exc:
            failures.append(exc)

    worker = threading.Thread(target=run_turn, name="temporary-multi-project-overview")
    worker.start()
    if not confirm_requested.wait(5.0):
        worker.join(5.0)
        if worker.is_alive():
            bridge.cancel_event.set()
            worker.join(2.0)
        assert not worker.is_alive(), "Agent route did not leave confirmation wait"
        if failures:
            raise failures[0]
        pytest.fail(
            "Agent route did not emit a real confirm.request; "
            f"turn_result={getattr(outcome.get('result'), 'assistant_text', '')!r}"
        )

    request = next(event for event in events if event.get("type") == "confirm.request")
    assert isinstance(request.get("request_id"), str) and request["request_id"]
    assert request.get("allow_approve_all") is False
    assert bridge.deliver_confirm(request["request_id"], choice) is True
    worker.join(5.0)
    assert not worker.is_alive(), "Agent route did not finish after confirmation"
    if failures:
        raise failures[0]
    return outcome["result"], events


def test_real_agent_route_rejection_does_not_read_any_project_sidecar(tmp_path, monkeypatch):
    agent, current, project_sessions, model = _make_fixture(tmp_path)
    target_ids = {session.conversation_id for session in project_sessions}
    sidecar_reads: list[str] = []
    original_read = WorkRecordStore._read

    def tracked_read(store: WorkRecordStore):
        if store._conversation_id in target_ids:
            sidecar_reads.append(store._conversation_id)
        return original_read(store)

    monkeypatch.setattr(WorkRecordStore, "_read", tracked_read)
    result, events = _run_with_real_bridge_confirmation(agent, choice="n")

    assert sidecar_reads == []
    assert any(event.get("type") == "confirm.done" and event.get("choice") == "n" for event in events)
    assert result.tool_rounds == result.total_tool_rounds == 0
    assert model.calls == []
    assert agent.session is current
    assert current.conversation_id == "20261030-00000001"
    assert current.meta.project_id == ""
    assert not any(event.get("type") == "tool.start" for event in events)


def test_real_agent_route_approved_groups_real_project_requirements_by_bound_session(tmp_path, monkeypatch):
    agent, current, project_sessions, model = _make_fixture(tmp_path)
    target_ids = {session.conversation_id for session in project_sessions}
    sidecar_reads: list[str] = []
    original_read = WorkRecordStore._read

    def tracked_read(store: WorkRecordStore):
        if store._conversation_id in target_ids:
            sidecar_reads.append(store._conversation_id)
        return original_read(store)

    monkeypatch.setattr(WorkRecordStore, "_read", tracked_read)
    result, events = _run_with_real_bridge_confirmation(agent, choice="y")

    assert any(event.get("type") == "confirm.done" and event.get("choice") == "y" for event in events)
    assert set(sidecar_reads) == target_ids
    assert result.finish_reason == "multi_project_work_record_overview"
    assert result.tool_rounds == result.total_tool_rounds == 0
    assert model.calls == []
    assert agent.session is current
    assert current.conversation_id == "20261030-00000001"
    assert current.meta.project_id == ""
    assert not any(event.get("type") == "tool.start" for event in events)

    reply = result.assistant_text
    for session in project_sessions:
        project_id = session.meta.project_id
        assert f"{project_id} recorded item" in reply
        for requirement_index in range(1, 4):
            assert f"{project_id} requirement {requirement_index}" in reply
    assert "\u4e0d\u662f\u9879\u76ee\u5b9e\u65f6\u6267\u884c\u72b6\u6001" in reply
    assert "\u5df2\u9a8c\u8bc1\u901a\u8fc7" not in reply
    assert "\u540e\u7eed\u5bf9\u8bdd\u6a21\u578b\u53ef\u80fd\u8bfb\u53d6" in reply
