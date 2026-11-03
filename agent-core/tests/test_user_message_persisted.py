"""Loopback coverage for explicit, post-append ordinary user-message receipts."""
from __future__ import annotations

import asyncio
import json
import threading
import uuid
from pathlib import Path
from types import SimpleNamespace

import websockets
from websockets.asyncio.server import serve

import agent as agent_module
import server
from llm_client import LLMResponse
from paths import AgentPaths
from server import WsSessionHandler


class _ReplyClient:
    def __init__(self) -> None:
        self.cancel_event = threading.Event()

    def set_cancel_event(self, event: threading.Event) -> None:
        self.cancel_event = event

    def chat(self, _messages, *, stream=None, **_kwargs):
        text = "fixture reply"
        if stream and stream.on_content_delta:
            stream.on_content_delta(text)
        return LLMResponse("fixture", text, [], "stop", None, {})


def _isolated_paths(tmp_path: Path, monkeypatch) -> AgentPaths:
    root = tmp_path / "agent-root"
    root.mkdir()
    (root / "evolve").mkdir()
    (root / "evolve" / "_index.toml").write_text("# fixture\n", encoding="utf-8")
    (root / "workspace").mkdir()
    monkeypatch.setenv("MY_AGENT_ROOT", str(root))
    monkeypatch.chdir(root)
    return AgentPaths.discover()


def _loopback(
    tmp_path: Path, monkeypatch, *, inject_internal_user_message: bool = False,
    enable_dialogue_recall: bool = False,
):
    paths = _isolated_paths(tmp_path, monkeypatch)
    original_build_repl = server._build_repl

    def build_repl(*args, **kwargs):
        repl = original_build_repl(*args, **kwargs)
        fixture = _ReplyClient()
        fixture.set_cancel_event(repl.agent.cancel_event)
        repl.agent.llm = fixture
        if enable_dialogue_recall:
            repl.agent.enable_work_record_recall = True
        return repl

    monkeypatch.setattr(server, "_build_repl", build_repl)
    if inject_internal_user_message:
        original_impl = agent_module.Agent._run_turn_impl

        def with_internal_append(self, user_text, **kwargs):
            self.session.append_message({"role": "user", "content": "[内核] internal hint"})
            return original_impl(self, user_text, **kwargs)

        monkeypatch.setattr(agent_module.Agent, "_run_turn_impl", with_internal_append)
    return paths, WsSessionHandler(paths)


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


async def _send_turn(ws, text: str, request_id: str | None = None) -> list[dict]:
    payload = {"type": "user.message", "text": text}
    if request_id is not None:
        payload["request_id"] = request_id
    await ws.send(json.dumps(payload))
    events = []
    while True:
        event = json.loads(await asyncio.wait_for(ws.recv(), 10))
        events.append(event)
        if event.get("type") == "turn.end":
            return events


def test_loopback_emits_bodyless_receipt_after_explicit_user_append(tmp_path, monkeypatch):
    paths, handler = _loopback(tmp_path, monkeypatch, inject_internal_user_message=True)
    request_id = str(uuid.uuid4())
    text = "ordinary externally supplied text"

    async def exercise():
        listener, ws, session_id = await _connect(handler)
        try:
            events = await _send_turn(ws, text, request_id)
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
        rows = [json.loads(line) for line in
                (paths.data / "sessions" / session_id / "messages.jsonl").read_text(encoding="utf-8").splitlines()]
        assert {"role": "user", "content": text} in rows
        assert {"role": "user", "content": "[内核] internal hint"} in rows

    asyncio.run(exercise())


def test_loopback_command_input_does_not_emit_user_persistence_receipt(tmp_path, monkeypatch):
    _paths, handler = _loopback(tmp_path, monkeypatch)
    request_id = str(uuid.uuid4())

    async def exercise():
        listener, ws, _session_id = await _connect(handler)
        try:
            events = await _send_turn(ws, "压缩", request_id)
            assert not any(event.get("type") == "user.message.persisted" for event in events)
        finally:
            await ws.close()
            listener.close()
            await listener.wait_closed()

    asyncio.run(exercise())


def test_loopback_user_append_failure_does_not_emit_receipt(tmp_path, monkeypatch):
    paths, handler = _loopback(tmp_path, monkeypatch)
    original = agent_module.Session._append_message_line

    def fail_external_user_append(self, message, *, durable=False):
        if message.get("role") == "user" and message.get("content") == "write failure input":
            raise OSError("fixture write failure")
        return original(self, message, durable=durable)

    monkeypatch.setattr(agent_module.Session, "_append_message_line", fail_external_user_append)
    request_id = str(uuid.uuid4())

    async def exercise():
        listener, ws, _session_id = await _connect(handler)
        try:
            await ws.send(json.dumps({"type": "user.message", "text": "write failure input", "request_id": request_id}))
            events = []
            try:
                while True:
                    events.append(json.loads(await asyncio.wait_for(ws.recv(), 5)))
                    if any(event.get("type") == "user.message.persisted" for event in events):
                        break
                    if events[-1].get("type") == "turn.end":
                        break
            except (asyncio.TimeoutError, websockets.ConnectionClosed):
                pass
            assert not any(event.get("type") == "user.message.persisted" for event in events)
        finally:
            await ws.close()
            listener.close()
            await listener.wait_closed()

    asyncio.run(exercise())



def test_loopback_explicit_current_dialogue_recall_emits_receipt_after_durable_append(tmp_path, monkeypatch):
    paths, handler = _loopback(tmp_path, monkeypatch, enable_dialogue_recall=True)
    request_id = str(uuid.uuid4())
    question = "请找回我们刚才讨论过什么"

    async def exercise():
        listener, ws, session_id = await _connect(handler)
        try:
            events = await _send_turn(ws, question, request_id)
            assert [event for event in events if event.get("type") == "user.message.persisted"] == [{
                "type": "user.message.persisted",
                "session_id": session_id,
                "request_id": request_id,
            }]
            history_path = paths.data / "sessions" / session_id / "messages.jsonl"
            rows = [json.loads(line) for line in history_path.read_text(encoding="utf-8").splitlines()]
            assert {"role": "user", "content": question} in rows
            assert any(row.get("role") == "assistant" for row in rows)
        finally:
            await ws.close()
            listener.close()
            await listener.wait_closed()

    asyncio.run(exercise())


def test_loopback_dialogue_recall_durable_write_failure_emits_no_receipt(tmp_path, monkeypatch):
    paths, handler = _loopback(tmp_path, monkeypatch, enable_dialogue_recall=True)
    original = agent_module.Session.append_message_durable
    question = "请找回我们刚才讨论过什么"

    def fail_query_append(self, message):
        if message.get("role") == "user" and message.get("content") == question:
            raise OSError("fixture durable write failure")
        return original(self, message)

    monkeypatch.setattr(agent_module.Session, "append_message_durable", fail_query_append)
    request_id = str(uuid.uuid4())

    async def exercise():
        listener, ws, session_id = await _connect(handler)
        try:
            events = await _send_turn(ws, question, request_id)
            assert any(event.get("type") == "turn.end" for event in events)
            assert not any(event.get("type") == "user.message.persisted" for event in events)
            rows = [json.loads(line) for line in
                    (paths.data / "sessions" / session_id / "messages.jsonl").read_text(encoding="utf-8").splitlines()]
            assert not any(row.get("content") == question for row in rows)
        finally:
            await ws.close()
            listener.close()
            await listener.wait_closed()

    asyncio.run(exercise())


def test_loopback_dialogue_recall_cancel_after_durable_append_still_emits_receipt(tmp_path, monkeypatch):
    _paths, handler = _loopback(tmp_path, monkeypatch, enable_dialogue_recall=True)
    original_recall = agent_module.Agent._run_current_dialogue_recall

    def cancel_after_durable_append(self, user_text, **kwargs):
        original_append = self.session.append_message_durable

        def append_then_cancel(message):
            result = original_append(message)
            self.cancel_event.set()
            return result

        had_instance_method = "append_message_durable" in self.session.__dict__
        previous_instance_method = self.session.__dict__.get("append_message_durable")
        self.session.append_message_durable = append_then_cancel
        try:
            return original_recall(self, user_text, **kwargs)
        finally:
            if had_instance_method:
                self.session.append_message_durable = previous_instance_method
            else:
                self.session.__dict__.pop("append_message_durable", None)

    monkeypatch.setattr(agent_module.Agent, "_run_current_dialogue_recall", cancel_after_durable_append)
    request_id = str(uuid.uuid4())
    question = "请找回我们刚才讨论过什么"

    async def exercise():
        listener, ws, session_id = await _connect(handler)
        try:
            events = await _send_turn(ws, question, request_id)
            assert any(event.get("type") == "turn.end" for event in events)
            receipts = [event for event in events if event.get("type") == "user.message.persisted"]
            assert receipts == [{
                "type": "user.message.persisted",
                "session_id": session_id,
                "request_id": request_id,
            }]
            rows = [json.loads(line) for line in
                    (_paths.data / "sessions" / session_id / "messages.jsonl").read_text(encoding="utf-8").splitlines()]
            assert {"role": "user", "content": question} in rows
        finally:
            await ws.close()
            listener.close()
            await listener.wait_closed()

    asyncio.run(exercise())


async def _stage_synthetic_file(ws, source: Path) -> dict:
    await ws.send(json.dumps({"type": "file.stage", "paths": [str(source)], "shell": "grow"}))
    while True:
        event = json.loads(await asyncio.wait_for(ws.recv(), 10))
        if event.get("type") == "file.staged":
            return event["items"][0]
        if event.get("type") == "file.error":
            raise AssertionError(f"fixture stage failed: {event!r}")


async def _send_attachment_turn(ws, text: str, attachment_ids: list[str], request_id: str) -> list[dict]:
    await ws.send(json.dumps({
        "type": "user.message", "text": text, "attachments": attachment_ids,
        "request_id": request_id,
    }))
    events = []
    while True:
        event = json.loads(await asyncio.wait_for(ws.recv(), 10))
        events.append(event)
        if event.get("type") in {"turn.end", "error"}:
            return events


def test_loopback_staged_attachment_ack_requires_full_host_compose_and_agent_append(tmp_path, monkeypatch):
    paths, handler = _loopback(tmp_path, monkeypatch)
    synthetic = tmp_path / "synthetic-attachment.txt"
    synthetic.write_text("synthetic attachment payload", encoding="utf-8")
    request_id = str(uuid.uuid4())
    user_text = "please inspect the attached synthetic fixture"

    async def exercise():
        listener, ws, session_id = await _connect(handler)
        try:
            staged = await _stage_synthetic_file(ws, synthetic)
            events = await _send_attachment_turn(ws, user_text, [staged["id"]], request_id)
            assert [event for event in events if event.get("type") == "user.message.persisted"] == [{
                "type": "user.message.persisted",
                "session_id": session_id,
                "request_id": request_id,
            }]
            rows = [json.loads(line) for line in
                    (paths.data / "sessions" / session_id / "messages.jsonl").read_text(encoding="utf-8").splitlines()]
            user_rows = [row for row in rows if row.get("role") == "user"]
            assert any(user_text in row.get("content", "") and staged["name"] in row.get("content", "")
                       for row in user_rows)
        finally:
            await ws.close()
            listener.close()
            await listener.wait_closed()

    asyncio.run(exercise())


def test_loopback_unknown_expired_and_partial_attachment_takes_never_ack(tmp_path, monkeypatch):
    _paths, handler = _loopback(tmp_path, monkeypatch)
    first = tmp_path / "staged-one.txt"
    second = tmp_path / "staged-two.txt"
    first.write_text("one", encoding="utf-8")
    second.write_text("two", encoding="utf-8")

    async def exercise():
        listener, ws, _session_id = await _connect(handler)
        try:
            one = await _stage_synthetic_file(ws, first)
            two = await _stage_synthetic_file(ws, second)
            unknown_id = "missing-attachment-id"

            unknown = await _send_attachment_turn(ws, "unknown", [unknown_id], str(uuid.uuid4()))
            assert any(event.get("type") == "error" for event in unknown)
            assert not any(event.get("type") == "user.message.persisted" for event in unknown)

            await ws.send(json.dumps({"type": "file.unstage", "attachment_id": two["id"]}))
            while True:
                event = json.loads(await asyncio.wait_for(ws.recv(), 10))
                if event.get("type") == "file.unstaged":
                    break
                # Other server-side notifications may be in flight on the same socket.
            expired = await _send_attachment_turn(ws, "expired", [two["id"]], str(uuid.uuid4()))
            assert any(event.get("type") == "error" for event in expired)
            assert not any(event.get("type") == "user.message.persisted" for event in expired)

            partial = await _send_attachment_turn(
                ws, "partial take", [one["id"], unknown_id], str(uuid.uuid4()),
            )
            assert any(event.get("type") == "error" for event in partial)
            assert not any(event.get("type") == "user.message.persisted" for event in partial)

            corrected_request_id = str(uuid.uuid4())
            corrected = await _send_attachment_turn(
                ws, "corrected after partial failure", [one["id"]], corrected_request_id,
            )
            receipts = [event for event in corrected if event.get("type") == "user.message.persisted"]
            assert receipts == [{
                "type": "user.message.persisted",
                "session_id": _session_id,
                "request_id": corrected_request_id,
            }]
        finally:
            await ws.close()
            listener.close()
            await listener.wait_closed()

    asyncio.run(exercise())


def test_loopback_attachment_staged_for_another_session_never_ack(tmp_path, monkeypatch):
    _paths, handler = _loopback(tmp_path, monkeypatch)
    synthetic = tmp_path / "cross-session.txt"
    synthetic.write_text("cross-session fixture", encoding="utf-8")

    async def exercise():
        listener, ws, first_session = await _connect(handler)
        try:
            staged = await _stage_synthetic_file(ws, synthetic)
            await ws.send(json.dumps({"type": "user.message", "text": "新会话"}))
            second_session = None
            while second_session is None or second_session == first_session:
                event = json.loads(await asyncio.wait_for(ws.recv(), 10))
                if event.get("type") == "session.banner":
                    second_session = event.get("session_id")
            events = await _send_attachment_turn(
                ws, "old-session attachment", [staged["id"]], str(uuid.uuid4()),
            )
            assert any(event.get("type") == "error" for event in events)
            assert not any(event.get("type") == "user.message.persisted" for event in events)
        finally:
            await ws.close()
            listener.close()
            await listener.wait_closed()

    asyncio.run(exercise())


def test_loopback_attachment_user_append_failure_never_ack(tmp_path, monkeypatch):
    paths, handler = _loopback(tmp_path, monkeypatch)
    synthetic = tmp_path / "append-failure.txt"
    synthetic.write_text("append failure fixture", encoding="utf-8")
    user_text = "attachment append failure body"
    original = agent_module.Session._append_message_line

    def fail_composed_attachment(self, message, *, durable=False):
        content = message.get("content")
        if (message.get("role") == "user" and isinstance(content, str)
                and user_text in content and "append-failure.txt" in content):
            raise OSError("fixture attachment append failure")
        return original(self, message, durable=durable)

    monkeypatch.setattr(agent_module.Session, "_append_message_line", fail_composed_attachment)
    request_id = str(uuid.uuid4())

    async def exercise():
        listener, ws, session_id = await _connect(handler)
        try:
            staged = await _stage_synthetic_file(ws, synthetic)
            events = await _send_attachment_turn(ws, user_text, [staged["id"]], request_id)
            assert not any(event.get("type") == "user.message.persisted" for event in events)
            rows = [json.loads(line) for line in
                    (paths.data / "sessions" / session_id / "messages.jsonl").read_text(encoding="utf-8").splitlines()]
            assert not any(user_text in str(row.get("content", "")) for row in rows)
        finally:
            await ws.close()
            listener.close()
            await listener.wait_closed()

    asyncio.run(exercise())


def test_loopback_attachment_on_special_dialogue_recall_path_does_not_ack(tmp_path, monkeypatch):
    _paths, handler = _loopback(tmp_path, monkeypatch, enable_dialogue_recall=True)
    synthetic = tmp_path / "special-recall-attachment.txt"
    synthetic.write_text("synthetic special-path attachment", encoding="utf-8")
    request_id = str(uuid.uuid4())

    async def exercise():
        listener, ws, _session_id = await _connect(handler)
        try:
            staged = await _stage_synthetic_file(ws, synthetic)
            events = await _send_attachment_turn(
                ws, "请找回我们刚才讨论过什么", [staged["id"]], request_id,
            )
            turn_end = next((event for event in events if event.get("type") == "turn.end"), None)
            assert turn_end is not None
            assert turn_end.get("finish_reason") == "dialogue_recall"
            assert not any(event.get("type") == "user.message.persisted" for event in events)
        finally:
            await ws.close()
            listener.close()
            await listener.wait_closed()

    asyncio.run(exercise())
