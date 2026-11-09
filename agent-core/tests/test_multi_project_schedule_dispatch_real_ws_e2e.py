"""POSIX: companion schedule.dispatch → real ProjectWorkerHost (mock HTTP transport only)."""
from __future__ import annotations

import asyncio
import json
import logging
import sys
import uuid
from pathlib import Path

import httpx
import os
import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

import server
from project_mode import create_project
from project_switch import record_project_session
from session import create_new
from tests.isolation_helpers import temporary_agent_paths
from tests.test_project_worker_host import _host, _response
from work_records import WorkRecordStore

COMPANION_ID = "20261109-aaaaaaa1"
PROJECTS = ("mp-ws-alpha", "mp-ws-beta")
TASKS = {
    "mp-ws-alpha": "Alpha WS schedule dispatch host task.",
    "mp-ws-beta": "Beta WS schedule dispatch host task.",
}
SOURCE_SESSIONS = {
    "mp-ws-alpha": "20261109-bbbbbbb2",
    "mp-ws-beta": "20261109-ccccccc3",
}


def _posix_test_publish_parent_by_handle(namespace, raw: bytes) -> None:
    """CI/POSIX shim: parent binding publication is Win32-native in production."""
    target = namespace._path
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(f".{uuid.uuid4().hex}.tmp")
    temporary.write_bytes(raw)
    temporary.replace(target)


def _worker_model(request: httpx.Request) -> httpx.Response:
    if "mp-ws-beta" in request.content.decode("utf-8"):
        return httpx.Response(503, request=request, json={"error": "isolated synthetic provider failure"})
    return _response(
        request,
        {"role": "assistant", "content": "Synthetic worker completed.", "tool_calls": []},
        "stop",
    )


def test_companion_schedule_dispatch_real_host_two_projects(monkeypatch, caplog):
    if os.name != "nt":
        import multi_project_work_record_bindings as mpb
        monkeypatch.setattr(mpb, "_publish_parent_by_handle", _posix_test_publish_parent_by_handle)
    with temporary_agent_paths(copy_tool_dirs=()) as paths:
        for project_id in PROJECTS:
            create_project(paths, project_id, project_template="light")
            source = create_new(paths, conversation_id=SOURCE_SESSIONS[project_id])
            source.meta.project_id = project_id
            source.meta.project_root = f"workspace/{project_id}"
            source.meta.harness = "desktop"
            source.meta.turn_mode = "agent"
            source.save()
            store = WorkRecordStore(source)
            item = store.create_item(
                title=f"Schedule item {project_id}",
                scope={"kind": "project", "project_id": project_id},
            )
            store.add_requirement(item["item_id"], TASKS[project_id], event_id=f"req-{project_id}")
            record_project_session(paths, project_id, source.conversation_id)

        companion = create_new(paths, conversation_id=COMPANION_ID)
        companion.meta.harness = "desktop"
        companion.meta.turn_mode = "agent"
        companion.meta.active_shell = "grow"
        companion.save()
        assert not (companion.meta.project_id or "").strip()

        monkeypatch.setattr(server, "resume_desktop_or_create", lambda _paths: companion)
        real_build = server._build_repl

        def build_repl(bound_session, bound_paths, bridge, **kwargs):
            repl = real_build(bound_session, bound_paths, bridge, **kwargs)
            host, _clients = _host(bound_paths, monkeypatch, _worker_model)
            repl.agent.project_worker_host = host
            return repl

        monkeypatch.setattr(server, "_build_repl", build_repl)
        monkeypatch.setattr(server.WsBridge, "emit_session_state", lambda *_a: None)
        monkeypatch.setattr(server, "emit_corruption_notices", lambda *_a: None)
        monkeypatch.setattr(server, "_emit_pending_work_card_references", lambda *_a: None)

        async def run_socket() -> list[dict]:
            received: list[dict] = []
            async with serve(server.WsSessionHandler(paths).handle, "127.0.0.1", 0) as ws_server:
                port = ws_server.sockets[0].getsockname()[1]
                async with connect(f"ws://127.0.0.1:{port}") as websocket:
                    deadline = asyncio.get_running_loop().time() + 45
                    overview_request = str(uuid.uuid4())
                    await websocket.send(json.dumps({
                        "type": "work-records.overview.query",
                        "session_id": COMPANION_ID,
                        "request_id": overview_request,
                        "project_ids": list(PROJECTS),
                        "page_size": 6,
                        "detail_paging": True,
                    }))
                    overview: dict | None = None
                    while asyncio.get_running_loop().time() < deadline:
                        event = json.loads(await asyncio.wait_for(websocket.recv(), timeout=10))
                        received.append(event)
                        if event.get("type") == "confirm.request":
                            await websocket.send(json.dumps({
                                "type": "confirm.response",
                                "request_id": event["request_id"],
                                "choice": "y",
                            }))
                        if (event.get("type") == "work-records.overview.result"
                                and event.get("request_id") == overview_request):
                            overview = event
                            break
                    assert overview is not None, received
                    assert overview.get("aggregation_scope") == "selected_project_sessions"
                    candidates = overview["schedule_preview"]["candidates"]
                    assert len(candidates) == 2
                    workers = []
                    for candidate in candidates:
                        workers.append({
                            "project_id": candidate["project_id"],
                            "item_id": candidate["item_id"],
                            "requirement_id": candidate["requirement_id"],
                            "priority": candidate["priority"],
                            "task": TASKS[candidate["project_id"]],
                            "read_targets": ["PROJECT.md"],
                            "write_targets": ["NOTES.md"],
                        })
                    dispatch_request = str(uuid.uuid4())
                    await websocket.send(json.dumps({
                        "type": "work-records.schedule.dispatch",
                        "session_id": COMPANION_ID,
                        "request_id": dispatch_request,
                        "project_ids": list(PROJECTS),
                        "workers": workers,
                    }))
                    dispatch_done: dict | None = None
                    while asyncio.get_running_loop().time() < deadline:
                        event = json.loads(await asyncio.wait_for(websocket.recv(), timeout=15))
                        received.append(event)
                        if event.get("type") == "confirm.request":
                            await websocket.send(json.dumps({
                                "type": "confirm.response",
                                "request_id": event["request_id"],
                                "choice": "y",
                            }))
                        if (event.get("type") == "work-records.schedule.dispatch.done"
                                and event.get("request_id") == dispatch_request):
                            dispatch_done = event
                            break
                    assert dispatch_done is not None, received
                    return received, overview, dispatch_done

            caplog.set_level(logging.INFO, logger="my_agent.sidecar")
            events, overview, dispatch_done = asyncio.run(run_socket())

            assert dispatch_done["status"] == "applied", dispatch_done.get("message")
            assert dispatch_done.get("tool_ok") is True
            worker_rows = dispatch_done.get("workers") or []
            assert len(worker_rows) == 2
            statuses = {row["project_id"]: row["status"] for row in worker_rows}
            assert statuses["mp-ws-alpha"] == "model_turn_finished"
            assert statuses["mp-ws-beta"] == "failed"
            assert not (companion.meta.project_id or "").strip()

            trace_lines = [record.getMessage() for record in caplog.records
                           if "event=project_worker_batch " in record.getMessage()]
            lifecycle = [dict(field.split("=", 1) for field in line.split() if "=" in field)
                         for line in trace_lines]
            ended = {row["project_id"]: row for row in lifecycle if row.get("phase") == "ended"}
            assert set(ended) == set(PROJECTS)
            assert ended["mp-ws-alpha"]["status"] == "model_turn_finished"
            assert ended["mp-ws-beta"]["status"] == "failed"

            chat_notices = [event for event in events if event.get("type") == "turn.notice"]
            assert not any("历史快照" in (event.get("text") or "") for event in chat_notices)
            assert overview["schedule_preview"]["candidates"]
