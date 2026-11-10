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
    ContextConfig,
    build_llm_messages,
    build_llm_messages_with_optional_trim,
    compact_context,
    count_digest_sections,
    estimate_context_tokens,
    is_over_hard_compact_limit,
    record_prompt_token_usage,
    should_auto_compact,
    summarize_timeout_sec,
    trim_aged_tool_payloads,
    trim_tool_payloads_for_llm,
)
from llm_client import LLMApiError, LLMResponse, LLMTimeoutError, load_config
from loader import load_digest
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
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "c1",
                            "type": "function",
                            "function": {"name": "run_command", "arguments": "{}"},
                        }
                    ],
                },
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


def _tool_turns(count: int, *, tool_chars: int) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    for index in range(count):
        call_id = f"c{index}"
        messages.extend(
            [
                {"role": "user", "content": f"u{index}"},
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {"name": "read_file", "arguments": "{}"},
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": call_id, "content": "t" * tool_chars},
                {"role": "assistant", "content": f"a{index}"},
            ]
        )
    return messages


def _tool_lengths(messages: list[dict[str, Any]]) -> list[int]:
    return [len(message["content"]) for message in messages if message.get("role") == "tool"]


class AgedToolTrimTests(unittest.TestCase):
    def test_recent_window_is_never_trimmed(self) -> None:
        messages = _tool_turns(15, tool_chars=9000)
        trimmed, count = trim_aged_tool_payloads(messages, keep_turns=8, step_turns=8)
        self.assertEqual(count, 0)
        self.assertIs(trimmed, messages)

    def test_aged_turns_are_trimmed_and_recent_turns_kept_whole(self) -> None:
        messages = _tool_turns(16, tool_chars=9000)
        trimmed, count = trim_aged_tool_payloads(messages, keep_turns=8, step_turns=8)
        self.assertEqual(count, 8)
        lengths = _tool_lengths(trimmed)
        self.assertTrue(all(length <= 4000 for length in lengths[:8]))
        self.assertEqual(lengths[8:], [9000] * 8)
        self.assertEqual(_tool_lengths(messages), [9000] * 16, "input must not be mutated")

    def test_cutoff_is_stable_between_steps_for_prompt_cache(self) -> None:
        at_16, _ = trim_aged_tool_payloads(_tool_turns(16, tool_chars=9000), keep_turns=8, step_turns=8)
        at_23, _ = trim_aged_tool_payloads(_tool_turns(23, tool_chars=9000), keep_turns=8, step_turns=8)
        at_24, _ = trim_aged_tool_payloads(_tool_turns(24, tool_chars=9000), keep_turns=8, step_turns=8)
        self.assertEqual(at_23[: len(at_16)], at_16)
        self.assertEqual(_tool_lengths(at_23)[8:], [9000] * 15)
        self.assertTrue(all(length <= 4000 for length in _tool_lengths(at_24)[:16]))

    def test_zero_step_disables_aged_trim(self) -> None:
        messages = _tool_turns(40, tool_chars=9000)
        trimmed, count = trim_aged_tool_payloads(messages, keep_turns=8, step_turns=0)
        self.assertEqual(count, 0)
        self.assertIs(trimmed, messages)

    def test_build_llm_messages_applies_aged_trim_without_touching_session(self) -> None:
        paths = make_temp_agent_paths(self)
        session = Session(
            conversation_id="_aged_trim",
            session_dir=paths.data / "sessions" / "_aged_trim",
            goal="",
            meta=SessionMeta(topics=[], llm_model="deepseek-v4-flash", updated_at=utc_now_iso(), phase="S4"),
            messages=_tool_turns(16, tool_chars=9000),
            paths=paths,
        )
        lengths = _tool_lengths(build_llm_messages(session))
        self.assertTrue(all(length <= 4000 for length in lengths[:8]))
        self.assertEqual(lengths[8:], [9000] * 8)
        self.assertEqual(_tool_lengths(session.messages), [9000] * 16)


class SoftCompactBudgetTests(unittest.TestCase):
    """Auto-compact must fire well below a 1M-token ceiling, without thrashing."""

    def _session(self, turns: int) -> Session:
        paths = make_temp_agent_paths(self)
        return Session(
            conversation_id="_soft_budget",
            session_dir=paths.data / "sessions" / "_soft_budget",
            goal="",
            meta=SessionMeta(topics=[], llm_model="deepseek-v4-flash", updated_at=utc_now_iso(), phase="S4"),
            messages=_tool_turns(turns, tool_chars=6000),
            paths=paths,
        )

    @staticmethod
    def _config(*, compact_max_tokens: int) -> ContextConfig:
        return ContextConfig(0.85, 8, 8000, compact_max_tokens=compact_max_tokens, tool_trim_step_turns=0)

    def test_soft_budget_triggers_far_below_model_ceiling(self) -> None:
        session = self._session(12)  # ~18k estimated tokens
        with patch("context.resolve_context_limit", return_value=1_000_000):
            self.assertTrue(should_auto_compact("", session, config=self._config(compact_max_tokens=10_000)))
            self.assertFalse(is_over_hard_compact_limit("", session, config=self._config(compact_max_tokens=10_000)))

    def test_under_budget_does_not_compact(self) -> None:
        session = self._session(12)
        with patch("context.resolve_context_limit", return_value=1_000_000):
            self.assertFalse(should_auto_compact("", session, config=self._config(compact_max_tokens=50_000)))

    def test_soft_budget_waits_for_half_a_window_of_digestible_turns(self) -> None:
        session = self._session(10)  # over budget, but only 2 turns beyond the kept 8
        with patch("context.resolve_context_limit", return_value=1_000_000):
            self.assertFalse(should_auto_compact("", session, config=self._config(compact_max_tokens=10_000)))

    def test_zero_budget_restores_ratio_only_behavior(self) -> None:
        session = self._session(12)
        with patch("context.resolve_context_limit", return_value=1_000_000):
            self.assertFalse(should_auto_compact("", session, config=self._config(compact_max_tokens=0)))

    def test_hard_limit_still_compacts_regardless_of_batching(self) -> None:
        session = self._session(10)
        with patch("context.resolve_context_limit", return_value=1_000):
            self.assertTrue(should_auto_compact("", session, config=self._config(compact_max_tokens=0)))
            self.assertTrue(is_over_hard_compact_limit("", session, config=self._config(compact_max_tokens=0)))


class TokenEstimateCalibrationTests(unittest.TestCase):
    """Budget checks follow the provider's real prompt_tokens, not just len // 4."""

    def _session(self) -> Session:
        paths = make_temp_agent_paths(self)
        return Session(
            conversation_id="_calibration",
            session_dir=paths.data / "sessions" / "_calibration",
            goal="",
            meta=SessionMeta(topics=[], llm_model="deepseek-v4-flash", updated_at=utc_now_iso(), phase="S4"),
            messages=_tool_turns(12, tool_chars=6000),  # ~18k estimated tokens
            paths=paths,
        )

    @staticmethod
    def _config() -> ContextConfig:
        return ContextConfig(0.85, 8, 8000, compact_max_tokens=30_000, tool_trim_step_turns=0)

    def test_under_counted_session_compacts_once_real_usage_is_known(self) -> None:
        session = self._session()
        messages = build_llm_messages(session, config=self._config())
        estimated = estimate_context_tokens("", messages)
        with patch("context.resolve_context_limit", return_value=1_000_000):
            self.assertFalse(should_auto_compact("", session, config=self._config()))
            record_prompt_token_usage(session, "", messages, {"prompt_tokens": estimated * 2})
            self.assertAlmostEqual(session.token_estimate_scale, 2.0)
            self.assertTrue(should_auto_compact("", session, config=self._config()))

    def test_scale_never_drops_below_one_and_is_capped(self) -> None:
        session = self._session()
        messages = build_llm_messages(session, config=self._config())
        estimated = estimate_context_tokens("", messages)
        record_prompt_token_usage(session, "", messages, {"prompt_tokens": estimated // 3})
        self.assertEqual(session.token_estimate_scale, 1.0)
        record_prompt_token_usage(session, "", messages, {"prompt_tokens": estimated * 50})
        self.assertEqual(session.token_estimate_scale, 4.0)

    def test_missing_usage_or_image_payload_keeps_previous_scale(self) -> None:
        session = self._session()
        messages = build_llm_messages(session, config=self._config())
        session.token_estimate_scale = 1.5
        record_prompt_token_usage(session, "", messages, None)
        record_prompt_token_usage(session, "", messages, {"prompt_tokens": 0})
        with_image = [*messages, {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "x"}}]}]
        record_prompt_token_usage(session, "", with_image, {"prompt_tokens": 900_000})
        self.assertEqual(session.token_estimate_scale, 1.5)


class CompactPolicyFallbackTests(unittest.TestCase):
    def test_content_policy_uses_mechanical_digest(self) -> None:
        paths = make_temp_agent_paths(self)
        session = Session(
            conversation_id="_policy_compact",
            session_dir=paths.data / "sessions" / "_policy_compact",
            goal="music project",
            meta=SessionMeta(
                topics=[],
                llm_model="deepseek-v4-flash",
                updated_at=utc_now_iso(),
                phase="S4",
                active_shell="project",
                project_root="workspace/music",
                project_plan_status="confirmed",
            ),
            messages=[
                {"role": "user", "content": "u1"},
                {"role": "assistant", "content": "see desktop/src/foo.ts"},
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

        result = compact_context(session, _PolicyRejectLLM(), force=True)
        self.assertTrue(result.compacted)
        self.assertIn("机械摘要", result.message)
        self.assertTrue(session.digest_path.is_file())
        digest_text = session.digest_path.read_text(encoding="utf-8")
        self.assertIn("desktop/src/foo.ts", digest_text)


class _ScriptedSummaryLLM:
    """Returns the queued summaries in order and records each prompt."""

    def __init__(self, *summaries: str) -> None:
        self._summaries = list(summaries)
        self.prompts: list[str] = []

    def chat(self, messages: list[dict[str, Any]], **_kwargs: Any) -> LLMResponse:
        self.prompts.append(messages[0]["content"])
        return LLMResponse(
            model="deepseek-v4-flash",
            content=self._summaries.pop(0),
            tool_calls=[],
            finish_reason="stop",
            usage=None,
            raw={},
        )


class _PolicyRejectLLM:
    def chat(self, *_args: Any, **_kwargs: Any) -> LLMResponse:
        raise LLMApiError("This request was rejected by content policy.", status_code=400)


def _plain_turns(start: int, count: int) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    for index in range(start, start + count):
        messages.append({"role": "user", "content": f"u{index}"})
        messages.append({"role": "assistant", "content": f"a{index}"})
    return messages


class RollingDigestTests(unittest.TestCase):
    """Each compaction folds the previous summary in; only the live part is injected."""

    def _session(self, name: str) -> Session:
        paths = make_temp_agent_paths(self)
        session = Session(
            conversation_id=name,
            session_dir=paths.data / "sessions" / name,
            goal="rolling",
            meta=SessionMeta(topics=[], llm_model="deepseek-v4-flash", updated_at=utc_now_iso(), phase="S4"),
            messages=_plain_turns(0, 11),
            paths=paths,
        )
        session.save()
        return session

    def test_second_compaction_merges_first_summary_and_supersedes_it(self) -> None:
        session = self._session("_rolling_merge")
        llm = _ScriptedSummaryLLM("## 目标\nFIRST-SUMMARY", "## 目标\nMERGED-SUMMARY")

        compact_context(session, llm, force=True)
        self.assertNotIn("已有摘要", llm.prompts[0])
        self.assertIn("FIRST-SUMMARY", load_digest(session) or "")

        session.messages.extend(_plain_turns(11, 3))
        result = compact_context(session, llm, force=True)

        self.assertEqual(result.digest_section, 2)
        self.assertIn("已有摘要：\n# 压缩 1（累计）\n\n## 目标\nFIRST-SUMMARY", llm.prompts[1])
        injected = load_digest(session) or ""
        self.assertIn("MERGED-SUMMARY", injected)
        self.assertNotIn("FIRST-SUMMARY", injected)
        on_disk = session.digest_path.read_text(encoding="utf-8")
        self.assertIn("FIRST-SUMMARY", on_disk, "digest.md stays append-only")
        self.assertEqual(count_digest_sections(session.digest_path), 2)

    def test_digest_written_before_cumulative_sections_is_injected_whole_then_merged(self) -> None:
        session = self._session("_rolling_legacy")
        session.session_dir.mkdir(parents=True, exist_ok=True)
        session.digest_path.write_text("# 压缩 1\n\nLEGACY-ONE\n\n# 压缩 2\n\nLEGACY-TWO\n", encoding="utf-8")
        injected = load_digest(session) or ""
        self.assertIn("LEGACY-ONE", injected)
        self.assertIn("LEGACY-TWO", injected)

        llm = _ScriptedSummaryLLM("## 目标\nMERGED-SUMMARY")
        result = compact_context(session, llm, force=True)

        self.assertEqual(result.digest_section, 3)
        self.assertIn("LEGACY-ONE", llm.prompts[0])
        self.assertIn("LEGACY-TWO", llm.prompts[0])
        self.assertEqual(load_digest(session), "# 压缩 3（累计）\n\n## 目标\nMERGED-SUMMARY")

    def test_mechanical_section_does_not_supersede_earlier_summary(self) -> None:
        session = self._session("_rolling_mechanical")
        compact_context(session, _ScriptedSummaryLLM("## 目标\nFIRST-SUMMARY"), force=True)
        session.messages.extend(_plain_turns(11, 3))

        result = compact_context(session, _PolicyRejectLLM(), force=True)

        self.assertIn("机械摘要", result.message)
        injected = load_digest(session) or ""
        self.assertIn("FIRST-SUMMARY", injected)
        self.assertIn("# 压缩 2\n", injected)

    def test_empty_model_reply_carries_previous_summary_forward(self) -> None:
        session = self._session("_rolling_empty")
        llm = _ScriptedSummaryLLM("## 目标\nFIRST-SUMMARY", "   ")
        compact_context(session, llm, force=True)
        session.messages.extend(_plain_turns(11, 3))

        compact_context(session, llm, force=True)

        self.assertIn("FIRST-SUMMARY", load_digest(session) or "")


if __name__ == "__main__":
    unittest.main()
