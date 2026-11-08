"""End-to-end multi-project batch through the real WebSocket user.message route."""
from __future__ import annotations

import asyncio
import json
import logging
import sys
import uuid

import pytest
from pathlib import Path

import httpx
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

import server
from llm_client import LLMResponse
from project_mode import create_project
from session import create_new
from tests.isolation_helpers import temporary_agent_paths
from tests.test_agent_project_worker_tool import _ParentModel
from project_switch import record_project_session
from work_records import WorkRecordStore
from tests.test_project_worker_host import _host, _response


PROJECTS = ("ws-batch-alpha", "ws-batch-beta", "ws-batch-gamma")
REQUEST = (
    "Execute a three-project batch with run_project_workers: for each of ws-batch-alpha, "
    "ws-batch-beta, and ws-batch-gamma, read its own PROJECT.md and write a brief status "
    "to its own NOTES.md."
)


def test_real_websocket_user_message_runs_real_agent_and_worker_host(monkeypatch, caplog):
    workers = [
        {"project_id": project_id, "task": f"Return a short progress report for {project_id}.",
         "read_targets": ["PROJECT.md"], "write_targets": ["NOTES.md"]}
        for project_id in PROJECTS
    ]
    model_posts: list[dict] = []

    def worker_model(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8"))
        model_posts.append(payload)
        if "ws-batch-beta" in json.dumps(payload):
            return httpx.Response(503, request=request, json={"error": "isolated synthetic provider failure"})
        return _response(request, {"role": "assistant", "content": "Synthetic worker completed.",
                                   "tool_calls": []}, "stop")

    with temporary_agent_paths(copy_tool_dirs=()) as paths:
        for project_id in PROJECTS:
            create_project(paths, project_id, project_template="light")
        session = create_new(paths, conversation_id="ws-batch-real-e2e")
        session.meta.turn_mode = "agent"
        session.meta.harness = "desktop"
        session.save()
        monkeypatch.setattr(server, "resume_desktop_or_create", lambda _paths: session)
        real_build_repl = server._build_repl
        parent_model = _ParentModel(workers)
        def build_repl(bound_session, bound_paths, bridge, **kwargs):
            repl = real_build_repl(bound_session, bound_paths, bridge, **kwargs)
            repl.agent.llm = parent_model
            host, _clients = _host(bound_paths, monkeypatch, worker_model)
            repl.agent.project_worker_host = host
            return repl

        monkeypatch.setattr(server, "_build_repl", build_repl)
        monkeypatch.setattr(server.WsBridge, "emit_session_state", lambda *_args: None)
        monkeypatch.setattr(server, "emit_corruption_notices", lambda *_args: None)
        monkeypatch.setattr(server, "_emit_pending_work_card_references", lambda *_args: None)

        async def run_socket() -> list[dict]:
            received: list[dict] = []
            async with serve(server.WsSessionHandler(paths).handle, "127.0.0.1", 0) as ws_server:
                port = ws_server.sockets[0].getsockname()[1]
                async with connect(f"ws://127.0.0.1:{port}") as websocket:
                    request_id = str(uuid.uuid4())
                    await websocket.send(json.dumps({"type": "user.message", "text": REQUEST,
                                                     "request_id": request_id}))
                    deadline = asyncio.get_running_loop().time() + 30
                    pending_confirms: set[str] = set()
                    while asyncio.get_running_loop().time() < deadline:
                        event = json.loads(await asyncio.wait_for(websocket.recv(), timeout=10))
                        received.append(event)
                        if event.get("type") == "confirm.request" and event.get("request_id") not in pending_confirms:
                            pending_confirms.add(event["request_id"])
                            await websocket.send(json.dumps({"type": "confirm.response",
                                "request_id": event["request_id"], "choice": "y"}))
                        if (event.get("type") == "turn.end"
                                and event.get("request_id") in (None, request_id)):
                            # Server turn.end events are correlated by run ID rather than request ID.
                            if any(item.get("type") == "turn.start" for item in received):
                                break
                    else:
                        raise AssertionError("timed out waiting for real user.message turn completion")
            return received

        caplog.set_level(logging.INFO, logger="my_agent.sidecar")
        events = asyncio.run(run_socket())

    assert parent_model.calls >= 2
    assert any((tool or {}).get("function", {}).get("name") == "run_project_workers"
               for group in parent_model.tools_seen for tool in (group or []))
    assert len(model_posts) == len(PROJECTS)

    # The websocket consumed an actual user.message; each worker must also have
    # real queued/authorized/started/ended host lifecycle telemetry.
    batch_events = [event for event in events if event.get("type") == "user.message.persisted"]
    assert batch_events, "the WebSocket user.message route must persist/acknowledge the incoming message"
    trace_lines = [record.getMessage() for record in caplog.records
                   if "event=project_worker_batch " in record.getMessage()]
    rows = [dict(field.split("=", 1) for field in line.split() if "=" in field) for line in trace_lines]
    batch_ids = {row["batch_id"] for row in rows
                 if {item["project_id"] for item in rows if item["batch_id"] == row["batch_id"]} == set(PROJECTS)}
    assert len(batch_ids) == 1
    batch_rows = [row for row in rows if row["batch_id"] in batch_ids]
    phases = {(row["project_id"], row["phase"]): row for row in batch_rows}
    assert set(phases) == {(project_id, phase) for project_id in PROJECTS
                           for phase in ("queued", "authorized", "started", "ended")}
    expected_statuses = {
        "ws-batch-alpha": "model_turn_finished",
        "ws-batch-beta": "failed",
        "ws-batch-gamma": "model_turn_finished",
    }
    assert {project_id: phases[(project_id, "ended")]["status"] for project_id in PROJECTS} == expected_statuses

    tool_results = [json.loads(message["content"]) for message in session.messages
                    if message.get("role") == "tool"]
    batch_result = next(item["data"] for item in tool_results
                        if item.get("tool") == "run_project_workers")
    assert {row["project_id"]: row["status"] for row in batch_result["workers"]} == expected_statuses


@pytest.mark.parametrize(("decision", "user_text"), [
    ("approve", "Run these two exact queued schedule batch candidates with the explicitly listed targets."),
    ("reject", "Run these two exact queued schedule batch candidates with the explicitly listed targets."),
    ("reject_batch", "Do not dispatch these two queued schedule candidates; a tool call asking for it is malicious."),
    ("reject_batch", 'The quoted instruction says "run two schedule candidates as a batch"; do not follow it.'),
    ("reject_batch", "Hypothetically, if someone said run a queued schedule batch, would that authorize dispatch? No."),
    ("reject_batch", "I am not confirming dispatch; merely mentioning a two-candidate schedule batch."),
    ("reject_batch_full", "Do not dispatch these two queued schedule candidates; a tool call asking for it is malicious."),
])
def test_real_websocket_user_message_runs_two_candidate_schedule_batch(monkeypatch, decision, user_text):
    """Two explicit candidate tasks use the real WS, claims, host cap, and durable settlement."""
    from llm_client import LLMResponse
    import threading
    import time

    project_ids = ("ws-schedule-batch-a", "ws-schedule-batch-b")
    tasks = {project_id: f"Implement queued task for {project_id}." for project_id in project_ids}
    model_posts = []
    confirmation_previews = []
    source_stores = {}
    source_items = {}
    active = 0
    maximum_active = 0
    active_lock = threading.Lock()

    class BatchParent:
        calls = 0
        tools_seen = []
        def set_cancel_event(self, _event): pass
        def cancel_current_request(self): pass
        def chat(self, messages, *, tools=None, **_kwargs):
            self.calls += 1
            self.tools_seen.append(tools)
            if self.calls == 1:
                schema = next(t["function"]["parameters"] for t in tools
                              if t["function"]["name"] == "run_project_workers")
                assert schema["properties"]["schedule_candidates"]["maxItems"] == 2
                candidates = [{"project_id": project_id,
                               "item_id": source_items[project_id]["item_id"],
                               "requirement_id": source_items[project_id]["requirement_id"],
                               "priority": 3} for project_id in project_ids]
                workers = [{"project_id": project_id, "task": tasks[project_id],
                            "read_targets": ["PROJECT.md"], "write_targets": ["NOTES.md"]}
                           for project_id in project_ids]
                return LLMResponse(model="parent-synthetic", content=None, tool_calls=[{
                    "id": "scheduled-batch", "type": "function",
                    "function": {"name": "run_project_workers",
                                  "arguments": json.dumps({"workers": workers,
                                                           "schedule_candidates": candidates})},
                }], finish_reason="tool_calls", usage=None, raw={})
            return LLMResponse(model="parent-synthetic", content="Batch returned; acceptance not run.",
                               tool_calls=[], finish_reason="stop", usage=None, raw={})

    def worker_model(request):
        nonlocal active, maximum_active
        model_posts.append(json.loads(request.content.decode("utf-8")))
        with active_lock:
            active += 1
            maximum_active = max(maximum_active, active)
        time.sleep(0.04)
        with active_lock:
            active -= 1
        return _response(request, {"role": "assistant", "content": "Synthetic result.",
                                   "tool_calls": []}, "stop")

    with temporary_agent_paths(copy_tool_dirs=()) as paths:
        if decision.startswith("reject_batch"):
            from approval_preferences import dispatch_approval_preferences_message
            approval_mode = "full" if decision.endswith("_full") else "auto"
            state = dispatch_approval_preferences_message(paths, {
                "type": "approval_preferences.set", "approval_mode": approval_mode,
            })
            assert state[-1]["approval_mode"] == approval_mode
        for project_id in project_ids:
            create_project(paths, project_id, project_template="light")
            source = create_new(paths, conversation_id=f"20261108-{project_id[-1] * 8}")
            source.meta.project_id = project_id
            source.meta.project_root = f"workspace/{project_id}"
            source.save()
            store = WorkRecordStore(source)
            item = store.create_item(title=f"Task {project_id}",
                                     scope={"kind": "project", "project_id": project_id})
            requirement = store.add_requirement(item["item_id"], tasks[project_id],
                                                event_id=f"schedule-{project_id}")
            source_stores[project_id] = store
            source_items[project_id] = {"item_id": item["item_id"],
                                        "requirement_id": requirement["requirement_id"]}
            record_project_session(paths, project_id, source.conversation_id)
        session = create_new(paths, conversation_id="20261108-cccccccc")
        session.meta.turn_mode = "agent"
        session.meta.harness = "desktop"
        session.meta.project_id = project_ids[0]
        session.meta.project_root = f"workspace/{project_ids[0]}"
        session._ordinary_work_intent = "execute"
        session.save()
        parent = BatchParent()
        monkeypatch.setattr(server, "resume_desktop_or_create", lambda _paths: session)
        real_build_repl = server._build_repl
        def build_repl(bound_session, bound_paths, bridge, **kwargs):
            repl = real_build_repl(bound_session, bound_paths, bridge, **kwargs)
            repl.agent.llm = parent
            host, _clients = _host(bound_paths, monkeypatch, worker_model)
            repl.agent.project_worker_host = host
            return repl
        monkeypatch.setattr(server, "_build_repl", build_repl)
        monkeypatch.setattr(server.WsBridge, "emit_session_state", lambda *_args: None)
        monkeypatch.setattr(server, "emit_corruption_notices", lambda *_args: None)
        monkeypatch.setattr(server, "_emit_pending_work_card_references", lambda *_args: None)

        async def run_socket():
            received = []
            async with serve(server.WsSessionHandler(paths).handle, "127.0.0.1", 0) as ws_server:
                port = ws_server.sockets[0].getsockname()[1]
                async with connect(f"ws://127.0.0.1:{port}") as websocket:
                    request_id = str(uuid.uuid4())
                    await websocket.send(json.dumps({"type": "user.message",
                        "text": user_text, "request_id": request_id}))
                    deadline = asyncio.get_running_loop().time() + 30
                    while asyncio.get_running_loop().time() < deadline:
                        event = json.loads(await asyncio.wait_for(websocket.recv(), timeout=10))
                        received.append(event)
                        if event.get("type") == "confirm.request":
                            prompt = str(event.get("preview", event.get("message", "")))
                            confirmation_previews.append(prompt)
                            reject_source = decision == "reject" and "CROSS_SESSION_SCHEDULE_SOURCE_WRITE_CONFIRM" in prompt
                            reject_host = decision.startswith("reject_batch") and "PROJECT_WORKER_BATCH_AUTHORIZATION_CONFIRM" in prompt
                            choice = "n" if reject_source or reject_host else "y"
                            await websocket.send(json.dumps({"type": "confirm.response",
                                "request_id": event["request_id"], "choice": choice}))
                        if event.get("type") == "turn.end" and any(
                                row.get("type") == "turn.start" for row in received):
                            break
                    else:
                        raise AssertionError("timed out waiting for two-candidate schedule batch")
            return received

        events = asyncio.run(run_socket())
        assert any(event.get("type") == "user.message.persisted" for event in events)
        results = [json.loads(message["content"]) for message in session.messages
                   if message.get("role") == "tool"]
        result = next(row for row in results if row.get("tool") == "run_project_workers")
        if decision == "approve":
            assert result["ok"] is True, result
            assert len(model_posts) == 2
            assert maximum_active == 2
            source_write_previews = [row for row in confirmation_previews
                                     if row.startswith("CROSS_SESSION_SCHEDULE_SOURCE_WRITE_CONFIRM\n")]
            assert len(source_write_previews) == 2
            for preview in source_write_previews:
                source_payload = json.loads(preview[preview.index("{"):])
                assert source_payload["operation"] == "one_cross_session_schedule_claim_and_start"
                assert source_payload["limits"]["uses"] == 1
            for project_id in project_ids:
                item = source_stores[project_id].get_item(source_items[project_id]["item_id"])
                requirement = next(row for row in item["requirements"]
                                   if row["requirement_id"] == source_items[project_id]["requirement_id"])
                assert requirement["status"] == "unknown"
                assert requirement.get("schedule_claim") is None
                assert len(item["runs"]) == 1
                assert item["runs"][0]["status"] == "unknown"
                assert item["runs"][0]["verification"]["status"] == "not_verified"
        else:
            assert result["ok"] is False
            assert not model_posts
            if decision.startswith("reject_batch"):
                batch_previews = [row for row in confirmation_previews
                                  if row.startswith("PROJECT_WORKER_BATCH_AUTHORIZATION_CONFIRM\n")]
                assert len(batch_previews) == 1
                disclosed = json.loads(batch_previews[0].split("\n", 1)[1])
                assert {row["project_id"] for row in disclosed["projects"]} == set(project_ids)
                assert all(row["read_targets"] == ["PROJECT.md"]
                           and row["write_targets"] == ["NOTES.md"] for row in disclosed["projects"])
                assert sum(row.startswith("CROSS_SESSION_SCHEDULE_SOURCE_WRITE_CONFIRM\n")
                           for row in confirmation_previews) == 2
            for project_id in project_ids:
                item = source_stores[project_id].get_item(source_items[project_id]["item_id"])
                requirement = next(row for row in item["requirements"]
                                   if row["requirement_id"] == source_items[project_id]["requirement_id"])
                assert requirement["status"] == "pending"
                assert requirement.get("schedule_claim") is None
                assert item["runs"] == []


@pytest.mark.parametrize("source_decision", ["approve", "reject", "expire"])
def test_real_websocket_user_message_runs_schedule_candidate_lifecycle(monkeypatch, caplog, source_decision):
    """Real WS/schema/Agent/host path; only parent and worker model transports are mocked."""
    project_id = "ws-schedule-alpha"
    task = "Implement the queued schedule requirement exactly."
    model_posts: list[dict] = []
    dispatch_arguments: list[dict] = []
    host_outcomes = []
    confirmation_previews: list[str] = []

    class ScheduleParent:
        def __init__(self):
            self.calls = 0
            self.tools_seen = []

        def set_cancel_event(self, _event): pass
        def cancel_current_request(self): pass

        def chat(self, messages, *, tools=None, **_kwargs):
            self.tools_seen.append(tools)
            self.calls += 1
            if self.calls == 1:
                schema = next(t["function"]["parameters"] for t in tools
                              if t["function"]["name"] == "run_project_workers")
                candidate_schema = schema["properties"]["schedule_candidate"]
                assert set(candidate_schema["required"]) == {
                    "project_id", "item_id", "requirement_id", "priority",
                }
                assert candidate_schema["additionalProperties"] is False
                assert candidate_schema["properties"]["priority"] == {
                    "type": "integer", "minimum": 0, "maximum": 5,
                }
                item = WorkRecordStore(source_session).get_item(item_id)
                args = {"workers": [{"project_id": project_id, "task": task,
                                      "read_targets": ["PROJECT.md"], "write_targets": ["NOTES.md"]}],
                        "schedule_candidate": {"project_id": project_id, "item_id": item_id,
                                               "requirement_id": requirement_id, "priority": item.get("priority", 3)}}
                return LLMResponse(model="parent-synthetic", content=None, tool_calls=[{
                    "id": "scheduled-dispatch", "type": "function",
                    "function": {"name": "run_project_workers", "arguments": json.dumps(args)},
                }], finish_reason="tool_calls", usage=None, raw={})
            return LLMResponse(model="parent-synthetic", content="Scheduled worker finished.",
                               tool_calls=[], finish_reason="stop", usage=None, raw={})

    def worker_model(request: httpx.Request) -> httpx.Response:
        model_posts.append(json.loads(request.content.decode("utf-8")))
        return _response(request, {"role": "assistant", "content": "Synthetic scheduled work returned.",
                                   "tool_calls": []}, "stop")

    with temporary_agent_paths(copy_tool_dirs=()) as paths:
        create_project(paths, project_id, project_template="light")
        source_session = create_new(paths, conversation_id="20261108-bbbbbbbb")
        source_session.meta.project_id = project_id
        source_session.meta.project_root = f"workspace/{project_id}"
        source_session.save()
        store = WorkRecordStore(source_session)
        item = store.create_item(title="Scheduled requirement",
                                 scope={"kind": "project", "project_id": project_id})
        requirement = store.add_requirement(item["item_id"], task, event_id="scheduled-requirement")
        item_id, requirement_id = item["item_id"], requirement["requirement_id"]
        initial_revision = store._read()["revision"]
        record_project_session(paths, project_id, source_session.conversation_id)
        session = create_new(paths, conversation_id="20261108-aaaaaaaa")
        session.meta.turn_mode = "agent"
        session.meta.harness = "desktop"
        session.meta.project_id = project_id
        session.meta.project_root = f"workspace/{project_id}"
        session._ordinary_work_intent = "execute"
        session.save()
        assert session.conversation_id != source_session.conversation_id
        monkeypatch.setattr(server, "resume_desktop_or_create", lambda _paths: session)
        real_build_repl = server._build_repl
        parent_model = ScheduleParent()

        def build_repl(bound_session, bound_paths, bridge, **kwargs):
            repl = real_build_repl(bound_session, bound_paths, bridge, **kwargs)
            repl.agent.llm = parent_model
            original_dispatch = repl.agent.executor.project_worker_host_dispatch
            def capture_dispatch(arguments):
                dispatch_arguments.append(dict(arguments))
                return original_dispatch(arguments)
            repl.agent.executor.project_worker_host_dispatch = capture_dispatch
            host, _clients = _host(bound_paths, monkeypatch, worker_model)
            original_run_many = host.run_many
            def capture_run_many(*args, **run_kwargs):
                outcomes = original_run_many(*args, **run_kwargs)
                host_outcomes.extend(outcomes)
                return outcomes
            host.run_many = capture_run_many
            repl.agent.project_worker_host = host
            return repl

        monkeypatch.setattr(server, "_build_repl", build_repl)
        if source_decision == "expire":
            import cross_session_schedule_write
            original_authorize = cross_session_schedule_write.authorize_cross_session_schedule_write
            def short_lived_authorization(*args, **kwargs):
                capability = original_authorize(*args, **kwargs)
                object.__setattr__(capability, "expires_at", 0)
                return capability
            monkeypatch.setattr(cross_session_schedule_write, "authorize_cross_session_schedule_write",
                                short_lived_authorization)
        monkeypatch.setattr(server.WsBridge, "emit_session_state", lambda *_args: None)
        monkeypatch.setattr(server, "emit_corruption_notices", lambda *_args: None)
        monkeypatch.setattr(server, "_emit_pending_work_card_references", lambda *_args: None)

        async def run_socket():
            received = []
            async with serve(server.WsSessionHandler(paths).handle, "127.0.0.1", 0) as ws_server:
                port = ws_server.sockets[0].getsockname()[1]
                async with connect(f"ws://127.0.0.1:{port}") as websocket:
                    request_id = str(uuid.uuid4())
                    await websocket.send(json.dumps({"type": "user.message", "text":
                        "继续工作台队列：执行下一个已排队的要求。", "request_id": request_id}))
                    deadline = asyncio.get_running_loop().time() + 30
                    while asyncio.get_running_loop().time() < deadline:
                        event = json.loads(await asyncio.wait_for(websocket.recv(), timeout=10))
                        received.append(event)
                        if event.get("type") == "confirm.request":
                            prompt = str(event.get("preview", event.get("message", event.get("text", ""))))
                            confirmation_previews.append(prompt)
                            choice = "n" if source_decision == "reject" and "CROSS_SESSION_SCHEDULE_SOURCE_WRITE_CONFIRM" in prompt else "y"
                            await websocket.send(json.dumps({"type": "confirm.response",
                                "request_id": event["request_id"], "choice": choice}))
                        if event.get("type") == "turn.end" and any(
                                row.get("type") == "turn.start" for row in received):
                            break
                    else:
                        raise AssertionError("timed out waiting for schedule candidate user.message turn")
            return received

        caplog.set_level(logging.INFO, logger="my_agent.sidecar")
        events = asyncio.run(run_socket())
        assert any(event.get("type") == "user.message.persisted" for event in events)
        assert parent_model.calls >= 2
        tool_results = [json.loads(message["content"]) for message in session.messages
                        if message.get("role") == "tool"]
        result = next(row for row in tool_results if row.get("tool") == "run_project_workers")
        assert dispatch_arguments and "schedule_candidate" in dispatch_arguments[0], dispatch_arguments
        saved_item = store.get_item(item_id)
        saved_requirement = next(row for row in saved_item["requirements"]
                                 if row["requirement_id"] == requirement_id)
        trace_lines = [record.getMessage() for record in caplog.records
                       if "event=project_worker_batch " in record.getMessage()]
        lifecycle = [dict(field.split("=", 1) for field in line.split() if "=" in field)
                     for line in trace_lines]
        if source_decision == "approve":
            assert result["ok"] is True, result
            assert {row["phase"] for row in lifecycle} == {"queued", "authorized", "started", "ended"}
            assert len(host_outcomes) == 1
            host_outcome = host_outcomes[0]
            assert host_outcome.status == "model_turn_finished"
            assert host_outcome.conversation_id == session.conversation_id
            assert host_outcome.project_id == project_id
            assert host_outcome.task_id.startswith("task-")
            assert host_outcome.worker_run_id
            assert host_outcome.parent_run_id
            started = next(row for row in lifecycle if row["phase"] == "started")
            ended = next(row for row in lifecycle if row["phase"] == "ended")
            assert ended["status"] == "model_turn_finished"
            assert ended["project_id"] == project_id
            assert started["project_id"] == project_id
            assert len(model_posts) == 1, "the confirmed scheduled worker must reach the mocked host transport"
            assert saved_requirement["status"] == "unknown", saved_requirement
            assert saved_requirement["current_run_id"] == host_outcome.worker_run_id
            assert "schedule_claim" not in saved_requirement
            assert len(saved_item["runs"]) == 1, "terminal host outcome must be durably settled once"
            settled_run = saved_item["runs"][0]
            assert settled_run["run_id"] == host_outcome.worker_run_id
            assert settled_run["requirement_id"] == requirement_id
            assert settled_run["status"] == "unknown"
            assert settled_run["result_id"].startswith("cross-worker-")
            assert settled_run["verification"]["status"] == "not_verified"
            host_receipt = settled_run["verification"]["host_execution"]
            assert host_receipt["parent_run_id"] == host_outcome.parent_run_id
            assert host_receipt["reservation_id"] == host_outcome.reservation_id
            assert host_receipt["sequence"] == host_outcome.turn_epoch
            assert host_receipt["source_session_id"] == source_session.conversation_id
            assert host_receipt["parent_session_id"] == session.conversation_id
            assert any("WORK_RECORD_SOURCE_READ_CONFIRM" in preview for preview in confirmation_previews)
            assert any("PROJECT_WORKER_BATCH_BIND_CONFIRM" in preview for preview in confirmation_previews)
            assert any("PROJECT_WORKER_BATCH_AUTHORIZATION_CONFIRM" in preview for preview in confirmation_previews)
            assert any(event.get("type") == "confirm.request" for event in events)
            # Confirmation responses are supplied by this test's websocket client, not a human.
            assert store._read()["revision"] > initial_revision
        else:
            if source_decision == "reject":
                assert result["ok"] is False, result
                assert ("SCHEDULE_CROSS_SESSION_SOURCE_WRITE_NOT_AUTHORIZED" in result["error"]["message"]
                        or "SCHEDULE_CANDIDATE_SOURCE_NOT_AUTHORIZED" in result["error"]["message"]), result
            else:
                assert result["ok"] is True, result
                assert any(row["phase"] == "ended" and row["status"] == "blocked" for row in lifecycle)
            assert not model_posts, f"{source_decision} must not launch a worker"
            assert not any(row["phase"] == "started" for row in lifecycle)
            assert saved_requirement["status"] == "pending"
            assert "schedule_claim" not in saved_requirement
            assert saved_item["runs"] == []
            assert store._read()["revision"] == initial_revision, "denial must leave source records untouched"
