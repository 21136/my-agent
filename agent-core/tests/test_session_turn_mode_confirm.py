from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import server
from paths import AgentPaths
from session import create_new


class _Bridge:
    def __init__(self) -> None:
        self.events: list[dict] = []
        self._turn_busy = __import__("threading").Event()

    def emit(self, event: dict) -> None:
        self.events.append(event)

    def emit_session_state(self, session) -> None:
        self.emit({"type": "session.banner", "session_id": session.conversation_id, "turn_mode": session.meta.turn_mode})


def test_set_turn_mode_requires_confirm_before_apply(tmp_path: Path) -> None:
    paths = AgentPaths(tmp_path, tmp_path / "workspace", tmp_path / "data", tmp_path / "evolve")
    session = create_new(paths)
    session.set_turn_mode("ask")
    session.save()
    exec_session = SimpleNamespace(turn_mode="ask")
    repl = SimpleNamespace(
        session=session,
        agent=SimpleNamespace(executor=SimpleNamespace(session=exec_session)),
    )
    handler = server.WsSessionHandler(paths)
    bridge = _Bridge()

    async def first() -> None:
        await handler._handle_incoming({"type": "session.set_turn_mode", "mode": "agent"}, repl, bridge)

    asyncio.run(first())
    req = next(e for e in bridge.events if e.get("type") == "session.turn_mode.request")
    assert req["to_mode"] == "agent"
    assert session.meta.turn_mode == "ask"

    bridge.events.clear()

    async def second() -> None:
        await handler._handle_incoming(
            {
                "type": "session.set_turn_mode",
                "mode": "agent",
                "confirm": True,
                "request_id": req["request_id"],
            },
            repl,
            bridge,
        )

    asyncio.run(second())
    assert session.meta.turn_mode == "agent"
    assert exec_session.turn_mode == "agent"
    assert any(e.get("type") == "session.turn_mode.done" for e in bridge.events)
