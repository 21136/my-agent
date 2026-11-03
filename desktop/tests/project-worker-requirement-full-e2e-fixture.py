"""End-to-end local fixture: real WsBridge/Agent/ProjectWorkerHost and pytest; model HTTP transport alone is mocked."""
from __future__ import annotations
import asyncio, json, pathlib, sys

core = pathlib.Path(sys.argv[1]).resolve()
sys.path.insert(0, str(core))
sys.path.insert(0, str(core / "tests"))
from project_mode import create_project
from server import WsBridge, WsSessionHandler, _build_repl, _run_line
from session import create_new
from work_records import WorkRecordStore
from work_record_settlement import BoundExecution
from tests.isolation_helpers import temporary_agent_paths
from tests.test_project_worker_record_integration import ParentToolModel
from tests.test_project_worker_host import _host, _response
import project_api

async def main():
    from websockets.asyncio.server import serve
    with temporary_agent_paths(copy_tool_dirs=("common/write_text", "coding/patch_file")) as paths:
        create_project(paths, "project-a", project_template="light")
        project_root = paths.workspace / "project-a"
        (project_root / "test_feature.py").write_text(
            "from pathlib import Path\ndef test_target(): Path('pytest-ran.txt').write_text('real pytest ran')\n",
            encoding="utf-8")
        session = create_new(paths, conversation_id="desktop-worker-e2e")
        session.meta.turn_mode = "agent"; session.meta.harness = "desktop"
        session.meta.project_id = "project-a"; session.meta.project_root = "workspace/project-a"
        session._ordinary_work_intent = "execute"
        store = WorkRecordStore(session)
        item = store.create_item(title="E2E requirement", scope={"kind": "project", "project_id": "project-a"})
        requirement = store.add_requirement(item["item_id"], "Feature works", event_id="e2e-requirement")

        # Only outbound model HTTP is mocked. The host, bridge, agent turn and pytest are real.
        import pytest
        monkeypatch = pytest.MonkeyPatch()
        host, model_clients = _host(paths, monkeypatch, lambda request: _response(
            request, {"role": "assistant", "content": "worker finished"}, "stop"))
        monkeypatch.setattr("server.session_memory_event", lambda *_args: {"type": "session.memory"})
        monkeypatch.setattr("server._proposals_payload", lambda *_args: {"type": "evolve.proposals", "items": []})
        monkeypatch.setattr("server._memories_payload", lambda *_args: {"type": "memories", "items": []})
        monkeypatch.setattr(project_api, "after_turn_project_hooks", lambda *_args: None)

        async def socket_handler(ws):
            loop = asyncio.get_running_loop(); outgoing = asyncio.Queue()
            bridge = WsBridge(emit=lambda event: loop.call_soon_threadsafe(outgoing.put_nowait, event), paths=paths)
            snapshot = bridge.lifecycle.start_or_activate("ordinary")
            bridge.lifecycle_conversation_id = session.conversation_id; bridge._turn_busy.set()
            store.begin_requirement_run(item["item_id"], requirement["requirement_id"], run_id=snapshot.run_id, expected_current_run_id=None)
            execution = BoundExecution(session.conversation_id, item["item_id"], requirement["requirement_id"], snapshot.run_id)
            repl = _build_repl(session, paths, bridge, work_record_controller_factory=lambda *_args: None)
            repl.agent.llm = ParentToolModel([{"project_id": "project-a", "task": "Feature works", "read_targets": [],
                "write_targets": ["result.txt"], "pytest_target": "test_feature.py::test_target"}])
            repl.agent.project_worker_host = host
            original = repl.handle_line
            def handle_line(text):
                result = original(text); repl.last_turn_finish_reason = getattr(result, "finish_reason", None); return "continue"
            repl.handle_line = handle_line
            outgoing.put_nowait({"type": "session.banner", "session_id": session.conversation_id, "project_id": "project-a",
                "topics": [], "turn_mode": "agent", "turn_mode_label": "执行", "phase": "S4"})
            outgoing.put_nowait({"type": "execution.state", "run_id": snapshot.run_id, "state": "running", "sequence": snapshot.sequence})
            async def sender():
                while True:
                    event = await outgoing.get()
                    if event is None: return
                    await ws.send(json.dumps(event, ensure_ascii=False))
            async def turn():
                try:
                    await _run_line(repl, bridge, "Run recorded feature", paths, prestarted_run_id=snapshot.run_id,
                                    work_record_binding=(store, execution))
                    row = WorkRecordStore(session).get_item(item["item_id"])["runs"][0]
                    outgoing.put_nowait({"type": "test.result", "row": row,
                        "pytest_marker": (project_root / "pytest-ran.txt").read_text(encoding="utf-8"),
                        "mocked_model_requests": len(model_clients)})
                except BaseException as exc:
                    outgoing.put_nowait({"type": "test.failure", "error": repr(exc)})
            send_task = asyncio.create_task(sender()); turn_task = asyncio.create_task(turn())
            dispatcher = WsSessionHandler.__new__(WsSessionHandler)
            try:
                async for raw in ws:
                    dispatcher._dispatch_inline(json.loads(raw), bridge)
                await turn_task
            finally:
                if not turn_task.done():
                    bridge.cancel_event.set(); await asyncio.wait_for(turn_task, 10)
                outgoing.put_nowait(None); await send_task
        try:
            async with serve(socket_handler, "127.0.0.1", 0) as server:
                port = server.sockets[0].getsockname()[1]
                print(json.dumps({"port": port}), flush=True)
                await asyncio.to_thread(sys.stdin.buffer.readline)
        finally:
            monkeypatch.undo()
asyncio.run(main())


