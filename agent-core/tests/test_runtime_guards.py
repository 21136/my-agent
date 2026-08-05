"""Phase 16 M0 runtime guards — unit tests (T-1518)."""

from __future__ import annotations

import sys
import tempfile
import threading
import time
import unittest
import uuid
import asyncio
import shutil
from pathlib import Path
from typing import Any

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from agent import Agent, ToolLoopSegmentResult
from llm_client import LLMResponse, LLMTimeoutError
from paths import AgentPaths
from runtime_guards import TurnWatchdog, stall_watchdog_sec, turn_wall_sec
from server import WsBridge
from server import WsBridge, _build_repl, _patch_repl, _run_line
from session import Session, SessionMeta, create_new, utc_now_iso
from tools.builtin.run_evolved import run
from tools.registry import ToolRegistry, parse_tool_manifest

from tests.isolation_helpers import make_temp_agent_paths


class _TimeoutLLM:
    def chat(self, *_args: Any, **_kwargs: Any) -> LLMResponse:
        raise LLMTimeoutError("timed out")

    def set_cancel_event(self, _event: threading.Event) -> None:
        return None


class TurnWatchdogTests(unittest.TestCase):
    def test_defaults(self) -> None:
        self.assertEqual(turn_wall_sec(), 900.0)
        self.assertEqual(stall_watchdog_sec(), 0.0)

    def test_stall_triggers_timeout_once(self) -> None:
        cancel = threading.Event()
        notices: list[str] = []
        watchdog = TurnWatchdog(
            cancel_event=cancel,
            on_auto_timeout=notices.append,
            wall_sec=0,
            stall_sec=0.05,
        )
        watchdog.begin()
        time.sleep(0.2)
        watchdog.end()
        self.assertTrue(cancel.is_set())
        self.assertEqual(watchdog.resolve_finish_reason(), "timeout")
        self.assertEqual(len(notices), 1)

    def test_reasoning_like_gap_without_progress_stalls(self) -> None:
        cancel = threading.Event()
        watchdog = TurnWatchdog(
            cancel_event=cancel,
            on_auto_timeout=lambda _m: None,
            wall_sec=0,
            stall_sec=0.05,
        )
        watchdog.begin()
        time.sleep(0.03)
        watchdog.note_progress_event("reasoning.delta")
        time.sleep(0.12)
        watchdog.end()
        self.assertTrue(cancel.is_set())
        self.assertEqual(watchdog.resolve_finish_reason(), "timeout")

    def test_assistant_delta_resets_stall(self) -> None:
        cancel = threading.Event()
        watchdog = TurnWatchdog(
            cancel_event=cancel,
            on_auto_timeout=lambda _m: None,
            wall_sec=0,
            stall_sec=0.2,
        )
        watchdog.begin()
        time.sleep(0.05)
        watchdog.note_progress_event("assistant.delta")
        time.sleep(0.08)
        watchdog.note_progress_event("assistant.delta")
        time.sleep(0.08)
        watchdog.end()
        self.assertFalse(cancel.is_set())

    def test_wall_pauses_during_tool_execution(self) -> None:
        cancel = threading.Event()
        notices: list[str] = []
        watchdog = TurnWatchdog(
            cancel_event=cancel,
            on_auto_timeout=notices.append,
            wall_sec=0.12,
            stall_sec=0,
        )
        watchdog.begin()
        watchdog.pause_wall()
        time.sleep(0.25)
        self.assertFalse(cancel.is_set())
        watchdog.resume_wall()
        time.sleep(0.25)
        watchdog.end()
        self.assertTrue(cancel.is_set())
        self.assertEqual(watchdog.resolve_finish_reason(), "timeout")
        self.assertEqual(len(notices), 1)

    def test_wall_clock_does_not_reset_on_touch(self) -> None:
        cancel = threading.Event()
        watchdog = TurnWatchdog(
            cancel_event=cancel,
            on_auto_timeout=lambda _m: None,
            wall_sec=0.08,
            stall_sec=0,
        )
        watchdog.begin()
        time.sleep(0.03)
        watchdog.touch_progress()
        time.sleep(0.1)
        watchdog.end()
        self.assertTrue(cancel.is_set())

    def test_user_cancel_beats_timeout_reason(self) -> None:
        cancel = threading.Event()
        watchdog = TurnWatchdog(
            cancel_event=cancel,
            on_auto_timeout=lambda _m: None,
            wall_sec=0,
            stall_sec=0,
        )
        watchdog.begin()
        watchdog.request_user_cancel()
        cancel.set()
        self.assertEqual(watchdog.resolve_finish_reason(), "cancelled")


class AgentTimeoutTests(unittest.TestCase):
    def test_llm_timeout_maps_to_finish_reason_timeout(self) -> None:
        paths = make_temp_agent_paths(self)
        session = Session(
            conversation_id="_guard_test",
            session_dir=paths.data / "sessions" / "_guard_test",
            goal="",
            meta=SessionMeta(
                topics=[],
                llm_model="deepseek-v4-flash",
                updated_at=utc_now_iso(),
                phase="S4",
            ),
            messages=[],
            paths=paths,
        )
        session.save()
        agent = Agent.create(session, confirm_fn=lambda _p, _a: "y")
        agent.session.append_message({"role": "user", "content": "hi"})

        class TimeoutLLM:
            def chat(self, *_args, **_kwargs) -> LLMResponse:
                raise LLMTimeoutError("timed out")

            def set_cancel_event(self, _event: threading.Event) -> None:
                return None

        agent.llm = TimeoutLLM()  # type: ignore[assignment]
        result = agent._run_parent_tool_loop(
            max_rounds=3,
            tools=[],
            model="deepseek-v4-flash",
            segment_start_index=0,
        )
        self.assertIsInstance(result, ToolLoopSegmentResult)
        self.assertEqual(result.finish_reason, "timeout")


class LlmTimeoutChainTests(unittest.TestCase):
    """T-1519 / IT-51 / T-1806-05: LLM timeout → finish_reason=timeout end-to-end."""

    def setUp(self) -> None:
        self.paths = make_temp_agent_paths(self)
        self.events: list[dict[str, Any]] = []

    def test_run_turn_llm_timeout_without_generic_error(self) -> None:
        session_id = f"_llm_timeout_turn_{uuid.uuid4().hex}"
        session_dir = self.paths.data / "sessions" / session_id
        session = create_new(self.paths, conversation_id=session_id)

        agent = Agent.create(session, llm=_TimeoutLLM(), confirm_fn=lambda _p, _a: "y")
        agent.on_turn_event = self.events.append
        result = agent.run_turn("1+1", spawn_explore=False)
        self.assertEqual(result.finish_reason, "timeout")
        self.assertEqual(result.assistant_text, "")
        self.assertFalse(any(event.get("type") == "error" for event in self.events))
        self.assertTrue(session_dir.is_dir())

    def test_run_line_emits_turn_end_with_timeout_reason(self) -> None:
        session_id = f"_llm_timeout_line_{uuid.uuid4().hex}"
        session = create_new(self.paths, conversation_id=session_id)
        bridge = WsBridge(emit=self.events.append, paths=self.paths)
        repl = _build_repl(session, self.paths, bridge)
        repl.agent = Agent.create(session, llm=_TimeoutLLM(), confirm_fn=lambda _p, _a: "y")
        _patch_repl(repl, bridge)
        asyncio.run(_run_line(repl, bridge, "1+1", self.paths))

        turn_ends = [event for event in self.events if event.get("type") == "turn.end"]
        self.assertEqual(len(turn_ends), 1)
        self.assertFalse(turn_ends[0].get("ok"))
        self.assertEqual(turn_ends[0].get("finish_reason"), "timeout")
        self.assertFalse(any(event.get("type") == "error" for event in self.events))
        self.assertFalse(bridge._turn_busy.is_set())


class RunEvolvedCancelTests(unittest.TestCase):
    def test_cancel_terminates_subprocess(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            evolve = Path(tmp)
            tool_dir = evolve / "tools" / "common" / "sleepy"
            tool_dir.mkdir(parents=True)
            (tool_dir / "main.py").write_text(
                """import json, sys, time
json.load(sys.stdin)
time.sleep(30)
print(json.dumps({"ok": True}))
""",
                encoding="utf-8",
            )
            manifest = tool_dir / "tool.toml"
            manifest.write_text(
                """[tool]
name = "sleepy"
description = "sleep"
version = "1.0.0"
status = "active"
topics = ["common"]

[entry]
type = "python"
path = "main.py"

[schema.input]
type = "object"

[schema.output]
type = "object"

[policy]
confirm = false
dry_run_supported = false
workspace_only = true
timeout_sec = 60
""",
                encoding="utf-8",
            )
            paths = AgentPaths.discover()
            tool = parse_tool_manifest(manifest, evolve_dir=evolve)
            cancel = threading.Event()

            def cancel_soon() -> None:
                time.sleep(0.15)
                cancel.set()

            threading.Thread(target=cancel_soon, daemon=True).start()
            started = time.perf_counter()
            result = run(
                {"tool_name": "sleepy", "arguments": {}},
                registry=ToolRegistry(agent_paths=paths, evolved=[tool]),
                cancel_event=cancel,
            )
            elapsed = time.perf_counter() - started
            self.assertFalse(result.ok)
            self.assertLess(elapsed, 5.0)


class WsBridgeFinishReasonTests(unittest.TestCase):
    def test_resolve_turn_finish_reason_timeout(self) -> None:
        paths = AgentPaths.discover()
        bridge = WsBridge(emit=lambda _e: None, paths=paths)
        bridge.begin_turn()
        bridge.turn_watchdog._cancel_reason = "timeout"  # type: ignore[union-attr]
        bridge.cancel_event.set()
        self.assertEqual(bridge.resolve_turn_finish_reason(None), "timeout")
        bridge.end_turn()


class WsBridgeExecutorEventTests(unittest.TestCase):
    def setUp(self) -> None:
        self.paths = AgentPaths.discover()
        self.events: list[dict[str, Any]] = []

    def _bridge(self) -> WsBridge:
        return WsBridge(emit=self.events.append, paths=self.paths)

    def test_plan_subagent_events_passthrough(self) -> None:
        bridge = self._bridge()
        bridge.on_executor_event(
            "plan.subagent.start",
            {"task_preview": "规划模块", "call_id": "c1"},
        )
        bridge.on_executor_event(
            "plan.subagent.done",
            {"summary": "完成", "proposal_count": 0, "ok": True, "call_id": "c1"},
        )
        self.assertEqual(self.events[0]["type"], "plan.subagent.start")
        self.assertEqual(self.events[0]["call_id"], "c1")
        self.assertEqual(self.events[1]["type"], "plan.subagent.done")
        self.assertTrue(self.events[1]["ok"])

    def test_project_plan_state_passthrough(self) -> None:
        bridge = self._bridge()
        bridge.on_executor_event(
            "project.plan.state",
            {"tasks_md": "# tasks", "changes_level": "none"},
        )
        self.assertEqual(self.events[0]["type"], "project.plan.state")
        self.assertEqual(self.events[0]["tasks_md"], "# tasks")

    def test_unknown_executor_event_not_spammed_as_notice(self) -> None:
        bridge = self._bridge()
        bridge.on_executor_event("debug.probe", {"blob": "x" * 200})
        self.assertEqual(self.events, [])


if __name__ == "__main__":
    unittest.main()
