"""Local-only WsBridge confirmation fixture for Desktop renderer tests; uses temporary data only."""
from __future__ import annotations
import asyncio, json, pathlib, sys, tempfile
core = pathlib.Path(sys.argv[1]).resolve()
sys.path.insert(0, str(core))
from paths import AgentPaths
from session import Session, SessionMeta
from server import WsBridge, WsSessionHandler
from websockets.asyncio.server import serve

async def main():
    with tempfile.TemporaryDirectory(prefix="desktop-project-worker-confirm-") as temp:
        root = pathlib.Path(temp).resolve()
        paths = AgentPaths(root, root / "workspace", root / "data", root / "evolve")
        session_dir = paths.data / "sessions" / "ui-confirm-synthetic"
        session_dir.mkdir(parents=True)
        session = Session(conversation_id="ui-confirm-synthetic", session_dir=session_dir, paths=paths, meta=SessionMeta(), messages=[], goal="")
        bridge = WsBridge(emit=lambda _event: None, paths=paths)
        bridge._session_provider = lambda: session
        if not bridge.begin_turn(mode="ordinary"):
            raise RuntimeError("synthetic run did not start")
        bridge.lifecycle_conversation_id = session.conversation_id
        snapshot = bridge.lifecycle.snapshot()
        if snapshot is None or snapshot.state != "running":
            raise RuntimeError("synthetic run is not running")
        payload = {"session_id": session.conversation_id, "project_id": "synthetic-project", "item_id": "synthetic-item",
                   "requirement_id": "synthetic-requirement", "project_name": "合成项目", "requirement_text": "合成要求（不执行 pytest）",
                   "pytest_target": "tests/test_synthetic.py::test_target", "worker_run_id": "synthetic-worker-run",
                   "reservation_id": "synthetic-reservation"}

        async def socket_handler(ws):
            loop = asyncio.get_running_loop()
            outgoing = asyncio.Queue()
            done_sent = asyncio.Event()
            bridge.emit = lambda event: loop.call_soon_threadsafe(outgoing.put_nowait, event)
            async def sender():
                while True:
                    event = await outgoing.get()
                    if event is None: return
                    await ws.send(json.dumps(event, ensure_ascii=False))
                    if event.get("type") == "confirm.done": done_sent.set()
            sender_task = asyncio.create_task(sender())
            outgoing.put_nowait({"type": "session.banner", "session_id": session.conversation_id, "topics": [], "turn_mode": "agent", "turn_mode_label": "执行", "phase": "S4"})
            outgoing.put_nowait({"type": "execution.state", "run_id": snapshot.run_id, "state": "running", "sequence": snapshot.sequence})
            confirm_task = asyncio.create_task(asyncio.to_thread(bridge.confirm_project_worker_requirement, session=session, run_id=snapshot.run_id, payload=payload))
            dispatcher = WsSessionHandler.__new__(WsSessionHandler)
            try:
                async for raw in ws:
                    try: message = json.loads(raw)
                    except (TypeError, ValueError): continue
                    dispatcher._dispatch_inline(message, bridge)
                    if confirm_task.done(): break
                await confirm_task
                await asyncio.wait_for(done_sent.wait(), 5)
            finally:
                if not confirm_task.done():
                    bridge.cancel_event.set()
                    await asyncio.wait_for(confirm_task, 5)
                outgoing.put_nowait(None)
                await sender_task

        async with serve(socket_handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            if port in (5173, 5174, 8000, 8765): raise RuntimeError("reserved port")
            print(json.dumps({"port": port, "session_id": session.conversation_id}), flush=True)
            await asyncio.to_thread(sys.stdin.buffer.readline)
asyncio.run(main())
