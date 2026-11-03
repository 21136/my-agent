from __future__ import annotations

import json
import sys
import threading
import time
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

import multi_project_work_records as multi_project_reader
from agent import Agent
from paths import AgentPaths
from server import WsBridge
from session import Session, SessionMeta
from work_records import WorkRecordStore
from work_records_experiment import WorkRecordExperimentController


class NoCallModel:
    def __init__(self):
        self.calls = []

    def chat(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        raise AssertionError("multi-project overview must not call the Agent model")


class SyntheticSources:
    def __init__(self, root: Path):
        root.mkdir(parents=True)
        self.paths = AgentPaths(
            agent_root=root,
            workspace=root / "workspace",
            data=root / "data",
            evolve=root / "evolve",
        )
        self.current = self._session("20261030-aaaaaaaa", "")
        self.project_ids = [f"project-{index}" for index in range(1, 8)]
        self.project_sessions = {}
        self.sessions = {self.current.conversation_id: self.current}
        self.sidecar_bytes = {}

        current_store = WorkRecordStore(self.current)
        independent = current_store.create_item(
            title="CURRENT_INDEPENDENT_ITEM",
            scope={"kind": "independent"},
        )
        current_store.add_requirement(
            independent["item_id"], "CURRENT_INDEPENDENT_REQUIREMENT_A", event_id="current-independent-a",
        )
        current_store.add_requirement(
            independent["item_id"], "CURRENT_INDEPENDENT_REQUIREMENT_B", event_id="current-independent-b",
        )

        for index, project_id in enumerate(self.project_ids, 1):
            session_id = f"20261030-{index:08x}"
            session = self._session(session_id, project_id)
            self.sessions[session_id] = session
            self.project_sessions[project_id] = session_id
            store = WorkRecordStore(session)
            project_item = store.create_item(
                title=f"PROJECT_ITEM::{project_id}",
                scope={"kind": "project", "project_id": project_id},
            )
            store.add_requirement(
                project_item["item_id"], f"{project_id}::REQ_A", event_id=f"{project_id}-req-a",
            )
            store.add_requirement(
                project_item["item_id"], f"{project_id}::REQ_B", event_id=f"{project_id}-req-b",
            )
            # The multi-project reader must not import independent entries from project sidecars.
            decoy = store.create_item(
                title=f"PROJECT_SCOPE_INDEPENDENT_DECOY::{project_id}",
                scope={"kind": "independent"},
            )
            store.add_requirement(
                decoy["item_id"], f"{project_id}::PRIVATE_DECOY_REQUIREMENT",
                event_id=f"{project_id}-decoy-req",
            )

        self.paths.data.mkdir(parents=True, exist_ok=True)
        (self.paths.data / "state.json").write_text(
            json.dumps({"project_sessions": self.project_sessions}, ensure_ascii=False),
            encoding="utf-8",
        )
        self.sidecar_bytes = {
            session_id: (session.session_dir / "work-records.v1.json").read_bytes()
            for session_id, session in self.sessions.items()
        }

    def _session(self, session_id: str, project_id: str) -> Session:
        session_dir = self.paths.data / "sessions" / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        session = Session(
            conversation_id=session_id,
            session_dir=session_dir,
            goal="synthetic multi-project overview fixture",
            meta=SessionMeta(project_id=project_id),
            messages=[],
            paths=self.paths,
        )
        session.save()
        return session


def _make_agent(session: Session, confirm_fn, model: NoCallModel) -> Agent:
    agent = Agent.create(
        session,
        llm=model,
        confirm_fn=confirm_fn,
        enable_work_record_recall=True,
    )
    controller = WorkRecordExperimentController(
        agent=agent,
        store=WorkRecordStore(session),
        recognizer=lambda *_args: {"action": "none"},
    )
    agent.work_record_recall_controller = controller
    return agent


def _start_confirmed_agent_turn(agent: Agent, text: str, bridge: WsBridge, events: list[dict], choice: str):
    result_box = []
    error_box = []

    def run():
        try:
            result_box.append(agent.run_turn(text))
        except BaseException as exc:
            error_box.append(exc)

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    deadline = time.monotonic() + 5
    request = None
    while time.monotonic() < deadline:
        request = next((event for event in events if event.get("type") == "confirm.request"), None)
        if request is not None:
            break
        if not worker.is_alive():
            break
        time.sleep(0.01)
    assert request is not None, f"overview confirmation was not requested; errors={error_box!r}"
    assert request.get("allow_approve_all") is False
    assert bridge.deliver_confirm(request["request_id"], choice) is True
    worker.join(timeout=5)
    assert not worker.is_alive()
    assert not error_box
    assert len(result_box) == 1
    return result_box[0], request


def test_real_agent_reads_exactly_six_selected_project_sessions_and_current_independent_scope(tmp_path, monkeypatch):
    sources = SyntheticSources(tmp_path / "synthetic-root")
    events: list[dict] = []
    bridge = WsBridge(emit=events.append, paths=sources.paths, confirm_timeout=5)
    model = NoCallModel()
    agent = _make_agent(sources.current, bridge.confirm_fn, model)
    executor_calls = []
    monkeypatch.setattr(agent.executor, "run", lambda *args, **kwargs: executor_calls.append((args, kwargs)))

    selected = sources.project_ids[:6]
    query = "\u67e5\u770b\u9879\u76ee " + "\u3001".join(selected) + " \u7684\u5de5\u4f5c\u6982\u51b5"
    sidecar_reads: list[str] = []
    original_list_items = WorkRecordStore.list_items

    def tracked_list_items(store):
        sidecar_reads.append(store._session.conversation_id)
        return original_list_items(store)

    monkeypatch.setattr(WorkRecordStore, "list_items", tracked_list_items)
    metadata_paths: list[Path] = []
    original_read_private_json = multi_project_reader._read_private_json

    def tracked_read_private_json(path, *, max_bytes):
        metadata_paths.append(Path(path))
        return original_read_private_json(path, max_bytes=max_bytes)

    monkeypatch.setattr(multi_project_reader, "_read_private_json", tracked_read_private_json)

    result, request = _start_confirmed_agent_turn(agent, query, bridge, events, "y")
    reply = result.assistant_text

    assert model.calls == []
    assert executor_calls == []
    assert result.finish_reason == "multi_project_work_record_overview"
    assert result.turn_intent == "recall"
    assert result.tool_rounds == result.total_tool_rounds == 0
    preview = request["preview"]
    assert "CURRENT_INDEPENDENT_ITEM" not in preview
    for project_id in selected:
        assert project_id in preview
        assert sources.project_sessions[project_id] in preview
    seventh_id = sources.project_ids[6]
    seventh_session_id = sources.project_sessions[seventh_id]
    assert seventh_id not in preview and seventh_session_id not in preview

    assert "CURRENT_INDEPENDENT_ITEM" in reply
    assert "CURRENT_INDEPENDENT_REQUIREMENT_A" in reply
    assert "CURRENT_INDEPENDENT_REQUIREMENT_B" in reply
    for project_id in selected[:5]:
        assert f"{project_id}::REQ_A" in reply
        assert f"{project_id}::REQ_B" in reply
    for project_id in selected:
        assert f"PROJECT_SCOPE_INDEPENDENT_DECOY::{project_id}" not in reply
        assert f"{project_id}::PRIVATE_DECOY_REQUIREMENT" not in reply
    assert "PROJECT_ITEM::project-6" not in reply
    assert "project-6::REQ_A" not in reply
    assert "PROJECT_ITEM::project-7" not in reply
    assert "project-7::REQ_A" not in reply
    assert "PROJECT_SCOPE_INDEPENDENT_DECOY::project-7" not in reply
    assert "\u5171 7 \u7ec4\uff086 \u4e2a\u9879\u76ee\u7ec4\u30011 \u4e2a\u72ec\u7acb\u4e8b\u9879\u7ec4\uff09" in reply
    assert "\u8fd8\u6709 1 \u7ec4\u672a\u5c55\u5f00\uff0c\u5305\u542b 1 \u4e2a\u4e8b\u9879\u30012 \u9879\u8981\u6c42\u672a\u5c55\u5f00" in reply
    assert "\u672a\u6620\u5c04\u9879\u76ee/\u5176\u4ed6\u5386\u53f2\u672a\u68c0\u67e5" in reply
    assert "\u540e\u7eed\u53ef\u80fd\u88ab\u6a21\u578b\u770b\u5230" in reply

    expected_reads = {sources.current.conversation_id, *(sources.project_sessions[pid] for pid in selected)}
    assert set(sidecar_reads) == expected_reads
    assert seventh_session_id not in sidecar_reads
    assert all(path != sources.sessions[seventh_session_id].session_dir / "meta.json" for path in metadata_paths)
    assert len(sources.current.messages) == 2
    assert sources.current.messages[0]["content"] == query
    assert sources.current.messages[1]["content"] == reply
    for session_id, raw in sources.sidecar_bytes.items():
        assert (sources.sessions[session_id].session_dir / "work-records.v1.json").read_bytes() == raw


def test_real_agent_rejection_does_not_read_selected_project_metadata_or_sidecars(tmp_path, monkeypatch):
    sources = SyntheticSources(tmp_path / "synthetic-reject-root")
    events: list[dict] = []
    bridge = WsBridge(emit=events.append, paths=sources.paths, confirm_timeout=5)
    model = NoCallModel()
    agent = _make_agent(sources.current, bridge.confirm_fn, model)
    selected = sources.project_ids[:6]
    query = "\u67e5\u770b\u9879\u76ee " + "\u3001".join(selected) + " \u7684\u5de5\u4f5c\u6982\u51b5"

    sidecar_reads: list[str] = []
    original_list_items = WorkRecordStore.list_items
    def tracked_list_items(store):
        sidecar_reads.append(store._session.conversation_id)
        return original_list_items(store)
    monkeypatch.setattr(WorkRecordStore, "list_items", tracked_list_items)

    metadata_paths: list[Path] = []
    original_read_private_json = multi_project_reader._read_private_json
    def tracked_read_private_json(path, *, max_bytes):
        metadata_paths.append(Path(path))
        return original_read_private_json(path, max_bytes=max_bytes)
    monkeypatch.setattr(multi_project_reader, "_read_private_json", tracked_read_private_json)

    result, request = _start_confirmed_agent_turn(agent, query, bridge, events, "n")
    assert model.calls == []
    assert result.finish_reason == "multi_project_work_record_overview"
    assert "\u5df2\u62d2\u7edd\u6216\u672a\u660e\u786e\u6388\u6743" in result.assistant_text
    assert "CURRENT_INDEPENDENT_ITEM" not in result.assistant_text
    assert sidecar_reads == []
    target_session_ids = {sources.project_sessions[pid] for pid in sources.project_ids}
    assert not any(
        path.name == "meta.json" and path.parent.name in target_session_ids
        for path in metadata_paths
    )
    assert request.get("allow_approve_all") is False