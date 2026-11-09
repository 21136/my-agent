"""Companion multi-project orchestration: no spurious session-switch lock; confirms surface on parent WS."""
from __future__ import annotations

import asyncio
import threading
import time
from project_mode import create_project
from server import WsBridge, WsSessionHandler
from session import Session, create_new
from tests.isolation_helpers import temporary_agent_paths


def _session(paths, name: str = "orch-parent") -> Session:
    return create_new(paths, conversation_id=name)


class _ExSession:
    def refresh_bound_project_meta(self) -> None:
        return None


def _minimal_repl(session: Session):
    executor_session = _ExSession()
    executor = type("Executor", (), {"session": executor_session})()
    agent = type("Agent", (), {"executor": executor})()
    return type(
        "Repl",
        (),
        {
            "session": session,
            "agent": agent,
            "_rebind_agent": lambda *_args, **_kwargs: None,
        },
    )()


def test_project_open_register_only_during_turn_skips_session_binding_lock(monkeypatch):
    import server

    with temporary_agent_paths() as paths:
        session = _session(paths)
        pid = "orch-open-a"
        create_project(paths, pid)
        repl = _minimal_repl(session)
        events: list[dict] = []
        bridge = WsBridge(emit=events.append, paths=paths)
        bridge._session_provider = lambda: session
        bridge.lifecycle_conversation_id = session.conversation_id
        bridge.lifecycle.start_or_activate("ordinary")
        bridge._turn_busy.set()
        handler = WsSessionHandler(paths)
        monkeypatch.setattr(server, "TURN_LOCK", asyncio.Lock())

        async def scenario():
            await server.TURN_LOCK.acquire()
            try:
                await handler._handle_incoming(
                    {"type": "project.open", "project_id": pid},
                    repl,
                    bridge,
                )
            finally:
                server.TURN_LOCK.release()

        asyncio.run(scenario())

        assert not any(
            event.get("type") == "error"
            and "session binding is busy" in str(event.get("message", "")).casefold()
            for event in events
        )
        assert any(event.get("type") == "notice" for event in events)


def test_task_authorization_confirm_emits_parent_chat_notice():
    with temporary_agent_paths() as paths:
        session = _session(paths, "20261106-orch-confirm")
        events: list[dict] = []
        bridge = WsBridge(emit=events.append, paths=paths, task_authorization_confirm_timeout=2.0)
        bridge._session_provider = lambda: session
        bridge.lifecycle_conversation_id = session.conversation_id
        bridge.lifecycle.start_or_activate("ordinary")

        result: list[str] = []

        def run():
            result.append(
                bridge.confirm_task_authorization(
                    'PROJECT_WORKER_BATCH_AUTHORIZATION_CONFIRM\n{"schema_version":1}',
                ),
            )

        worker = threading.Thread(target=run)
        worker.start()
        deadline = time.monotonic() + 1.0
        request = None
        while time.monotonic() < deadline:
            request = next((e for e in events if e.get("type") == "confirm.request"), None)
            if request is not None:
                break
            time.sleep(0.01)
        assert request is not None
        assert request["session_id"] == session.conversation_id
        assert any(
            event.get("type") == "turn.notice"
            and "小魔王空间" in (event.get("text") or "")
            for event in events
        )
        bridge.deliver_confirm(request["request_id"], "n")
        worker.join(2)
        assert result == ["n"]


def test_session_switch_busy_message_is_user_facing_chinese(monkeypatch):
    import server

    with temporary_agent_paths() as paths:
        session = _session(paths)
        new_session = _session(paths, "orch-switch-target")
        repl = _minimal_repl(session)
        events: list[dict] = []
        bridge = WsBridge(emit=events.append, paths=paths)
        monkeypatch.setattr(server, "TURN_LOCK", asyncio.Lock())
        monkeypatch.setattr(server, "SESSION_SWITCH_WAIT_SECONDS", 0.01, raising=False)
        monkeypatch.setattr(server, "load_session_for_harness", lambda *_a, **_k: new_session)
        monkeypatch.setattr(bridge, "emit_session_state", lambda _s: None)
        monkeypatch.setattr(server, "emit_corruption_notices", lambda *_a, **_k: None)
        handler = WsSessionHandler(paths)

        async def scenario():
            await server.TURN_LOCK.acquire()
            try:
                await handler._handle_incoming(
                    {"type": "session.open", "session_id": new_session.conversation_id},
                    repl,
                    bridge,
                )
            finally:
                server.TURN_LOCK.release()

        asyncio.run(scenario())
        err = next((e for e in events if e.get("type") == "error"), None)
        assert err is not None
        message = str(err.get("message", ""))
        assert "session binding is busy" not in message.casefold()
        assert "主会话" in message or "确认" in message
