"""Real WebSocket + Agent + ProjectWorkerHost for multi-project schedule.dispatch GUI E2E.

Mock HTTP model transport only; run_project_workers host path and settlement are real.
Companion session stays desktop-unbound (no project_id).
"""
from __future__ import annotations

import asyncio
import json
import os
import pathlib
import sys
import uuid

core = pathlib.Path(sys.argv[1]).resolve()
sys.path[:0] = [str(core), str(core / "tests")]

import httpx
import pytest
import server
from project_mode import create_project
from project_switch import record_project_session
from session import create_new
from tests.isolation_helpers import temporary_agent_paths
from tests.test_project_worker_host import _host, _response
from websockets.asyncio.server import serve
from work_records import WorkRecordStore

COMPANION_ID = "20261109-ddddddd4"
PROJECTS = ("mp-host-alpha", "mp-host-beta")
TASKS = {
    "mp-host-alpha": "Alpha parallel schedule task for real host E2E.",
    "mp-host-beta": "Beta parallel schedule task for real host E2E.",
}
SOURCE_SESSIONS = {
    "mp-host-alpha": "20261109-eeeeeee5",
    "mp-host-beta": "20261109-fffffff6",
}


def worker_model(request: httpx.Request) -> httpx.Response:
    payload = request.content.decode("utf-8")
    if "mp-host-beta" in payload:
        return httpx.Response(503, request=request, json={"error": "isolated synthetic provider failure"})
    return _response(
        request,
        {"role": "assistant", "content": "Synthetic worker completed schedule task.", "tool_calls": []},
        "stop",
    )


def _posix_test_publish_parent_by_handle(namespace, raw: bytes) -> None:
    target = namespace._path
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(f".{uuid.uuid4().hex}.tmp")
    temporary.write_bytes(raw)
    temporary.replace(target)


async def main() -> None:
    if os.name != "nt":
        import multi_project_work_record_bindings as mpb
        mpb._publish_parent_by_handle = _posix_test_publish_parent_by_handle
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

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(server, "resume_desktop_or_create", lambda _paths: companion)
        real_build = server._build_repl

        def build_repl(bound_session, bound_paths, bridge, **kwargs):
            repl = real_build(bound_session, bound_paths, bridge, **kwargs)
            host, _clients = _host(bound_paths, monkeypatch, worker_model)
            repl.agent.project_worker_host = host
            return repl

        monkeypatch.setattr(server, "_build_repl", build_repl)
        monkeypatch.setattr(server.WsBridge, "emit_session_state", lambda *_a: None)
        monkeypatch.setattr(server, "emit_corruption_notices", lambda *_a: None)
        monkeypatch.setattr(server, "_emit_pending_work_card_references", lambda *_a: None)

        ready = {
            "port": 0,
            "companion_session_id": COMPANION_ID,
            "project_ids": list(PROJECTS),
            "source_sessions": dict(SOURCE_SESSIONS),
            "synthetic_model_transport": True,
            "real_project_worker_host": True,
        }

        async with serve(server.WsSessionHandler(paths).handle, "127.0.0.1", 0) as ws:
            ready["port"] = ws.sockets[0].getsockname()[1]
            print(json.dumps(ready), flush=True)
            await asyncio.to_thread(sys.stdin.buffer.readline)
        monkeypatch.undo()


if __name__ == "__main__":
    asyncio.run(main())
