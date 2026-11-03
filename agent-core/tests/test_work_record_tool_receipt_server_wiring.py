from __future__ import annotations

import asyncio
import json
import pytest
import sys
from pathlib import Path
from types import SimpleNamespace

from pathlib import Path as _Path

_CORE = _Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

import project_api
import server
from agent import Agent
from llm_client import LLMResponse
from evolution.context import normalize_host_run_id
from paths import AgentPaths
from server import WsBridge, _run_line
from session import Session, SessionMeta
from work_records import WorkRecordStore
from work_record_settlement import BoundExecution


class ReadOnlyToolLLM:
    """Local scripted model response; no provider/network call is possible."""

    def __init__(self, path: str, *, no_tool: bool = False):
        self.path = path
        self.no_tool = no_tool
        self.calls = 0

    def set_cancel_event(self, _event):
        return None

    def chat(self, *_args, **_kwargs):
        self.calls += 1
        if self.no_tool:
            return LLMResponse(
                model="local-synthetic", content="No tool was needed.", tool_calls=[],
                finish_reason="stop", usage=None, raw={},
            )
        if self.calls == 1:
            return LLMResponse(
                model="local-synthetic",
                content=None,
                tool_calls=[{
                    "id": "model-tool-call-1",
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "arguments": json.dumps({"path": self.path}),
                    },
                }],
                finish_reason="tool_calls",
                usage=None,
                raw={},
            )
        return LLMResponse(
            model="local-synthetic",
            content="Read-only synthetic file was read.",
            tool_calls=[],
            finish_reason="stop",
            usage=None,
            raw={},
        )


def make_session(tmp_path: Path) -> Session:
    root = tmp_path / "agent-root"
    root.mkdir()
    paths = AgentPaths(
        agent_root=root,
        workspace=root / "workspace",
        data=root / "data",
        evolve=root / "evolve",
    )
    paths.workspace.mkdir(parents=True)
    session_dir = paths.data / "sessions" / "executor-receipt-session"
    session_dir.mkdir(parents=True)
    session = Session(
        conversation_id="executor-receipt-session",
        session_dir=session_dir,
        goal="synthetic real-executor receipt test",
        meta=SessionMeta(turn_mode="agent"),
        messages=[],
        paths=paths,
    )
    session.save()
    return session


def test_server_settles_real_read_file_executor_receipt_to_the_exact_work_run(tmp_path, monkeypatch):
    session = make_session(tmp_path)
    file_path = session.paths.workspace / "receipt-probe.txt"
    file_path.write_text("synthetic local read-only content\n", encoding="utf-8")
    store = WorkRecordStore(session)
    item = store.create_item(title="Synthetic task", scope={"kind": "independent"})
    requirement = store.add_requirement(
        item["item_id"], "Inspect the local synthetic note", event_id="executor-receipt-requirement",
    )

    events = []
    bridge = WsBridge(emit=events.append, paths=session.paths)
    assert bridge.begin_turn(mode="ordinary")
    snapshot = bridge.lifecycle.snapshot()
    assert snapshot is not None
    bridge.lifecycle_conversation_id = session.conversation_id
    bridge._turn_busy.set()
    run_id = snapshot.run_id
    store.begin_requirement_run(
        item["item_id"], requirement["requirement_id"],
        run_id=run_id, expected_current_run_id=None,
    )
    binding = BoundExecution(
        session.conversation_id, item["item_id"], requirement["requirement_id"], run_id,
    )

    llm = ReadOnlyToolLLM("workspace/receipt-probe.txt")
    confirm_calls = []
    agent = Agent.create(
        session,
        llm=llm,
        confirm_fn=lambda *args: confirm_calls.append(args) or "n",
    )
    agent.on_turn_event = bridge.emit_turn_event
    agent.executor.on_event = bridge.on_executor_event
    agent.bind_cancel_event(bridge.cancel_event)
    agent.bind_cancel_finish_reason(bridge.resolve_cancel_finish_reason)

    monkeypatch.setattr(server, "session_memory_event", lambda *_args: {"type": "session.memory"})
    monkeypatch.setattr(server, "_proposals_payload", lambda *_args: {"type": "evolve.proposals", "items": []})
    monkeypatch.setattr(server, "_memories_payload", lambda *_args: {"type": "evolve.memories", "items": []})
    monkeypatch.setattr(project_api, "after_turn_project_hooks", lambda *_args: None)

    repl = SimpleNamespace(session=session, agent=agent, last_turn_finish_reason=None)

    def handle_line(text):
        turn = agent.run_turn(text, spawn_explore=False)
        repl.last_turn_finish_reason = turn.finish_reason
        return "continue"

    repl.handle_line = handle_line
    asyncio.run(_run_line(
        repl,
        bridge,
        "Read the local synthetic note",
        session.paths,
        prestarted_run_id=run_id,
        work_record_binding=(store, binding),
    ))

    terminal = bridge.lifecycle.snapshot()
    assert terminal is not None and terminal.run_id == run_id and terminal.state == "settled"
    assert llm.calls == 2
    assert confirm_calls == []  # local read_file retains the existing no-confirm policy
    assert any(message.get("role") == "tool" for message in session.messages)

    recorded_item = store.get_item(item["item_id"])
    assert recorded_item is not None
    recorded_requirement = recorded_item["requirements"][0]
    assert recorded_requirement["current_run_id"] == run_id
    assert recorded_requirement["status"] == "unknown"
    assert len(recorded_item["runs"]) == 1
    run = recorded_item["runs"][0]
    assert run["run_id"] == run_id
    assert run["status"] == "unknown"
    verification = run["verification"]
    assert verification["status"] == "not_verified"
    assert verification["evidence_refs"] == []
    host = verification["host_execution"]
    receipts = host.get("host_tool_receipts")
    assert isinstance(receipts, list) and len(receipts) == 1
    receipt = receipts[0]
    assert set(receipt) == {"host_run_id", "call_id", "tool", "ok"}
    assert len(run_id) == 32 and "-" not in run_id
    assert receipt["host_run_id"] != run_id
    assert normalize_host_run_id(receipt["host_run_id"]) == normalize_host_run_id(run_id)
    assert str(__import__("uuid").UUID(receipt["call_id"])) == receipt["call_id"]
    assert receipt["tool"] == "read_file"
    assert receipt["ok"] is True
    assert host["omitted_count"] == 0
    assert "synthetic local read-only content" not in json.dumps(host)
    assert "Inspect the local synthetic note" not in json.dumps(host)


def _run_read_file_case(tmp_path, monkeypatch, *, sabotage=None, no_tool=False):
    session = make_session(tmp_path)
    (session.paths.workspace / "receipt-probe.txt").write_text(
        "synthetic local read-only content\n", encoding="utf-8",
    )
    store = WorkRecordStore(session)
    item = store.create_item(title="Synthetic task", scope={"kind": "independent"})
    requirement = store.add_requirement(
        item["item_id"], "Inspect the local synthetic note", event_id="executor-receipt-requirement",
    )
    bridge = WsBridge(emit=lambda _event: None, paths=session.paths)
    assert bridge.begin_turn(mode="ordinary")
    lifecycle = bridge.lifecycle.snapshot()
    assert lifecycle is not None
    bridge.lifecycle_conversation_id = session.conversation_id
    bridge._turn_busy.set()
    run_id = lifecycle.run_id
    store.begin_requirement_run(
        item["item_id"], requirement["requirement_id"], run_id=run_id,
        expected_current_run_id=None,
    )
    binding = BoundExecution(
        session.conversation_id, item["item_id"], requirement["requirement_id"], run_id,
    )
    llm = ReadOnlyToolLLM("workspace/receipt-probe.txt")
    if no_tool:
        llm = ReadOnlyToolLLM("workspace/receipt-probe.txt", no_tool=True)
    agent = Agent.create(session, llm=llm, confirm_fn=lambda *_args: "n")
    agent.on_turn_event = bridge.emit_turn_event
    agent.executor.on_event = bridge.on_executor_event
    agent.bind_cancel_event(bridge.cancel_event)
    agent.bind_cancel_finish_reason(bridge.resolve_cancel_finish_reason)
    events = []
    bridge.emit = events.append

    def after_turn(_session, _paths, _emit):
        executor_session = agent.executor.session
        if sabotage == "snapshot_run":
            generation, _recorded_run, entries, omitted = executor_session.snapshot_host_tool_receipts()
            wrong_run = "e268b0a9-1f74-41aa-9f62-a75f53c3cb26"
            executor_session.snapshot_host_tool_receipts = lambda: (
                generation, wrong_run,
                [{**entry, "host_run_id": wrong_run} for entry in entries], omitted,
            )
        elif sabotage == "executor_session":
            executor_session.session_dir = tmp_path / "different-executor-session"
        elif sabotage == "tool_message_count":
            session.append_message({
                "role": "tool", "tool_call_id": "unmatched-model-call", "content": "{}",
            })
        elif sabotage == "snapshot_unavailable":
            executor_session.snapshot_host_tool_receipts = None

    monkeypatch.setattr(server, "session_memory_event", lambda *_args: {"type": "session.memory"})
    monkeypatch.setattr(server, "_proposals_payload", lambda *_args: {"type": "evolve.proposals", "items": []})
    monkeypatch.setattr(server, "_memories_payload", lambda *_args: {"type": "evolve.memories", "items": []})
    monkeypatch.setattr(project_api, "after_turn_project_hooks", after_turn)
    repl = SimpleNamespace(session=session, agent=agent, last_turn_finish_reason=None)

    def handle_line(text):
        turn = agent.run_turn(text, spawn_explore=False)
        repl.last_turn_finish_reason = turn.finish_reason
        return "continue"

    repl.handle_line = handle_line
    asyncio.run(_run_line(
        repl, bridge, "Read the local synthetic note", session.paths,
        prestarted_run_id=run_id, work_record_binding=(store, binding),
    ))
    return session, store, item, requirement, bridge, agent, llm, events


@pytest.mark.parametrize("sabotage", [
    "snapshot_run", "executor_session", "tool_message_count", "snapshot_unavailable",
])
def test_unprovable_executor_snapshot_never_persists_wrong_receipts_and_reports_incomplete(
    tmp_path, monkeypatch, sabotage,
):
    session, store, item, requirement, bridge, agent, llm, events = _run_read_file_case(
        tmp_path, monkeypatch, sabotage=sabotage,
    )
    assert llm.calls == 2
    assert bridge.lifecycle.snapshot().state == "settled"
    persisted_item = store.get_item(item["item_id"])
    persisted_requirement = persisted_item["requirements"][0]
    assert persisted_requirement["status"] == "unknown"
    assert len(persisted_item["runs"]) == 1
    run = persisted_item["runs"][0]
    assert run["status"] == "unknown"
    host = run["verification"]["host_execution"]
    assert "host_tool_receipts" not in host
    assert "omitted_count" not in host
    assert any(
        event.get("type") == "turn.notice"
        and event.get("level") == "warn"
        and "\u4e0d\u5b8c\u6574" in event.get("text", "")
        and "\u4e0d\u4ee3\u8868\u672c\u56de\u5408\u6ca1\u6709\u5de5\u5177\u8c03\u7528" in event.get("text", "")
        for event in events
    )


def test_exact_bound_empty_executor_snapshot_does_not_claim_no_tool_calls(tmp_path, monkeypatch):
    session, store, item, _requirement, bridge, _agent, llm, events = _run_read_file_case(
        tmp_path, monkeypatch, no_tool=True,
    )
    assert llm.calls == 1
    assert bridge.lifecycle.snapshot().state == "settled"
    run = store.get_item(item["item_id"])["runs"][0]
    host = run["verification"]["host_execution"]
    assert "host_tool_receipts" not in host
    assert "omitted_count" not in host
    assert any(
        event.get("type") == "turn.notice"
        and "\u4e0d\u636e\u6b64\u65ad\u8a00\u672a\u8c03\u7528\u5de5\u5177" in event.get("text", "")
        for event in events
    )
