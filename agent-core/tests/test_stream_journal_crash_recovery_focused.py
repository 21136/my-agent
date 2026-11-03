from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import websockets


CORE = Path(__file__).resolve().parents[1]
PYTHON = Path(r"F:\my-agent-main\.venv\Scripts\python.exe")


def test_killed_sidecar_recovers_visible_stream_fragment_in_original_session(tmp_path):
    root = tmp_path / "agent"
    root.mkdir()
    (root / "evolve").mkdir()
    (root / "evolve" / "_index.toml").write_text("# isolated\n", encoding="utf-8")
    (root / "workspace").mkdir()
    env = os.environ.copy()
    env["MY_AGENT_ROOT"] = str(root)
    env["PYTHONPATH"] = str(CORE) + os.pathsep + env.get("PYTHONPATH", "")
    producer = r'''
import asyncio, json, threading
from types import SimpleNamespace
import server
from paths import AgentPaths
from server import WsSessionHandler
from llm_client import LLMResponse
from websockets.asyncio.server import serve

class CrashClient:
    def __init__(self): self.cancel_event = threading.Event()
    def set_cancel_event(self, event): self.cancel_event = event
    def chat(self, _messages, *, stream=None, **_kwargs):
        text = "visible-before-crash-unique-fragment"
        stream.on_content_delta(text)
        self.cancel_event.wait(120)
        return LLMResponse("fixture", text, [], "stop", None, {})

async def main():
    paths = AgentPaths.from_root(__import__('pathlib').Path(__import__('os').environ['MY_AGENT_ROOT']))
    handler = WsSessionHandler(paths)
    build = server._build_repl
    def build_fixture(*args, **kwargs):
        repl = build(*args, **kwargs)
        client = CrashClient()
        client.set_cancel_event(repl.agent.cancel_event)
        repl.agent.llm = client
        return repl
    server._build_repl = build_fixture
    async with serve(handler.handle, "127.0.0.1", 0, max_size=65536) as listener:
        print(json.dumps({"port": listener.sockets[0].getsockname()[1]}), flush=True)
        await asyncio.Future()
asyncio.run(main())
'''
    child = subprocess.Popen(
        [str(PYTHON), "-c", producer], cwd=root, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        assert child.stdout is not None
        ready = json.loads(child.stdout.readline())
        port = ready["port"]

        reader = r'''
import json, os
from pathlib import Path
from paths import AgentPaths
from session import resume_desktop_or_create, session_history_event
paths = AgentPaths.from_root(Path(os.environ['MY_AGENT_ROOT']))
s = resume_desktop_or_create(paths)
print(json.dumps(session_history_event(s), ensure_ascii=False))
'''
        async def capture_delta_and_read_live():
            async with websockets.connect(f"ws://127.0.0.1:{port}", open_timeout=5) as ws:
                session_id = None
                await ws.send(json.dumps({"type": "user.message", "text": "crash recovery request identity"}))
                while True:
                    event = json.loads(await asyncio.wait_for(ws.recv(), 10))
                    if event.get("type") == "session.banner":
                        session_id = event["session_id"]
                    if event.get("type") == "assistant.delta":
                        env["TARGET_SESSION"] = session_id
                        live = subprocess.run(
                            [str(PYTHON), "-c", reader], cwd=root, env=env,
                            capture_output=True, text=True, timeout=15, check=True,
                        )
                        return session_id, event["text"], json.loads(live.stdout.strip())
        session_id, visible, live_history = asyncio.run(capture_delta_and_read_live())
        assert visible == "visible-before-crash-unique-fragment"
        assert not any(visible in item.get("text", "") for item in live_history["items"])
        child.kill()
        child.wait(timeout=10)

        recovered = subprocess.run(
            [str(PYTHON), "-c", reader], cwd=root, env=env,
            capture_output=True, text=True, timeout=15, check=True,
        )
        history = json.loads(recovered.stdout.strip())
        fragments = [item for item in history["items"]
                     if item.get("role") == "assistant" and visible in item.get("text", "")]
        assert len(fragments) == 1, history
        assert "回复中断" in fragments[0]["text"]
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=10)


def _fresh_paths(tmp_path):
    from paths import AgentPaths
    root = tmp_path / "agent"
    root.mkdir()
    (root / "evolve").mkdir()
    (root / "evolve" / "_index.toml").write_text("# isolated\n", encoding="utf-8")
    (root / "workspace").mkdir()
    return AgentPaths.from_root(root)


def test_final_message_identity_recovers_crash_before_settlement_without_duplicate(tmp_path):
    from session import Session, create_new
    from stream_journal import StreamJournal
    paths = _fresh_paths(tmp_path)
    session = create_new(paths, conversation_id="20261101-final-window")
    session.save()
    journal = StreamJournal(session.session_dir, session.conversation_id)
    text = "same text from this exact completed request"
    journal.append(text)
    session.append_message_durable({
        "role": "assistant", "content": text,
        "_host_internal": {"stream_request_id": journal.request_id,
                           "stream_run_id": journal.run_id},
    })
    # Simulate process death after final message reached disk but before journal settlement.
    journal.release_lease()
    restored = Session.load(paths, session.conversation_id)
    rows = [row for row in restored.messages if row.get("role") == "assistant" and row.get("content") == text]
    assert len(rows) == 1
    assert not list((session.session_dir / ".stream-journal").glob("*.jsonl"))


def test_torn_journal_fails_closed_and_keeps_damaged_bytes(tmp_path):
    import pytest
    from session import Session, SessionError, create_new
    from stream_journal import StreamJournal
    paths = _fresh_paths(tmp_path)
    session = create_new(paths, conversation_id="20261101-torn-stream")
    session.save()
    journal = StreamJournal(session.session_dir, session.conversation_id)
    journal.append("durable prefix")
    journal.release_lease()
    with journal.path.open("ab") as handle:
        handle.write(b'{"kind":"delta"')
        handle.flush()
        os.fsync(handle.fileno())
    damaged = journal.path.read_bytes()
    with pytest.raises(SessionError, match="stream journal recovery failed closed"):
        Session.load(paths, session.conversation_id)
    assert journal.path.exists()
    assert journal.path.read_bytes() == damaged
    assert not any(row.get("role") == "assistant" for row in session.messages)


def test_two_concurrent_same_text_requests_recover_as_distinct_fragments(tmp_path):
    import threading
    from session import Session, create_new
    from stream_journal import StreamJournal
    paths = _fresh_paths(tmp_path)
    session = create_new(paths, conversation_id="20261101-concurrent-streams")
    session.save()
    journals = [StreamJournal(session.session_dir, session.conversation_id) for _ in range(2)]
    barrier = threading.Barrier(3)
    def write(journal):
        barrier.wait()
        journal.append("identical text is distinct across request identities")
        journal.release_lease()
    threads = [threading.Thread(target=write, args=(journal,)) for journal in journals]
    for thread in threads: thread.start()
    barrier.wait()
    for thread in threads: thread.join(timeout=5)
    restored = Session.load(paths, session.conversation_id)
    matches = [row for row in restored.messages if row.get("role") == "assistant"
               and row.get("content") == "identical text is distinct across request identities"]
    ids = {row["_host_internal"]["stream_request_id"] for row in matches}
    assert len(matches) == 2 and ids == {journal.request_id for journal in journals}


def test_interrupted_recovery_draft_is_not_sent_as_complete_model_history(tmp_path):
    from context import build_llm_messages
    from session import create_new, resume_desktop_or_create
    from stream_journal import StreamJournal
    paths = _fresh_paths(tmp_path)
    session = create_new(paths, conversation_id="20261101-model-filter")
    session.save()
    journal = StreamJournal(session.session_dir, session.conversation_id)
    journal.append("must not become complete model context")
    journal.release_lease()
    restored = resume_desktop_or_create(paths)
    model_messages = build_llm_messages(restored)
    assert not any("must not become complete model context" in str(message.get("content", ""))
                   for message in model_messages)


def test_journal_rejects_settled_then_delta_and_preserves_file(tmp_path):
    import pytest
    from session import Session, SessionError, create_new
    from stream_journal import StreamJournal, _sync_line
    paths = _fresh_paths(tmp_path)
    session = create_new(paths, conversation_id="20261101-settled-tail")
    session.save()
    journal = StreamJournal(session.session_dir, session.conversation_id)
    journal.append("prefix")
    journal.release_lease()
    with journal.path.open("a", encoding="utf-8", newline="\n") as handle:
        _sync_line(handle, {"kind": "settled", "session_id": session.conversation_id,
                            "request_id": journal.request_id, "run_id": journal.run_id})
        _sync_line(handle, {"kind": "delta", "session_id": session.conversation_id,
                            "request_id": journal.request_id, "run_id": journal.run_id, "text": "after"})
    before = journal.path.read_bytes()
    with pytest.raises(SessionError, match="stream journal recovery failed closed"):
        Session.load(paths, session.conversation_id)
    assert journal.path.read_bytes() == before


def test_streamed_tool_preamble_remains_in_hydrated_history():
    from session import _build_chat_history_from_messages
    messages = [{"role": "assistant", "content": "visible tool preamble",
                 "tool_calls": [{"id": "call-1"}],
                 "_host_internal": {"stream_request_id": "0123456789abcdef0123456789abcdef"}}]
    items, total = _build_chat_history_from_messages(messages)
    assert total == 1
    assert items == [{"role": "assistant", "text": "visible tool preamble"}]

def test_stream_journal_enforces_uuid_record_and_total_bounds(tmp_path, monkeypatch):
    import pytest
    import stream_journal as journal_module
    from stream_journal import StreamJournal
    paths = _fresh_paths(tmp_path)
    session_dir = paths.data / "sessions" / "bound-tests"
    session_dir.mkdir(parents=True)
    with pytest.raises(ValueError, match="UUIDv4"):
        StreamJournal(session_dir, "bound-tests", request_id="same-text-not-uuid")
    journal = StreamJournal(session_dir, "bound-tests")
    before = journal.path.read_bytes()
    monkeypatch.setattr(journal_module, "MAX_RECORD_BYTES", len(before) + 32)
    with pytest.raises(ValueError, match="record exceeds size limit"):
        journal.append("x" * 512)
    assert journal.path.read_bytes() == before
    journal.release_lease()


def test_symlink_journal_is_rejected_without_touching_target(tmp_path):
    import os
    import pytest
    from stream_journal import pending_journals
    paths = _fresh_paths(tmp_path)
    session_dir = paths.data / "sessions" / "symlink-bound"
    session_dir.mkdir(parents=True)
    journal_dir = session_dir / ".stream-journal"
    journal_dir.mkdir()
    outside = tmp_path / "outside.jsonl"
    outside.write_text("must remain unchanged", encoding="utf-8")
    link = journal_dir / "1234567890abcdef1234567890abcdef.jsonl"
    try:
        os.symlink(outside, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is not available in this Windows environment")
    with pytest.raises(ValueError, match="symlink|reparse"):
        pending_journals(session_dir, "symlink-bound")
    assert outside.read_text(encoding="utf-8") == "must remain unchanged"
    assert link.is_symlink()

def test_stream_append_does_not_hash_or_rewrite_long_session_history(tmp_path, monkeypatch):
    import session as session_module
    from session import create_new
    from stream_journal import StreamJournal
    paths = _fresh_paths(tmp_path)
    session = create_new(paths, conversation_id="20261101-long-history")
    session.messages = [{"role": "user", "content": f"historical row {index}"} for index in range(3000)]
    session.save()
    loaded = session_module.Session.load(paths, session.conversation_id)
    before = loaded.messages_path.read_bytes()
    calls = []
    original = session_module._messages_version
    def counted(path):
        calls.append(path)
        return original(path)
    monkeypatch.setattr(session_module, "_messages_version", counted)
    journal = StreamJournal(loaded.session_dir, loaded.conversation_id)
    for index in range(20):
        journal.append(f"delta-{index};")
    assert calls == []
    assert loaded.messages_path.read_bytes() == before
    journal.release_lease()

def test_stream_journal_record_count_limit_is_fail_closed(tmp_path, monkeypatch):
    import pytest
    import stream_journal as journal_module
    from stream_journal import StreamJournal
    paths = _fresh_paths(tmp_path)
    session_dir = paths.data / "sessions" / "record-count-bound"
    session_dir.mkdir(parents=True)
    journal = StreamJournal(session_dir, "record-count-bound")
    before = journal.path.read_bytes()
    monkeypatch.setattr(journal_module, "MAX_RECORD_COUNT", 1)
    with pytest.raises(ValueError, match="limit exceeded"):
        journal.append("must not append")
    assert journal.path.read_bytes() == before
    journal.release_lease()

def test_session_load_rejects_traversal_without_touching_outside_journal(tmp_path):
    import pytest
    from session import Session, SessionError, create_new
    from stream_journal import StreamJournal
    paths = _fresh_paths(tmp_path)
    create_new(paths, conversation_id="safe-control").save()
    outside = paths.data / "victim"
    outside.mkdir()
    (outside / "messages.jsonl").write_text('{"role":"user","content":"outside"}\n', encoding="utf-8")
    journal = StreamJournal(outside, "../victim")
    journal.append("must not be restored outside sessions root")
    journal.release_lease()
    before_messages = (outside / "messages.jsonl").read_bytes()
    before_journal = journal.path.read_bytes()
    with pytest.raises(SessionError):
        Session.load(paths, "../victim")
    assert (outside / "messages.jsonl").read_bytes() == before_messages
    assert journal.path.exists() and journal.path.read_bytes() == before_journal


def test_request_identity_on_non_assistant_row_fails_closed_and_keeps_journal(tmp_path):
    import pytest
    import uuid
    from session import Session, SessionError, create_new
    from stream_journal import StreamJournal
    paths = _fresh_paths(tmp_path)
    session = create_new(paths, conversation_id="20261101-role-conflict")
    request_id = uuid.uuid4().hex
    session.append_message({"role": "user", "content": "same fragment",
                           "_host_internal": {"stream_request_id": request_id}})
    journal = StreamJournal(session.session_dir, session.conversation_id, request_id=request_id)
    journal.append("same fragment")
    journal.release_lease()
    before = journal.path.read_bytes()
    with pytest.raises(SessionError, match="stream journal recovery failed closed"):
        Session.load(paths, session.conversation_id)
    assert journal.path.exists() and journal.path.read_bytes() == before


def test_local_journal_lease_registry_does_not_grow_with_completed_requests(tmp_path):
    import gc
    import stream_journal as journal_module
    from stream_journal import StreamJournal
    paths = _fresh_paths(tmp_path)
    session_dir = paths.data / "sessions" / "lease-registry"
    session_dir.mkdir(parents=True)
    for _ in range(300):
        journal = StreamJournal(session_dir, "lease-registry")
        journal.release_lease()
        journal.path.unlink()
        del journal
    gc.collect()
    assert len(journal_module._local_lease_locks) <= 1

def test_recovery_refuses_hardlinked_messages_file_outside_session(tmp_path):
    import os
    import pytest
    from session import Session, SessionError, create_new
    from stream_journal import StreamJournal
    paths = _fresh_paths(tmp_path)
    session = create_new(paths, conversation_id="20261101-hardlink-messages")
    session.append_message({"role": "user", "content": "isolated hardlink target"})
    session.save()
    external = tmp_path / "external-transcript.jsonl"
    external.write_bytes(session.messages_path.read_bytes())
    session.messages_path.unlink()
    try:
        os.link(external, session.messages_path)
    except OSError as exc:
        pytest.skip(f"hardlink creation unavailable in test filesystem: {exc}")
    assert os.stat(external).st_ino == os.stat(session.messages_path).st_ino
    assert os.stat(session.messages_path).st_nlink >= 2
    journal = StreamJournal(session.session_dir, session.conversation_id)
    journal.append("must never be written through a hardlink")
    journal.release_lease()
    before = external.read_bytes()
    journal_before = journal.path.read_bytes()
    with pytest.raises(SessionError, match="stream journal recovery failed closed"):
        Session.load(paths, session.conversation_id)
    assert external.read_bytes() == before
    assert journal.path.exists() and journal.path.read_bytes() == journal_before

def test_recovery_detects_messages_path_swap_after_open_before_append(tmp_path, monkeypatch):
    import os
    import pytest
    import session as session_module
    from session import Session, SessionError, create_new
    from stream_journal import StreamJournal
    paths = _fresh_paths(tmp_path)
    session = create_new(paths, conversation_id="20261101-open-race")
    session.append_message({"role": "user", "content": "session-owned only"})
    session.save()
    external = tmp_path / "external-race-target.jsonl"
    external.write_text('{"role":"user","content":"external unchanged"}\n', encoding="utf-8")
    journal = StreamJournal(session.session_dir, session.conversation_id)
    journal.append("must not follow a swapped path")
    journal.release_lease()
    before_external = external.read_bytes()
    before_journal = journal.path.read_bytes()
    original_append = session_module._append_jsonl_record_to_open_fd
    def swap_then_append(fd, record, verify_identity):
        try:
            session.messages_path.unlink()
        except OSError:
            # Windows handle sharing may correctly prevent replacement while pinned.
            raise
        os.link(external, session.messages_path)
        return original_append(fd, record, verify_identity)
    monkeypatch.setattr(session_module, "_append_jsonl_record_to_open_fd", swap_then_append)
    with pytest.raises(SessionError):
        Session.load(paths, session.conversation_id)
    assert external.read_bytes() == before_external
    assert journal.path.exists() and journal.path.read_bytes() == before_journal
