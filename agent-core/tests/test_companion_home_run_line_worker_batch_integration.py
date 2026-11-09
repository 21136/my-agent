"""Companion home (grow): live _run_line → work-record batch → batch authorization → workers (#41 defer)."""
from __future__ import annotations

import asyncio
import json
import secrets
import sys
from pathlib import Path

import httpx
import pytest

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from active_work_multi_project import mark_session_companion_multi_project_orchestration
from project_mode import create_project
from project_worker_host import _host_failure
from server import WsBridge, WsSessionHandler, _build_repl, _run_line
from session import Session, create_new
from tests.isolation_helpers import temporary_agent_paths
from tests.test_agent_project_worker_tool import _ParentModel


def _companion_grow_session(paths, conversation_id: str):
    session = create_new(paths, conversation_id=conversation_id)
    session.meta.active_shell = "grow"
    session.meta.harness = "desktop"
    session.meta.turn_mode = "agent"
    session.meta.project_root = ""
    session.save()
    return session


def test_companion_run_line_threaded_batch_confirms_invoke_workers_and_defer_chat(monkeypatch):
    """One asyncio turn: record batch → batch auth → run_many; blocked batch must not push work cards."""
    monkeypatch.setattr(httpx, "Client", lambda *args, **kwargs: pytest.fail("unexpected outbound HTTP"))
    monkeypatch.setattr(
        "project_worker_host._worker_model_snapshot",
        lambda paths: ("test-model", "test-provider", "test-model", "https://example.invalid"),
    )
    monkeypatch.setattr("server.PROJECT_WORK_CARD_MERGE_SECONDS", 0.01)

    token = secrets.token_hex(3)
    project_ids = [f"companion-{label}-{token}" for label in ("alpha", "beta", "gamma")]
    line = (
        f"请在一次 run_project_workers 中同时提交 {project_ids[0]}、{project_ids[1]}、{project_ids[2]}；"
        "每个项目只读自身 PROJECT.md、只写自身 NOTES.md，允许此次模型调用，不要执行命令。"
    )

    with temporary_agent_paths() as paths:
        for project_id in project_ids:
            create_project(paths, project_id, project_template="light")

        session = _companion_grow_session(paths, f"companion-run-line-{secrets.token_hex(4)}")
        events: list[dict] = []
        bridge = WsBridge(emit=events.append, paths=paths, confirm_timeout=5.0)
        repl = _build_repl(session, paths, bridge, work_record_controller_factory=lambda *_: None)
        mark_session_companion_multi_project_orchestration(
            session, active=True, executor=repl.agent.executor,
        )
        repl.agent.llm = _ParentModel([
            {
                "project_id": project_id,
                "task": f"Draft NOTES for {project_id}",
                "read_targets": ["PROJECT.md"],
                "write_targets": ["NOTES.md"],
            }
            for project_id in project_ids
        ])
        parent_run_turn = repl.agent.run_turn
        repl.agent.run_turn = lambda text, **_kwargs: parent_run_turn(text, spawn_explore=False)
        host = repl.agent.project_worker_host
        run_many_calls: list[tuple] = []

        def run_many_stub(session_arg, requests, **kwargs):
            run_many_calls.append((session_arg, tuple(requests), dict(kwargs)))
            assert len(requests) == 1
            request = requests[0]
            reservation = kwargs["reservations"][0]
            return (_host_failure(session_arg, request, "companion_harness_blocked", reservation=reservation),)

        monkeypatch.setattr(host, "run_many", run_many_stub)

        queued = bridge.lifecycle.enqueue("ordinary")
        bridge.lifecycle_conversation_id = session.conversation_id
        bridge._turn_busy.set()
        handler = object.__new__(WsSessionHandler)

        async def run_turn():
            task = asyncio.create_task(
                _run_line(repl, bridge, line, paths, lock_held=True, prestarted_run_id=queued.run_id),
            )
            answered: set[str] = set()
            deadline = asyncio.get_running_loop().time() + 12
            while not task.done():
                for event in list(events):
                    request_id = event.get("request_id")
                    if event.get("type") != "confirm.request" or request_id in answered:
                        continue
                    answered.add(request_id)
                    preview = event.get("preview") or ""
                    assert event["session_id"] == session.conversation_id
                    assert event["run_id"] == queued.run_id
                    assert (
                        "CURRENT_DIALOGUE_WORK_RECORD_BATCH_CONFIRM" in preview
                        or "PROJECT_WORKER_BATCH_AUTHORIZATION_CONFIRM" in preview
                    )
                    assert handler._dispatch_inline(
                        {"type": "confirm.response", "request_id": request_id, "choice": "y"},
                        bridge,
                    )
                if asyncio.get_running_loop().time() >= deadline:
                    bridge.request_cancel()
                    raise AssertionError("companion _run_line timed out")
                await asyncio.sleep(0.005)
            await task

        asyncio.run(run_turn())

        confirms = [event for event in events if event.get("type") == "confirm.request"]
        assert len(confirms) == 2
        assert "CURRENT_DIALOGUE_WORK_RECORD_BATCH_CONFIRM" in confirms[0]["preview"]
        assert "PROJECT_WORKER_BATCH_AUTHORIZATION_CONFIRM" in confirms[1]["preview"]
        assert len(run_many_calls) == len(project_ids)
        assert {call[1][0].project_id for call in run_many_calls} == set(project_ids)

        tool_results = [
            json.loads(message["content"])
            for message in Session.load(paths, session.conversation_id).messages
            if message.get("role") == "tool"
        ]
        batch = next(result for result in tool_results if result.get("tool") == "run_project_workers")
        assert batch.get("ok") is True
        workers = batch["data"]["workers"]
        assert {row["project_id"] for row in workers} == set(project_ids)
        assert all(row["status"] == "blocked" for row in workers)

        assert not any(event.get("type") == "work-card.present" for event in events)

        reloaded = Session.load(paths, session.conversation_id)
        assert reloaded.meta.active_shell == "grow"
        assert not (reloaded.meta.project_id or "").strip()
        assert not getattr(reloaded, "_companion_multi_project_orchestration_active", False)
