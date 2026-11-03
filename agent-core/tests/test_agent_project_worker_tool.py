"""Actual Agent -> ToolExecutor -> WsBridge -> project worker vertical test."""
from __future__ import annotations

import asyncio
import json
import re
import sys
import threading
from pathlib import Path
from typing import Any

import httpx
import pytest

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

import llm_client
from llm_client import LLMResponse
from project_mode import create_project
from project_switch import record_project_session
from paths import AgentPaths
from server import WsBridge, _begin_work_record_capture_turn, _build_repl, _run_line
from session import create_new
from work_records import WorkRecordStore
from multi_project_work_record_bindings import ConversationWorkRecordBindings
from tests.isolation_helpers import temporary_agent_paths

_REAL_HTTPX_CLIENT = httpx.Client


class _ParentModel:
    def __init__(self, workers: list[dict[str, Any]], *, final_text: str = "The worker turns returned; acceptance was not run.") -> None:
        self.workers = workers
        self.final_text = final_text
        self.calls = 0
        self.tools_seen: list[list[dict[str, Any]] | None] = []
        self.cancel_event = threading.Event()

    def set_cancel_event(self, event: threading.Event) -> None:
        self.cancel_event = event

    def cancel_current_request(self) -> None:
        return None

    def chat(self, messages, *, model=None, tools=None, temperature=0.0, stream=None, **_extra):
        self.tools_seen.append(tools)
        self.calls += 1
        if self.calls == 1:
            args = json.dumps({"workers": self.workers})
            tool_calls = [{"id": "parent-worker-dispatch", "type": "function",
                           "function": {"name": "run_project_workers", "arguments": args}}]
            return LLMResponse(model="parent-synthetic", content=None, tool_calls=tool_calls,
                               finish_reason="tool_calls", usage=None, raw={})
        return LLMResponse(model="parent-synthetic",
                           content=self.final_text,
                           tool_calls=[], finish_reason="stop", usage=None, raw={})




def test_mixed_worker_outcomes_preserve_verified_tasks_and_mark_only_conflicts_unknown():
    from agent import reconcile_project_worker_batch_outcomes
    from project_worker_host import ProjectWorkerHostOutcome, ProjectWorkerReservation

    bindings=[];reservations=[];outcomes=[]
    for index,project_id in enumerate(("project-alpha","project-beta","project-gamma")):
        task_id=f"task-{index}";reservation=ProjectWorkerReservation(
            reservation_id=f"reservation-{index}",worker_id=f"worker-{index}",worker_run_id=f"worker-run-{index}",
            parent_run_id="parent-mixed",turn_epoch=7,conversation_id="20261106-mixed0001",
            project_id=project_id,task_id=task_id)
        reservations.append(reservation)
        bindings.append({"task_id":task_id,"project_id":project_id,"item_id":"item-1","requirement_id":f"req-{index}",
                         "task_input":f"task input {index}","worker_id":reservation.worker_id,"run_id":reservation.worker_run_id})
        if index < 2:
            outcomes.append(ProjectWorkerHostOutcome(
                worker_id=reservation.worker_id,run_id=reservation.parent_run_id,turn_epoch=reservation.turn_epoch,
                conversation_id=reservation.conversation_id,project_id=project_id,task_id=task_id,status="cancelled",
                parent_run_id=reservation.parent_run_id,worker_run_id=reservation.worker_run_id,
                reservation_id=reservation.reservation_id))
        else:
            outcomes.append(ProjectWorkerHostOutcome(
                worker_id="wrong-worker",run_id=reservation.parent_run_id,turn_epoch=reservation.turn_epoch,
                conversation_id=reservation.conversation_id,project_id=project_id,task_id=task_id,status="model_turn_finished",
                parent_run_id=reservation.parent_run_id,worker_run_id=reservation.worker_run_id,
                reservation_id=reservation.reservation_id))
    unexpected=ProjectWorkerHostOutcome(
        worker_id="extra-worker",run_id="parent-mixed",turn_epoch=7,conversation_id="20261106-mixed0001",
        project_id="project-extra",task_id="unexpected-task",status="model_turn_finished",
        parent_run_id="parent-mixed",worker_run_id="extra-run",reservation_id="extra-reservation")
    verified,unknown,unexpected_count=reconcile_project_worker_batch_outcomes(bindings,reservations,(*outcomes,unexpected))
    assert [row[2].task_id for row in verified]==["task-0","task-1"]
    assert len(unknown)==1
    assert unknown[0]["task_id"]=="task-2" and unknown[0]["status"]=="unknown"
    assert unknown[0]["error_code"]=="worker_outcome_identity_mismatch"
    assert unexpected_count==1

@pytest.mark.parametrize("prestart_mode", ["work_record_capture", "queued_activation"])
def test_agent_run_line_dispatches_five_project_tasks_with_two_active_cap(monkeypatch, prestart_mode):
    with temporary_agent_paths(copy_tool_dirs=("common/write_text", "coding/patch_file")) as paths:
        project_ids = ("project-alpha", "project-beta", "project-gamma", "project-delta", "project-epsilon")
        marker_prefix = {
            "project-alpha": "ALPHA", "project-beta": "BETA", "project-gamma": "GAMMA",
            "project-delta": "DELTA", "project-epsilon": "EPSILON",
        }
        markers = tuple(f"{marker_prefix[project_id]}_{index}" for project_id in project_ids for index in range(1, 4))
        for project_id in project_ids:
            create_project(paths, project_id, project_template="light")
            (paths.workspace / project_id / "README.md").write_text(f"source for {project_id}\n", encoding="utf-8")
        from project_worker_read import ProjectReadTransientError, ProjectTextReader
        original_read_texts = ProjectTextReader.read_texts
        injected_retry = False
        retry_injection_lock = threading.Lock()
        def one_transient_read_failure(reader, requested_paths):
            nonlocal injected_retry
            if requested_paths == ["README.md"]:
                with retry_injection_lock:
                    if not injected_retry:
                        injected_retry = True
                        raise ProjectReadTransientError("sharing_violation")
            return original_read_texts(reader, requested_paths)
        monkeypatch.setattr(ProjectTextReader, "read_texts", one_transient_read_failure)
        task_specs = [(project_id, marker) for project_id, marker in zip(
            (project_id for project_id in project_ids for _ in range(3)), markers,
        )]
        project_source_ids = {}
        for index, project_id in enumerate(project_ids):
            source_id = f"20261102-{index:08x}"
            project_source_ids[project_id] = source_id
            project_session = create_new(paths, conversation_id=source_id)
            project_session.meta.project_id = project_id
            project_session.meta.project_root = f"workspace/{project_id}"
            project_session.save()
            record_project_session(paths, project_id, source_id)
            source_store = WorkRecordStore(project_session)
            project_tasks = [marker for task_project, marker in task_specs if task_project == project_id]
            if project_tasks:
                item = source_store.create_item(
                    title=f"Existing {project_id} work", scope={"kind": "project", "project_id": project_id},
                )
                for task_index, marker in enumerate(project_tasks):
                    source_store.add_requirement(
                        item["item_id"], f"Write result marker {marker}.",
                        event_id=f"source-{project_id}-{task_index}",
                    )
        session = create_new(paths, conversation_id="20261102-aaaa1111")
        session.meta.active_shell = "project"
        session.meta.project_id = "project-alpha"
        session.meta.project_root = "workspace/project-alpha"
        session.messages.append({"role": "user", "content": "PARENT_SECRET_MUST_NOT_LEAK"})
        session.save()
        workers = [
            {"project_id": project_id, "task": f"Write result marker {marker}.",
             "read_targets": ["README.md"], "write_targets": ["PROJECT.md"]}
            for project_id, marker in task_specs
        ]
        parent = _ParentModel(workers)
        events: list[dict[str, Any]] = []
        event_lock = threading.Lock()
        pending: set[str] = set()
        max_pending = 0

        def emit(event: dict[str, Any]) -> None:
            nonlocal max_pending
            with event_lock:
                events.append(dict(event))
                if event.get("type") == "confirm.request":
                    pending.add(str(event.get("request_id")))
                    max_pending = max(max_pending, len(pending))
                elif event.get("type") == "confirm.done":
                    pending.discard(str(event.get("request_id")))

        bridge = WsBridge(emit=emit, paths=paths, confirm_timeout=5)
        repl = _build_repl(session, paths, bridge, work_record_controller_factory=lambda *_: None)
        repl.agent.llm = parent
        parent.set_cancel_event(repl.agent.cancel_event)
        parent_run_turn = repl.agent.run_turn
        repl.agent.run_turn = lambda text, **_kwargs: parent_run_turn(text, spawn_explore=False)

        rounds: dict[str, int] = {}
        round_lock = threading.Lock()
        first_pair = threading.Barrier(2, timeout=4)
        payloads: list[dict[str, Any]] = []
        active_markers: set[str] = set()
        peak_active = 0

        def provider(request: httpx.Request) -> httpx.Response:
            nonlocal peak_active
            body = json.loads(request.content.decode("utf-8"))
            payloads.append(body)
            system = body["messages"][0]["content"]
            project_id = next(pid for pid in project_ids if f"project_id is '{pid}'" in system)
            task_text = body["messages"][1]["content"]
            marker = next(item for item in markers if item in task_text)
            with round_lock:
                round_no = rounds.get(marker, 0)
                rounds[marker] = round_no + 1
                active_markers.add(marker)
                peak_active = max(peak_active, len(active_markers))
            try:
                if round_no == 0 and marker in {"ALPHA_1", "BETA_1"}:
                    first_pair.wait()
                if round_no == 0:
                    msg = {"role": "assistant", "content": None,
                           "tool_calls": [{"id": f"read-{marker}", "type": "function",
                               "function": {"name": "read_project_file",
                                            "arguments": json.dumps({"path": "README.md"})}}]}
                    finish = "tool_calls"
                elif round_no == 1:
                    msg = {"role": "assistant", "content": None,
                           "tool_calls": [{"id": f"write-{marker}", "type": "function",
                               "function": {"name": "write_project_file", "arguments": json.dumps({
                                   "path": "PROJECT.md", "content": f"Worker marker: {marker}"})}}]}
                    finish = "tool_calls"
                else:
                    msg = {"role": "assistant", "content": f"Turn ended for {marker}; acceptance not run.",
                           "tool_calls": []}
                    finish = "stop"
                return httpx.Response(200, request=request, json={"model": "gpt-6-luna", "choices": [
                    {"finish_reason": finish, "message": msg}]})
            finally:
                with round_lock:
                    active_markers.discard(marker)

        monkeypatch.setenv("OX567_API_KEY", "host-test-secret-never-display")
        monkeypatch.setenv("OX567_BASE_URL", "https://api-cdn.0x567.com")
        monkeypatch.setenv("OX567_MODEL_FLASH", "gpt-6-luna")
        monkeypatch.setattr(llm_client.httpx, "Client", lambda **kwargs: _REAL_HTTPX_CLIENT(
            transport=httpx.MockTransport(provider), **kwargs))

        async def run_and_confirm():
            if prestart_mode == "work_record_capture":
                prestarted_run_id = _begin_work_record_capture_turn(repl, bridge)
            else:
                queued = bridge.lifecycle.enqueue("ordinary")
                bridge.lifecycle_conversation_id = session.conversation_id
                bridge._turn_busy.set()
                prestarted_run_id = queued.run_id
            assert prestarted_run_id
            run_task = asyncio.create_task(_run_line(
                repl, bridge,
                "请分别实现 project-alpha 的 ALPHA_1 和 ALPHA_2、project-beta 的 BETA_1、"
                "project-gamma 的 GAMMA_1、project-delta 的 DELTA_1。",
                paths, lock_held=True, prestarted_run_id=prestarted_run_id,
            ))
            deadline = asyncio.get_running_loop().time() + 15
            while not run_task.done():
                pending_id = bridge._pending_confirm_id
                if pending_id:
                    assert bridge.deliver_confirm(pending_id, "y")
                if asyncio.get_running_loop().time() >= deadline:
                    bridge.request_cancel()
                    raise AssertionError("Agent/WsBridge worker dispatch timed out")
                await asyncio.sleep(0.005)
            await run_task

        asyncio.run(run_and_confirm())
        assert parent.calls == 2
        tools = parent.tools_seen[0]
        dispatch = next(t for t in tools if t["function"]["name"] == "run_project_workers")
        worker_schema = dispatch["function"]["parameters"]["properties"]["workers"]
        from project_worker_host import _MAX_WORKER_BATCH_SIZE
        assert worker_schema["maxItems"] == _MAX_WORKER_BATCH_SIZE == 25
        assert str(_MAX_WORKER_BATCH_SIZE) in dispatch["function"]["description"]
        worker_properties = worker_schema["items"]["properties"]
        worker_required = {"project_id", "task", "read_targets", "write_targets"}
        assert set(worker_schema["items"]["required"]) == worker_required
        assert set(worker_properties) == worker_required | {"pytest_target"}
        assert worker_properties["pytest_target"]["type"] == "string"
        assert "pytest_target" not in worker_schema["items"]["required"]
        from agent import build_llm_tools
        previous_mode = session.meta.turn_mode
        session.meta.turn_mode = "ask"
        assert not any(tool["function"]["name"] == "run_project_workers"
                       for tool in build_llm_tools(session, registry=repl.agent.executor.registry))
        session.meta.turn_mode = previous_mode
        assert len(payloads) == 45
        assert rounds == {marker: 3 for marker in markers}
        assert peak_active >= 2, "the first Alpha/Beta projects did not overlap"
        assert max_pending == 1 and pending == set(), "WsBridge confirmation slot overlapped"
        confirm_events = [event for event in events if event.get("type") == "confirm.request"]
        previews = [event["preview"] for event in confirm_events]
        source_previews = [text for text in previews if "是否授权当前对话读取以下所选项目 Session" in text]
        batch_binding_previews = [text for text in previews if text.startswith("PROJECT_WORKER_BATCH_BIND_CONFIRM\n")]
        scope_previews = [text for text in previews if text.startswith("项目 worker 范围确认：")]
        outbound_previews = [text for text in previews if text.startswith("项目 worker 即将向 0x567 gpt-6-luna 外发")]
        write_previews = [text for text in previews if text.startswith("项目 worker 文件写入确认：")]
        item_copy_previews = [text for text in previews if "LINKED_WORK_ITEM_COPY_CONFIRM" in text]
        project_copy_previews = [text for text in previews if "LINKED_PROJECT_SESSION_COPY_CONFIRM" in text]
        # The original 77 execution/read/bind confirmations remain, with one
        # separate consent for parent-persisting selected linked items and one
        # broader source-session-copy consent per selected project (five here).
        assert (len(source_previews), len(batch_binding_previews), len(scope_previews),
                len(outbound_previews), len(write_previews), len(item_copy_previews),
                len(project_copy_previews)) == (1, 1, 15, 45, 15, 1, 5)
        assert len(confirm_events) == sum((1, 1, 15, 45, 15, 1, 5)) == 83
        assert all(marker in item_copy_previews[0] for marker in markers)
        for project_id in project_ids:
            selected_copy = [text for text in project_copy_previews if project_id in text]
            assert len(selected_copy) == 1, f"expected one explicitly scoped project-session copy consent for {project_id}"
            own_markers = [marker for task_project, marker in task_specs if task_project == project_id]
            other_markers = [marker for task_project, marker in task_specs if task_project != project_id]
            assert all(marker in selected_copy[0] for marker in own_markers)
            assert all(marker not in selected_copy[0] for marker in other_markers), "project copy consent leaked another source project's requirements"
            assert "并非整个项目的全部历史" in selected_copy[0]
            assert "不保证该 Session 仍是项目当前映射" in selected_copy[0]
            assert "不授权写来源或未来重读" in selected_copy[0]
        request_ids = [event["request_id"] for event in confirm_events]
        assert len(set(request_ids)) == len(request_ids)
        active_run = next(event["run_id"] for event in events
                          if event.get("type") == "execution.state" and event.get("state") == "running")
        assert all(event["run_id"] == active_run for event in confirm_events)
        run_snapshot = next(event for event in events
                            if event.get("type") == "execution.state" and event.get("state") == "running")
        assert len(source_previews) == 1
        assert all(project_id in source_previews[0] and source_id in source_previews[0]
                   for project_id, source_id in project_source_ids.items())
        assert len(batch_binding_previews) == 1
        assert all(marker in batch_binding_previews[0] for marker in markers)
        scope_previews = [text.split("\n", 1)[1] for text in scope_previews]
        outbound_previews = [text.split("\n", 1)[1] for text in outbound_previews]
        assert len(scope_previews) == 15 and len(outbound_previews) == 45 and len(write_previews) == 15
        assert all((doc := json.loads(text))["run_id"] == run_snapshot["run_id"]
                   and doc["turn_epoch"] == run_snapshot["sequence"] for text in scope_previews)
        assert all((doc := json.loads(text))["scope"]["run_id"] == run_snapshot["run_id"]
                   and doc["scope"]["turn_epoch"] == run_snapshot["sequence"] for text in outbound_previews)
        assert repl.agent._project_worker_live_binding is None
        assert repl.agent.project_worker_host._reservations == {}
        assert repl.agent.project_worker_host._reservations_by_parent == {}
        assert repl.agent.project_worker_host._reserved_task_scopes == set()
        expected = {project_id: f"{marker_prefix[project_id]}_3" for project_id in project_ids}
        for pid, marker in expected.items():
            assert (paths.workspace / pid / "PROJECT.md").read_text(encoding="utf-8").endswith(
                f"Worker marker: {marker}"
            )
        assert session.meta.project_id == "project-alpha"
        assert "PARENT_SECRET_MUST_NOT_LEAK" not in json.dumps(payloads, ensure_ascii=False)
        tool_results = [json.loads(message["content"]) for message in session.messages if message.get("role") == "tool"]
        result_data = next(item["data"] for item in tool_results if item.get("tool") == "run_project_workers")
        assert len(result_data["workers"]) == 15
        assert all(row["status"] == "model_turn_finished" for row in result_data["workers"])
        assert all(row["acceptance_status"] == "not_run" for row in result_data["workers"])
        assert result_data["work_record_binding_status"] == "confirmed"
        assert all(row["record_binding_status"] == "confirmed" for row in result_data["workers"])
        assert all(row["record_lifecycle_status"] == "unknown_recorded" for row in result_data["workers"])
        retry_evidence = [attempt for row in result_data["workers"] for attempt in row["read_attempts"]
                          if attempt["result"] == "retrying"]
        assert injected_retry and len(retry_evidence) == 1
        assert retry_evidence[0]["error_code"] == "sharing_violation"
        assert all("content" not in attempt and "body" not in attempt
                   for row in result_data["workers"] for attempt in row["read_attempts"])
        binding_rows = ConversationWorkRecordBindings(session).list_bindings()
        assert len(binding_rows) == 15
        assert {(row["project_id"], row["task_input"], row["source_session_id"]) for row in binding_rows} == {
            (project_id, f"Write result marker {marker}.", project_source_ids[project_id])
            for project_id, marker in task_specs
        }
        assert all(row["run_id"] == worker["worker_run_id"]
                   and row["worker_id"] == worker["worker_id"]
                   for row, worker in zip(binding_rows, result_data["workers"]))
        assert all(row["lifecycle"] == {
            "sequence": worker["turn_epoch"], "status": "model_turn_finished", "finish_reason": "unknown",
        } for row, worker in zip(binding_rows, result_data["workers"]))
        assert all(row["read_attempts_available"] == worker["read_attempts_available"]
                   and row["read_attempts"] == worker["read_attempts"]
                   for row, worker in zip(binding_rows, result_data["workers"]))
        assert all(row["record_status"] == "unknown" and row["verification_status"] == "not_verified"
                   for row in binding_rows)
        assert len({row["worker_id"] for row in result_data["workers"]}) == 15
        assert len({row["worker_run_id"] for row in result_data["workers"]}) == 15
        assert len({row["reservation_id"] for row in result_data["workers"]}) == 15
        assert all(row["parent_run_id"] == row["run_id"] == active_run for row in result_data["workers"])
        assert all(row["worker_run_id"] != row["parent_run_id"] for row in result_data["workers"])







@pytest.mark.parametrize(
    ("records", "task", "expected_code"),
    [
        ([], "Exact requirement missing.", "WORK_RECORD_REQUIREMENT_MISSING_EXACT_REQUIREMENT"),
        (["Different existing requirement."], "Exact requirement missing.",
         "WORK_RECORD_REQUIREMENT_MISSING_EXACT_REQUIREMENT"),
        (["Duplicate requirement.", "Duplicate requirement."], "Duplicate requirement.",
         "WORK_RECORD_REQUIREMENT_AMBIGUOUS_EXACT_REQUIREMENT"),
    ],
)
def test_missing_or_ambiguous_requirement_asks_user_and_refuses_dispatch(
    tmp_path, records, task, expected_code,
):
    with temporary_agent_paths() as paths:
        create_project(paths, "project-alpha", project_template="light")
        project_session = create_new(paths, conversation_id="20261103-00000001")
        project_session.meta.project_id = "project-alpha"
        project_session.meta.project_root = "workspace/project-alpha"
        project_session.save()
        record_project_session(paths, "project-alpha", project_session.conversation_id)
        store = WorkRecordStore(project_session)
        for index, requirement_text in enumerate(records):
            item = store.create_item(
                title=f"Existing item {index}",
                scope={"kind": "project", "project_id": "project-alpha"},
            )
            store.add_requirement(item["item_id"], requirement_text, event_id=f"source-{index}")

        session = create_new(paths, conversation_id="20261103-00000002")
        session.meta.turn_mode = "agent"
        session.meta.harness = "desktop"
        session._ordinary_work_intent = "execute"
        events: list[dict[str, Any]] = []
        bridge = WsBridge(emit=events.append, paths=paths, confirm_timeout=5)
        repl = _build_repl(session, paths, bridge, work_record_controller_factory=lambda *_args: None)
        repl.agent.llm = _ParentModel(
            [{"project_id": "project-alpha", "task": task,
              "read_targets": ["README.md"], "write_targets": ["PROJECT.md"]}],
            final_text="我没有找到唯一的精确匹配 requirement。请提供该项目中已有 requirement 的准确文本；本批次不会派发，也不会创建记录。",
        )
        host = repl.agent.project_worker_host
        reservations = host.reserve_many
        reservation_calls = []

        def observe_reserve(*args, **kwargs):
            reservation_calls.append((args, kwargs))
            return reservations(*args, **kwargs)

        host.reserve_many = observe_reserve
        queued = bridge.lifecycle.enqueue("ordinary")
        bridge.lifecycle_conversation_id = session.conversation_id
        bridge._turn_busy.set()

        async def run_and_confirm_source():
            task_run = asyncio.create_task(_run_line(
                repl, bridge, "Run the exact task in project-alpha.", paths,
                lock_held=True, prestarted_run_id=queued.run_id,
            ))
            deadline = asyncio.get_running_loop().time() + 8
            while not task_run.done():
                pending_id = bridge._pending_confirm_id
                if pending_id:
                    pending_event = next(
                        event for event in events
                        if event.get("type") == "confirm.request" and event.get("request_id") == pending_id
                    )
                    choice = "n" if ("PROJECT_WORKER_UNLINKED_EXECUTION_CONFIRM" in pending_event["preview"]
                                      or "CURRENT_DIALOGUE_WORK_RECORD_BATCH_CONFIRM" in pending_event["preview"]) else "y"
                    assert bridge.deliver_confirm(pending_id, choice)
                if asyncio.get_running_loop().time() >= deadline:
                    bridge.request_cancel()
                    raise AssertionError("record source confirmation/clarification timed out")
                await asyncio.sleep(0.005)
            await task_run

        asyncio.run(run_and_confirm_source())
        tool_messages = [json.loads(message["content"]) for message in session.messages
                         if message.get("role") == "tool"]
        result = next(message for message in tool_messages if message.get("tool") == "run_project_workers")
        assert result["ok"] is False
        assert expected_code in result["error"]["message"]
        assert "no record was created" in result["error"]["message"]
        assert "no worker was dispatched" in result["error"]["message"]
        assert reservation_calls == []
        assert host._reservations == {}
        assert ConversationWorkRecordBindings(session).list_bindings() == []
        confirms = [event["preview"] for event in events if event.get("type") == "confirm.request"]
        assert len(confirms) == 3
        assert "WORK_RECORD_SOURCE_READ_CONFIRM" in confirms[0]
        assert "project-alpha" in confirms[0] and project_session.conversation_id in confirms[0]
        assert "CURRENT_DIALOGUE_WORK_RECORD_BATCH_CONFIRM" in confirms[1]
        assert "PROJECT_WORKER_UNLINKED_EXECUTION_CONFIRM" in confirms[2]
        assert "no task-to-requirement binding or lifecycle tracking" in confirms[2]
        assert all("PROJECT_WORKER_BATCH_BIND_CONFIRM" not in text for text in confirms)
        assistant_text = "\n".join(
            message["content"] for message in session.messages
            if message.get("role") == "assistant" and type(message.get("content")) is str
        )
        assert "requirement" in assistant_text or "准确文本" in assistant_text

@pytest.mark.parametrize("source_read_choice", ["n", "y"])
def test_user_can_explicitly_run_unlinked_when_no_existing_record_is_available(monkeypatch, source_read_choice):
    with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
        create_project(paths, "project-alpha", project_template="light")
        project_session = create_new(paths, conversation_id="20261104-00000001")
        project_session.meta.project_id = "project-alpha"
        project_session.meta.project_root = "workspace/project-alpha"
        project_session.save()
        record_project_session(paths, "project-alpha", project_session.conversation_id)
        session = create_new(paths, conversation_id="20261104-00000002")
        session.meta.turn_mode = "agent"
        session.meta.harness = "desktop"
        session._ordinary_work_intent = "execute"
        events: list[dict[str, Any]] = []
        bridge = WsBridge(emit=events.append, paths=paths, confirm_timeout=5)
        repl = _build_repl(session, paths, bridge, work_record_controller_factory=lambda *_args: None)
        repl.agent.llm = _ParentModel([{
            "project_id": "project-alpha", "task": "Update the existing project file.",
            "read_targets": ["README.md"], "write_targets": ["PROJECT.md"],
        }])
        calls = 0

        def provider(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            if calls == 1:
                message = {"role": "assistant", "content": None, "tool_calls": [{
                    "id": "unlinked-write", "type": "function", "function": {
                        "name": "write_project_file",
                        "arguments": json.dumps({"path": "PROJECT.md", "content": "Unlinked work completed."}),
                    },
                }]}
                finish = "tool_calls"
            else:
                message = {"role": "assistant", "content": "Worker turn returned; no record linkage or acceptance.",
                           "tool_calls": []}
                finish = "stop"
            return httpx.Response(200, request=request, json={
                "model": "gpt-6-luna", "choices": [{"finish_reason": finish, "message": message}],
            })

        monkeypatch.setenv("OX567_API_KEY", "host-test-secret-never-display")
        monkeypatch.setenv("OX567_BASE_URL", "https://api-cdn.0x567.com")
        monkeypatch.setenv("OX567_MODEL_FLASH", "gpt-6-luna")
        monkeypatch.setattr(llm_client.httpx, "Client", lambda **kwargs: _REAL_HTTPX_CLIENT(
            transport=httpx.MockTransport(provider), **kwargs))
        queued = bridge.lifecycle.enqueue("ordinary")
        bridge.lifecycle_conversation_id = session.conversation_id
        bridge._turn_busy.set()

        async def run_and_choose_unlinked():
            turn = asyncio.create_task(_run_line(
                repl, bridge, "Update project-alpha without linking a work record.", paths,
                lock_held=True, prestarted_run_id=queued.run_id,
            ))
            deadline = asyncio.get_running_loop().time() + 12
            while not turn.done():
                pending_id = bridge._pending_confirm_id
                if pending_id:
                    prompt = next(event["preview"] for event in events
                                  if event.get("type") == "confirm.request"
                                  and event.get("request_id") == pending_id)
                    choice = (source_read_choice if "WORK_RECORD_SOURCE_READ_CONFIRM" in prompt else
                              "n" if "CURRENT_DIALOGUE_WORK_RECORD_BATCH_CONFIRM" in prompt else "y")
                    assert bridge.deliver_confirm(pending_id, choice)
                if asyncio.get_running_loop().time() >= deadline:
                    bridge.request_cancel()
                    raise AssertionError("unlinked worker dispatch timed out")
                await asyncio.sleep(0.005)
            await turn

        asyncio.run(run_and_choose_unlinked())
        confirms = [event["preview"] for event in events if event.get("type") == "confirm.request"]
        assert len(confirms) == 7
        assert sum("WORK_RECORD_SOURCE_READ_CONFIRM" in text for text in confirms) == 1
        assert sum("CURRENT_DIALOGUE_WORK_RECORD_BATCH_CONFIRM" in text for text in confirms) == 1
        assert sum("PROJECT_WORKER_UNLINKED_EXECUTION_CONFIRM" in text for text in confirms) == 1
        assert sum(text.startswith("项目 worker 范围确认：") for text in confirms) == 1
        assert sum(text.startswith("项目 worker 即将向 0x567 gpt-6-luna 外发") for text in confirms) == 2
        assert sum(text.startswith("项目 worker 文件写入确认：") for text in confirms) == 1
        assert calls == 2
        assert (paths.workspace / "project-alpha" / "PROJECT.md").read_text(encoding="utf-8") == "Unlinked work completed."
        assert WorkRecordStore(project_session).list_items() == []
        assert ConversationWorkRecordBindings(session).list_bindings() == []
        tool_messages = [json.loads(message["content"]) for message in session.messages
                         if message.get("role") == "tool"]
        result = next(message for message in tool_messages if message.get("tool") == "run_project_workers")
        assert result["ok"] is True
        assert result["data"]["acceptance_status"] == "not_run"
        assert result["data"]["work_record_binding_status"] == "not_linked"
        assert len(result["data"]["workers"]) == 1
        row = result["data"]["workers"][0]
        assert row["record_binding_status"] == "not_linked"
        assert row["record_lifecycle_status"] == "not_applicable"
        assert row["worker_run_id"] != row["parent_run_id"]
        assert repl.agent.project_worker_host._reservations == {}



def test_two_batches_same_run_emit_increasing_sequence_and_reject_stale_callback(monkeypatch):
    source_read_choice = "n"
    with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
        create_project(paths, "project-alpha", project_template="light")
        project_session = create_new(paths, conversation_id="20261104-00000001")
        project_session.meta.project_id = "project-alpha"
        project_session.meta.project_root = "workspace/project-alpha"
        project_session.save()
        record_project_session(paths, "project-alpha", project_session.conversation_id)
        session = create_new(paths, conversation_id="20261104-00000002")
        session.meta.turn_mode = "agent"
        session.meta.harness = "desktop"
        session._ordinary_work_intent = "execute"
        events: list[dict[str, Any]] = []
        bridge = WsBridge(emit=events.append, paths=paths, confirm_timeout=5)
        repl = _build_repl(session, paths, bridge, work_record_controller_factory=lambda *_args: None)
        workers = [{"project_id": "project-alpha", "task": "Update the existing project file.",
                    "read_targets": ["README.md"], "write_targets": ["PROJECT.md"]}]
        class TwoBatches(_ParentModel):
            def chat(self, messages, **kwargs):
                if self.calls == 1:
                    self.calls = 0
                    result = super().chat(messages, **kwargs)
                    self.calls = 2
                    return result
                return super().chat(messages, **kwargs)
        repl.agent.llm = TwoBatches(workers)
        host = repl.agent.project_worker_host
        old_run_many = host.run_many
        batch_callbacks = []
        def inspect_batches(parent, requests, **kwargs):
            if batch_callbacks:
                callback, prev_requests, prev_reservations = batch_callbacks[0]
                assert len(prev_reservations) == len(prev_requests) == 1
                before = len(events)
                from dataclasses import replace
                callback("started", prev_requests[0], replace(prev_reservations[0], reservation_id="late-old-reservation"))
                assert len(events) == before
            reservations = tuple(kwargs.get("reservations") or ())
            assert len(reservations) == len(requests), "Agent must pass the batch's host-issued identities to run_many"
            result = old_run_many(parent, requests, **kwargs)
            batch_callbacks.append((kwargs["on_worker_progress"], requests, reservations))
            return result
        host.run_many = inspect_batches
        calls = 0

        def provider(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            if calls % 2 == 1:
                message = {"role": "assistant", "content": None, "tool_calls": [{
                    "id": "unlinked-write", "type": "function", "function": {
                        "name": "write_project_file",
                        "arguments": json.dumps({"path": "PROJECT.md", "content": "Unlinked work completed."}),
                    },
                }]}
                finish = "tool_calls"
            else:
                message = {"role": "assistant", "content": "Worker turn returned; no record linkage or acceptance.",
                           "tool_calls": []}
                finish = "stop"
            return httpx.Response(200, request=request, json={
                "model": "gpt-6-luna", "choices": [{"finish_reason": finish, "message": message}],
            })

        monkeypatch.setenv("OX567_API_KEY", "host-test-secret-never-display")
        monkeypatch.setenv("OX567_BASE_URL", "https://api-cdn.0x567.com")
        monkeypatch.setenv("OX567_MODEL_FLASH", "gpt-6-luna")
        monkeypatch.setattr(llm_client.httpx, "Client", lambda **kwargs: _REAL_HTTPX_CLIENT(
            transport=httpx.MockTransport(provider), **kwargs))
        queued = bridge.lifecycle.enqueue("ordinary")
        bridge.lifecycle_conversation_id = session.conversation_id
        bridge._turn_busy.set()

        async def run_and_choose_unlinked():
            turn = asyncio.create_task(_run_line(
                repl, bridge, "Update project-alpha without linking a work record.", paths,
                lock_held=True, prestarted_run_id=queued.run_id,
            ))
            deadline = asyncio.get_running_loop().time() + 12
            while not turn.done():
                pending_id = bridge._pending_confirm_id
                if pending_id:
                    prompt = next(event["preview"] for event in events
                                  if event.get("type") == "confirm.request"
                                  and event.get("request_id") == pending_id)
                    choice = (source_read_choice if "WORK_RECORD_SOURCE_READ_CONFIRM" in prompt else
                              "n" if "CURRENT_DIALOGUE_WORK_RECORD_BATCH_CONFIRM" in prompt else "y")
                    assert bridge.deliver_confirm(pending_id, choice)
                if asyncio.get_running_loop().time() >= deadline:
                    bridge.request_cancel()
                    raise AssertionError("unlinked worker dispatch timed out")
                await asyncio.sleep(0.005)
            await turn

        asyncio.run(run_and_choose_unlinked())
        progress = [event for event in events if event.get("activity_source") == "project_workers"]
        assert [(e["batch_seq"], e["phase"], e["started"], e["returned"]) for e in progress] == [
            (1, "started", 1, 0), (1, "returned", 1, 1),
            (2, "started", 1, 0), (2, "returned", 1, 1),
        ], f"parent_calls={repl.agent.llm.calls}, batches={len(batch_callbacks)}, tools_seen={[[tool['function']['name'] for tool in (row or [])] for row in repl.agent.llm.tools_seen]}, tool_ends={[(event.get('tool'), event.get('ok'), event.get('summary')) for event in events if event.get('type') == 'tool.end']}"
        assert all(e["run_id"] == queued.run_id and e["session_id"] == session.conversation_id for e in progress)
        assert calls == 4
        assert len(batch_callbacks) == 2
        assert WorkRecordStore(project_session).list_items() == []
        assert ConversationWorkRecordBindings(session).list_bindings() == []


@pytest.mark.parametrize("mode", ["cancel", "missing", "mismatch", "submit_failure", "outer_submit_failure"])
def test_agent_real_host_two_started_one_queued_cancel_settles_only_started_requirements(monkeypatch, mode):
    with temporary_agent_paths(copy_tool_dirs=("common/write_text", "coding/patch_file")) as paths:
        project_ids = ("project-alpha", "project-beta", "project-gamma")
        source_sessions = {}
        for index, project_id in enumerate(project_ids):
            create_project(paths, project_id, project_template="light")
            source = create_new(paths, conversation_id=f"20261106-{index:08x}")
            source.meta.project_id = project_id
            source.meta.project_root = f"workspace/{project_id}"
            source.save(); record_project_session(paths, project_id, source.conversation_id)
            source_sessions[project_id] = source
        session = create_new(paths, conversation_id="20261106-00000020")
        session.meta.turn_mode = "agent"; session.meta.harness = "desktop"
        session.meta.active_shell = "project"; session.meta.project_id = "project-alpha"
        session.meta.project_root = "workspace/project-alpha"; session._ordinary_work_intent = "execute"
        original_request = "执行 alpha、beta、gamma 三项任务，并在当前对话跟踪。"
        workers = [
            {"project_id": project_id, "task": f"Synthetic task for {project_id}.",
             "read_targets": ["README.md"], "write_targets": ["PROJECT.md"]}
            for project_id in project_ids
        ]
        for project_id in project_ids:
            (paths.workspace / project_id / "README.md").write_text(f"synthetic {project_id}\n", encoding="utf-8")
        events: list[dict[str, Any]] = []
        bridge = WsBridge(emit=events.append, paths=paths, confirm_timeout=5)
        repl = _build_repl(session, paths, bridge, work_record_controller_factory=lambda *_args: None)
        parent = _ParentModel(workers); repl.agent.llm = parent; parent.set_cancel_event(repl.agent.cancel_event)
        parent_run_turn = repl.agent.run_turn
        repl.agent.run_turn = lambda text, **_kwargs: parent_run_turn(text, spawn_explore=False)
        monkeypatch.setattr("project_api.after_turn_project_hooks", lambda *_args: None)
        monkeypatch.setattr("server.session_memory_event", lambda *_args: {"type": "session.memory"})
        monkeypatch.setattr("server._proposals_payload", lambda *_args: {"type": "evolve.proposals", "items": []})
        monkeypatch.setattr("server._memories_payload", lambda *_args: {"type": "evolve.memories", "items": []})
        monkeypatch.setenv("OX567_API_KEY", "host-test-secret-never-display")
        monkeypatch.setenv("OX567_BASE_URL", "https://api-cdn.0x567.com")
        monkeypatch.setenv("OX567_MODEL_FLASH", "gpt-6-luna")

        lock = threading.Lock(); both_model_requests = threading.Event()
        release_model_by_project = {project_id: threading.Event() for project_id in project_ids}
        active_by_project: dict[str, int] = {}; max_by_project: dict[str, int] = {}
        posts: list[tuple[str, str]] = []
        submit_failures: list[str] = []; outer_submit_failures: list[str] = []
        gamma_notice_seen = threading.Event(); notice_while_beta_active = []; notice_kinds = []
        task_by_project = {row["project_id"]: row["task"] for row in workers}
        def provider(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content.decode("utf-8")); system = body["messages"][0]["content"]
            project_id = next(pid for pid in project_ids if f"project_id is '{pid}'" in system)
            with lock:
                active_by_project[project_id] = active_by_project.get(project_id, 0) + 1
                max_by_project[project_id] = max(max_by_project.get(project_id, 0), active_by_project[project_id])
                posts.append((project_id, task_by_project[project_id]))
                if len(posts) >= 2: both_model_requests.set()
            try:
                assert release_model_by_project[project_id].wait(8), "test did not release the controlled model request"
                return httpx.Response(200, request=request, json={"model":"gpt-6-luna","choices":[
                    {"finish_reason":"stop","message":{"role":"assistant","content":"synthetic cancelled turn","tool_calls":[]}}]})
            finally:
                with lock: active_by_project[project_id] -= 1
        monkeypatch.setattr(llm_client.httpx, "Client", lambda **kwargs: _REAL_HTTPX_CLIENT(transport=httpx.MockTransport(provider), **kwargs))
        host = repl.agent.project_worker_host
        if mode in {"missing", "mismatch"}:
            from dataclasses import replace
            original_run_many = host.run_many
            def corrupt_one_outcome(session_arg, request_args, **kwargs):
                assert len(request_args) == 1  # Match the real singleton host boundary.
                project_id = request_args[0].project_id
                outcomes = list(original_run_many(session_arg, request_args, **kwargs))
                if mode == "missing" and project_id in {"project-alpha", "project-gamma"}:
                    return ()  # Preserve the original missing-outcome injection points.
                if mode == "mismatch" and project_id == "project-alpha":
                    assert len(outcomes) == 1
                    outcomes[0] = replace(outcomes[0], worker_id="wrong-worker")
                return tuple(outcomes)
            host.run_many = corrupt_one_outcome
        if mode == "submit_failure":
            import project_worker_host as host_module
            from concurrent.futures import ThreadPoolExecutor as RealExecutor
            class FailGammaSubmit:
                def __init__(self, *args, **kwargs): self.executor = RealExecutor(*args, **kwargs)
                def __enter__(self): self.executor.__enter__(); return self
                def __exit__(self, *args): return self.executor.__exit__(*args)
                def submit(self, fn, *args, **kwargs):
                    request = args[1] if len(args) > 1 else None
                    if getattr(request, "project_id", None) == "project-gamma":
                        submit_failures.append(request.project_id)
                        raise RuntimeError("synthetic submit failure for gamma")
                    return self.executor.submit(fn, *args, **kwargs)
            monkeypatch.setattr(host_module, "ThreadPoolExecutor", FailGammaSubmit)
        if mode == "outer_submit_failure":
            import agent as agent_module
            from concurrent.futures import ThreadPoolExecutor as RealExecutor
            class FailGammaOuterSubmit:
                def __init__(self, *args, **kwargs): self.executor = RealExecutor(*args, **kwargs)
                def __enter__(self): self.executor.__enter__(); return self
                def __exit__(self, *args): return self.executor.__exit__(*args)
                def submit(self, fn, *args, **kwargs):
                    request_batch = args[1] if len(args) > 1 else None
                    if (type(request_batch) is list and len(request_batch) == 1
                            and request_batch[0].project_id == "project-gamma"):
                        outer_submit_failures.append(request_batch[0].project_id)
                        outer_submit_failed.set()
                        raise RuntimeError("synthetic outer dispatch submit failure")
                    return self.executor.submit(fn, *args, **kwargs)
            monkeypatch.setattr(agent_module, "ThreadPoolExecutor", FailGammaOuterSubmit)
        reservations = []; reserve_many = host.reserve_many
        def capture_reservations(*args, **kwargs):
            reserved = reserve_many(*args, **kwargs); reservations.extend(reserved); return reserved
        host.reserve_many = capture_reservations
        captured_progress_by_project = {}; capture_progress_lock = threading.Lock()
        actual_run_many = host.run_many
        def capture_progress(session_arg, request_args, **kwargs):
            assert len(request_args) == 1
            with capture_progress_lock:
                captured_progress_by_project[request_args[0].project_id] = (
                    kwargs["on_worker_progress"], request_args,
                )
            return actual_run_many(session_arg, request_args, **kwargs)
        host.run_many = capture_progress
        confirm_log = []
        bridge.confirm_fn = lambda preview, _all=False: (
            confirm_log.append(preview) or ("n" if "WORK_RECORD_SOURCE_READ_CONFIRM" in preview else "y")
        )
        outer_submit_failed = threading.Event()
        original_emit_turn_event = bridge.emit_turn_event
        def observe_turn_notice(event):
            original_emit_turn_event(event)
            expected_by_kind = {
                "host": (
                    "\u9879\u76ee\u300cproject-gamma\u300d\uff1a"
                    "\u5f53\u524d worker \u4efb\u52a1\u672a\u80fd\u63d0\u4ea4\uff0c"
                    "\u6682\u65f6\u65e0\u6cd5\u5f00\u59cb\u5904\u7406\u3002"
                ),
                "agent": (
                    "\u9879\u76ee\u300cproject-gamma\u300d\uff1a"
                    "\u5f53\u524d worker \u8c03\u5ea6\u672a\u80fd\u63d0\u4ea4\uff0c"
                    "\u6682\u65f6\u65e0\u6cd5\u5f00\u59cb\u5904\u7406\u3002"
                ),
            }
            kind = next((name for name, text in expected_by_kind.items() if event.get("text") == text), None)
            if event.get("type") == "turn.notice" and kind is not None:
                notice_kinds.append(kind)
                with lock:
                    notice_while_beta_active.append(active_by_project.get("project-beta", 0) == 1)
                gamma_notice_seen.set()
        repl.agent.on_turn_event = observe_turn_notice
        queued = bridge.lifecycle.enqueue("ordinary"); bridge.lifecycle_conversation_id = session.conversation_id; bridge._turn_busy.set()

        async def run_then_cancel_queued():
            run_task = asyncio.create_task(_run_line(repl, bridge, original_request, paths,
                lock_held=True, prestarted_run_id=queued.run_id))
            deadline = asyncio.get_running_loop().time() + 14
            while not both_model_requests.is_set() and not run_task.done():
                pending_id = bridge._pending_confirm_id
                if pending_id:
                    assert bridge.deliver_confirm(pending_id, "y")
                if asyncio.get_running_loop().time() >= deadline:
                    bridge.request_cancel()
                    for release in release_model_by_project.values():
                        release.set()
                    raise AssertionError("two worker slots did not reach the controlled mock provider")
                await asyncio.sleep(0.005)
            assert both_model_requests.is_set(), "batch ended before two workers occupied the host slots"
            from dataclasses import replace
            progress, progress_requests = captured_progress_by_project["project-alpha"]
            alpha_reservation = next(row for row in reservations if row.project_id == "project-alpha")
            prior_events = len([e for e in events if e.get("activity_source") == "project_workers"])
            progress("returned", progress_requests[0], replace(alpha_reservation, parent_run_id="other-run"))
            progress("returned", progress_requests[0], replace(alpha_reservation, conversation_id="other-session"))
            assert len([e for e in events if e.get("activity_source") == "project_workers"]) == prior_events
            current = WorkRecordStore(session).list_items()[0]
            current_by_text = {row["text"]: row for row in current["requirements"]}
            started_projects = {pid for pid, task in posts if current_by_text[task].get("current_run_id")}
            assert started_projects == {"project-alpha", "project-beta"}
            assert current_by_text[task_by_project["project-gamma"]].get("current_run_id") is None
            start_events = [event for event in current["history"] if event.get("kind") == "requirement_run_started"]
            assert len(start_events) == 2
            failure_modes = {"submit_failure", "outer_submit_failure"}
            if mode not in failure_modes:
                bridge.request_cancel()
                for release in release_model_by_project.values():
                    release.set()
            elif mode == "submit_failure":
                # Free one slot only. Gamma must fail host submit and notify before beta returns.
                release_model_by_project["project-alpha"].set()
                notice_deadline = asyncio.get_running_loop().time() + 3
                while not gamma_notice_seen.is_set():
                    pending_id = bridge._pending_confirm_id
                    if pending_id:
                        bridge.deliver_confirm(pending_id, "y")
                    if asyncio.get_running_loop().time() >= notice_deadline:
                        bridge.request_cancel()
                        raise AssertionError(f"host submit failure was not immediately reported: failures={submit_failures}, keys={list(captured_progress_by_project)}")
                    await asyncio.sleep(0.005)
                assert notice_while_beta_active == [True]
                assert not release_model_by_project["project-beta"].is_set()
                release_model_by_project["project-beta"].set()
            else:
                # Free alpha so the outer scheduler attempts gamma while beta stays live.
                release_model_by_project["project-alpha"].set()
                # Outer scheduling failure is not a host outcome; preserve the live sibling.
                outer_deadline = asyncio.get_running_loop().time() + 3
                while not outer_submit_failed.is_set():
                    if asyncio.get_running_loop().time() >= outer_deadline:
                        bridge.request_cancel()
                        raise AssertionError("outer dispatch submit failure was not injected")
                    await asyncio.sleep(0.005)
                assert not release_model_by_project["project-beta"].is_set()
                notice_deadline = asyncio.get_running_loop().time() + 3
                while not gamma_notice_seen.is_set():
                    if asyncio.get_running_loop().time() >= notice_deadline:
                        bridge.request_cancel()
                        raise AssertionError("outer dispatcher submit failure was not reported")
                    await asyncio.sleep(0.005)
                assert notice_kinds == ["agent"] and notice_while_beta_active == [True]
                release_model_by_project["project-beta"].set()
            if mode in failure_modes:
                while not run_task.done():
                    pending_id = bridge._pending_confirm_id
                    if pending_id:
                        bridge.deliver_confirm(pending_id, "y")
                    if asyncio.get_running_loop().time() >= deadline:
                        bridge.request_cancel()
                        raise AssertionError("worker dispatch failure scenario did not return")
                    await asyncio.sleep(0.005)
            await run_task

        try:
            asyncio.run(run_then_cancel_queued())
        finally:
            for release in release_model_by_project.values():
                release.set()
        assert len(reservations) == 3
        alpha_reservation = next(row for row in reservations if row.project_id == "project-alpha")
        before_late = len(events)
        retained_progress, retained_requests = captured_progress_by_project["project-alpha"]
        retained_progress("returned", retained_requests[0], alpha_reservation)
        assert len(events) == before_late  # after turn teardown, even if the callback is retained
        activities = [event for event in events if event.get("type") == "activity.update"
                      and event.get("activity_source") == "project_workers"]
        assert [event["phase"] for event in activities[:2]] == ["started", "started"]
        assert {event["project_id"] for event in activities[:2]} == {"project-alpha", "project-beta"}
        assert [event["started"] for event in activities[:2]] == [1, 2]
        assert all(event["run_id"] == queued.run_id and event["session_id"] == session.conversation_id
                   and event["batch_seq"] == 1 and event["total"] == 3 for event in activities)
        assert all(set(event) == {"type", "run_id", "session_id", "activity_source", "batch_seq", "phase", "project_id", "started", "returned", "total", "text"} for event in activities)
        assert all("完成" not in event["text"] and "验证" not in event["text"] and "README" not in event["text"] for event in activities)
        if mode in {"submit_failure", "outer_submit_failure"}:
            assert [event["phase"] for event in activities] == ["started", "started", "returned", "returned"]
            assert [event["returned"] for event in activities[-2:]] == [1, 2]
        if mode == "submit_failure":
            assert submit_failures == ["project-gamma"]
            assert gamma_notice_seen.is_set() and notice_while_beta_active == [True]
            notices = [event for event in events if event.get("type") == "turn.notice"
                       and event.get("text", "").startswith("\u9879\u76ee\u300cproject-gamma\u300d")]
            assert len(notices) == 1
            assert notices[0]["text"] == (
                "\u9879\u76ee\u300cproject-gamma\u300d\uff1a"
                "\u5f53\u524d worker \u4efb\u52a1\u672a\u80fd\u63d0\u4ea4\uff0c"
                "\u6682\u65f6\u65e0\u6cd5\u5f00\u59cb\u5904\u7406\u3002"
            )
            assert "worker_dispatch_submit_failed" not in notices[0]["text"]
            assert "reservation_id" not in notices[0]["text"] and str(paths.agent_root) not in notices[0]["text"]
        if mode == "outer_submit_failure":
            assert outer_submit_failures == ["project-gamma"]
            assert gamma_notice_seen.is_set() and notice_kinds == ["agent"]
            notices = [event for event in events if event.get("type") == "turn.notice"
                       and event.get("text", "").startswith("\u9879\u76ee\u300cproject-gamma\u300d")]
            assert len(notices) == 1
            assert notices[0]["text"] == (
                "\u9879\u76ee\u300cproject-gamma\u300d\uff1a"
                "\u5f53\u524d worker \u8c03\u5ea6\u672a\u80fd\u63d0\u4ea4\uff0c"
                "\u6682\u65f6\u65e0\u6cd5\u5f00\u59cb\u5904\u7406\u3002"
            )
            assert "agent_dispatch_pool_submit_failed" not in notices[0]["text"]
            assert "reservation_id" not in notices[0]["text"] and str(paths.agent_root) not in notices[0]["text"]
            assert [event["returned"] for event in activities[-2:]] == [1, 2]
        if mode not in {"submit_failure", "outer_submit_failure"}:
            assert len(activities) == 2  # cancellation suppresses late returns
        reservation_by_project = {row.project_id: row for row in reservations}
        current = WorkRecordStore(session).list_items()[0]
        by_text = {row["text"]: row for row in current["requirements"]}
        alpha = by_text[task_by_project["project-alpha"]]
        beta = by_text[task_by_project["project-beta"]]
        gamma = by_text[task_by_project["project-gamma"]]
        assert alpha["current_run_id"] == reservation_by_project["project-alpha"].worker_run_id
        assert beta["current_run_id"] == reservation_by_project["project-beta"].worker_run_id
        assert gamma.get("current_run_id") is None and gamma["status"] != "in_progress"
        start_event_requirements = {event["payload"]["requirement_id"] for event in current["history"] if event.get("kind") == "requirement_run_started"}
        assert start_event_requirements == {alpha["requirement_id"], beta["requirement_id"]}
        assert len(current["runs"]) == (3 if mode in {"submit_failure", "outer_submit_failure"} else 2)
        lifecycle_by_run = {row["run_id"]: json.loads(row["result_summary"]) for row in current["runs"]}
        run_by_requirement = {row["requirement_id"]: row for row in current["runs"]}
        alpha_lifecycle = lifecycle_by_run[alpha["current_run_id"]]
        if mode in {"missing", "mismatch"}:
            assert alpha_lifecycle["host_status"] == "unknown"
            assert alpha_lifecycle["worker_result_claimed"] is False
            assert alpha_lifecycle["outcome_integrity"] == ("worker_outcome_missing" if mode == "missing" else "worker_outcome_identity_mismatch")
        else:
            assert alpha_lifecycle["host_status"] == ("model_turn_finished" if mode in {"submit_failure", "outer_submit_failure"} else "cancelled")
        assert lifecycle_by_run[beta["current_run_id"]]["host_status"] == ("model_turn_finished" if mode in {"submit_failure", "outer_submit_failure"} else "cancelled")
        if mode in {"submit_failure", "outer_submit_failure"}:
            gamma_block = run_by_requirement[gamma["requirement_id"]]
            gamma_summary = json.loads(gamma_block["result_summary"])
            assert gamma_block["status"] == "blocked" and gamma_block["verification"]["status"] == "not_verified"
            assert gamma_summary["host_status"] == "blocked"
            assert gamma_summary["worker_execution_started"] is False
            assert gamma_summary["worker_result_claimed"] is False and gamma_summary["acceptance_status"] == "not_run"
            assert "current_run_id" not in gamma
            assert gamma_block["run_id"] == reservation_by_project["project-gamma"].worker_run_id
        assert all(row["verification"]["status"] == "not_verified" for row in current["runs"])
        assert max(max_by_project.values()) == 1
        assert len(posts) == 2 and {pid for pid, _task in posts} == {"project-alpha", "project-beta"}
        assert all(source.messages == [] for source in source_sessions.values())
        tool_rows = [json.loads(row["content"]) for row in session.messages if row.get("role") == "tool"]
        result = next(row for row in tool_rows if row.get("tool") == "run_project_workers")
        worker_rows = {row["project_id"]: row for row in result["data"]["workers"]}
        assert worker_rows["project-beta"]["record_lifecycle_status"] == "unknown_recorded"
        if mode in {"cancel", "mismatch"}:
            assert worker_rows["project-gamma"]["record_lifecycle_status"] == "not_started"
        if mode == "submit_failure":
            assert worker_rows["project-gamma"]["record_lifecycle_status"] == "blocked_recorded"
        if mode in {"missing", "mismatch"}:
            assert "project-alpha" not in worker_rows
            unknown = {row["project_id"]: row for row in result["data"]["unverified_worker_tasks"]}
            assert set(unknown) == ({"project-alpha", "project-gamma"} if mode == "missing" else {"project-alpha"})
            assert unknown["project-alpha"]["record_lifecycle_status"] == "unknown_recorded"
            if mode == "missing":
                assert unknown["project-gamma"]["record_lifecycle_status"] == "unknown_not_recorded"
        else:
            assert worker_rows["project-alpha"]["record_lifecycle_status"] == "unknown_recorded"
        if mode in {"submit_failure", "outer_submit_failure"}:
            expected_error = ("worker_dispatch_submit_failed" if mode == "submit_failure"
                              else "agent_dispatch_pool_submit_failed")
            assert worker_rows["project-gamma"]["error_code"] == expected_error
            assert worker_rows["project-gamma"]["status"] == "blocked"
@pytest.mark.parametrize("mode", ["cancel", "missing", "mismatch"])
def test_linked_existing_real_binding_two_started_one_queued(monkeypatch, mode):
    with temporary_agent_paths(copy_tool_dirs=("common/write_text", "coding/patch_file")) as paths:
        project_ids = ("project-alpha", "project-beta", "project-gamma")
        source_sessions = {}
        for index, project_id in enumerate(project_ids):
            create_project(paths, project_id, project_template="light")
            source = create_new(paths, conversation_id=f"20261106-{index:08x}")
            source.meta.project_id = project_id
            source.meta.project_root = f"workspace/{project_id}"
            source.save(); record_project_session(paths, project_id, source.conversation_id)
            source_sessions[project_id] = source
            source_store = WorkRecordStore(source)
            item = source_store.create_item(title="Existing linked task", scope={"kind": "project", "project_id": project_id})
            source_store.add_requirement(item["item_id"], f"Synthetic task for {project_id}.", event_id=f"req-{project_id}")
        session = create_new(paths, conversation_id="20261106-00000020")
        session.meta.turn_mode = "agent"; session.meta.harness = "desktop"
        session.meta.active_shell = "project"; session.meta.project_id = "project-alpha"
        session.meta.project_root = "workspace/project-alpha"; session._ordinary_work_intent = "execute"
        original_request = "执行 alpha、beta、gamma 三项任务，并在当前对话跟踪。"
        workers = [
            {"project_id": project_id, "task": f"Synthetic task for {project_id}.",
             "read_targets": ["README.md"], "write_targets": ["PROJECT.md"]}
            for project_id in project_ids
        ]
        for project_id in project_ids:
            (paths.workspace / project_id / "README.md").write_text(f"synthetic {project_id}\n", encoding="utf-8")
        events: list[dict[str, Any]] = []
        bridge = WsBridge(emit=events.append, paths=paths, confirm_timeout=5)
        repl = _build_repl(session, paths, bridge, work_record_controller_factory=lambda *_args: None)
        parent = _ParentModel(workers); repl.agent.llm = parent; parent.set_cancel_event(repl.agent.cancel_event)
        parent_run_turn = repl.agent.run_turn
        repl.agent.run_turn = lambda text, **_kwargs: parent_run_turn(text, spawn_explore=False)
        monkeypatch.setattr("project_api.after_turn_project_hooks", lambda *_args: None)
        monkeypatch.setattr("server.session_memory_event", lambda *_args: {"type": "session.memory"})
        monkeypatch.setattr("server._proposals_payload", lambda *_args: {"type": "evolve.proposals", "items": []})
        monkeypatch.setattr("server._memories_payload", lambda *_args: {"type": "evolve.memories", "items": []})
        monkeypatch.setenv("OX567_API_KEY", "host-test-secret-never-display")
        monkeypatch.setenv("OX567_BASE_URL", "https://api-cdn.0x567.com")
        monkeypatch.setenv("OX567_MODEL_FLASH", "gpt-6-luna")

        lock = threading.Lock(); both_model_requests = threading.Event(); release_model = threading.Event()
        active_by_project: dict[str, int] = {}; max_by_project: dict[str, int] = {}
        posts: list[tuple[str, str]] = []
        task_by_project = {row["project_id"]: row["task"] for row in workers}
        def provider(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content.decode("utf-8")); system = body["messages"][0]["content"]
            project_id = next(pid for pid in project_ids if f"project_id is '{pid}'" in system)
            with lock:
                active_by_project[project_id] = active_by_project.get(project_id, 0) + 1
                max_by_project[project_id] = max(max_by_project.get(project_id, 0), active_by_project[project_id])
                posts.append((project_id, task_by_project[project_id]))
                if len(posts) >= 2: both_model_requests.set()
            try:
                assert release_model.wait(8), "test did not release the two controlled model requests"
                return httpx.Response(200, request=request, json={"model":"gpt-6-luna","choices":[
                    {"finish_reason":"stop","message":{"role":"assistant","content":"synthetic cancelled turn","tool_calls":[]}}]})
            finally:
                with lock: active_by_project[project_id] -= 1
        monkeypatch.setattr(llm_client.httpx, "Client", lambda **kwargs: _REAL_HTTPX_CLIENT(transport=httpx.MockTransport(provider), **kwargs))
        host = repl.agent.project_worker_host
        if mode in {"missing", "mismatch"}:
            from dataclasses import replace
            original_run_many = host.run_many
            def corrupt_one_outcome(session_arg, request_args, **kwargs):
                assert len(request_args) == 1  # Match the real singleton host boundary.
                project_id = request_args[0].project_id
                outcomes = list(original_run_many(session_arg, request_args, **kwargs))
                if mode == "missing" and project_id in {"project-alpha", "project-gamma"}:
                    return ()  # Preserve the original missing-outcome injection points.
                if mode == "mismatch" and project_id == "project-alpha":
                    assert len(outcomes) == 1
                    outcomes[0] = replace(outcomes[0], worker_id="wrong-worker")
                return tuple(outcomes)
            host.run_many = corrupt_one_outcome
        if mode == "submit_failure":
            import project_worker_host as host_module
            from concurrent.futures import ThreadPoolExecutor as RealExecutor
            class FailThirdSubmit:
                def __init__(self, **kwargs): self.executor = RealExecutor(**kwargs); self.count = 0
                def __enter__(self): self.executor.__enter__(); return self
                def __exit__(self, *args): return self.executor.__exit__(*args)
                def submit(self, *args, **kwargs):
                    self.count += 1
                    if self.count == 3: raise RuntimeError("synthetic submit failure")
                    return self.executor.submit(*args, **kwargs)
            monkeypatch.setattr(host_module, "ThreadPoolExecutor", FailThirdSubmit)
        reservations = []; reserve_many = host.reserve_many
        def capture_reservations(*args, **kwargs):
            reserved = reserve_many(*args, **kwargs); reservations.extend(reserved); return reserved
        host.reserve_many = capture_reservations
        confirm_log = []
        bridge.confirm_fn = lambda preview, _all=False: (
            confirm_log.append(preview) or "y"
        )
        queued = bridge.lifecycle.enqueue("ordinary"); bridge.lifecycle_conversation_id = session.conversation_id; bridge._turn_busy.set()

        async def run_then_cancel_queued():
            run_task = asyncio.create_task(_run_line(repl, bridge, original_request, paths,
                lock_held=True, prestarted_run_id=queued.run_id))
            deadline = asyncio.get_running_loop().time() + 14
            while not both_model_requests.is_set() and not run_task.done():
                pending_id = bridge._pending_confirm_id
                if pending_id:
                    assert bridge.deliver_confirm(pending_id, "y")
                if asyncio.get_running_loop().time() >= deadline:
                    bridge.request_cancel(); release_model.set()
                    raise AssertionError("two worker slots did not reach the controlled mock provider")
                await asyncio.sleep(0.005)
            assert both_model_requests.is_set(), "batch ended before two workers occupied the host slots"
            linked = {row["project_id"]: row for row in ConversationWorkRecordBindings(session).list_bindings()}
            assert set(linked) == set(project_ids)
            assert all(linked[pid].get("start_receipt") is not None for pid in project_ids[:2])
            assert linked["project-gamma"].get("start_receipt") is None
            assert all(row["lifecycle"] is None for row in linked.values())
            if mode != "submit_failure":
                bridge.request_cancel()
            release_model.set()
            if mode == "submit_failure":
                while not run_task.done():
                    pending_id = bridge._pending_confirm_id
                    if pending_id:
                        bridge.deliver_confirm(pending_id, "y")
                    if asyncio.get_running_loop().time() >= deadline:
                        bridge.request_cancel()
                        raise AssertionError("submit failure batch did not return")
                    await asyncio.sleep(0.005)
            await run_task

        try:
            asyncio.run(run_then_cancel_queued())
        finally:
            release_model.set()
        assert len(reservations) == 3
        linked = {row["project_id"]: row for row in ConversationWorkRecordBindings(session).list_bindings()}
        assert linked["project-gamma"].get("start_receipt") is None
        assert linked["project-gamma"]["lifecycle"] is None
        for pid in project_ids[:2]:
            assert linked[pid]["start_receipt"]["reservation_id"] == next(r.reservation_id for r in reservations if r.project_id == pid)
            lifecycle = linked[pid]["lifecycle"]
            assert lifecycle["status"] == ("unknown" if pid == "project-alpha" and mode in {"missing", "mismatch"} else "cancelled")
            assert linked[pid]["record_status"] == "unknown" and linked[pid]["verification_status"] == "not_verified"
        assert max(max_by_project.values()) == 1
        assert len(posts) == 2 and {pid for pid, _task in posts} == {"project-alpha", "project-beta"}
        assert all(source.messages == [] for source in source_sessions.values())
        tool_rows = [json.loads(row["content"]) for row in session.messages if row.get("role") == "tool"]
        result = next(row for row in tool_rows if row.get("tool") == "run_project_workers")
        worker_rows = {row["project_id"]: row for row in result["data"]["workers"]}
        assert worker_rows["project-beta"]["record_lifecycle_status"] == "unknown_recorded"
        if mode != "missing":
            assert worker_rows["project-gamma"]["record_lifecycle_status"] == "unknown_not_recorded"
        if mode in {"missing", "mismatch"}:
            assert "project-alpha" not in worker_rows
            unknown = {row["project_id"]: row for row in result["data"]["unverified_worker_tasks"]}
            assert set(unknown) == ({"project-alpha", "project-gamma"} if mode == "missing" else {"project-alpha"})
            assert unknown["project-alpha"]["record_lifecycle_status"] == "unknown_recorded"
            if mode == "missing":
                assert unknown["project-gamma"]["record_lifecycle_status"] == "unknown_not_recorded"
        else:
            assert worker_rows["project-alpha"]["record_lifecycle_status"] == "unknown_recorded"
        if mode == "submit_failure":
            assert worker_rows["project-gamma"]["error_code"] == "worker_dispatch_submit_failed"
            assert worker_rows["project-gamma"]["status"] == "blocked"

def test_creation_storage_failure_does_not_reserve_or_claim_a_tracked_batch(monkeypatch):
    with temporary_agent_paths() as paths:
        create_project(paths, "project-alpha", project_template="light")
        source = create_new(paths, conversation_id="20261106-00000001")
        source.meta.project_id = "project-alpha"
        source.meta.project_root = "workspace/project-alpha"
        source.save()
        record_project_session(paths, "project-alpha", source.conversation_id)
        session = create_new(paths, conversation_id="20261106-00000002")
        session.meta.turn_mode = "agent"
        session.meta.harness = "desktop"
        session._ordinary_work_intent = "execute"
        events: list[dict[str, Any]] = []
        bridge = WsBridge(emit=events.append, paths=paths, confirm_timeout=5)
        repl = _build_repl(session, paths, bridge, work_record_controller_factory=lambda *_args: None)
        repl.agent.llm = _ParentModel([{
            "project_id": "project-alpha", "task": "Update alpha config.",
            "read_targets": ["README.md"], "write_targets": ["PROJECT.md"],
        }])
        host = repl.agent.project_worker_host
        reserve = host.reserve_many
        reserve_calls = []

        def observe_reserve(*args, **kwargs):
            reserve_calls.append((args, kwargs))
            return reserve(*args, **kwargs)

        host.reserve_many = observe_reserve
        monkeypatch.setattr(
            WorkRecordStore, "create_item_with_requirement",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("injected storage failure")),
        )
        bridge.confirm_fn = lambda *_args: "y"
        queued = bridge.lifecycle.enqueue("ordinary")
        bridge.lifecycle_conversation_id = session.conversation_id
        bridge._turn_busy.set()

        async def run():
            turn = asyncio.create_task(_run_line(
                repl, bridge, "Track this project-alpha task in the current dialogue.", paths,
                lock_held=True, prestarted_run_id=queued.run_id,
            ))
            deadline = asyncio.get_running_loop().time() + 8
            while not turn.done():
                pending = bridge._pending_confirm_id
                if pending:
                    assert bridge.deliver_confirm(pending, "y")
                if asyncio.get_running_loop().time() >= deadline:
                    bridge.request_cancel()
                    raise AssertionError("storage failure path did not return")
                await asyncio.sleep(0.005)
            await turn

        asyncio.run(run())
        tool_messages = [json.loads(message["content"]) for message in session.messages
                         if message.get("role") == "tool"]
        result = next(message for message in tool_messages if message.get("tool") == "run_project_workers")
        assert result["ok"] is False
        assert "CURRENT_DIALOGUE_RECORD_PARTIAL" in result["error"]["message"]
        assert "no worker was dispatched" in result["error"]["message"]
        assert reserve_calls == []
        assert host._reservations == {}
        assert WorkRecordStore(session).list_items() == []
        assert WorkRecordStore(source).list_items() == []

def test_current_dialogue_record_creation_stops_between_atomic_writes_on_cancel(tmp_path):
    from agent import _CurrentDialogueWorkerRecordWriteError, _create_current_dialogue_worker_records
    from project_worker_host import ProjectWorkerHostRequest

    paths = AgentPaths(
        agent_root=tmp_path, workspace=tmp_path / "workspace",
        data=tmp_path / "data", evolve=tmp_path / "evolve",
    )
    session = create_new(paths, conversation_id="20261106-00000003")
    checks = 0

    def continue_check():
        nonlocal checks
        checks += 1
        return checks == 1

    with pytest.raises(_CurrentDialogueWorkerRecordWriteError) as error:
        _create_current_dialogue_worker_records(
            session, "Original multi-project request.",
            [ProjectWorkerHostRequest(
                "project-alpha", "task-id", "Update alpha.", ("README.md",), ("PROJECT.md",),
            )],
            parent_run_id="parent-create-run", turn_epoch=1,
            continue_check=continue_check,
        )
    assert error.value.cancelled is True
    assert error.value.item_id
    assert error.value.persisted_task_count == 0
    items = WorkRecordStore(session).list_items()
    assert len(items) == 1
    assert [row["text"] for row in items[0]["requirements"]] == ["Original multi-project request."]


def test_agent_worker_batch_schema_and_runtime_follow_host_limit(monkeypatch):
    from agent import build_llm_tools
    from project_worker_host import _MAX_WORKER_BATCH_SIZE

    with temporary_agent_paths() as paths:
        session = create_new(paths, conversation_id="20261106-00000004")
        session.meta.turn_mode = "agent"
        session._ordinary_work_intent = "execute"
        repl = _build_repl(session, paths, WsBridge(emit=lambda _event: None, paths=paths),
                           work_record_controller_factory=lambda *_args: None)
        tool = next(item for item in build_llm_tools(session, registry=repl.agent.executor.registry)
                    if item["function"]["name"] == "run_project_workers")
        assert tool["function"]["parameters"]["properties"]["workers"]["maxItems"] == _MAX_WORKER_BATCH_SIZE == 25
        assert str(_MAX_WORKER_BATCH_SIZE) in tool["function"]["description"]
        worker = {"project_id": "project-alpha", "task": "Task", "read_targets": [],
                  "write_targets": ["PROJECT.md"]}
        result = repl.agent._dispatch_project_workers_tool({"workers": [worker.copy() for _ in range(26)]})
        assert result.ok is False
        assert result.error.code.value == "permission_denied"
        assert repl.agent.project_worker_host._reservations == {}


def test_current_dialogue_batch_identity_is_idempotent_within_parent_but_new_across_runs(tmp_path):
    from agent import _create_current_dialogue_worker_records
    from project_worker_host import ProjectWorkerHostRequest

    paths = AgentPaths(agent_root=tmp_path, workspace=tmp_path / "workspace",
                       data=tmp_path / "data", evolve=tmp_path / "evolve")
    session = create_new(paths, conversation_id="20261106-00000005")
    tasks = [ProjectWorkerHostRequest(
        "project-alpha", "provisional-task", "Update alpha config.", ("README.md",), ("PROJECT.md",),
    )]

    first = _create_current_dialogue_worker_records(
        session, "Original request", tasks, parent_run_id="parent-run-1", turn_epoch=1,
    )
    retry = _create_current_dialogue_worker_records(
        session, "Original request", tasks, parent_run_id="parent-run-1", turn_epoch=1,
    )
    next_run = _create_current_dialogue_worker_records(
        session, "Original request", tasks, parent_run_id="parent-run-2", turn_epoch=3,
    )

    assert first[1] == retry[1]
    assert first[3][0]["requirement_id"] == retry[3][0]["requirement_id"]
    assert first[3][0]["task_id"] == retry[3][0]["task_id"]
    assert next_run[1] != first[1]
    assert next_run[3][0]["requirement_id"] != first[3][0]["requirement_id"]
    assert next_run[3][0]["task_id"] != first[3][0]["task_id"]
    assert len(WorkRecordStore(session).list_items()) == 2


@pytest.mark.parametrize("failure_envelope", [False, True])
def test_cancelled_worker_unknown_write_is_explained_to_user_not_retried(monkeypatch, failure_envelope):
    from tools.schema import tool_ok, tool_fail
    with temporary_agent_paths() as paths:
        session = create_new(paths, conversation_id="20261109-cancel01")
        session.meta.turn_mode = "agent"
        session.meta.harness = "desktop"
        session.messages.append({"role": "user", "content": "Synthetic worker task"})
        events = []
        bridge = WsBridge(emit=events.append, paths=paths)
        repl = _build_repl(session, paths, bridge, work_record_controller_factory=lambda *_args: None)
        worker = {"project_id": "project-alpha", "task": "Synthetic task", "read_targets": [], "write_targets": ["result.txt"]}
        parent = _ParentModel([worker]); repl.agent.llm = parent
        calls = []
        def dispatch(name, args):
            calls.append(name)
            repl.agent.cancel_event.set()
            data = {"workers": [{"project_id": "project-alpha", "status": "cancelled",
                "writes": [{"path": "result.txt", "status": "outcome_unknown", "verification": "readback_failed", "error_code": "cancelled"}]}]}
            return (tool_fail("run_project_workers", "cancelled", "cancelled", details=data)
                    if failure_envelope else tool_ok("run_project_workers", data))
        monkeypatch.setattr(repl.agent.executor, "run", dispatch)
        turn = repl.agent._run_parent_tool_loop(max_rounds=2, tools=[{"type": "function", "function": {"name": "run_project_workers"}}], model="synthetic", segment_start_index=0)
        assert turn.finish_reason == "cancelled"
        assert calls == ["run_project_workers"] and parent.calls == 1
        assert "写入结果无法确认" in turn.final_text
        assert "不要直接重试" in turn.final_text
        assert "result.txt" not in turn.final_text
        assert any(message.get("role") == "tool" and "outcome_unknown" in message.get("content", "") for message in session.messages)
