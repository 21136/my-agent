from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

import server
from interface_lock import read_lock
from paths import AgentPaths


def _prepare_auto_launcher(monkeypatch, tmp_path, *, handler=None):
    root = tmp_path / "agent"
    (root / "evolve").mkdir(parents=True)
    (root / "evolve" / "_index.core.toml").write_text("", encoding="utf-8")
    paths = AgentPaths.from_root(root)
    monkeypatch.setattr(server.AgentPaths, "discover", lambda: paths)
    policy_file = tmp_path / "policy.json"
    policy_file.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(server, "_resolve_self_evolution_auto_policy_path", lambda *_a, **_k: policy_file)
    monkeypatch.setattr(server, "_require_self_evolution_auto_auth", lambda *_a, **_k: "a" * 64)
    from self_evolution_auto import AutoPolicy
    monkeypatch.setattr(AutoPolicy, "load", classmethod(lambda cls, *_a, **_k: object()))
    monkeypatch.setattr(server, "_self_evolution_backend_for_server", lambda *_a, **_k: (None, None, None, ()))
    if handler is not None:
        class FakeHandler:
            async def handle(self, websocket):
                await handler(websocket)
        monkeypatch.setattr(server, "WsSessionHandler", lambda *_a, **_k: FakeHandler())
    return paths


def test_main_passes_piped_stdin_only_for_stable_auto_launcher(monkeypatch):
    calls = []
    class Stream:
        buffer = object()
    monkeypatch.setattr(server.sys, "stdin", Stream())
    monkeypatch.setattr(server, "run_server", lambda *a, **kw: calls.append(kw) or _done())
    def fake_run(coro):
        coro.close()
        return 0
    monkeypatch.setattr(server.asyncio, "run", fake_run)

    assert server.main(["--self-evolution-auto-stable-launcher"]) == 0
    assert calls[0]["self_evolution_auto_stable_stdin"] is Stream.buffer


def test_main_does_not_inject_stdin_for_other_launch_modes(monkeypatch):
    calls = []
    monkeypatch.setattr(server, "run_server", lambda *a, **kw: calls.append(kw) or _done())
    monkeypatch.setattr(server.asyncio, "run", lambda coro: (coro.close(), 0)[1])

    assert server.main([]) == 0
    assert "self_evolution_auto_stable_stdin" not in calls[0]


def _done():
    async def result():
        return 0
    return result()


def test_real_stdin_eof_stops_listener_and_releases_interface_lock(monkeypatch, tmp_path):
    paths = _prepare_auto_launcher(monkeypatch, tmp_path)
    read_fd, write_fd = os.pipe()
    stdin = os.fdopen(read_fd, "rb", buffering=0)
    stdin_write = os.fdopen(write_fd, "wb", buffering=0)
    messages = []
    monkeypatch.setattr(server, "print", lambda text, **_kwargs: messages.append(json.loads(text)), raising=False)

    async def scenario():
        task = asyncio.create_task(server.run_server(
            "127.0.0.1", 0,
            self_evolution_auto_stable_launcher=True,
            self_evolution_auto_policy_path="registered-policy",
            self_evolution_auto_stable_stdin=stdin,
        ))
        # Wait for real readiness while the pipe's write end remains open.
        for _ in range(200):
            if any(item.get("ready") for item in messages):
                break
            await asyncio.sleep(0.01)
        assert any(item.get("ready") for item in messages)
        stdin_write.write(b"not a shutdown command\n")
        await asyncio.sleep(0.05)
        assert not task.done(), "stdin content must not act as a shutdown protocol"
        stdin_write.close()
        assert await asyncio.wait_for(task, timeout=3) == 0

    try:
        asyncio.run(scenario())
        assert read_lock(paths) is None
    finally:
        stdin.close()
        stdin_write.close()


def test_eof_before_ready_exits_without_leaving_interface_lock(monkeypatch, tmp_path):
    paths = _prepare_auto_launcher(monkeypatch, tmp_path)
    read_fd, write_fd = os.pipe()
    stdin = os.fdopen(read_fd, "rb", buffering=0)
    stdin_write = os.fdopen(write_fd, "wb", buffering=0)
    stdin_write.close()
    messages = []
    monkeypatch.setattr(server, "print", lambda text, **_kwargs: messages.append(json.loads(text)), raising=False)
    try:
        result = asyncio.run(server.run_server(
            "127.0.0.1", 0,
            self_evolution_auto_stable_launcher=True,
            self_evolution_auto_policy_path="registered-policy",
            self_evolution_auto_stable_stdin=stdin,
        ))
        assert result == 0
        assert not any(item.get("ready") for item in messages)
        assert read_lock(paths) is None
    finally:
        stdin.close()
        stdin_write.close()



def test_eof_closes_active_websocket_and_releases_lock(monkeypatch, tmp_path):
    import websockets
    from websockets.exceptions import ConnectionClosed

    connected = asyncio.Event()
    async def wait_for_close(websocket):
        connected.set()
        await websocket.wait_closed()

    paths = _prepare_auto_launcher(monkeypatch, tmp_path, handler=wait_for_close)
    read_fd, write_fd = os.pipe()
    stdin = os.fdopen(read_fd, "rb", buffering=0)
    stdin_write = os.fdopen(write_fd, "wb", buffering=0)
    messages = []
    monkeypatch.setattr(server, "print", lambda text, **_kwargs: messages.append(json.loads(text)), raising=False)

    async def scenario():
        task = asyncio.create_task(server.run_server(
            "127.0.0.1", 0, self_evolution_auto_stable_launcher=True,
            self_evolution_auto_policy_path="registered-policy",
            self_evolution_auto_stable_stdin=stdin,
        ))
        for _ in range(200):
            ready = next((item for item in messages if item.get("ready")), None)
            if ready is not None:
                break
            await asyncio.sleep(0.01)
        assert ready is not None
        async with websockets.connect(
            f"ws://127.0.0.1:{ready['port']}",
            subprotocols=["my-agent-auto." + "a" * 64],
        ) as websocket:
            await asyncio.wait_for(connected.wait(), timeout=1)
            stdin_write.close()
            with pytest.raises(ConnectionClosed):
                await asyncio.wait_for(websocket.recv(), timeout=1)
        assert await asyncio.wait_for(task, timeout=3) == 0

    try:
        asyncio.run(scenario())
        assert read_lock(paths) is None
    finally:
        stdin.close()
        stdin_write.close()


def test_nonresponsive_websocket_handler_cannot_hold_orphan_alive(monkeypatch, tmp_path):
    never = asyncio.Event()
    async def ignore_shutdown(_websocket):
        await never.wait()

    paths = _prepare_auto_launcher(monkeypatch, tmp_path, handler=ignore_shutdown)
    read_fd, write_fd = os.pipe()
    stdin = os.fdopen(read_fd, "rb", buffering=0)
    stdin_write = os.fdopen(write_fd, "wb", buffering=0)
    messages = []
    monkeypatch.setattr(server, "print", lambda text, **_kwargs: messages.append(json.loads(text)), raising=False)

    async def scenario():
        task = asyncio.create_task(server.run_server(
            "127.0.0.1", 0, self_evolution_auto_stable_launcher=True,
            self_evolution_auto_policy_path="registered-policy",
            self_evolution_auto_stable_stdin=stdin,
        ))
        for _ in range(200):
            ready = next((item for item in messages if item.get("ready")), None)
            if ready is not None:
                break
            await asyncio.sleep(0.01)
        assert ready is not None
        websocket = await asyncio.wait_for(websockets.connect(
            f"ws://127.0.0.1:{ready['port']}",
            subprotocols=["my-agent-auto." + "a" * 64],
        ), timeout=1)
        await asyncio.sleep(0.02)
        stdin_write.close()
        await asyncio.wait_for(task, timeout=4)
        await websocket.close()

    import websockets
    try:
        asyncio.run(scenario())
        assert read_lock(paths) is None
    finally:
        stdin.close()
        stdin_write.close()

@pytest.mark.parametrize("rejection", ["lock_conflict", "policy_reject", "port_reject"])
def test_real_child_rejection_exits_with_parent_pipe_open_without_losing_lock(
    tmp_path, rejection,
):
    root = tmp_path / "agent"
    (root / "evolve").mkdir(parents=True)
    (root / "evolve" / "_index.core.toml").write_text("", encoding="utf-8")
    (root / "data" / "sessions").mkdir(parents=True)
    (root / "workspace").mkdir()
    policy = root / "data" / "self-evolution-auto.policy.json"
    policy.write_text("not-json" if rejection == "policy_reject" else "{}", encoding="utf-8")
    paths = AgentPaths.from_root(root)
    if rejection == "lock_conflict":
        from interface_lock import write_lock
        write_lock(paths, "electron")
    lock_path = root / "data" / "sessions" / ".interface.lock"
    lock_before = lock_path.read_bytes() if lock_path.exists() else None

    child_code = r'''
import threading, time
original_start = threading.Thread.start
def start_and_let_stdin_block(self, *args, **kwargs):
    result = original_start(self, *args, **kwargs)
    if self.name == "self-evolution-stable-launcher-stdin":
        time.sleep(0.25)
    return result
threading.Thread.start = start_and_let_stdin_block
from server import main
raise SystemExit(main(["--self-evolution-auto-stable-launcher", "--port", PORT]))
'''.replace("PORT", chr(34) + ("8765" if rejection == "port_reject" else "0") + chr(34))
    env = os.environ.copy()
    env["PYTHONPATH"] = str(_AGENT_CORE)
    env["MY_AGENT_ROOT"] = str(root)
    env["MY_AGENT_SELF_EVOLUTION_AUTO_POLICY"] = str(policy)
    env["MY_AGENT_SELF_EVOLUTION_AUTO_TOKEN"] = "a" * 64
    started = time.monotonic()
    child = subprocess.Popen(
        [sys.executable, "-c", child_code], cwd=_AGENT_CORE.parent, env=env,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        returncode = child.wait(timeout=5)
        elapsed = time.monotonic() - started
        assert elapsed < 5
        stdout = child.stdout.read().decode("utf-8", errors="replace")
        stderr = child.stderr.read().decode("utf-8", errors="replace")
        assert returncode == {"lock_conflict": 2, "policy_reject": 4, "port_reject": 4}[rejection], (stdout, stderr)
        assert "_enter_buffered_busy" not in stderr
        if rejection == "lock_conflict":
            assert lock_path.read_bytes() == lock_before, "the live holder's lock must remain untouched"
        else:
            assert not lock_path.exists(), "a rejected child must release only its own lock"
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=2)
        if child.stdin and not child.stdin.closed:
            child.stdin.close()
        if child.stdout:
            child.stdout.close()
        if child.stderr:
            child.stderr.close()
