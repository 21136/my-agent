"""Phase 15 turn cancellation protocol and cooperative LLM cancellation."""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import threading
import time
import unittest
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

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
from runaway_v2.checklist import build_checklist, save_checklist
from server import (
    TURN_LOCK,
    WsBridge,
    WsSessionHandler,
    _build_repl,
    _chain_runaway_after_turn,
    _patch_repl,
    _run_line,
)
from session import create_new
from tests.isolation_helpers import temporary_agent_paths
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

    def test_bridge_emits_execution_state_and_finalizes_once(self) -> None:
        bridge = self._bridge()

        self.assertTrue(bridge.begin_turn())
        running = [event for event in self.events if event.get("type") == "execution.state"][-1]
        run_id = running["run_id"]
        self.assertEqual(running["state"], "running")

        bridge.emit_turn_event({"type": "turn.start", "intent": "execute", "intent_label": "执行"})
        turn_start = [event for event in self.events if event.get("type") == "turn.start"][-1]
        self.assertEqual(turn_start["run_id"], run_id)

        self.assertTrue(bridge.finish_execution("completed", ok=True))
        self.assertFalse(bridge.finish_execution("error", ok=False))
        finals = [
            event
            for event in self.events
            if event.get("type") == "execution.state" and event.get("state") == "settled"
        ]
        self.assertEqual(len(finals), 1)
        self.assertEqual(finals[0]["run_id"], run_id)

    def test_bridge_scopes_execution_events_to_current_run(self) -> None:
        bridge = self._bridge()
        self.assertTrue(bridge.begin_turn())
        run_id = [
            event for event in self.events if event.get("type") == "execution.state"
        ][-1]["run_id"]

        bridge.emit_turn_event({"type": "turn.start", "intent": "execute", "intent_label": "执行"})
        bridge.emit_turn_event({"type": "llm.pending"})
        bridge.emit_content_delta("partial")
        bridge.on_executor_event("tool.start", {"tool": "run_command", "call_id": "c1", "summary": "run"})

        scoped = [
            event
            for event in self.events
            if event.get("type") in {"turn.start", "llm.pending", "assistant.delta", "tool.start"}
        ]
        self.assertEqual(len(scoped), 4)
        self.assertTrue(all(event.get("run_id") == run_id for event in scoped))

    def test_stop_marks_lifecycle_and_does_not_cancel_agent_twice(self) -> None:
        bridge = self._bridge()
        calls: list[str] = []
        bridge._cancel_turn = lambda: calls.append("cancel")
        self.assertTrue(bridge.begin_turn())
        bridge._turn_busy.set()

        self.assertTrue(bridge.request_cancel())
        self.assertTrue(bridge.request_cancel())

        stopping = [event for event in self.events if event.get("type") == "execution.state"][-1]
        self.assertEqual(stopping["state"], "stopping")
        self.assertTrue(stopping["cancel_requested"])
        self.assertEqual(calls, ["cancel"])

    def test_cancelled_queued_continuation_cannot_activate(self) -> None:
        bridge = self._bridge()

        self.assertTrue(bridge.begin_continuation())
        self.assertTrue(bridge.request_cancel())
        self.assertFalse(bridge.begin_turn(runaway=True))
        self.assertTrue(bridge.finish_execution("cancelled", ok=False))

        states = [
            event.get("state")
            for event in self.events
            if event.get("type") == "execution.state"
        ]
        self.assertEqual(states, ["queued", "stopping", "settled"])

    def test_duplicate_runaway_lease_closes_execution_lifecycle(self) -> None:
        """A rejected runaway attempt must not leave an untracked turn end."""
        bridge = self._bridge()
        repl = MagicMock()
        repl.session.meta.project_runaway_enabled = True

        async def _run() -> None:
            with patch("server._acquire_runaway_lease", return_value=None):
                await _run_line(repl, bridge, "继续", self.paths)

        asyncio.run(_run())

        execution_events = [
            event
            for event in self.events
            if event.get("type") == "execution.state"
        ]
        self.assertEqual([event.get("state") for event in execution_events], ["running", "failed"])
        run_id = execution_events[-1].get("run_id")
        turn_ends = [event for event in self.events if event.get("type") == "turn.end"]
        self.assertEqual(len(turn_ends), 1)
        self.assertEqual(turn_ends[0].get("run_id"), run_id)
        self.assertEqual(turn_ends[0].get("finish_reason"), "runaway_duplicate")

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

    def test_cancel_stops_runaway_chain_waiting_for_turn(self) -> None:
        bridge = self._bridge()
        bridge._runaway_chain_busy.set()

        self.assertTrue(bridge.request_cancel())
        self.assertTrue(bridge.cancel_event.is_set())
        self.assertTrue(
            any(
                event.get("type") == "notice"
                and event.get("runaway_cancel_available") is False
                for event in self.events
            )
        )

    def test_runaway_chain_reuses_lock_owned_by_current_turn(self) -> None:
        bridge = self._bridge()
        repl = MagicMock()
        repl.session.meta.project_runaway_enabled = True
        repl.agent.should_chain_runaway_after_turn.return_value = True
        repl.agent.runaway_chain_user_line.return_value = "继续狂奔"

        async def _run() -> None:
            await TURN_LOCK.acquire()
            try:
                with patch("server.RUNAWAY_CHAIN_COOLDOWN_SEC", 0), patch(
                    "server._run_line", new_callable=AsyncMock
                ) as run_line:
                    await asyncio.wait_for(
                        _chain_runaway_after_turn(
                            repl,
                            bridge,
                            self.paths,
                            "completed",
                            lock_held=True,
                        ),
                        timeout=0.5,
                    )
                run_line.assert_awaited_once()
            finally:
                TURN_LOCK.release()

        asyncio.run(_run())

    def test_runaway_chain_publishes_cancel_available_before_waiting(self) -> None:
        """The UI must expose Stop during the server-side continuation gap."""
        bridge = self._bridge()
        repl = MagicMock()
        repl.session.meta.project_runaway_enabled = True
        repl.agent.should_chain_runaway_after_turn.return_value = True
        repl.agent.runaway_chain_user_line.return_value = "继续狂奔"

        async def _run() -> None:
            with patch("server.RUNAWAY_CHAIN_COOLDOWN_SEC", 0), patch(
                "server._run_line", new_callable=AsyncMock
            ) as run_line:
                await _chain_runaway_after_turn(
                    repl,
                    bridge,
                    self.paths,
                    "completed",
                )
                run_line.assert_awaited_once()

        asyncio.run(_run())
        self.assertTrue(
            any(
                event.get("type") == "turn.notice"
                and event.get("runaway_cancel_available") is True
                for event in self.events
            )
        )

    @patch.dict(os.environ, {"MY_AGENT_RUNAWAY_V2": "1"}, clear=False)
    def test_v2_server_does_not_start_a_second_chain_owner(self) -> None:
        """v2 controller owns continuation; server must not start another turn."""
        bridge = self._bridge()
        repl = MagicMock()
        repl.session.meta.project_id = "demo"
        repl.session.meta.project_runaway_enabled = True
        repl.agent.should_chain_runaway_after_turn.return_value = True

        async def _run() -> None:
            with patch("server._run_line", new_callable=AsyncMock) as run_line:
                await _chain_runaway_after_turn(
                    repl,
                    bridge,
                    self.paths,
                    "completed",
                )
                run_line.assert_not_awaited()

        asyncio.run(_run())
        repl.agent.should_chain_runaway_after_turn.assert_not_called()

    def test_startup_runaway_wait_publishes_cancel_state_until_lock_timeout(self) -> None:
        """A reconnecting runaway turn must expose and clear its stop state."""
        bridge = self._bridge()
        repl = MagicMock()
        handler = WsSessionHandler(self.paths)

        async def _run() -> None:
            with patch(
                "server._try_acquire_turn_lock",
                new_callable=AsyncMock,
                return_value=False,
            ):
                await handler._resume_runaway(repl, bridge)

        asyncio.run(_run())
        flags = [
            event["runaway_cancel_available"]
            for event in self.events
            if event.get("type") == "turn.notice"
            and "runaway_cancel_available" in event
        ]
        self.assertEqual(flags, [True, False])

    @patch.dict(os.environ, {"MY_AGENT_RUNAWAY_V2": "1"}, clear=False)
    def test_stale_v2_resume_does_not_start_turn_at_release_wait(self) -> None:
        """A queued UI resume must not re-enter the model after acceptance."""
        with temporary_agent_paths() as paths:
            root = paths.workspace / "demo"
            root.mkdir(parents=True, exist_ok=True)
            (root / "PROJECT.md").write_text(
                "REQ-001\nAC-001\n\n## 验收标准\n\n"
                "- 命令：`python verify.py` 期望退出码：0\n",
                encoding="utf-8",
            )
            (root / "DESIGN.md").write_text("UX-001\nTD-001\n", encoding="utf-8")
            (root / "TASKS.md").write_text("- [x] T-001 done\n", encoding="utf-8")
            (root / "VERIFY.md").write_text("V-001 T-001 pass\nAC-001\n", encoding="utf-8")
            (root / "ENV.md").write_text(
                "quality:\n  commands:\n    - id: smoke\n      cmd: [\"echo\", \"ok\"]\n",
                encoding="utf-8",
            )

            session = create_new(paths, conversation_id="_stale_release_resume_")
            session.meta.project_id = "demo"
            session.meta.project_root = "workspace/demo"
            session.meta.active_shell = "project"
            # Simulate the stale legacy mirror that caused the UI to enqueue
            # another prepare turn after v2 had already reached release_wait.
            session.meta.project_plan_status = "plan_dirty"
            session.meta.project_runaway_enabled = True
            session.meta.project_runaway_checkpoint = "release_wait"
            session.meta.project_runaway_v2_phase = "release_wait"
            checklist = build_checklist(paths, "demo")
            for item in checklist.items:
                item.status = "passed"
            save_checklist(paths, checklist)

            bridge = self._bridge()
            handler = WsSessionHandler(paths)
            repl = MagicMock()
            repl.session = session

            with patch(
                "project_api.dispatch_project_message",
                return_value={"_events": []},
            ), patch("server._try_acquire_turn_lock", new_callable=AsyncMock) as acquire, patch(
                "server._run_line", new_callable=AsyncMock
            ) as run_line:
                awaitable = handler._dispatch_project(
                    {"type": "project.runaway.set", "enabled": True, "resume": True},
                    repl,
                    bridge,
                )
                asyncio.run(awaitable)

            acquire.assert_not_awaited()
            run_line.assert_not_awaited()
            self.assertFalse(bridge._runaway_chain_busy.is_set())
            self.assertTrue(
                any(
                    event.get("type") == "notice"
                    and "跳过模型调用" in str(event.get("text"))
                    for event in self.events
                )
            )

            repl.agent.should_chain_runaway_after_turn.return_value = True
            with patch("server._run_line", new_callable=AsyncMock) as run_line:
                asyncio.run(
                    _chain_runaway_after_turn(
                        repl,
                        bridge,
                        paths,
                        "completed",
                    )
                )
            run_line.assert_not_awaited()

    @patch.dict(os.environ, {"MY_AGENT_RUNAWAY_V2": "1"}, clear=False)
    def test_duplicate_v2_resume_is_rejected_while_chain_is_busy(self) -> None:
        with temporary_agent_paths() as paths:
            session = create_new(paths, conversation_id="_duplicate_resume_")
            session.meta.project_id = "demo"
            session.meta.project_root = "workspace/demo"
            session.meta.active_shell = "project"
            session.meta.project_plan_status = "confirmed"
            session.meta.project_runaway_enabled = True
            root = paths.workspace / "demo"
            root.mkdir(parents=True, exist_ok=True)
            (root / "PROJECT.md").write_text("REQ-001\nAC-001\n", encoding="utf-8")
            (root / "DESIGN.md").write_text("UX-001\nTD-001\n", encoding="utf-8")
            (root / "TASKS.md").write_text("- [ ] T-001 work\n", encoding="utf-8")
            (root / "VERIFY.md").write_text("V-001 T-001\nAC-001\n", encoding="utf-8")

            bridge = self._bridge()
            bridge._runaway_chain_busy.set()
            handler = WsSessionHandler(paths)
            repl = MagicMock()
            repl.session = session
            with patch("project_api.dispatch_project_message", return_value={"_events": []}), patch(
                "server._run_line", new_callable=AsyncMock
            ) as run_line:
                asyncio.run(
                    handler._dispatch_project(
                        {"type": "project.runaway.set", "enabled": True, "resume": True},
                        repl,
                        bridge,
                    )
                )

            run_line.assert_not_awaited()
            self.assertTrue(any("无需重复点击继续" in str(e.get("text")) for e in self.events))

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
