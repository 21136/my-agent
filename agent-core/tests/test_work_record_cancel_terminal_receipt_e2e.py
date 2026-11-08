from __future__ import annotations

import asyncio
import json
import sys
import threading
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import httpx

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

import llm_client
import server
from paths import AgentPaths
from project_mode import create_project
from project_worker_host import ProjectWorkerHost, ProjectWorkerHostRequest
from project_worker_work_record_settlement import ProjectWorkerWorkRecordBinding
from session import Session, SessionMeta
from tests.isolation_helpers import temporary_agent_paths
from work_record_settlement import BoundExecution
from work_records import WorkRecordStore

_REAL_HTTPX_CLIENT = httpx.Client


def _model_response(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, request=request, json={
        "model": "gpt-6-luna",
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": "synthetic response", "tool_calls": []}}],
    })


def test_ws_cancel_runs_real_worker_to_terminal_receipt_and_record_settlement(tmp_path, monkeypatch):
    async def scenario():
        with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
            project_id = "project-cancel-e2e"
            create_project(paths, project_id, project_template="light")
            session_id = "20261108-cancel-e2e"
            session_dir = paths.data / "sessions" / session_id
            session_dir.mkdir(parents=True)
            session = Session(session_id, session_dir, "synthetic", SessionMeta(
                project_id=project_id, project_root=f"workspace/{project_id}",
                active_shell="project", harness="desktop", turn_mode="agent",
            ), [], paths)
            session._ordinary_work_intent = "execute"
            store = WorkRecordStore(session)
            item = store.create_item(title="Synthetic cancel", scope={"kind": "project", "project_id": project_id})
            requirement = store.add_requirement(item["item_id"], "Synthetic cancellable task", event_id="cancel-e2e-requirement")

            events = []
            bridge = server.WsBridge(emit=events.append, paths=paths)
            assert bridge.begin_turn(mode="ordinary")
            lifecycle = bridge.lifecycle.snapshot()
            bridge.lifecycle_conversation_id = session_id
            store.begin_requirement_run(item["item_id"], requirement["requirement_id"],
                                        run_id=lifecycle.run_id, expected_current_run_id=None)

            entered_model = threading.Event()
            allow_model_return = threading.Event()
            posts = []

            def transport(request: httpx.Request) -> httpx.Response:
                posts.append(request)
                entered_model.set()
                assert allow_model_return.wait(5), "test did not release synthetic model transport"
                return _model_response(request)

            monkeypatch.setenv("OX567_API_KEY", "synthetic-test-key")
            monkeypatch.setenv("OX567_BASE_URL", "https://api-cdn.0x567.com")
            monkeypatch.setenv("OX567_MODEL_FLASH", "gpt-6-luna")
            monkeypatch.setattr(llm_client.httpx, "Client", lambda **kwargs: _REAL_HTTPX_CLIENT(
                transport=httpx.MockTransport(transport), **kwargs,
            ))
            from llm_client import LLMClient, LLMConfig
            config = LLMConfig(None, "https://unused.example", "0x567-flash", "0x567-pro", 5, None)
            host = ProjectWorkerHost(lambda: LLMClient(config, paths=paths))
            request = ProjectWorkerHostRequest(
                project_id, "cancel-e2e-task", "Synthetic cancellable task",
                (), ("cancel-output.txt",),
            )
            reservation = host.reserve_many(
                session, [request], parent_run_id=lifecycle.run_id, turn_epoch=1,
                live_binding_check=lambda bound, run, epoch: bound is session and run == lifecycle.run_id and epoch == 1,
            )[0]
            binding = ProjectWorkerWorkRecordBinding(
                BoundExecution(session_id, item["item_id"], requirement["requirement_id"], lifecycle.run_id),
                request, reservation,
            )
            from project_worker_host import ProjectWorkerCancelTarget
            target = ProjectWorkerCancelTarget(
                conversation_id=session_id, parent_run_id=lifecycle.run_id, turn_epoch=1,
                project_id=project_id, item_id=item["item_id"], requirement_id=requirement["requirement_id"],
                task_id=request.task_id, worker_id=reservation.worker_id,
                worker_run_id=reservation.worker_run_id, reservation_id=reservation.reservation_id,
            )
            outcomes = []
            errors = []

            def run_host():
                try:
                    outcomes.extend(host.run_many(
                        session, [request], scope_confirm_fn=lambda _preview: "y",
                        outbound_request_confirm_fn=lambda _preview: "y",
                        write_confirm_fn=lambda _preview, _all: "y",
                        cancel_event=threading.Event(), parent_run_id=lifecycle.run_id, turn_epoch=1,
                        live_binding_check=lambda bound, run, epoch: bound is session and run == lifecycle.run_id and epoch == 1,
                        reservations=(reservation,), before_worker_start=lambda *_args: True,
                        cancel_target_for_worker=lambda *_args: target,
                        cancel_target_is_current=lambda got, reserved: got == target and reserved == reservation,
                    ))
                except BaseException as exc:
                    errors.append(exc)

            worker = threading.Thread(target=run_host, daemon=True)
            worker.start()
            try:
                assert await asyncio.to_thread(entered_model.wait, 3), f"real host never entered synthetic transport: {errors!r}, outcomes={outcomes!r}"
                handler = server.WsSessionHandler(paths)
                repl = SimpleNamespace(session=session, agent=SimpleNamespace(session=session, project_worker_host=host))
                request_id = str(uuid.uuid4())
                control = {
                    "type": "work-record.control", "session_id": session_id,
                    "request_id": request_id, "item_id": item["item_id"],
                    "requirement_id": requirement["requirement_id"], "action": "cancel_execution",
                    "expected_status": "in_progress",
                    "expected_revision": store.with_locked_data(lambda data: data["revision"]),
                    "event_id": str(uuid.uuid4()), "idempotency_key": request_id,
                }
                await handler._handle_incoming(control, repl, bridge)
                done = next(event for event in reversed(events)
                            if event.get("type") == "work-record.control.done" and event.get("request_id") == request_id)
                assert done["status"] == "applied", json.dumps(done, ensure_ascii=False)
                assert store.get_item(item["item_id"])["requirements"][0]["status"] == "in_progress"
                allow_model_return.set()
                await asyncio.to_thread(worker.join, 6)
                assert not worker.is_alive(), "worker did not reach terminal state"
            finally:
                allow_model_return.set()
                if worker.is_alive():
                    await asyncio.to_thread(worker.join, 6)

            assert errors == []
            assert len(posts) == 1
            assert len(outcomes) == 1 and outcomes[0].status == "cancelled"
            assert host._terminal_user_stops.get(reservation.reservation_id) is not None
            assert host._accepted_worker_stops.get(reservation.reservation_id) == target
            from project_worker_work_record_settlement import project_worker_outcome_matches_binding
            assert project_worker_outcome_matches_binding(binding, outcomes[0])

            assert bridge.finish_execution("cancelled", ok=False, run_id=lifecycle.run_id)
            settled = server._settle_work_record_run(
                current_session=session, bridge=bridge, store=store,
                binding=BoundExecution(session_id, item["item_id"], requirement["requirement_id"], lifecycle.run_id),
                emit=events.append, agent=repl.agent, repl=repl,
                worker_work_record_result=(binding, outcomes[0]),
            )
            assert settled is True
            terminal = store.get_item(item["item_id"])
            assert terminal["requirements"][0]["status"] == "stopped", json.dumps({"terminal": terminal, "outcome": outcomes[0], "events": events}, ensure_ascii=False, default=str)
            assert len(terminal["runs"]) == 1
            assert terminal["runs"][0]["status"] == "stopped"
            assert terminal["runs"][0]["verification"]["status"] == "not_verified"

    asyncio.run(scenario())
