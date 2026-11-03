from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path

import websockets
from websockets.asyncio.server import serve

import server
from paths import AgentPaths
from session import Session


def _isolated_handler(tmp_path: Path, monkeypatch):
    root = tmp_path / "agent-root"
    root.mkdir()
    (root / "evolve").mkdir()
    (root / "evolve" / "_index.toml").write_text("# fixture\n", encoding="utf-8")
    (root / "workspace").mkdir()
    monkeypatch.setenv("MY_AGENT_ROOT", str(root))
    monkeypatch.chdir(root)
    return AgentPaths.discover(), server.WsSessionHandler(AgentPaths.discover())


async def _connect(handler):
    listener = await serve(handler.handle, "127.0.0.1", 0, max_size=65536)
    port = listener.sockets[0].getsockname()[1]
    ws = await websockets.connect(f"ws://127.0.0.1:{port}", open_timeout=5, close_timeout=5)
    session_id = None
    while session_id is None:
        event = json.loads(await asyncio.wait_for(ws.recv(), 5))
        if event.get("type") == "session.banner":
            session_id = event["session_id"]
    return listener, ws, session_id


async def _send_until_turn_end(ws, text: str, request_id):
    await ws.send(json.dumps({"type": "user.message", "text": text, "request_id": request_id}))
    events = []
    while True:
        event = json.loads(await asyncio.wait_for(ws.recv(), 10))
        events.append(event)
        if event.get("type") == "turn.end":
            return events


def test_real_ws_day_journal_summary_and_recall_ack_bind_exact_session_and_request(tmp_path, monkeypatch):
    _paths, handler = _isolated_handler(tmp_path, monkeypatch)
    summary_text = "\u603b\u7ed3\u4eca\u5929\u7684\u9879\u76ee\u65e5\u8bb0"
    recall_text = "\u67e5" + "2026-10-29" + "\u7684\u9879\u76ee\u65e5\u8bb0"

    async def exercise():
        listener, ws, session_id = await _connect(handler)
        try:
            for text in (summary_text, recall_text):
                request_id = str(uuid.uuid4())
                events = await _send_until_turn_end(ws, text, request_id)
                receipts = [event for event in events if event.get("type") == "user.message.persisted"]
                assert receipts == [{
                    "type": "user.message.persisted",
                    "session_id": session_id,
                    "request_id": request_id,
                }]
        finally:
            await ws.close()
            listener.close()
            await listener.wait_closed()

    asyncio.run(exercise())


def test_real_ws_day_journal_write_failure_invalid_id_and_command_never_ack(tmp_path, monkeypatch):
    _paths, handler = _isolated_handler(tmp_path, monkeypatch)
    failed_text = "\u603b\u7ed3\u4eca\u5929\u7684\u9879\u76ee\u65e5\u8bb0"
    original_append = Session._append_message_line

    def fail_question_append(self, message, *, durable=False):
        if message.get("role") == "user" and message.get("content") == failed_text:
            raise OSError("private fixture write failure")
        return original_append(self, message, durable=durable)

    monkeypatch.setattr(Session, "_append_message_line", fail_question_append)

    async def exercise():
        listener, ws, _session_id = await _connect(handler)
        try:
            failed = await _send_until_turn_end(ws, failed_text, str(uuid.uuid4()))
            assert not any(event.get("type") == "user.message.persisted" for event in failed)
            failure_text = " ".join(str(event.get("text", "")) for event in failed)
            assert "private fixture write failure" not in failure_text
            assert any("写入失败" in text for text in (event.get("text", "") for event in failed))

            invalid = await _send_until_turn_end(ws, "ordinary text", "not-a-canonical-request-id")
            assert not any(event.get("type") == "user.message.persisted" for event in invalid)

            command = await _send_until_turn_end(ws, "\u538b\u7f29", str(uuid.uuid4()))
            assert not any(event.get("type") == "user.message.persisted" for event in command)
        finally:
            await ws.close()
            listener.close()
            await listener.wait_closed()

    asyncio.run(exercise())


def _capture_live_repl_and_bridge(monkeypatch):
    original_build = server._build_repl
    captured = {}

    def build(*args, **kwargs):
        repl = original_build(*args, **kwargs)
        captured["repl"] = repl
        captured["bridge"] = args[2]
        return repl

    monkeypatch.setattr(server, "_build_repl", build)
    return captured


def test_real_ws_session_rebind_before_receipt_suppresses_ack(tmp_path, monkeypatch):
    _paths, handler = _isolated_handler(tmp_path, monkeypatch)
    captured = _capture_live_repl_and_bridge(monkeypatch)
    original_append = Session.append_message
    question = "\u603b\u7ed3\u4eca\u5929\u7684\u9879\u76ee\u65e5\u8bb0"

    def append_then_rebind(self, message, *, persist=True):
        result = original_append(self, message, persist=persist)
        if message.get("role") == "user" and message.get("content") == question:
            repl = captured["repl"]
            replacement_dir = repl.session.paths.data / "sessions" / "replacement-session"
            replacement_dir.mkdir(parents=True, exist_ok=True)
            repl.session = Session(
                "replacement-session", replacement_dir, "replacement",
                SessionMeta(), [], repl.session.paths,
            )
        return result

    monkeypatch.setattr(Session, "append_message", append_then_rebind)

    async def exercise():
        listener, ws, _session_id = await _connect(handler)
        try:
            events = await _send_until_turn_end(ws, question, str(uuid.uuid4()))
            assert not any(event.get("type") == "user.message.persisted" for event in events)
        finally:
            await ws.close()
            listener.close()
            await listener.wait_closed()

    asyncio.run(exercise())


def test_real_ws_closed_connection_at_append_suppresses_ack(tmp_path, monkeypatch):
    _paths, handler = _isolated_handler(tmp_path, monkeypatch)
    captured = _capture_live_repl_and_bridge(monkeypatch)
    original_append = Session.append_message
    question = "\u603b\u7ed3\u4eca\u5929\u7684\u9879\u76ee\u65e5\u8bb0"

    def append_then_close(self, message, *, persist=True):
        result = original_append(self, message, persist=persist)
        if message.get("role") == "user" and message.get("content") == question:
            captured["bridge"]._connection_closed.set()
        return result

    monkeypatch.setattr(Session, "append_message", append_then_close)

    async def exercise():
        listener, ws, _session_id = await _connect(handler)
        try:
            events = await _send_until_turn_end(ws, question, str(uuid.uuid4()))
            assert not any(event.get("type") == "user.message.persisted" for event in events)
        finally:
            await ws.close()
            listener.close()
            await listener.wait_closed()

    asyncio.run(exercise())
