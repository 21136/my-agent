from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import pytest

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

from llm_client import LLMResponse
from project_mode import create_project
from project_worker_host import ProjectWorkerHostOutcome, ProjectWorkerReservation
from project_worker_work_record_settlement import ProjectWorkerWorkRecordBinding
from server import WsBridge, _build_repl, _run_line
from session import create_new
from project_switch import record_project_session
from work_record_settlement import BoundExecution
from multi_project_work_record_bindings import ConversationWorkRecordBindings
from work_records import WorkRecordStore
from tests.isolation_helpers import temporary_agent_paths


class ParentToolModel:
    def __init__(self, workers: list[dict[str, Any]]) -> None:
        self.workers = workers
        self.calls = 0
        self.cancel_event = None
        self.tool_result = None

    def set_cancel_event(self, event):
        self.cancel_event = event

    def cancel_current_request(self):
        pass

    def chat(self, messages, *, model=None, tools=None, temperature=0.0, stream=None, **_kwargs):
        self.calls += 1
        if self.calls == 1:
            args = json.dumps({"workers": self.workers})
            return LLMResponse(
                model="test", content=None,
                tool_calls=[{"id": "dispatch", "type": "function", "function": {
                    "name": "run_project_workers", "arguments": args,
                }}], finish_reason="tool_calls", usage=None, raw={},
            )
        return LLMResponse(
            model="test", content="Worker result remains unaccepted.",
            tool_calls=[], finish_reason="stop", usage=None, raw={},
        )


class RecordingFakeWorkerHost:
    def __init__(self, session_id: str, run_id: str) -> None:
        self.session_id = session_id
        self.run_id = run_id
        self.calls = 0
        self.request_batches: list[tuple[tuple[str, str, str], ...]] = []
        self.scope_previews: list[str] = []
        self.reservations: tuple[ProjectWorkerReservation, ...] = ()
        self.release_calls = []

    def _serialized_confirm(self, callback, cancel_event, *args):
        return "n" if cancel_event.is_set() else callback(*args)

    def reserve_many(self, session, requests, *, parent_run_id, turn_epoch, live_binding_check):
        assert session.conversation_id == self.session_id
        assert callable(live_binding_check)
        self.reservations = tuple(ProjectWorkerReservation(
            reservation_id=f"reservation-{index}-{request.task_id}",
            worker_id=f"worker-{index}", worker_run_id=f"worker-run-{index}",
            parent_run_id=parent_run_id, turn_epoch=turn_epoch,
            conversation_id=session.conversation_id, project_id=request.project_id,
            task_id=request.task_id,
        ) for index, request in enumerate(requests))
        return self.reservations

    def release_parent_run(self, **kwargs):
        self.release_calls.append(kwargs)
        return len(self.reservations)

    def run_many(self, session, requests, *, scope_confirm_fn, reservations=None, **_kwargs):
        self.calls += 1
        self.request_batches.append(tuple(
            (request.project_id, request.task_id, request.task) for request in requests
        ))
        if reservations is not None:
            assert reservations == self.reservations
            assert len(requests) == len(reservations)
            outcomes = []
            for request, reservation in zip(requests, reservations):
                preview = json.dumps({
                    "project_id": request.project_id, "task_id": request.task_id,
                    "task": request.task, "worker_run_id": reservation.worker_run_id,
                })
                self.scope_previews.append(preview)
                assert scope_confirm_fn(preview) == "y"
                outcomes.append(ProjectWorkerHostOutcome(
                    worker_id=reservation.worker_id, run_id=reservation.parent_run_id,
                    turn_epoch=reservation.turn_epoch, conversation_id=reservation.conversation_id,
                    project_id=reservation.project_id, task_id=reservation.task_id,
                    status="model_turn_finished", acceptance_status="not_run",
                    parent_run_id=reservation.parent_run_id, worker_run_id=reservation.worker_run_id,
                    reservation_id=reservation.reservation_id,
                ))
            return tuple(outcomes)
        assert len(requests) == 1
        preview = json.dumps({
            "project_id": requests[0].project_id,
            "task_id": requests[0].task_id,
            "task": requests[0].task,
        })
        self.scope_previews.append(preview)
        assert scope_confirm_fn(preview) == "y"
        return (ProjectWorkerHostOutcome(
            worker_id="worker-1", run_id=self.run_id, turn_epoch=1,
            conversation_id=self.session_id, project_id=requests[0].project_id,
            task_id=requests[0].task_id, status="model_turn_finished",
            acceptance_status="not_run",
        ),)


def _workers(count: int = 1):
    return [
        {"project_id": "project-alpha", "task": f"Change requirement {index + 1}.",
         "read_targets": ["README.md"], "write_targets": ["PROJECT.md"]}
        for index in range(count)
    ]


@pytest.mark.parametrize(("count", "link_choice", "mismatch"), [(1, "y", False), (1, "y", True), (1, "n", False), (2, "y", False)])
def test_parent_record_remains_independent_from_multi_project_worker_bindings(
    tmp_path, monkeypatch, count, link_choice, mismatch,
):
    with temporary_agent_paths(copy_tool_dirs=("common/write_text", "coding/patch_file")) as paths:
        create_project(paths, "project-alpha", project_template="light")
        project_session = create_new(paths, conversation_id="20261030-abc00001")
        project_session.meta.project_id = "project-alpha"
        project_session.meta.project_root = "workspace/project-alpha"
        project_session.save()
        record_project_session(paths, "project-alpha", project_session.conversation_id)
        project_store = WorkRecordStore(project_session)
        project_item = project_store.create_item(
            title="Mapped project requirements",
            scope={"kind": "project", "project_id": "project-alpha"},
        )
        project_store.add_requirement(project_item["item_id"], "Change requirement 1.", event_id="mapped-1")
        project_store.add_requirement(project_item["item_id"], "Change requirement 2.", event_id="mapped-2")
        session = create_new(paths, conversation_id=f"worker-record-link-{count}")
        session.meta.turn_mode = "agent"
        session.meta.harness = "desktop"
        session.meta.project_id = "project-alpha"
        session.meta.project_root = "workspace/project-alpha"
        session._ordinary_work_intent = "execute"
        store = WorkRecordStore(session)
        item = store.create_item(title="Confirmed task", scope={"kind": "project", "project_id": "project-alpha"})
        requirement = store.add_requirement(
            item["item_id"], "Change requirement 1.", event_id=f"source-{count}",
        )

        events: list[dict[str, Any]] = []
        confirmations: list[str] = []
        import project_worker_work_record_settlement as worker_settlement
        settlement_calls = []
        original_worker_settlement = worker_settlement.settle_project_worker_work_record

        def observe_worker_settlement(*args, **kwargs):
            settlement_calls.append((args, kwargs))
            return original_worker_settlement(*args, **kwargs)

        monkeypatch.setattr(
            worker_settlement, "settle_project_worker_work_record", observe_worker_settlement,
        )
        bridge = WsBridge(emit=events.append, paths=paths)
        def confirm(preview, _approve_all=False):
            confirmations.append(preview)
            return link_choice if ("WORK_RECORD_LINK_CONFIRM" in preview
                                   or "PROJECT_WORKER_BATCH_BIND_CONFIRM" in preview) else "y"
        bridge.confirm_fn = confirm
        work_run = bridge.lifecycle.start_or_activate("ordinary")
        bridge.lifecycle_conversation_id = session.conversation_id
        bridge._turn_busy.set()
        store.begin_requirement_run(
            item["item_id"], requirement["requirement_id"],
            run_id=work_run.run_id, expected_current_run_id=None,
        )
        execution = BoundExecution(
            session.conversation_id, item["item_id"], requirement["requirement_id"], work_run.run_id,
        )
        work_binding = (store, execution)

        repl = _build_repl(
            session, paths, bridge,
            work_record_controller_factory=lambda *_args: None,
        )
        repl.agent.llm = ParentToolModel(_workers(count))
        fake_host = RecordingFakeWorkerHost(session.conversation_id, work_run.run_id)
        if mismatch:
            from dataclasses import replace
            normal_run = fake_host.run_many
            def wrong_reservation(*args, **kwargs):
                outcomes = normal_run(*args, **kwargs)
                return (replace(outcomes[0], reservation_id="different-reservation"),)
            fake_host.run_many = wrong_reservation
        repl.agent.project_worker_host = fake_host
        original_handle_line = repl.handle_line

        def handle_line(text: str):
            result = original_handle_line(text)
            repl.last_turn_finish_reason = getattr(result, "finish_reason", None)
            return "continue"

        repl.handle_line = handle_line
        monkeypatch.setattr("server.session_memory_event", lambda *_args: {"type": "session.memory"})
        monkeypatch.setattr("server._proposals_payload", lambda *_args: {"type": "evolve.proposals", "items": []})
        monkeypatch.setattr("server._memories_payload", lambda *_args: {"type": "memories", "items": []})
        monkeypatch.setattr("project_api.after_turn_project_hooks", lambda *_args: None)

        asyncio.run(_run_line(
            repl, bridge, "Run the explicitly recorded project-alpha requirement.", paths,
            prestarted_run_id=work_run.run_id, work_record_binding=work_binding,
        ))

        tool_messages = [json.loads(message["content"]) for message in session.messages
                         if message.get("role") == "tool"]
        result = next((row for row in tool_messages if row.get("tool") == "run_project_workers"), None)
        assert result is not None
        should_run_worker = link_choice == "y"
        expected_dispatch_count = count if should_run_worker else 0
        assert fake_host.calls == expected_dispatch_count
        assert len(fake_host.request_batches) == expected_dispatch_count
        assert all(len(batch) == 1 for batch in fake_host.request_batches)
        dispatched_requests = [request for batch in fake_host.request_batches for request in batch]
        assert len(dispatched_requests) == expected_dispatch_count
        if should_run_worker:
            expected_requests = [
                (reservation.project_id, reservation.task_id, f"Change requirement {index + 1}.")
                for index, reservation in enumerate(fake_host.reservations)
            ]
            assert sorted(dispatched_requests) == sorted(expected_requests)
            assert len({(project_id, task_id) for project_id, task_id, _task in dispatched_requests}) == count
            assert len(fake_host.reservations) == count
        if should_run_worker and count == 1:
            mapping_prompts = [text for text in confirmations if "WORK_RECORD_LINK_CONFIRM" in text]
            assert len(mapping_prompts) == 1
            assert "Change requirement 1." in mapping_prompts[0]
            assert "Change requirement 1." in fake_host.scope_previews[0]
            assert result["data"]["workers"][0]["record_binding_status"] == ("mismatch" if mismatch else "confirmed")
            assert result["data"]["work_record_binding_status"] == ("mismatch" if mismatch else "confirmed")
        elif should_run_worker:
            source_prompts = [text for text in confirmations if "WORK_RECORD_SOURCE_READ_CONFIRM" in text]
            batch_prompts = [text for text in confirmations if "PROJECT_WORKER_BATCH_BIND_CONFIRM" in text]
            assert len(source_prompts) == len(batch_prompts) == 1
            assert "20261030-abc00001" in source_prompts[0]
            assert "Change requirement 1." in batch_prompts[0]
            assert "Change requirement 2." in batch_prompts[0]
            assert len(fake_host.reservations) == 2
            assert all(row["record_binding_status"] == "confirmed" for row in result["data"]["workers"])
            assert all(row["worker_run_id"] == reservation.worker_run_id
                       for row, reservation in zip(result["data"]["workers"], fake_host.reservations))
            rows = ConversationWorkRecordBindings(session).list_bindings()
            assert len(rows) == 2
            assert all(row["source_session_id"] == "20261030-abc00001" for row in rows)
            assert all(row["item_id"] != item["item_id"] for row in rows)
        elif count == 1:
            assert any("WORK_RECORD_LINK_CONFIRM" in text for text in confirmations)
            assert not fake_host.scope_previews
            assert result["ok"] is False
        else:
            if count == 2:
                assert any("WORK_RECORD_SOURCE_READ_CONFIRM" in text for text in confirmations)
                assert any("PROJECT_WORKER_BATCH_BIND_CONFIRM" in text for text in confirmations)
            else:
                assert not any("WORK_RECORD_LINK_CONFIRM" in text for text in confirmations)
            assert not fake_host.scope_previews
            assert result["ok"] is False

        assert len(settlement_calls) == (1 if should_run_worker and count == 1 and not mismatch else 0)
        if should_run_worker and count == 1 and not mismatch:
            linked_binding, linked_outcome = settlement_calls[0][0][1:3]
            assert linked_binding.execution == execution
            assert linked_binding.request.task_id == linked_outcome.task_id
            assert settlement_calls[0][1]["execution_receipt"].run_id == work_run.run_id

        stored = WorkRecordStore(session).get_item(item["item_id"])
        saved_requirement = stored["requirements"][0]
        assert saved_requirement["current_run_id"] == work_run.run_id
        assert saved_requirement["status"] == "unknown"
        assert stored["runs"][0]["verification"]["status"] == "not_verified"
        assert repl.agent._project_worker_work_record_context is None
        assert repl.agent._project_worker_work_record_required is False
        coalescer = getattr(bridge, "_work_card_update_coalescer", None)
        if coalescer is not None:
            coalescer.close()

