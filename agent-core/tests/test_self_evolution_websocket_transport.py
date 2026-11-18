from __future__ import annotations

import asyncio
import os
import tempfile
import json
import threading
import uuid
from pathlib import Path
from typing import Any

import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from self_evolution_host import HostRunContextProvider, SelfEvolutionHost
from self_evolution_store import EvolutionStore
from self_evolution_types import EvolutionIdentity, EvolutionSnapshot
from server import WsSessionHandler
from session import resume_desktop_or_create
from tests.isolation_helpers import temporary_agent_paths


def _isolated_store(path: Path) -> EvolutionStore:
    options: dict[str, Any] = {}
    if os.name == "nt":
        # The test database is under pytest's private temporary directory.
        options["trusted_acl_verifier"] = lambda candidate: candidate.parent.resolve().is_relative_to(
            Path(tempfile.gettempdir()).resolve()
        )
    return EvolutionStore(path, **options)


async def _receive_type(websocket: Any, event_type: str) -> dict[str, Any]:
    async with asyncio.timeout(8):
        while True:
            event = json.loads(await websocket.recv())
            if event.get("type") == event_type:
                return event


def test_loopback_websocket_reconnect_restores_snapshot_and_rejects_foreign_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise real TCP/WebSocket framing and the server's actual incoming-message path."""
    asyncio.run(_exercise_loopback_recovery(tmp_path, monkeypatch))


async def _exercise_loopback_recovery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _isolated_store(tmp_path / "self-evolution-loopback.sqlite3")
    provider = HostRunContextProvider()
    # The real Host type is used because the production integration deliberately
    # rejects duck-typed Hosts. Its worker/model dependencies are inert test doubles;
    # this test exercises only read-only recovery and never starts a worker.
    host = SelfEvolutionHost.__new__(SelfEvolutionHost)
    host._store = store
    host._worker_capabilities = object()
    host._startup_reconciliation_error = None
    host._tasks = {}
    host._tasks_lock = threading.RLock()
    host._context_provider = provider
    monkeypatch.setattr(SelfEvolutionHost, "production_ready", property(lambda _self: True))

    with temporary_agent_paths(prefix="self-evolution-ws-a-") as paths_a, \
            temporary_agent_paths(prefix="self-evolution-ws-b-") as paths_b:
        session_a = resume_desktop_or_create(paths_a)
        session_b = resume_desktop_or_create(paths_b)
        assert session_a.conversation_id != session_b.conversation_id

        identity = EvolutionIdentity(
            session_a.conversation_id, "task-loopback", "attempt-loopback", "run-old", 4,
        )
        initial = EvolutionSnapshot(
            identity, "preparing", 1, allowed_next_actions=("cancel", "get_snapshot"),
        )
        store.create_task_with_writer(identity, initial, "isolated-loopback-owner")
        expected = store.mark_task_unknown(identity.task_id, "worker_owner_unconfirmed")

        handler_a = WsSessionHandler(
            paths_a, self_evolution_host=host, self_evolution_context_provider=provider,
        )
        handler_b = WsSessionHandler(
            paths_b, self_evolution_host=host, self_evolution_context_provider=provider,
        )
        async with serve(handler_a.handle, "127.0.0.1", 0) as server_a, \
                serve(handler_b.handle, "127.0.0.1", 0) as server_b:
            port_a = server_a.sockets[0].getsockname()[1]
            port_b = server_b.sockets[0].getsockname()[1]
            url_a = f"ws://127.0.0.1:{port_a}"
            url_b = f"ws://127.0.0.1:{port_b}"

            # Drop the initial socket, then reconnect to the same isolated session.
            async with connect(url_a):
                pass
            async with connect(url_a) as client:
                await client.send(json.dumps({
                    "type": "self_evolution.snapshot.get",
                    "task_id": identity.task_id,
                    "request_id": str(uuid.uuid4()),
                }))
                restored = await _receive_type(client, "self_evolution.snapshot")

            assert restored["session_id"] == session_a.conversation_id
            assert restored["task_id"] == identity.task_id
            assert restored["attempt_id"] == identity.attempt_id
            assert restored["phase"] == expected.phase == "unknown"
            assert restored["snapshot_revision"] == expected.snapshot_revision
            assert restored["history_status"] == "not_restored"

            # A different real session over a second loopback listener cannot read
            # the first session's durable task, even though both share one test store.
            async with connect(url_b) as other_client:
                await other_client.send(json.dumps({
                    "type": "self_evolution.snapshot.get",
                    "task_id": identity.task_id,
                    "request_id": str(uuid.uuid4()),
                }))
                rejected = await _receive_type(other_client, "self_evolution.snapshot.unavailable")

            assert rejected["session_id"] == session_b.conversation_id
            assert rejected["task_id"] == identity.task_id
            assert store.read_task(session_a.conversation_id, identity.task_id) == expected
            assert store.read_task(session_b.conversation_id, identity.task_id) is None
