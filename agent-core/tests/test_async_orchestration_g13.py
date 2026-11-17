"""G13 service tool choice and discussion behavior."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from typing import Any

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from agent import Agent
from llm_client import LLMResponse
from session import Session, SessionMeta, utc_now_iso
from tests.isolation_helpers import temporary_agent_paths


class OrchestrationDeferGuidanceTests(unittest.TestCase):
    def test_discussion_of_deferred_service_check_can_finish_as_text(self) -> None:
        with temporary_agent_paths() as paths:
            reply = LLMResponse(model="mock", content="This is a proposed checking schedule.",
                                tool_calls=[], finish_reason="stop", usage=None, raw={})
            session = Session(
                conversation_id="_it560_discussion",
                session_dir=paths.data / "sessions" / "_it560_discussion",
                goal="service discussion",
                meta=SessionMeta(topics=[], llm_model="mock", updated_at=utc_now_iso(),
                                 phase="S4", turn_mode="agent"),
                messages=[], paths=paths,
            )
            session.save()
            mock = _MockLLM([reply])
            agent = Agent.create(session, llm=mock, confirm_fn=lambda _p, _a: "y")
            result = agent.run_turn("Explain waiting before checking service logs.", spawn_explore=False)

            self.assertEqual(mock.calls, 1)
            self.assertEqual(result.assistant_text, "This is a proposed checking schedule.")
            self.assertFalse(any(m.get("role") == "tool" for m in session.messages))


class _MockLLM:
    def __init__(self, responses: list[LLMResponse]) -> None:
        self._responses = list(responses)
        self.calls = 0
        self.requests: list[tuple[list[dict[str, Any]], Any]] = []

    def set_cancel_event(self, _event: Any) -> None:
        return None

    def chat(self, *args: Any, **kwargs: Any) -> LLMResponse:
        self.calls += 1
        self.requests.append((args[0], kwargs.get("tools")))
        if not self._responses:
            raise AssertionError("no mock LLM responses left")
        return self._responses.pop(0)


class OrchestrationDeferNudgeLoopTests(unittest.TestCase):
    def test_model_selects_safe_run_service_status_tool(self) -> None:
        with temporary_agent_paths() as paths:
            run_args = json.dumps({
                "tool_name": "run_service",
                "arguments": {"action": "status", "name": "gateway"},
            }, ensure_ascii=True)
            tool_response = LLMResponse(
                model="mock", content=None,
                tool_calls=[{"id": "call_status", "type": "function",
                             "function": {"name": "run_evolved", "arguments": run_args}}],
                finish_reason="tool_calls", usage=None, raw={},
            )
            final = LLMResponse(model="mock", content="Status checked.", tool_calls=[],
                                 finish_reason="stop", usage=None, raw={})
            session = Session(
                conversation_id="_it560_model_choice",
                session_dir=paths.data / "sessions" / "_it560_model_choice",
                goal="service status",
                meta=SessionMeta(topics=[], llm_model="mock", updated_at=utc_now_iso(),
                                 phase="S4", turn_mode="agent"),
                messages=[], paths=paths,
            )
            session.save()
            mock = _MockLLM([tool_response, final])
            agent = Agent.create(session, llm=mock, confirm_fn=lambda _p, _a: "y")
            result = agent.run_turn("Check gateway service status.", spawn_explore=False)

            self.assertEqual(mock.calls, 2)
            self.assertTrue(any(m.get("role") == "tool" for m in session.messages))
            self.assertTrue(result.assistant_text)
            available = {item["function"]["name"] for item in mock.requests[0][1]}
            self.assertIn("run_evolved", available)


if __name__ == "__main__":
    unittest.main()
