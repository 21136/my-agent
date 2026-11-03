from __future__ import annotations

import asyncio
import sys
import threading
import time

import pytest
from pathlib import Path
from types import SimpleNamespace

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

import server
from paths import AgentPaths
from server import WsBridge, WsSessionHandler, _begin_work_record_capture_turn, _finish_work_record_capture_turn, _run_line
from session import Session, SessionMeta


def make_session(tmp_path: Path) -> Session:
    paths = AgentPaths(
        agent_root=tmp_path,
        workspace=tmp_path / "workspace",
        data=tmp_path / "data",
        evolve=tmp_path / "evolve",
    )
    session_dir = paths.data / "sessions" / "capture-cancel-session"
    session_dir.mkdir(parents=True)
    return Session(
        conversation_id="capture-cancel-session",
        session_dir=session_dir,
        goal="",
        meta=SessionMeta(),
        messages=[],
        paths=paths,
    )


def make_repl(session: Session, *, calls: list[str]) -> SimpleNamespace:
    agent_session = SimpleNamespace(turn_mode=session.meta.turn_mode)
    return SimpleNamespace(
        session=session,
        last_turn_finish_reason=None,
        agent=SimpleNamespace(
            bind_host_run_context=None,
            executor=SimpleNamespace(session=agent_session),
        ),
        handle_line=lambda line: calls.append(line) or "continue",
    )


def prepare_run_line_stubs(monkeypatch, session: Session):
    monkeypatch.setattr(server, "session_memory_event", lambda *_args: {"type": "session.memory"})
    monkeypatch.setattr(server, "_proposals_payload", lambda *_args: {"type": "evolve.proposals", "items": []})
    monkeypatch.setattr(server, "_memories_payload", lambda *_args: {"type": "evolve.memories", "items": []})
    import project_api
    monkeypatch.setattr(project_api, "after_turn_project_hooks", lambda *_args: None)


def wait_for_confirm(events: list[dict]) -> str:
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        request = next((event for event in events if event.get("type") == "confirm.request"), None)
        if request is not None:
            return request["request_id"]
        time.sleep(0.01)
    raise AssertionError("confirmation request was not emitted")


def test_capture_turn_cancel_skips_run_line_and_closes_busy_lifecycle(tmp_path, monkeypatch):
    session = make_session(tmp_path)
    events: list[dict] = []
    bridge = WsBridge(emit=events.append, paths=session.paths)
    calls: list[str] = []
    repl = make_repl(session, calls=calls)
    prepare_run_line_stubs(monkeypatch, session)

    assert _begin_work_record_capture_turn(repl, bridge)
    run_id = bridge.lifecycle.snapshot().run_id
    assert bridge._turn_busy.is_set()
    confirmed: list[tuple[str, str]] = []
    waiting = threading.Thread(
        target=lambda: confirmed.append(bridge.confirm_with_receipt("record consent", False))
    )
    waiting.start()
    wait_for_confirm(events)
    assert bridge.request_cancel()
    waiting.join(timeout=2)
    assert not waiting.is_alive()
    assert confirmed[0][0] == "n"
    assert any(event.get("type") == "confirm.done" and event.get("choice") == "cancelled" for event in events)

    asyncio.run(_run_line(repl, bridge, "user request", session.paths, prestarted_run_id=run_id))

    assert calls == []
    assert bridge._turn_busy.is_set() is False
    assert bridge.lifecycle.snapshot().state == "settled"
    assert bridge.lifecycle.snapshot().finish_reason == "cancelled"
    assert any(event.get("type") == "turn.end" and event.get("finish_reason") == "cancelled" for event in events)
    assert all(event.get("run_id") == run_id for event in events if event.get("type") == "turn.end")


def test_previous_cancel_is_cleared_before_capture_and_refusal_can_run_normally(tmp_path, monkeypatch):
    session = make_session(tmp_path)
    events: list[dict] = []
    bridge = WsBridge(emit=events.append, paths=session.paths)
    bridge.cancel_event.set()  # left over from the previously cancelled turn
    calls: list[str] = []
    repl = make_repl(session, calls=calls)
    prepare_run_line_stubs(monkeypatch, session)

    assert _begin_work_record_capture_turn(repl, bridge)
    assert bridge.cancel_event.is_set() is False
    confirmed: list[tuple[str, str]] = []
    waiting = threading.Thread(
        target=lambda: confirmed.append(bridge.confirm_with_receipt("record consent", False))
    )
    waiting.start()
    request_id = wait_for_confirm(events)
    assert bridge.deliver_confirm(request_id, "n")
    waiting.join(timeout=2)
    assert not waiting.is_alive()
    assert confirmed[0] == ("n", request_id)
    assert bridge.cancel_event.is_set() is False
    run_id = bridge.lifecycle.snapshot().run_id
    asyncio.run(_run_line(repl, bridge, "user request", session.paths, prestarted_run_id=run_id))

    assert calls == ["user request"]
    assert bridge._turn_busy.is_set() is False
    assert bridge.lifecycle.snapshot().state == "settled"
    assert bridge.lifecycle.snapshot().finish_reason == "completed"


def test_invalid_turn_mode_cleanup_settles_only_the_capture_run(tmp_path):
    session = make_session(tmp_path)
    events: list[dict] = []
    bridge = WsBridge(emit=events.append, paths=session.paths)
    repl = make_repl(session, calls=[])
    assert _begin_work_record_capture_turn(repl, bridge)
    run_id = bridge.lifecycle.snapshot().run_id

    assert _finish_work_record_capture_turn(bridge, run_id, finish_reason="error")
    assert bridge.lifecycle.snapshot().run_id == run_id
    assert bridge.lifecycle.snapshot().finish_reason == "error"
    assert bridge._turn_busy.is_set() is False
    assert any(event.get("type") == "turn.end" and event.get("run_id") == run_id for event in events)


def test_exception_cleanup_does_not_finish_a_replacement_run(tmp_path):
    session = make_session(tmp_path)
    events: list[dict] = []
    bridge = WsBridge(emit=events.append, paths=session.paths)
    repl = make_repl(session, calls=[])
    assert _begin_work_record_capture_turn(repl, bridge)
    abandoned_run_id = bridge.lifecycle.snapshot().run_id
    bridge.finish_execution("error", ok=False, run_id=abandoned_run_id)
    assert bridge.begin_turn(mode="ordinary")
    replacement_run_id = bridge.lifecycle.snapshot().run_id
    bridge._turn_busy.set()

    assert not _finish_work_record_capture_turn(bridge, abandoned_run_id, finish_reason="error")
    assert bridge.lifecycle.snapshot().run_id == replacement_run_id
    assert bridge.lifecycle.snapshot().state == "running"
    assert bridge._turn_busy.is_set() is True


def test_invalid_turn_mode_after_capture_closes_prestarted_run(tmp_path, monkeypatch):
    session = make_session(tmp_path)
    events: list[dict] = []
    bridge = WsBridge(emit=events.append, paths=session.paths)
    session.meta.harness = "terminal"
    repl = make_repl(session, calls=[])
    handler = object.__new__(WsSessionHandler)
    handler.paths = session.paths
    handler._file_stage = SimpleNamespace(take=lambda *_args: [])
    handler.managed_readonly_context = None

    import work_records_service
    import llm_client

    class FakeClient:
        def __init__(self, **_kwargs): pass
        def set_cancel_event(self, _event): pass

    class FakeCaptureService:
        def __init__(self, **_kwargs): pass
        def capture(self, *_args, **_kwargs):
            return SimpleNamespace(status="not_recorded", message="not recorded", item_id=None)

    monkeypatch.setattr(work_records_service, "is_explicit_record_request", lambda _text: True)
    monkeypatch.setattr(work_records_service, "WorkRecordCaptureService", FakeCaptureService)
    monkeypatch.setattr(llm_client, "LLMClient", FakeClient)

    asyncio.run(handler._dispatch(
        {"type": "user.message", "text": "please record this", "request_id": "req-1", "turn_mode": "ask"},
        repl, bridge,
    ))
    assert any(event.get("type") == "error" for event in events)

    assert bridge._turn_busy.is_set() is False
    assert bridge.lifecycle.snapshot().state == "failed"
    assert bridge.lifecycle.snapshot().finish_reason == "error"
    assert any(event.get("type") == "turn.end" and event.get("finish_reason") == "error" for event in events)


def test_exception_after_capture_closes_prestarted_run(tmp_path, monkeypatch):
    session = make_session(tmp_path)
    events: list[dict] = []
    bridge = WsBridge(emit=events.append, paths=session.paths)
    calls: list[str] = []
    repl = make_repl(session, calls=calls)
    handler = object.__new__(WsSessionHandler)
    handler.paths = session.paths
    handler._file_stage = SimpleNamespace(take=lambda *_args: [])
    handler.managed_readonly_context = None

    import companion_world_context
    import work_records_service
    import llm_client

    class FakeClient:
        def __init__(self, **_kwargs): pass
        def set_cancel_event(self, _event): pass

    class FakeCaptureService:
        def __init__(self, **_kwargs): pass
        def capture(self, *_args, **_kwargs):
            return SimpleNamespace(status="not_recorded", message="not recorded", item_id=None)

    monkeypatch.setattr(work_records_service, "is_explicit_record_request", lambda _text: True)
    monkeypatch.setattr(work_records_service, "WorkRecordCaptureService", FakeCaptureService)
    monkeypatch.setattr(llm_client, "LLMClient", FakeClient)
    monkeypatch.setattr(companion_world_context, "is_companion_world_context_allowed", lambda **_kwargs: True)
    monkeypatch.setattr(
        companion_world_context,
        "format_companion_world_context",
        lambda _value: (_ for _ in ()).throw(RuntimeError("synthetic context failure")),
    )

    with pytest.raises(RuntimeError, match="synthetic context failure"):
        asyncio.run(handler._dispatch(
            {"type": "user.message", "text": "please record this", "request_id": "req-2"},
            repl, bridge,
        ))

    assert calls == []  # the handler failure did not retry execution
    assert bridge._turn_busy.is_set() is False
    assert bridge.lifecycle.snapshot().state == "failed"
    assert bridge.lifecycle.snapshot().finish_reason == "error"
    assert any(event.get("type") == "turn.end" and event.get("finish_reason") == "error" for event in events)


def test_outer_task_cancel_during_capture_settles_the_prestarted_run(tmp_path, monkeypatch):
    session = make_session(tmp_path)
    events: list[dict] = []
    bridge = WsBridge(emit=events.append, paths=session.paths)
    calls: list[str] = []
    repl = make_repl(session, calls=calls)
    handler = object.__new__(WsSessionHandler)
    handler.paths = session.paths
    handler._file_stage = SimpleNamespace(take=lambda *_args: [])
    handler.managed_readonly_context = None
    capture_started = threading.Event()
    capture_finished = threading.Event()
    capture_cancel_seen = threading.Event()
    side_effect_committed = threading.Event()

    import work_records_service
    import llm_client

    class FakeClient:
        def __init__(self, **_kwargs):
            self.cancel_event = None
        def set_cancel_event(self, event):
            self.cancel_event = event

    class FakeCaptureService:
        def __init__(self, *, client, **_kwargs):
            self.client = client
        def capture(self, *_args, **_kwargs):
            capture_started.set()
            assert self.client.cancel_event.wait(timeout=3)
            capture_cancel_seen.set()
            # Cancellation cannot prove an already-committed local write was undone.
            side_effect_committed.set()
            capture_finished.set()
            return SimpleNamespace(status="recorded", message="capture committed", item_id="item-1")

    monkeypatch.setattr(work_records_service, "is_explicit_record_request", lambda _text: True)
    monkeypatch.setattr(work_records_service, "WorkRecordCaptureService", FakeCaptureService)
    monkeypatch.setattr(llm_client, "LLMClient", FakeClient)

    async def cancel_dispatch():
        task = asyncio.create_task(handler._dispatch(
            {"type": "user.message", "text": "please record this", "request_id": "req-cancel"},
            repl, bridge,
        ))
        assert await asyncio.to_thread(capture_started.wait, 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(cancel_dispatch())

    assert capture_cancel_seen.is_set()
    assert capture_finished.is_set()
    assert side_effect_committed.is_set()
    assert calls == []
    assert bridge._turn_busy.is_set() is False
    assert bridge.lifecycle.snapshot().state == "settled"
    assert bridge.lifecycle.snapshot().finish_reason == "cancelled"
    assert any(event.get("type") == "turn.end" and event.get("finish_reason") == "cancelled" for event in events)
    assert any(event.get("type") == "turn.notice" and event.get("text") == "capture committed" for event in events)

