# -*- coding: utf-8 -*-
"""Regression for the scoped read/write intent in the isolated three-project batch."""
import asyncio
import json

import httpx
import pytest

from project_mode import create_project
from server import WsBridge, WsSessionHandler, _build_repl, _run_line
from session import create_new
from tests.isolation_helpers import temporary_agent_paths
from tests.test_agent_project_worker_tool import _ParentModel
from turn_intent import classify_turn, resolve_ordinary_work_intent

REQUEST = (
    "请重新执行刚才失败的隔离三项目批次：创建并关联一条新的独立工作记录，"
    "在一次 run_project_workers 调用中提交 real-batch-20261107-alpha、"
    "real-batch-20261107-beta、real-batch-20261107-gamma；"
    "每个项目只读自身 PROJECT.md、只写自身 NOTES.md，每份不少于900个汉字，"
    "分别讨论返回普通对话、多个长任务的进度展示、区分实验会话与普通对话；"
    "允许此次模型调用，不要执行命令或改其他文件，最多两个并行。"
)


def intent(text):
    return resolve_ordinary_work_intent(text, classify_turn(text), turn_mode="agent")


def test_original_batch_request_resolves_to_execution():
    assert classify_turn(REQUEST) == "execute"
    assert intent(REQUEST) == "execute"


@pytest.mark.parametrize("text", [
    "只读自身 PROJECT.md",
    "只读自身 PROJECT.md，同时只写自身 NOTES.md",
    "请重新执行批次，只读自身 PROJECT.md，不要写 NOTES.md",
    "本轮只读，各项目只读自身 PROJECT.md，只写自身 NOTES.md，请执行批次",
    "请执行批次，只读自身 PROJECT.md，只写自身 NOTES.md，不要改任何文件",
])
def test_scoped_read_does_not_bypass_negative_or_global_read_only(text):
    assert intent(text) == "read_only"


@pytest.mark.parametrize("record_choice", ["n", "y"])
def test_real_request_reaches_record_consent_but_rejection_dispatches_no_worker(monkeypatch, record_choice):
    # No real outbound requests or worker execution can occur in this test.
    monkeypatch.setattr(httpx, "Client", lambda *args, **kwargs: pytest.fail("unexpected outbound HTTP"))
    monkeypatch.setattr("project_worker_host._worker_model_snapshot", lambda paths: ("test-model", "test-provider", "test-model", "https://example.invalid"))
    with temporary_agent_paths() as paths:
        project_ids = [f"real-batch-20261107-{name}" for name in ("alpha", "beta", "gamma")]
        for project_id in project_ids:
            create_project(paths, project_id, project_template="light")
        session = create_new(paths, conversation_id="20261107-00001234")
        session.meta.turn_mode = "agent"
        session.meta.harness = "desktop"
        session.save()
        events = []
        bridge = WsBridge(emit=events.append, paths=paths, confirm_timeout=2)
        repl = _build_repl(session, paths, bridge, work_record_controller_factory=lambda *_: None)
        repl.agent.llm = _ParentModel([
            {"project_id": project_id, "task": f"Draft notes for {project_id}",
             "read_targets": ["PROJECT.md"], "write_targets": ["NOTES.md"]}
            for project_id in project_ids
        ])
        parent_run_turn = repl.agent.run_turn
        repl.agent.run_turn = lambda text, **_kwargs: parent_run_turn(text, spawn_explore=False)
        host = repl.agent.project_worker_host
        monkeypatch.setattr(host, "run_many", lambda *args, **kwargs: pytest.fail("worker dispatched without consent"))
        queued = bridge.lifecycle.enqueue("ordinary")
        bridge.lifecycle_conversation_id = session.conversation_id
        bridge._turn_busy.set()
        handler = object.__new__(WsSessionHandler)

        async def run():
            task = asyncio.create_task(_run_line(repl, bridge, REQUEST, paths,
                                                lock_held=True, prestarted_run_id=queued.run_id))
            answered = set()
            deadline = asyncio.get_running_loop().time() + 8
            while not task.done():
                for event in list(events):
                    if event.get("type") == "confirm.request" and event["request_id"] not in answered:
                        answered.add(event["request_id"])
                        assert event["session_id"] == session.conversation_id
                        assert event["run_id"] == queued.run_id
                        is_record = "CURRENT_DIALOGUE_WORK_RECORD_BATCH_CONFIRM" in event["preview"]
                        assert is_record or "PROJECT_WORKER_BATCH_AUTHORIZATION_CONFIRM" in event["preview"]
                        choice = record_choice if is_record else "n"
                        assert handler._dispatch_inline({"type": "confirm.response", "request_id": event["request_id"], "choice": choice}, bridge)
                if asyncio.get_running_loop().time() >= deadline:
                    bridge.request_cancel()
                    raise AssertionError("isolated turn timed out")
                await asyncio.sleep(.005)
            await task

        asyncio.run(run())
        confirms = [e for e in events if e.get("type") == "confirm.request"]
        assert len(confirms) == (1 if record_choice == "n" else 2)
        assert "CURRENT_DIALOGUE_WORK_RECORD_BATCH_CONFIRM" in confirms[0]["preview"]
        if record_choice == "y":
            assert "PROJECT_WORKER_BATCH_AUTHORIZATION_CONFIRM" in confirms[1]["preview"]
        done = [e for e in events if e.get("type") == "confirm.done"]
        assert [e["request_id"] for e in done] == [e["request_id"] for e in confirms]
        assert [e["choice"] for e in done] == (["n"] if record_choice == "n" else ["y", "n"])
        assert all(e["run_id"] == queued.run_id for e in done)
        tool_results = [json.loads(m["content"]) for m in session.messages if m.get("role") == "tool"]
        result = next(r for r in tool_results if r.get("tool") == "run_project_workers")
        assert not result["ok"]
        expected = ("CURRENT_DIALOGUE_RECORD_NOT_AUTHORIZED" if record_choice == "n"
                    else "PROJECT_WORKER_BATCH_AUTHORIZATION_DENIED_OR_EXPIRED")
        assert expected in result["error"]["message"]
        assert host._reservations == {}


def test_real_outer_batch_logs_one_queue_approval_and_end_timeline_per_project(monkeypatch, caplog):
    # Exercise the Agent's real outer scheduler and host authorization, but never
    # issue model HTTP or start project workers.
    import logging
    import threading
    import time

    from project_worker_host import _host_failure

    monkeypatch.setattr(httpx, "Client", lambda *args, **kwargs: pytest.fail("unexpected outbound HTTP"))
    monkeypatch.setattr("project_worker_host._worker_model_snapshot", lambda paths: ("test-model", "test-provider", "test-model", "https://example.invalid"))
    with temporary_agent_paths() as paths:
        project_ids = [f"real-batch-20261107-{name}" for name in ("alpha", "beta", "gamma")]
        for project_id in project_ids:
            create_project(paths, project_id, project_template="light")
        session = create_new(paths, conversation_id="20261107-00001235")
        session.meta.turn_mode = "agent"
        session.meta.harness = "desktop"
        session.save()
        events = []
        bridge = WsBridge(emit=events.append, paths=paths, confirm_timeout=2)
        repl = _build_repl(session, paths, bridge, work_record_controller_factory=lambda *_: None)
        repl.agent.llm = _ParentModel([
            {"project_id": project_id, "task": f"Draft notes for {project_id}",
             "read_targets": ["PROJECT.md"], "write_targets": ["NOTES.md"]}
            for project_id in project_ids
        ])
        parent_run_turn = repl.agent.run_turn
        repl.agent.run_turn = lambda text, **_kwargs: parent_run_turn(text, spawn_explore=False)
        host = repl.agent.project_worker_host

        active_lock = threading.Lock()
        active_workers = 0
        max_active_workers = 0
        first_two_started = threading.Event()

        def stop_at_host(session_arg, requests, **kwargs):
            nonlocal active_workers, max_active_workers
            assert len(requests) == 1
            reservation, = kwargs["reservations"]
            assert kwargs["before_worker_start"](requests[0], reservation) is True
            with active_lock:
                active_workers += 1
                max_active_workers = max(max_active_workers, active_workers)
                if active_workers == 2:
                    first_two_started.set()
            if requests[0].project_id != project_ids[2]:
                assert first_two_started.wait(2), "both scheduler slots should be occupied before either returns"
            time.sleep({project_ids[0]: .04, project_ids[1]: .08}.get(requests[0].project_id, .01))
            with active_lock:
                active_workers -= 1
            return (_host_failure(session_arg, requests[0], "isolated_test_stop", reservation=reservation),)

        monkeypatch.setattr(host, "run_many", stop_at_host)
        queued = bridge.lifecycle.enqueue("ordinary")
        bridge.lifecycle_conversation_id = session.conversation_id
        bridge._turn_busy.set()
        handler = object.__new__(WsSessionHandler)

        async def run_and_approve():
            task = asyncio.create_task(_run_line(repl, bridge, REQUEST, paths,
                                                lock_held=True, prestarted_run_id=queued.run_id))
            answered = set()
            deadline = asyncio.get_running_loop().time() + 8
            while not task.done():
                for event in list(events):
                    if event.get("type") == "confirm.request" and event["request_id"] not in answered:
                        answered.add(event["request_id"])
                        assert handler._dispatch_inline({"type": "confirm.response", "request_id": event["request_id"], "choice": "y"}, bridge)
                if asyncio.get_running_loop().time() >= deadline:
                    bridge.request_cancel()
                    raise AssertionError("isolated turn timed out")
                await asyncio.sleep(.005)
            await task

        with caplog.at_level(logging.INFO, logger="my_agent.sidecar"):
            asyncio.run(run_and_approve())

        rows = [line for line in caplog.messages if "event=project_worker_batch " in line]
        parsed = [dict(field.split("=", 1) for field in line.split() if "=" in field) for line in rows]
        batch_ids = {row["batch_id"] for row in parsed}
        assert len(batch_ids) == 1
        batch_id = next(iter(batch_ids))
        assert len(batch_id) == 32
        assert {row["project_id"] for row in parsed} == set(project_ids)
        assert all("task" not in row and "session" not in row and "credential" not in row for row in parsed)
        phases = {(row["project_id"], row["phase"]): row for row in parsed}
        assert set(phases) == {(project_id, phase) for project_id in project_ids
                               for phase in ("queued", "authorized", "started", "ended")}
        assert max_active_workers == 2
        queued_at_values = {phases[(project_id, "queued")]["queued_at"] for project_id in project_ids}
        assert len(queued_at_values) == 1
        queued_at = next(iter(queued_at_values))
        authorized_at_values = {phases[(project_id, "authorized")]["authorized_at"] for project_id in project_ids}
        assert len(authorized_at_values) == 1
        for project_id in project_ids:
            project_rows = [phases[(project_id, phase)] for phase in ("queued", "authorized", "started", "ended")]
            assert [row["phase"] for row in project_rows] == ["queued", "authorized", "started", "ended"]
            assert {row["batch_id"] for row in project_rows} == {batch_id}
            assert {row["queued_at"] for row in project_rows} == {queued_at}
            assert project_rows[1]["authorized_at"] >= queued_at
            assert project_rows[2]["started_at"] >= project_rows[1]["authorized_at"]
            assert project_rows[3]["ended_at"] >= project_rows[2]["started_at"]
            assert project_rows[3]["status"] == "blocked"
        assert phases[(project_ids[2], "started")]["started_at"] >= phases[(project_ids[0], "ended")]["ended_at"]
