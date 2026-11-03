"""Isolated loopback transport test; never starts an Agent, model, or product server."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import uuid
from pathlib import Path
from types import SimpleNamespace

import websockets
from websockets.asyncio.server import serve

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))
from paths import AgentPaths
from session import create_new
from work_records import WorkRecordStore
from server import WsBridge, WsSessionHandler


def test_loopback_overview_all_pages_legacy_and_invalidation(tmp_path, monkeypatch):
    # Fail before opening a socket if the pytest directory is not genuinely
    # within the OS temp root, or any created component is a link/junction.
    temp = Path(tempfile.gettempdir()).resolve(strict=True)
    root = tmp_path / "isolated-agent-root"
    root.mkdir()
    assert root.resolve(strict=True).is_relative_to(temp) and root.resolve(strict=True) != temp
    for part in (tmp_path, root):
        assert not part.is_symlink() and not getattr(part, "is_junction", lambda: False)()
    (root / "evolve").mkdir()
    (root / "evolve" / "_index.toml").write_text("# isolated synthetic fixture\n", encoding="utf-8")
    monkeypatch.setenv("MY_AGENT_ROOT", str(root))
    monkeypatch.chdir(root)
    paths = AgentPaths.discover()
    assert Path(os.environ["MY_AGENT_ROOT"]).resolve(strict=True) == root.resolve(strict=True)
    assert paths.agent_root == root.resolve(strict=True)
    assert (paths.workspace, paths.data, paths.evolve) == (root / "workspace", root / "data", root / "evolve")
    session = create_new(paths, conversation_id="20261030-abcdef12")
    assert session.paths is paths and session.session_dir.resolve().is_relative_to((root / "data" / "sessions").resolve())
    store = WorkRecordStore(session)
    assert store._path.resolve().is_relative_to(root.resolve())
    expected = {}
    for project_index, project in enumerate(("project-api", "project-beta")):
        expected[project] = {}
        for i in range(6):
            digest = f"{project_index * 6 + i + 1:064x}"
            # Both project groups come from trusted batch projections, which
            # avoid changing the fixture Session's project-write authority.
            item = store.create_item_with_requirement(title="\u591a\u9879\u76ee\u4efb\u52a1\u6279\u6b21", scope={"kind": "independent"},
                requirement=f"source-{project}-{i}", event_id=f"worker-batch-original-{digest}",
                idempotency_key=f"worker-batch-original:{digest}")
            expected[project][item["item_id"]] = set()
            for n in range(6):
                req = store.add_requirement(item["item_id"], f"synthetic-{project}-{i}-{n}",
                    event_id=f"worker-batch-task-{digest}-{n}", idempotency_key=f"worker-batch-task:{digest}:{n}",
                    source_ref={"kind": "project", "id": project})
                expected[project][item["item_id"]].add(req["requirement_id"])

    async def exercise():
        handler = WsSessionHandler(paths)
        repl = SimpleNamespace(session=session, agent=SimpleNamespace(session=session))
        async def connection(socket):
            loop = asyncio.get_running_loop()
            queue = asyncio.Queue()
            bridge = WsBridge(emit=lambda event: loop.call_soon_threadsafe(queue.put_nowait, event), paths=paths)
            async def sender():
                while True:
                    event = await queue.get()
                    if event is None:
                        return
                    await socket.send(json.dumps(event, ensure_ascii=False))
            task = asyncio.create_task(sender())
            try:
                async for raw in socket:
                    message = json.loads(raw)
                    # No arbitrary traffic can invoke another handler path.
                    assert message.get("type") == "work-records.overview.query"
                    await handler._handle_incoming(message, repl, bridge)
            finally:
                bridge._connection_closed.set()
                bridge.cancel_overview_query()
                await queue.put(None)
                await task
        async with serve(connection, "127.0.0.1", 0, max_size=65536) as listener:
            assert len(listener.sockets) == 1 and listener.sockets[0].getsockname()[0] == "127.0.0.1"
            port = listener.sockets[0].getsockname()[1]
            async with websockets.connect(f"ws://127.0.0.1:{port}", open_timeout=5, close_timeout=5) as ws:
                async def ask(cursor=None, detail=True):
                    request = {"type": "work-records.overview.query", "session_id": session.conversation_id,
                               "request_id": str(uuid.uuid4()), "page_size": 1}
                    if detail is not None:
                        request["detail_paging"] = detail
                    if cursor:
                        request["cursor"] = cursor
                    await ws.send(json.dumps(request))
                    result = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
                    assert result["request_id"] == request["request_id"] and result["session_id"] == session.conversation_id
                    return result
                bad = await ask("invalid-cursor")
                assert bad["state"] == "error" and bad["groups"] == []
                foreign_request = {"type": "work-records.overview.query", "session_id": "20261030-foreign1",
                                   "request_id": str(uuid.uuid4()), "detail_paging": True}
                await ws.send(json.dumps(foreign_request))
                foreign = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
                assert foreign["request_id"] == foreign_request["request_id"]
                assert foreign["reason"] == "session_mismatch" and foreign["groups"] == []
                first = await ask()
                assert first["state"] == "visible" and first["page"]["total_groups"] == 2
                snapshot = first["snapshot_id"]
                groups = []
                page = first
                while True:
                    assert page["snapshot_id"] == snapshot
                    groups.extend(page["groups"])
                    cursor = page["page"]["next_cursor"]
                    if not cursor: break
                    page = await ask(cursor)
                seen = {}
                stale_cursor = None
                for group in groups:
                    items = list(group["items"])
                    cursor = group["items_page"]["next_cursor"]
                    stale_cursor = stale_cursor or cursor
                    while cursor:
                        page = await ask(cursor)
                        assert page["snapshot_id"] == snapshot and page["detail_page"]["kind"] == "items"
                        assert page["groups"][0]["id"] == group["id"]
                        items.extend(page["groups"][0]["items"])
                        cursor = page["detail_page"]["next_cursor"]
                    assert len(items) == len({item["item_id"] for item in items})
                    seen[group["id"]] = {}
                    for item in items:
                        requirements = list(item["requirements"])
                        cursor = item["requirements_page"]["next_cursor"]
                        while cursor:
                            page = await ask(cursor)
                            assert page["snapshot_id"] == snapshot and page["detail_page"]["item_id"] == item["item_id"]
                            requirements.extend(page["groups"][0]["items"][0]["requirements"])
                            cursor = page["detail_page"]["next_cursor"]
                        ids = [r["requirement_id"] for r in requirements]
                        assert len(ids) == len(set(ids))
                        seen[group["id"]][item["item_id"]] = set(ids)
                assert seen == expected and stale_cursor
                legacy = await ask(detail=None)
                while True:
                    group = legacy["groups"][0]
                    assert len(group["items"]) == 6 and "items_page" not in group
                    assert all(len(item["requirements"]) == 6 and "requirements_page" not in item for item in group["items"])
                    assert {item["item_id"]: {r["requirement_id"] for r in item["requirements"]} for item in group["items"]} == expected[group["id"]]
                    cursor = legacy["page"]["next_cursor"]
                    if not cursor: break
                    legacy = await ask(cursor, detail=None)
                group = groups[0]
                store.add_requirement(group["items"][0]["item_id"], "synthetic changed", event_id="changed")
                expired = await ask(stale_cursor)
                assert expired["state"] == "expired" and expired["groups"] == []
    asyncio.run(exercise())
