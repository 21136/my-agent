"""BUG-023 / T-2092～T-2094: compact summarize wall pause + LLM timeout notices."""

from __future__ import annotations

import sys
import threading
import time
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from agent import Agent, ToolLoopSegmentResult
from context import (
    build_llm_messages_with_optional_trim,
    compact_context,
    summarize_timeout_sec,
    trim_tool_payloads_for_llm,
)
from llm_client import LLMResponse, LLMTimeoutError, load_config
from runtime_guards import TurnWatchdog
from session import Session, SessionMeta, utc_now_iso

from tests.isolation_helpers import make_temp_agent_paths


class _SummarizeSleepLLM:
    def __init__(self, *, sleep_sec: float = 0.05) -> None:
        self.sleep_sec = sleep_sec
        self.timeout_sec: float | None = None

    def chat(self, *_args: Any, **kwargs: Any) -> LLMResponse:
        self.timeout_sec = kwargs.get("timeout_sec")
        time.sleep(self.sleep_sec)
        return LLMResponse(
            model="deepseek-v4-flash",
            content="## 目标\nok",
            tool_calls=[],
            finish_reason="stop",
            usage=None,
            raw={},
        )

    def set_cancel_event(self, _event: threading.Event) -> None:
        return None


class _TimeoutMainLLM:
    def chat(self, *_args: Any, **_kwargs: Any) -> LLMResponse:
        raise LLMTimeoutError("timed out")

    def set_cancel_event(self, _event: threading.Event) -> None:
        return None


class CompactWallPauseTests(unittest.TestCase):
    """IT-95: digest summarize pauses turn wall clock."""

    def test_compact_summarize_pauses_wall_during_sleep(self) -> None:
        cancel = threading.Event()
        notices: list[str] = []
        watchdog = TurnWatchdog(
            cancel_event=cancel,
            on_auto_timeout=lambda message: notices.append(message),
            wall_sec=0.5,
            stall_sec=0,
        )
        watchdog.begin()

        def pause() -> None:
            watchdog.pause_wall()

        def resume() -> None:
            watchdog.resume_wall()

        paths = make_temp_agent_paths(self)
        session_dir = paths.data / "sessions" / "_compact_wall"
        session = Session(
            conversation_id="_compact_wall",
            session_dir=session_dir,
            goal="test",
            meta=SessionMeta(
                topics=[],
                llm_model="deepseek-v4-flash",
                updated_at=utc_now_iso(),
                phase="S4",
            ),
            messages=[
                {"role": "user", "content": "u1"},
                {"role": "assistant", "content": "a1"},
                {"role": "user", "content": "u2"},
                {"role": "assistant", "content": "a2"},
                {"role": "user", "content": "u3"},
                {"role": "assistant", "content": "a3"},
                {"role": "user", "content": "u4"},
                {"role": "assistant", "content": "a4"},
                {"role": "user", "content": "u5"},
                {"role": "assistant", "content": "a5"},
                {"role": "user", "content": "u6"},
                {"role": "assistant", "content": "a6"},
                {"role": "user", "content": "u7"},
                {"role": "assistant", "content": "a7"},
                {"role": "user", "content": "u8"},
                {"role": "assistant", "content": "a8"},
                {"role": "user", "content": "u9"},
                {"role": "assistant", "content": "a9"},
            ],
            paths=paths,
        )
        session.save()

        llm = _SummarizeSleepLLM(sleep_sec=0.12)
        with patch("context.should_auto_compact", return_value=True):
            result = compact_context(
                session,
                llm,
                force=True,
                on_summarize_begin=pause,
                on_summarize_end=resume,
            )

        watchdog.end()
        self.assertTrue(result.compacted)
        self.assertFalse(cancel.is_set(), msg=f"unexpected wall timeout notices: {notices}")
        self.assertAlmostEqual(llm.timeout_sec or 0, summarize_timeout_sec(), places=1)


class LlmTimeoutNoticeTests(unittest.TestCase):
    """IT-96: LLMTimeoutError emits explicit turn.notice."""

    def test_tool_loop_emits_llm_timeout_notice(self) -> None:
        paths = make_temp_agent_paths(self)
        session = Session(
            conversation_id="_llm_notice",
            session_dir=paths.data / "sessions" / "_llm_notice",
            goal="",
            meta=SessionMeta(
                topics=[],
                llm_model="deepseek-v4-flash",
                updated_at=utc_now_iso(),
                phase="S4",
            ),
            messages=[{"role": "user", "content": "hi"}],
            paths=paths,
        )
        session.save()
        events: list[dict[str, Any]] = []
        agent = Agent.create(session, confirm_fn=lambda _p, _a: "y")
        agent.llm = _TimeoutMainLLM()  # type: ignore[assignment]
        agent.on_turn_event = events.append

        result = agent._run_parent_tool_loop(
            max_rounds=2,
            tools=[],
            model="deepseek-v4-flash",
            segment_start_index=0,
        )
        self.assertEqual(result.finish_reason, "timeout")
        notices = [
            event.get("text", "")
            for event in events
            if event.get("type") == "turn.notice"
        ]
        self.assertTrue(any("LLM 请求超时" in text for text in notices))
        self.assertIn(str(int(load_config().timeout_sec)), "".join(notices))


class PayloadTrimTests(unittest.TestCase):
    def test_trim_tool_payloads_shortens_only_tool_role(self) -> None:
        long_tool = "x" * 8000
        messages = [
            {"role": "user", "content": "hi"},
            {"role": "tool", "tool_call_id": "c1", "content": long_tool},
        ]
        trimmed, count = trim_tool_payloads_for_llm(messages, max_chars=500)
        self.assertEqual(count, 1)
        self.assertLess(len(trimmed[1]["content"]), len(long_tool))
        self.assertIn("truncated", trimmed[1]["content"])

    def test_build_llm_messages_with_optional_trim_when_forced(self) -> None:
        paths = make_temp_agent_paths(self)
        session = Session(
            conversation_id="_trim",
            session_dir=paths.data / "sessions" / "_trim",
            goal="",
            meta=SessionMeta(
                topics=[],
                llm_model="deepseek-v4-flash",
                updated_at=utc_now_iso(),
                phase="S4",
                compact_before_index=1,
            ),
            messages=[
                {"role": "user", "content": "hi"},
                {"role": "tool", "tool_call_id": "c1", "content": "y" * 9000},
            ],
            paths=paths,
        )
        working, trimmed = build_llm_messages_with_optional_trim(
            session,
            "system",
            "deepseek-v4-flash",
            force_trim=True,
        )
        self.assertTrue(trimmed)
        self.assertLess(len(working[-1]["content"]), 9000)


if __name__ == "__main__":
    unittest.main()
