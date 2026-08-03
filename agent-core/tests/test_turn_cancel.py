"""Phase 15 turn cancellation protocol and cooperative LLM cancellation."""

from __future__ import annotations

import asyncio
import shutil
import sys
import threading
import time
import unittest
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from agent import Agent
from llm_client import (
    LLMCancelledError,
    LLMClient,
    LLMConfig,
    LLMResponse,
    StreamHandlers,
    _consume_sse_stream,
)
from paths import AgentPaths
from server import WsBridge, WsSessionHandler, _build_repl, _patch_repl, _run_line
from session import create_new
from tools.schema import tool_ok


class TurnCancelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.paths = AgentPaths.discover()
        self.events: list[dict[str, Any]] = []

    def _bridge(self, *, timeout: float = 1.0) -> WsBridge:
        return WsBridge(
            emit=self.events.append,
            paths=self.paths,
            confirm_timeout=timeout,
        )

    def test_cancel_unblocks_pending_confirm(self) -> None:
        bridge = self._bridge()
        bridge._turn_busy.set()
        result: list[str] = []

        worker = threading.Thread(
            target=lambda: result.append(bridge.confirm_fn("preview", False)),
            daemon=True,
        )
        worker.start()
        for _ in range(100):
            if bridge._pending_confirm_id:
                break
            time.sleep(0.01)

        self.assertTrue(bridge.request_cancel())
        worker.join(timeout=1)
        self.assertEqual(result, ["n"])
        self.assertTrue(
            any(
                event.get("type") == "confirm.done"
                and event.get("choice") == "cancelled"
                for event in self.events
            )
        )

    def test_cancel_dominates_racing_confirm_and_next_confirm_is_clean(self) -> None:
        bridge = self._bridge()
        bridge._turn_busy.set()
        first: list[str] = []
        worker = threading.Thread(
            target=lambda: first.append(bridge.confirm_fn("first", False)),
            daemon=True,
        )
        worker.start()
        for _ in range(100):
            if bridge._pending_confirm_id:
                break
            time.sleep(0.01)
        first_id = bridge._pending_confirm_id
        self.assertIsNotNone(first_id)

        bridge.cancel_event.set()
        self.assertFalse(bridge.deliver_confirm(first_id or "", "y"))
        worker.join(timeout=1)
        self.assertEqual(first, ["n"])
        self.assertTrue(
            any(
                event.get("type") == "confirm.done"
                and event.get("request_id") == first_id
                and event.get("choice") == "cancelled"
                for event in self.events
            )
        )

        bridge.cancel_event.clear()
        second: list[str] = []
        worker2 = threading.Thread(
            target=lambda: second.append(bridge.confirm_fn("second", False)),
            daemon=True,
        )
        worker2.start()
        for _ in range(100):
            if bridge._pending_confirm_id and bridge._pending_confirm_id != first_id:
                break
            time.sleep(0.01)
        second_id = bridge._pending_confirm_id
        self.assertIsNotNone(second_id)
        self.assertNotEqual(first_id, second_id)
        self.assertTrue(bridge.deliver_confirm(second_id or "", "y"))
        worker2.join(timeout=1)
        self.assertEqual(second, ["y"])

    def test_cancel_is_inline_and_idempotent(self) -> None:
        bridge = self._bridge()
        handler = WsSessionHandler(self.paths)
        calls: list[str] = []
        bridge._turn_busy.set()
        bridge._cancel_turn = lambda: calls.append("cancel")

        self.assertTrue(handler._dispatch_inline({"type": "turn.cancel"}, bridge))
        self.assertTrue(handler._dispatch_inline({"type": "turn.cancel"}, bridge))
        self.assertEqual(calls, ["cancel"])
        self.assertTrue(bridge.cancel_event.is_set())

    def test_cancel_without_active_turn_is_noop(self) -> None:
        bridge = self._bridge()
        self.assertFalse(bridge.request_cancel())
        self.assertTrue(
            any(
                event.get("type") == "notice"
                and "无进行中" in str(event.get("text"))
                for event in self.events
            )
        )

    def test_sse_consumer_honors_cancel_event(self) -> None:
        cancel_event = threading.Event()
        cancel_event.set()
        response = httpx.Response(
            200,
            text='data: {"choices":[{"delta":{"content":"late"}}]}\n\ndata: [DONE]\n\n',
        )
        with self.assertRaises(LLMCancelledError):
            _consume_sse_stream(
                response,
                handlers=StreamHandlers(),
                fallback_model="demo",
                cancel_event=cancel_event,
            )

    def test_cancel_closes_silent_stream_handles(self) -> None:
        class CloseTracker:
            def __init__(self) -> None:
                self.closed = False

            def close(self) -> None:
                self.closed = True

        client = LLMClient(
            LLMConfig(
                api_key="test",
                base_url="https://example.invalid",
                model="demo",
                model_coding="demo-pro",
                timeout_sec=120,
                context_limit_override=None,
            )
        )
        cancel_event = threading.Event()
        client.set_cancel_event(cancel_event)
        response = CloseTracker()
        transport = CloseTracker()
        client._active_response = response  # type: ignore[assignment]
        client._active_client = transport  # type: ignore[assignment]

        cancel_event.set()
        client.cancel_current_request()

        self.assertTrue(response.closed)
        self.assertTrue(transport.closed)
        with self.assertRaises(LLMCancelledError):
            client._raise_if_cancelled()

    def test_agent_returns_cancelled_turn_without_error_text(self) -> None:
        class CancelledLLM:
            def set_cancel_event(self, _event: threading.Event) -> None:
                pass

            def chat(self, *_args: Any, **_kwargs: Any) -> Any:
                raise LLMCancelledError("cancelled")

        session_id = f"_turn_cancel_agent_test_{uuid.uuid4().hex}"
        session_dir = self.paths.data / "sessions" / session_id
        if session_dir.is_dir():
            shutil.rmtree(session_dir)
        session = create_new(self.paths, conversation_id=session_id)
        try:
            agent = Agent.create(session, llm=CancelledLLM())
            result = agent.run_turn("直接回答", spawn_explore=False)
            self.assertEqual(result.finish_reason, "cancelled")
            self.assertEqual(result.assistant_text, "")
            self.assertFalse(result.tool_loop_exceeded)
        finally:
            if session_dir.is_dir():
                shutil.rmtree(session_dir)

    def test_cancel_at_tool_budget_boundary_does_not_fall_back(self) -> None:
        class ToolCallingLLM:
            def __init__(self) -> None:
                self.calls = 0

            def set_cancel_event(self, _event: threading.Event) -> None:
                pass

            def chat(self, *_args: Any, **_kwargs: Any) -> LLMResponse:
                self.calls += 1
                if self.calls > 1:
                    raise AssertionError("cancelled turn called the LLM again")
                return LLMResponse(
                    model="demo",
                    content=None,
                    tool_calls=[
                        {
                            "id": "call-cancel",
                            "type": "function",
                            "function": {
                                "name": "list_dir",
                                "arguments": '{"path":"."}',
                            },
                        }
                    ],
                    finish_reason="tool_calls",
                    usage=None,
                    raw={},
                )

        session_id = f"_turn_cancel_tool_loop_test_{uuid.uuid4().hex}"
        session_dir = self.paths.data / "sessions" / session_id
        if session_dir.is_dir():
            shutil.rmtree(session_dir)
        session = create_new(self.paths, conversation_id=session_id)
        llm = ToolCallingLLM()
        try:
            agent = Agent.create(session, llm=llm)

            def cancel_during_tool(tool_name: str, _arguments: dict[str, Any]) -> Any:
                agent.request_cancel()
                return tool_ok(tool_name, {"cancelled": True})

            agent.executor.run = cancel_during_tool  # type: ignore[method-assign]
            with patch.dict(
                "os.environ",
                {
                    "PARENT_EXECUTE_SEGMENT_MAX": "1",
                    "PARENT_EXECUTE_TOTAL_MAX": "1",
                },
            ):
                result = agent.run_turn("执行一次目录读取", spawn_explore=False)

            self.assertEqual(result.finish_reason, "cancelled")
            self.assertIn("不是工具回合上限", result.assistant_text)
            self.assertFalse(result.tool_loop_exceeded)
            self.assertEqual(llm.calls, 1)
        finally:
            if session_dir.is_dir():
                shutil.rmtree(session_dir)

    def test_cancel_emits_turn_end_with_cancelled_reason(self) -> None:
        """T-1804-05 / T-1407 R3: Stop must close the turn with finish_reason=cancelled."""

        class SlowCancelLLM:
            def __init__(self) -> None:
                self._cancel_event: threading.Event | None = None
                self.chat_started = threading.Event()

            def set_cancel_event(self, event: threading.Event) -> None:
                self._cancel_event = event

            def chat(self, *_args: Any, **_kwargs: Any) -> Any:
                self.chat_started.set()
                assert self._cancel_event is not None
                deadline = time.monotonic() + 2.0
                while time.monotonic() < deadline:
                    if self._cancel_event.is_set():
                        raise LLMCancelledError("cancelled")
                    time.sleep(0.01)
                raise AssertionError("timed out waiting for cancel")

        session_id = f"_turn_end_cancel_{uuid.uuid4().hex}"
        session_dir = self.paths.data / "sessions" / session_id
        if session_dir.is_dir():
            shutil.rmtree(session_dir)
        session = create_new(self.paths, conversation_id=session_id)
        bridge = self._bridge()
        llm = SlowCancelLLM()
        repl = _build_repl(session, self.paths, bridge)
        repl.agent = Agent.create(session, llm=llm)
        _patch_repl(repl, bridge)

        async def _run() -> None:
            task = asyncio.create_task(_run_line(repl, bridge, "停下", self.paths))
            for _ in range(200):
                if llm.chat_started.is_set():
                    break
                await asyncio.sleep(0.01)
            self.assertTrue(llm.chat_started.is_set())
            self.assertTrue(bridge._turn_busy.is_set())
            self.assertTrue(bridge.request_cancel())
            await task

        try:
            asyncio.run(_run())
        finally:
            if session_dir.is_dir():
                shutil.rmtree(session_dir)

        turn_ends = [event for event in self.events if event.get("type") == "turn.end"]
        self.assertEqual(len(turn_ends), 1)
        self.assertFalse(turn_ends[0].get("ok"))
        self.assertEqual(turn_ends[0].get("finish_reason"), "cancelled")
        self.assertFalse(bridge._turn_busy.is_set())


if __name__ == "__main__":
    unittest.main()
