"""Pack 6 T-5602 · G13 orchestration defer nudge (IT-560)."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from typing import Any

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from agent import (
    ORCH_DEFER_NUDGE_MESSAGE,
    Agent,
    announces_orchestration_defer,
    announces_pending_action,
    pending_action_nudge_message,
)
from llm_client import LLMResponse
from session import Session, SessionMeta, utc_now_iso
from tests.isolation_helpers import temporary_agent_paths


class OrchestrationDeferDetectTests(unittest.TestCase):
    """IT-560 · announces_orchestration_defer."""

    def test_it560_zh_wait_then_logs(self) -> None:
        self.assertTrue(announces_orchestration_defer("等 20 秒后查 gateway 日志再起前端。"))
        self.assertTrue(announces_orchestration_defer("等15～20秒我检查日志。"))

    def test_it560_en_wait_then_check(self) -> None:
        self.assertTrue(
            announces_orchestration_defer("wait 15 seconds then check logs before frontend.")
        )
        self.assertTrue(
            announces_orchestration_defer("After 20 seconds check gateway logs.")
        )

    def test_it560_later_start_phrases(self) -> None:
        self.assertTrue(announces_orchestration_defer("启动中，稍后再起前端。"))
        self.assertTrue(announces_orchestration_defer("我先去查一下再告诉你。"))

    def test_plain_status_not_matched(self) -> None:
        self.assertFalse(announces_orchestration_defer("gateway 已在 8080 running。"))
        self.assertFalse(announces_orchestration_defer("你想先测登录还是先看日志？"))

    def test_pending_action_nudge_prefers_orchestration(self) -> None:
        text = "等 20 秒后查日志。"
        self.assertEqual(pending_action_nudge_message(text), ORCH_DEFER_NUDGE_MESSAGE)
        self.assertFalse(announces_pending_action(text))


class _MockLLM:
    def __init__(self, responses: list[LLMResponse]) -> None:
        self._responses = list(responses)
        self.calls = 0

    def set_cancel_event(self, _event: Any) -> None:
        return None

    def chat(self, *args: Any, **kwargs: Any) -> LLMResponse:
        self.calls += 1
        if not self._responses:
            raise AssertionError("no mock LLM responses left")
        return self._responses.pop(0)


class OrchestrationDeferNudgeLoopTests(unittest.TestCase):
    def test_it560_nudge_then_tool_on_second_round(self) -> None:
        with temporary_agent_paths() as paths:
            defer = LLMResponse(
                model="mock",
                content="服务已 start，等 20 秒后查 gateway 日志再起前端。",
                tool_calls=[],
                finish_reason="stop",
                usage=None,
                raw={},
            )
            run_args = json.dumps(
                {
                    "tool_name": "run_service",
                    "arguments": {"action": "wait", "name": "gateway", "timeout_sec": 25},
                },
                ensure_ascii=False,
            )
            with_tools = LLMResponse(
                model="mock",
                content=None,
                tool_calls=[
                    {
                        "id": "call_wait",
                        "type": "function",
                        "function": {"name": "run_evolved", "arguments": run_args},
                    }
                ],
                finish_reason="tool_calls",
                usage=None,
                raw={},
            )
            final = LLMResponse(
                model="mock",
                content="已 wait gateway。",
                tool_calls=[],
                finish_reason="stop",
                usage=None,
                raw={},
            )
            session = Session(
                conversation_id="_it560_nudge",
                session_dir=paths.data / "sessions" / "_it560_nudge",
                goal="orch",
                meta=SessionMeta(
                    topics=[],
                    llm_model="mock",
                    updated_at=utc_now_iso(),
                    phase="S4",
                    turn_mode="agent",
                ),
                messages=[],
                paths=paths,
            )
            session.save()
            mock = _MockLLM(responses=[defer, with_tools, final])
            agent = Agent.create(session, llm=mock, confirm_fn=lambda _p, _a: "y")
            result = agent.run_turn("起 gateway 和前端", spawn_explore=False)

            nudge_msgs = [
                m
                for m in session.messages
                if m.get("content") == ORCH_DEFER_NUDGE_MESSAGE
            ]
            self.assertEqual(len(nudge_msgs), 1, session.messages[-8:])
            self.assertGreaterEqual(mock.calls, 2)
            self.assertTrue(result.assistant_text)


if __name__ == "__main__":
    unittest.main()
