import asyncio
from pathlib import Path

import server
from paths import AgentPaths

ENABLE = "\u5f00\u542f\u9879\u76ee\u65e5\u8bb0\u8bb0\u5f55"
DISABLE = "\u5173\u95ed\u9879\u76ee\u65e5\u8bb0\u8bb0\u5f55"


class FakeBridge(server.WsBridge):
    def __init__(self, answer):
        self.answer = answer
        self.events = []
        paths = AgentPaths.from_root(Path(__file__).resolve().parents[2])
        super().__init__(emit=self.events.append, paths=paths)

    def confirm_with_receipt(self, preview, allow):
        self.events.append({"type": "confirm.request", "preview": preview, "allow": allow})
        return self.answer, "confirmation-id"


class FakeHandler(server.WsSessionHandler):
    def __init__(self, recorder, journal):
        paths = AgentPaths.from_root(Path(__file__).resolve().parents[2])
        super().__init__(paths, project_boundary_recorder=recorder, project_boundary_journal=journal)


def _stub_session():
    paths = AgentPaths.from_root(Path(__file__).resolve().parents[2])
    return type("Session", (), {"conversation_id": "synthetic-session", "paths": paths})()


async def command(handler, text, bridge):
    return await handler._handle_project_journal_command(text, bridge)


def test_exact_enable_phrase_requires_confirmation_and_is_connection_scoped():
    handler = FakeHandler(lambda *a, **kw: None, object())
    first, second = FakeBridge("yes"), FakeBridge("yes")

    asyncio.run(command(handler, ENABLE, first))
    asyncio.run(command(handler, ENABLE + ".", second))

    assert first.project_journal_enabled is True
    assert second.project_journal_enabled is False
    preview = next(event["preview"] for event in first.events if event.get("type") == "confirm.request")
    for phrase in ("\u4ec5\u5728\u4e4b\u540e\u6210\u529f\u5207\u6362", "\u4e0d\u4f1a\u53d1\u9001\u7ed9\u6a21\u578b", "\u4e0d\u4f1a\u626b\u63cf\u6216\u56de\u586b\u65e7\u5386\u53f2"):
        assert phrase in preview


def test_reject_cancel_timeout_and_close_leave_connection_disabled():
    handler = FakeHandler(lambda *a, **kw: None, object())
    for answer in ("n", "cancelled", "timeout", "stale"):
        bridge = FakeBridge(answer)
        asyncio.run(command(handler, ENABLE, bridge))
        assert bridge.project_journal_enabled is False

    bridge = FakeBridge("yes")
    asyncio.run(command(handler, ENABLE, bridge))
    asyncio.run(command(handler, DISABLE, bridge))
    assert bridge.project_journal_enabled is False


def test_project_switch_writes_on_success_without_connection_opt_in():
    calls = []
    recorder = lambda *a, **kw: calls.append((a, kw)) or {}
    handler = FakeHandler(recorder, object())
    event = {"type": "project.switch.done", "request_id": "evt-1", "action": "switch"}

    assert not handler._record_project_switch_for_connection(None, _stub_session(), event)
    bridge = FakeBridge("yes")
    assert handler._record_project_switch_for_connection(bridge, _stub_session(), event)
    assert len(calls) == 1
    assert not handler._record_project_switch_for_connection(bridge, _stub_session(), {**event, "action": "noop"})
    assert len(calls) == 1


def test_confirmation_exception_fails_closed_with_clear_notice():
    handler = FakeHandler(None, None)
    bridge = FakeBridge("yes")
    bridge.confirm_with_receipt = lambda *_: (_ for _ in ()).throw(RuntimeError("boom"))
    asyncio.run(command(handler, ENABLE, bridge))
    assert bridge.project_journal_enabled is False
    assert any("\u672a\u80fd\u5b89\u5168\u542f\u7528" in e.get("text", "") for e in bridge.events)


def test_write_failure_surfaces_notice_without_disabling_legacy_opt_in_flag():
    writes = []
    def broken_recorder(*args, **kwargs):
        writes.append((args, kwargs))
        raise OSError("synthetic disk failure")

    handler = FakeHandler(broken_recorder, object())
    bridge = FakeBridge("yes")
    asyncio.run(command(handler, ENABLE, bridge))
    assert bridge.project_journal_enabled
    notices = []
    ok = handler._record_project_switch_for_connection(
        bridge,
        _stub_session(),
        {"type": "project.switch.done", "request_id": "evt-fail", "action": "switch"},
        notices.append,
    )
    assert ok is False
    assert bridge.project_journal_enabled is True
    assert writes and notices and "\u5199\u5165\u5931\u8d25" in notices[0]


def test_command_emits_real_turn_lifecycle_and_routes_prompt_input_first():
    paths = AgentPaths.from_root(Path(__file__).resolve().parents[2])
    handler = server.WsSessionHandler(paths, project_boundary_recorder=lambda *a, **k: None, project_boundary_journal=object())
    events = []
    bridge = server.WsBridge(emit=events.append, paths=paths)
    bridge.confirm_with_receipt = lambda *_: ("n", "receipt")
    repl = type("Repl", (), {"session": type("Session", (), {"conversation_id": "synthetic-session"})()})()

    asyncio.run(handler._handle_incoming({"type": "user.message", "text": ENABLE}, repl, bridge))
    assert any(event.get("type") == "turn.end" for event in events)
    assert bridge.lifecycle.snapshot().state == "settled"
    assert any(event.get("type") == "turn.notice" for event in events)


def test_close_command_waits_for_existing_turn_lock_before_changing_consent():
    async def scenario():
        paths = AgentPaths.from_root(Path(__file__).resolve().parents[2])
        handler = server.WsSessionHandler(paths, project_boundary_recorder=lambda *a, **k: None, project_boundary_journal=object())
        bridge = server.WsBridge(emit=lambda _event: None, paths=paths)
        bridge.project_journal_enabled = True
        await server.TURN_LOCK.acquire()
        try:
            close = asyncio.create_task(handler._handle_incoming(
                {"type": "user.message", "text": DISABLE},
                type("Repl", (), {"session": type("Session", (), {"conversation_id": "synthetic-session"})()})(),
                bridge,
            ))
            await asyncio.sleep(0.02)
            assert bridge.project_journal_enabled is True
        finally:
            server.TURN_LOCK.release()
        await close
        assert bridge.project_journal_enabled is False

    original_lock = server.TURN_LOCK
    server.TURN_LOCK = asyncio.Lock()
    try:
        asyncio.run(scenario())
    finally:
        server.TURN_LOCK = original_lock


def test_enable_confirmation_waits_behind_existing_interaction_turn():
    async def scenario():
        paths = AgentPaths.from_root(Path(__file__).resolve().parents[2])
        handler = server.WsSessionHandler(paths, project_boundary_recorder=lambda *a, **k: None, project_boundary_journal=object())
        bridge = server.WsBridge(emit=lambda _event: None, paths=paths)
        confirm_calls = []
        bridge.confirm_with_receipt = lambda *args: (confirm_calls.append(args) or ("n", "receipt"))
        repl = type("Repl", (), {"session": type("Session", (), {"conversation_id": "synthetic-session"})()})()
        await server.TURN_LOCK.acquire()
        try:
            command_task = asyncio.create_task(handler._handle_incoming(
                {"type": "user.message", "text": ENABLE}, repl, bridge
            ))
            await asyncio.sleep(0.02)
            assert confirm_calls == []
        finally:
            server.TURN_LOCK.release()
        await command_task
        assert len(confirm_calls) == 1

    original_lock = server.TURN_LOCK
    server.TURN_LOCK = asyncio.Lock()
    try:
        asyncio.run(scenario())
    finally:
        server.TURN_LOCK = original_lock
