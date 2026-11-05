"""Real loopback regression coverage for ordinary streamed-message persistence."""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import websockets
from websockets.asyncio.server import serve

import agent as agent_module
import server
from llm_client import LLMApiError, LLMCancelledError, LLMNetworkError, LLMResponse
from paths import AgentPaths
from server import WsSessionHandler
from session import create_new, load_session_for_harness


class _FixtureChatClient:
    def __init__(self) -> None:
        self.cancel_event = threading.Event()
        self.mode = "complete"
        self.calls = 0
        self.started = threading.Event()

    def set_cancel_event(self, event: threading.Event) -> None:
        self.cancel_event = event

    def set_mode(self, mode: str) -> None:
        self.mode = mode
        self.calls = 0
        self.started.clear()

    @staticmethod
    def _emit(stream, text: str) -> None:
        if stream and stream.on_content_delta:
            stream.on_content_delta(text)

    def chat(self, _messages, *, stream=None, **_kwargs):
        self.calls += 1
        if self.mode == "complete":
            text = "assistant completed exact raw text"
            self._emit(stream, text)
            return LLMResponse("fixture", text, [], "stop", None, {})
        if self.mode == "interrupt":
            text = "assistant partial exact raw text"
            self._emit(stream, text)
            if not self.cancel_event.wait(8):
                raise AssertionError("fixture stream was not cancelled")
            raise LLMCancelledError("fixture stream cancelled")
        if self.mode == "retry_then_complete":
            if self.calls == 1:
                self._emit(stream, "retry attempt exact raw text")
                raise LLMNetworkError("connection refused on first attempt")
            text = "recovered completed exact raw text"
            self._emit(stream, text)
            return LLMResponse("fixture", text, [], "stop", None, {})
        if self.mode == "provider_failure":
            # Identical text from distinct requests must still produce distinct records.
            self._emit(stream, "same failed attempt fragment")
            raise LLMNetworkError("connection refused")
        if self.mode == "pre_delta_failure":
            self.started.set()
            raise LLMNetworkError("private provider diagnostic must not enter history")
        if self.mode == "pre_delta_cancel":
            self.started.set()
            if not self.cancel_event.wait(8):
                raise AssertionError("fixture stream was not cancelled")
            raise LLMCancelledError("private cancellation diagnostic")
        raise AssertionError(f"unknown fixture mode: {self.mode}")


def _history_rows(session_dir: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in (session_dir / "messages.jsonl").read_text(encoding="utf-8").splitlines()
    ]


def _after_user(rows: list[dict], text: str) -> list[dict]:
    start = next(i for i, row in enumerate(rows) if row.get("role") == "user" and row.get("content") == text)
    end = next((i for i in range(start + 1, len(rows)) if rows[i].get("role") == "user"), len(rows))
    return rows[start + 1:end]


def _is_interrupted_stream(row: dict) -> bool:
    host_meta = row.get("_host_internal")
    return isinstance(host_meta, dict) and host_meta.get("stream_incomplete") is True


def test_real_loopback_persists_each_failed_attempt_and_marks_partial_history(tmp_path, monkeypatch):
    root = tmp_path / "isolated-agent-root"
    root.mkdir()
    (root / "evolve").mkdir()
    (root / "evolve" / "_index.toml").write_text("# isolated fixture\n", encoding="utf-8")
    (root / "workspace").mkdir()
    monkeypatch.setenv("MY_AGENT_ROOT", str(root))
    monkeypatch.chdir(root)
    paths = AgentPaths.discover()
    handler = WsSessionHandler(paths)
    original_build_repl = server._build_repl
    clients: list[_FixtureChatClient] = []
    monkeypatch.setattr(agent_module, "time", SimpleNamespace(sleep=lambda _seconds: None))

    def build_repl(*args, **kwargs):
        repl = original_build_repl(*args, **kwargs)
        fixture = _FixtureChatClient()
        fixture.set_cancel_event(repl.agent.cancel_event)
        repl.agent.llm = fixture
        clients.append(fixture)
        return repl

    monkeypatch.setattr(server, "_build_repl", build_repl)

    async def exercise():
        async with serve(handler.handle, "127.0.0.1", 0, max_size=65536) as listener:
            port = listener.sockets[0].getsockname()[1]
            url = f"ws://127.0.0.1:{port}"
            async with websockets.connect(url, open_timeout=5, close_timeout=5) as ws:
                session_id = None
                while session_id is None:
                    event = json.loads(await asyncio.wait_for(ws.recv(), 5))
                    if event.get("type") == "session.banner":
                        session_id = event["session_id"]

                async def send_turn(text: str, *, cancel: bool = False):
                    await ws.send(json.dumps({"type": "user.message", "text": text}))
                    deltas: list[str] = []
                    sent_cancel = False
                    while True:
                        event = json.loads(await asyncio.wait_for(ws.recv(), 10))
                        if event.get("type") == "assistant.delta":
                            deltas.append(event.get("text", ""))
                            if cancel and not sent_cancel:
                                sent_cancel = True
                                await ws.send(json.dumps({"type": "execution.stop", "run_id": event["run_id"]}))
                        if event.get("type") == "turn.end":
                            return deltas, event

                _complete_deltas, completed = await send_turn("ordinary completed user raw text")
                assert completed["finish_reason"] == "stop"

                clients[-1].set_mode("interrupt")
                interrupted_deltas, interrupted = await send_turn(
                    "ordinary interrupted user raw text", cancel=True,
                )
                assert interrupted_deltas == ["assistant partial exact raw text"]
                assert interrupted["finish_reason"] == "cancelled"

                clients[-1].set_mode("retry_then_complete")
                retry_deltas, retry_result = await send_turn("ordinary retry user raw text")
                assert retry_result["finish_reason"] == "stop"
                assert retry_deltas == ["retry attempt exact raw text", "recovered completed exact raw text"]

                clients[-1].set_mode("provider_failure")
                failed_deltas, failed = await send_turn("ordinary provider failure user raw text")
                assert failed["finish_reason"] == "provider_error"
                assert len(failed_deltas) >= 2
                assert all(delta == "same failed attempt fragment" for delta in failed_deltas)

        session_dir = paths.data / "sessions" / session_id
        rows = _history_rows(session_dir)
        problems: list[str] = []
        if {"role": "user", "content": "ordinary completed user raw text"} not in rows:
            problems.append("completed user original missing")
        if not any(row.get("role") == "assistant" and row.get("content") == "assistant completed exact raw text"
                   for row in rows):
            problems.append("completed assistant original missing")

        interrupted_rows = _after_user(rows, "ordinary interrupted user raw text")
        if len(interrupted_rows) != 1 or interrupted_rows[0].get("content") != "assistant partial exact raw text":
            problems.append(f"cancelled fragment missing or altered: {interrupted_rows!r}")
        elif not _is_interrupted_stream(interrupted_rows[0]):
            problems.append("cancelled stream lacks incomplete marker")

        retry_rows = _after_user(rows, "ordinary retry user raw text")
        retry_assistants = [row for row in retry_rows if row.get("role") == "assistant"]
        retry_texts = [row.get("content") for row in retry_assistants]
        expected_retry_texts = ["retry attempt exact raw text", "recovered completed exact raw text"]
        if retry_texts != expected_retry_texts:
            problems.append(f"retry request boundary/order mismatch: {retry_texts!r}")
        if len(retry_assistants) >= 2:
            if not _is_interrupted_stream(retry_assistants[0]):
                problems.append("failed retry fragment lacks incomplete marker")
            if _is_interrupted_stream(retry_assistants[1]):
                problems.append("complete retry reply incorrectly marked incomplete")

        failure_rows = _after_user(rows, "ordinary provider failure user raw text")
        failed_partials = [row for row in failure_rows if _is_interrupted_stream(row)]
        failed_partial_texts = [row.get("content") for row in failed_partials]
        if failed_partial_texts != failed_deltas:
            problems.append(f"provider attempts not saved once each: emitted={failed_deltas!r}, saved={failed_partial_texts!r}")
        failure_assistants = [row for row in failure_rows if row.get("role") == "assistant"]
        if not failure_assistants or failure_assistants[-1].get("content") == "same failed attempt fragment":
            problems.append("provider_error response missing or partial was left as final normal reply")
        elif _is_interrupted_stream(failure_assistants[-1]):
            problems.append("provider_error terminal response marked as a partial stream")
        elif any(failure_rows.index(row) > failure_rows.index(failure_assistants[-1]) for row in failed_partials):
            problems.append("partial attempts appear after terminal provider_error reply")
        if any(str(row.get("role", "")).startswith("work_card") for row in rows):
            problems.append("ordinary chat was converted into a work card")

        restarted_handler = WsSessionHandler(paths)
        async with serve(restarted_handler.handle, "127.0.0.1", 0, max_size=65536) as listener:
            port = listener.sockets[0].getsockname()[1]
            async with websockets.connect(f"ws://127.0.0.1:{port}", open_timeout=5, close_timeout=5) as ws:
                history = None
                while history is None:
                    event = json.loads(await asyncio.wait_for(ws.recv(), 5))
                    if event.get("type") == "session.history":
                        history = event
                assert history["session_id"] == session_id
                items = history["items"]
                if not any(item.get("role") == "assistant" and "回复中断" in item.get("text", "")
                           and "assistant partial exact raw text" in item.get("text", "") for item in items):
                    problems.append("server history did not visibly mark interrupted assistant text")
                restored_failed_count = sum(item.get("role") == "assistant"
                    and "same failed attempt fragment" in item.get("text", "") for item in items)
                if restored_failed_count != len(failed_deltas):
                    problems.append(f"server history attempt count mismatch: {restored_failed_count} != {len(failed_deltas)}")

        core_path = str(Path(server.__file__).resolve().parent)
        child_script = f"""
import json, os, sys
sys.path.insert(0, {core_path!r})
from pathlib import Path
from types import SimpleNamespace
from paths import AgentPaths
from session import resume_desktop_or_create, session_history_event
paths = AgentPaths.from_root(Path(os.environ['MY_AGENT_ROOT']))
session = resume_desktop_or_create(paths)
print(json.dumps(session_history_event(session), ensure_ascii=False))
"""
        resumed = subprocess.run(
            [sys.executable, "-c", child_script], cwd=root,
            env=os.environ.copy(), capture_output=True, text=True, timeout=15, check=True,
        )
        process_history = json.loads(resumed.stdout.strip())
        if process_history["session_id"] != session_id:
            problems.append("fresh process did not resume the captured session")
        process_attempt_count = sum(item.get("role") == "assistant"
            and "same failed attempt fragment" in item.get("text", "") for item in process_history["items"])
        if process_attempt_count != len(failed_deltas):
            problems.append(f"fresh process attempt count mismatch: {process_attempt_count} != {len(failed_deltas)}")
        assert not problems, "\n".join(problems)

    asyncio.run(asyncio.wait_for(exercise(), timeout=60))


def test_session_switch_saves_stream_to_bound_session_only(tmp_path, monkeypatch):
    root = tmp_path / "isolated-switch-root"
    root.mkdir()
    (root / "evolve").mkdir()
    (root / "evolve" / "_index.toml").write_text("# isolated fixture\n", encoding="utf-8")
    (root / "workspace").mkdir()
    monkeypatch.setenv("MY_AGENT_ROOT", str(root))
    monkeypatch.chdir(root)
    paths = AgentPaths.discover()
    handler = WsSessionHandler(paths)
    original_build_repl = server._build_repl
    clients: list[_FixtureChatClient] = []
    target_sessions = []

    def build_repl(*args, **kwargs):
        repl = original_build_repl(*args, **kwargs)
        fixture = _FixtureChatClient()
        fixture.set_mode("interrupt")
        fixture.set_cancel_event(repl.agent.cancel_event)
        repl.agent.llm = fixture
        target = create_new(paths, conversation_id="20261101-bound-target")
        target_sessions.append(target)
        clients.append(fixture)
        bridge = args[2] if len(args) > 2 else kwargs["bridge"]
        emit = bridge.emit

        def emit_and_switch(event):
            emit(event)
            if event.get("type") == "assistant.done":
                repl.session = target
                repl._rebind_agent()

        bridge.emit = emit_and_switch
        return repl

    monkeypatch.setattr(server, "_build_repl", build_repl)

    async def exercise():
        async with serve(handler.handle, "127.0.0.1", 0, max_size=65536) as listener:
            port = listener.sockets[0].getsockname()[1]
            async with websockets.connect(f"ws://127.0.0.1:{port}", open_timeout=5, close_timeout=5) as ws:
                session_id = None
                while session_id is None:
                    event = json.loads(await asyncio.wait_for(ws.recv(), 5))
                    if event.get("type") == "session.banner":
                        session_id = event["session_id"]
                await ws.send(json.dumps({"type": "user.message", "text": "bound old session request"}))
                seen_delta = False
                while True:
                    event = json.loads(await asyncio.wait_for(ws.recv(), 8))
                    if event.get("type") == "assistant.delta":
                        seen_delta = True
                        await ws.send(json.dumps({"type": "execution.stop", "run_id": event["run_id"]}))
                    if event.get("type") == "turn.end":
                        assert event.get("finish_reason") == "cancelled"
                        break
                assert seen_delta
        return session_id

    session_id = asyncio.run(exercise())
    old_rows = _history_rows(paths.data / "sessions" / session_id)
    target_rows = _history_rows(target_sessions[0].session_dir)
    assert {"role": "user", "content": "bound old session request"} in old_rows
    old_streams = [row for row in old_rows if row.get("content") == "assistant partial exact raw text"]
    assert len(old_streams) == 1 and _is_interrupted_stream(old_streams[0])
    assert not any(row.get("content") in {"bound old session request", "assistant partial exact raw text"}
                   for row in target_rows)

def test_persist_false_keeps_journal_until_session_save_confirms_durability(tmp_path, monkeypatch):
    root = tmp_path / "isolated-deferred-agent"
    root.mkdir()
    (root / "evolve").mkdir()
    (root / "evolve" / "_index.toml").write_text("# isolated\n", encoding="utf-8")
    (root / "workspace").mkdir()
    monkeypatch.setenv("MY_AGENT_ROOT", str(root))
    monkeypatch.chdir(root)
    paths = AgentPaths.discover()
    handler = WsSessionHandler(paths)
    original_build = server._build_repl
    clients = []
    observed_journal_before_save = []

    def build_repl(*args, **kwargs):
        repl = original_build(*args, **kwargs)
        fixture = _FixtureChatClient()
        fixture.set_cancel_event(repl.agent.cancel_event)
        repl.agent.llm = fixture
        clients.append(fixture)
        def handle_line(_line):
            response = fixture.chat([], stream=repl.agent.stream_handlers)
            repl.session.append_message({"role": "assistant", "content": response.content}, persist=False)
            journal_dir = repl.session.session_dir / ".stream-journal"
            observed_journal_before_save.append(bool(list(journal_dir.glob("*.jsonl"))))
            repl.last_turn_finish_reason = "stop"
        repl.handle_line = handle_line
        return repl
    monkeypatch.setattr(server, "_build_repl", build_repl)

    async def exercise():
        async with serve(handler.handle, "127.0.0.1", 0, max_size=65536) as listener:
            port = listener.sockets[0].getsockname()[1]
            async with websockets.connect(f"ws://127.0.0.1:{port}", open_timeout=5) as ws:
                session_id = None
                while session_id is None:
                    event = json.loads(await asyncio.wait_for(ws.recv(), 5))
                    if event.get("type") == "session.banner":
                        session_id = event["session_id"]
                await ws.send(json.dumps({"type": "user.message", "text": "persist false request"}))
                while True:
                    event = json.loads(await asyncio.wait_for(ws.recv(), 8))
                    if event.get("type") == "turn.end":
                        assert event.get("finish_reason") == "stop"
                        return session_id

    session_id = asyncio.run(exercise())
    assert observed_journal_before_save == [True]
    rows = _history_rows(paths.data / "sessions" / session_id)
    saved = [row for row in rows if row.get("role") == "assistant"
             and row.get("content") == "assistant completed exact raw text"]
    assert len(saved) == 1
    assert isinstance(saved[0].get("_host_internal", {}).get("stream_request_id"), str)
    assert not list((paths.data / "sessions" / session_id / ".stream-journal").glob("*.jsonl"))

def test_journal_io_failure_never_emits_unpersisted_delta(tmp_path, monkeypatch):
    import stream_journal
    root = tmp_path / "isolated-journal-io-agent"
    root.mkdir()
    (root / "evolve").mkdir()
    (root / "evolve" / "_index.toml").write_text("# isolated\n", encoding="utf-8")
    (root / "workspace").mkdir()
    monkeypatch.setenv("MY_AGENT_ROOT", str(root))
    monkeypatch.chdir(root)
    paths = AgentPaths.discover()
    handler = WsSessionHandler(paths)
    original_build = server._build_repl
    clients = []
    def build_repl(*args, **kwargs):
        repl = original_build(*args, **kwargs)
        fixture = _FixtureChatClient()
        fixture.set_cancel_event(repl.agent.cancel_event)
        repl.agent.llm = fixture
        clients.append(fixture)
        return repl
    def fail_append(_journal, _text):
        raise OSError("injected append failure")
    monkeypatch.setattr(server, "_build_repl", build_repl)
    monkeypatch.setattr(stream_journal.StreamJournal, "append", fail_append)

    async def exercise():
        async with serve(handler.handle, "127.0.0.1", 0, max_size=65536) as listener:
            port = listener.sockets[0].getsockname()[1]
            async with websockets.connect(f"ws://127.0.0.1:{port}", open_timeout=5) as ws:
                session_id = None
                while session_id is None:
                    event = json.loads(await asyncio.wait_for(ws.recv(), 5))
                    if event.get("type") == "session.banner":
                        session_id = event["session_id"]
                await ws.send(json.dumps({"type": "user.message", "text": "journal io fail-closed"}))
                deltas = []
                while True:
                    event = json.loads(await asyncio.wait_for(ws.recv(), 8))
                    if event.get("type") == "assistant.delta":
                        deltas.append(event.get("text"))
                    if event.get("type") == "turn.end":
                        return session_id, deltas
    session_id, deltas = asyncio.run(exercise())
    assert deltas == []
    rows = _history_rows(paths.data / "sessions" / session_id)
    assert not any(row.get("content") == "assistant completed exact raw text" for row in rows)


def test_pre_delta_failure_and_cancel_persist_empty_terminal_outcomes(tmp_path, monkeypatch):
    root = tmp_path / "isolated-pre-delta-agent"
    root.mkdir()
    (root / "evolve").mkdir()
    (root / "evolve" / "_index.toml").write_text("# isolated fixture\n", encoding="utf-8")
    (root / "workspace").mkdir()
    monkeypatch.setenv("MY_AGENT_ROOT", str(root))
    monkeypatch.chdir(root)
    paths = AgentPaths.discover()
    handler = WsSessionHandler(paths)
    original_build = server._build_repl
    clients: list[_FixtureChatClient] = []
    monkeypatch.setattr(agent_module, "time", SimpleNamespace(sleep=lambda _seconds: None))

    def build_repl(*args, **kwargs):
        repl = original_build(*args, **kwargs)
        fixture = _FixtureChatClient()
        fixture.set_cancel_event(repl.agent.cancel_event)
        repl.agent.llm = fixture
        clients.append(fixture)
        return repl

    monkeypatch.setattr(server, "_build_repl", build_repl)

    async def exercise():
        async with serve(handler.handle, "127.0.0.1", 0, max_size=65536) as listener:
            port = listener.sockets[0].getsockname()[1]
            async with websockets.connect(f"ws://127.0.0.1:{port}", open_timeout=5) as ws:
                session_id = None
                while session_id is None:
                    event = json.loads(await asyncio.wait_for(ws.recv(), 5))
                    if event.get("type") == "session.banner":
                        session_id = event["session_id"]

                async def send_no_delta(text: str, mode: str, *, cancel: bool = False):
                    clients[-1].set_mode(mode)
                    await ws.send(json.dumps({"type": "user.message", "text": text}))
                    if cancel:
                        run_id = None
                        while run_id is None:
                            event = json.loads(await asyncio.wait_for(ws.recv(), 5))
                            if event.get("type") == "execution.state" and event.get("state") == "running":
                                run_id = event.get("run_id")
                        assert await asyncio.to_thread(clients[-1].started.wait, 5)
                        await ws.send(json.dumps({"type": "execution.stop", "run_id": run_id}))
                    deltas = []
                    while True:
                        event = json.loads(await asyncio.wait_for(ws.recv(), 10))
                        if event.get("type") == "assistant.delta":
                            deltas.append(event.get("text", ""))
                        if event.get("type") == "turn.end":
                            return deltas, event

                failed_deltas, failed = await send_no_delta(
                    "accepted pre-delta provider failure", "pre_delta_failure",
                )
                assert failed_deltas == []
                assert failed["finish_reason"] == "provider_error"
                cancelled_deltas, cancelled = await send_no_delta(
                    "accepted pre-delta cancellation", "pre_delta_cancel", cancel=True,
                )
                assert cancelled_deltas == []
                assert cancelled["finish_reason"] == "cancelled"
                return session_id

    session_id = asyncio.run(exercise())
    rows = _history_rows(paths.data / "sessions" / session_id)
    expected = {
        "accepted pre-delta provider failure": "provider_error",
        "accepted pre-delta cancellation": "cancelled",
    }
    for user_text, finish_reason in expected.items():
        after_user = _after_user(rows, user_text)
        markers = [row for row in after_user
                   if row.get("role") == "assistant"
                   and row.get("_host_internal", {}).get("turn_outcome") == finish_reason]
        assert len(markers) == 1, f"missing one durable terminal outcome for {user_text!r}: {after_user!r}"
        assert markers[0].get("content") == "", "terminal marker must not invent assistant content"
        assert markers[0]["_host_internal"].get("stream_incomplete") is True
        assert "private" not in json.dumps(after_user).lower()

    restarted = WsSessionHandler(paths)
    async def read_restarted_history():
        async with serve(restarted.handle, "127.0.0.1", 0, max_size=65536) as listener:
            port = listener.sockets[0].getsockname()[1]
            async with websockets.connect(f"ws://127.0.0.1:{port}", open_timeout=5) as ws:
                while True:
                    event = json.loads(await asyncio.wait_for(ws.recv(), 5))
                    if event.get("type") == "session.history":
                        return event
                    if event.get("type") == "session.banner":
                        await ws.send(json.dumps({"type": "session.switch", "session_id": session_id}))

    history = asyncio.run(read_restarted_history())
    assert history["session_id"] == session_id
    assert all({"role": "user", "content": text} in rows for text in expected)
    recovered = load_session_for_harness(paths, session_id, expected="desktop", full_messages=True)
    for finish_reason in expected.values():
        recovered_markers = [row for row in recovered.messages
                             if row.get("_host_internal", {}).get("turn_outcome") == finish_reason]
        assert len(recovered_markers) == 1
