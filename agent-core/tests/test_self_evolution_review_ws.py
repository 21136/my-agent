from __future__ import annotations

import asyncio
import json
import threading
import uuid
from types import SimpleNamespace

from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from self_evolution_host import HostReadContext, HostRunContextProvider, SelfEvolutionHost
from self_evolution_types import EvolutionIdentity
from server import WsSessionHandler
from session import resume_desktop_or_create
from tests.isolation_helpers import temporary_agent_paths


async def receive(ws, kind):
    async with asyncio.timeout(12):
        while True:
            item = json.loads(await ws.recv())
            if item.get("type") == kind:
                return item


def test_review_websocket_dispatch_confirm_and_rejections(monkeypatch):
    asyncio.run(exercise(monkeypatch))


async def exercise(monkeypatch):
    provider = HostRunContextProvider()
    host = SelfEvolutionHost.__new__(SelfEvolutionHost)
    host._context_provider = provider
    host._store = object()
    host._worker_capabilities = object()
    host._tasks = {}
    host._tasks_lock = threading.RLock()
    calls = []
    with temporary_agent_paths(prefix="review-ws-") as paths:
        session = resume_desktop_or_create(paths)
        identity = EvolutionIdentity(session.conversation_id, "task-1", "attempt-1", "run-1", 1)
        snapshot = SimpleNamespace(identity=identity, snapshot_revision=7)
        def get_snapshot(_host, task_id, *, session_id=None):
            ctx = provider()
            if (type(ctx) is HostReadContext and ctx.active and ctx.conversation_id == session_id
                    and session_id == identity.session_id and task_id == identity.task_id):
                return snapshot
            return None
        def review(_host, task_id, attempt_id, callback):
            ctx = provider()
            assert type(ctx) is HostReadContext and ctx.active
            calls.append((ctx.conversation_id, task_id, attempt_id))
            digest = "d" * 64
            request = {"changed_files": ({"path": "agent-core/a.py", "change": "modified"},),
                       "request_sha256": "f" * 64, "candidate_digest": digest}
            decision = callback(request)
            if not decision.approved:
                return {"status": "failed"}
            assert decision.approved_paths == ("agent-core/a.py",)
            return {"status": "reviewed", "review": {
                "disclosure_binding": {"session_id": identity.session_id, "task_id": task_id,
                                       "attempt_id": attempt_id, "candidate_digest": digest},
                "candidate_digest": digest, "truncated": False, "content_complete": True,
                "files": [{"path": "agent-core/a.py", "change": "modified", "before_content_status": "text",
                           "after_content_status": "text", "diff": "-old\\n+new\\n", "private": "not sent"}]}}
        monkeypatch.setattr(SelfEvolutionHost, "get_snapshot", get_snapshot)
        monkeypatch.setattr(SelfEvolutionHost, "_review_candidate_content_for_session_with_disclosure", review)
        handler = WsSessionHandler(paths, self_evolution_host=host, self_evolution_context_provider=provider)
        async with serve(handler.handle, "127.0.0.1", 0) as listener:
            port = listener.sockets[0].getsockname()[1]
            async with connect(f"ws://127.0.0.1:{port}") as ws:
                request = {"type": "self_evolution.review.get", "task_id": "task-1",
                           "attempt_id": "attempt-1", "request_id": str(uuid.uuid4())}
                await ws.send(json.dumps({**request, "approved_paths": ["secret"]}))
                denied = await receive(ws, "self_evolution.review.unavailable")
                assert denied["request_id"] == request["request_id"] and not calls
                await ws.send(json.dumps(request))
                prompt = await receive(ws, "confirm.request")
                assert "agent-core/a.py" in prompt["preview"]
                assert prompt["allow_approve_all"] is False
                await ws.send(json.dumps({"type": "confirm.response", "request_id": prompt["request_id"], "choice": "y"}))
                result = await receive(ws, "self_evolution.review.result")
                assert result["request_id"] == request["request_id"]
                assert result["snapshot_revision"] == 7 and result["session_id"] == identity.session_id
                assert result["files"][0]["diff"] == "-old\\n+new\\n"
                assert "private" not in result["files"][0]
                await ws.send(json.dumps({**request, "request_id": str(uuid.uuid4()), "attempt_id": "wrong"}))
                assert (await receive(ws, "self_evolution.review.unavailable"))["attempt_id"] == "wrong"
                assert len(calls) == 1
                request["request_id"] = str(uuid.uuid4())
                await ws.send(json.dumps(request))
                prompt = await receive(ws, "confirm.request")
                await ws.send(json.dumps({"type": "confirm.response", "request_id": prompt["request_id"], "choice": "n"}))
                assert (await receive(ws, "self_evolution.review.unavailable"))["request_id"] == request["request_id"]
