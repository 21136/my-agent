"""Provider/API errors must not be reported as tool-budget exhaustion."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from agent import Agent
from llm_client import LLMApiError, LLMNetworkError, LLMResponse
from session import Session, SessionMeta, utc_now_iso
from tests.isolation_helpers import make_temp_agent_paths

_SESSION_LIMIT = "Active session limit reached: 15 sessions, limit 15"


class _RaisingLLM:
    def __init__(self, exc: BaseException) -> None:
        self.exc = exc
        self.calls = 0

    def set_cancel_event(self, _event: Any) -> None:
        return None

    def chat(self, *_args: Any, **_kwargs: Any) -> LLMResponse:
        self.calls += 1
        raise self.exc


class _RecoveringLLM:
    def __init__(self, exc: BaseException) -> None:
        self.exc = exc
        self.calls = 0

    def set_cancel_event(self, _event: Any) -> None:
        return None

    def chat(self, *_args: Any, **_kwargs: Any) -> LLMResponse:
        self.calls += 1
        if self.calls == 1:
            raise self.exc
        return LLMResponse(
            model="mock",
            content="已经改好了。",
            tool_calls=[],
            finish_reason="stop",
            usage=None,
            raw={},
        )


class ProviderTransportErrorTests(unittest.TestCase):
    def _agent(self, llm: Any) -> Agent:
        paths = make_temp_agent_paths(self)
        session = Session(
            conversation_id="_provider_transport",
            session_dir=paths.data / "sessions" / "_provider_transport",
            goal="",
            meta=SessionMeta(
                topics=[],
                llm_model="deepseek-v4-flash",
                updated_at=utc_now_iso(),
                phase="S4",
                active_shell="project",
                turn_mode="agent",
                project_runaway_enabled=False,
            ),
            messages=[{"role": "user", "content": "改一下 README"}],
            paths=paths,
        )
        session.save()
        agent = Agent.create(session, llm=llm, confirm_fn=lambda _p, _a: "y")
        agent.on_turn_event = lambda _event: None
        return agent

    def _run(self, llm: Any) -> tuple[Any, list[float]]:
        agent = self._agent(llm)
        sleeps: list[float] = []

        def _sleep(seconds: float) -> None:
            sleeps.append(float(seconds))

        with patch("agent.time.sleep", _sleep):
            result = agent._run_execute_segments(
                intent="execute",
                subagent_used=False,
                subagent_tool_rounds=0,
                tools=[],
                model="deepseek-v4-flash",
            )
        return result, sleeps

    def test_session_limit_is_a_model_service_error_not_the_tool_budget(self) -> None:
        llm = _RaisingLLM(
            LLMApiError(_SESSION_LIMIT, status_code=429),
        )
        result, sleeps = self._run(llm)
        self.assertEqual(llm.calls, 2)
        self.assertLess(llm.calls, 15)
        self.assertEqual(sleeps, [2.0])
        self.assertFalse(result.tool_loop_exceeded)
        self.assertEqual(result.finish_reason, "error")
        self.assertEqual(result.tool_rounds, 0)
        self.assertIn("模型服务达到并发上限", result.assistant_text)
        self.assertIn("稍后重试", result.assistant_text)
        self.assertNotIn("工具预算", result.assistant_text)
        self.assertNotIn("继续", result.assistant_text)
        self.assertNotIn("Active session", result.assistant_text)
        self.assertNotIn("上限 15", result.assistant_text)

    def test_rate_limit_auth_network_and_5xx_stay_model_service_errors(self) -> None:
        cases = [
            (LLMApiError("rate limit exceeded", status_code=429), "请求过于频繁"),
            (LLMApiError("invalid api key", status_code=401), "鉴权失败"),
            (LLMNetworkError("connection refused"), "暂时连不上"),
            (LLMApiError("internal error", status_code=500), "暂时不可用"),
        ]
        for exc, snippet in cases:
            with self.subTest(snippet=snippet):
                llm = _RaisingLLM(exc)
                result, sleeps = self._run(llm)
                self.assertEqual(llm.calls, 2)
                self.assertEqual(sleeps, [2.0])
                self.assertFalse(result.tool_loop_exceeded)
                self.assertEqual(result.finish_reason, "error")
                self.assertIn(snippet, result.assistant_text)
                self.assertNotIn("工具预算", result.assistant_text)
                self.assertNotIn("继续", result.assistant_text)
                self.assertNotIn(str(exc), result.assistant_text)

    def test_one_provider_retry_does_not_consume_the_tool_round_budget(self) -> None:
        llm = _RecoveringLLM(LLMApiError(_SESSION_LIMIT, status_code=429))
        result, sleeps = self._run(llm)
        self.assertEqual(llm.calls, 2)
        self.assertEqual(sleeps, [2.0])
        self.assertEqual(result.tool_rounds, 0)
        self.assertFalse(result.tool_loop_exceeded)
        self.assertEqual(result.finish_reason, "stop")
        self.assertIn("已经改好了", result.assistant_text)
        self.assertNotIn("工具预算", result.assistant_text)


if __name__ == "__main__":
    unittest.main()
