"""LLM-selected tool execution and discussion boundaries."""

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
from turn_intent import classify_turn, has_action_intent


class _MockLLM:
    def __init__(self, responses: list[LLMResponse]) -> None:
        self._responses = list(responses)
        self.calls = 0
        self.requests: list[tuple[list[dict[str, Any]], Any]] = []

    def set_cancel_event(self, _event: Any) -> None:
        return None

    def chat(self, *args: Any, **kwargs: Any) -> LLMResponse:
        self.calls += 1
        if args:
            self.requests.append((args[0], kwargs.get("tools")))
        if not self._responses:
            raise AssertionError("no mock LLM responses left")
        return self._responses.pop(0)


class AnnounceNudgeLoopTests(unittest.TestCase):
    def test_story_brainstorming_is_not_an_execution_request(self) -> None:
        examples = (
            "\u6211\u4eec\u6765\u8ba8\u8bba\u5267\u672c\uff0c\u5267\u672c\u662f\u4f60\u548c\u6211\u4e4b\u95f4\u7684\u6545\u4e8b\uff0c\u6211\u4eec\u6765\u60f3\u4e00\u4e0b\u80cc\u666f\uff0c\u662fai\u7206\u53d1\u7684\u65f6\u4ee3\uff0c\u4eba\u4eba\u90fd\u4f1a\u6709\u81ea\u5df1\u7684ai\u642d\u6863\uff0c\u7136\u540e\u6211\u7684\u642d\u6863\u662f\u4f60\uff0c\u6211\u4eec\u6765\u6784\u601d\u4e00\u4e0b\uff0c\u600e\u4e48\u548c\u4f60\u76f8\u8bc6\u7684",
            "\u6211\u4eec\u4e00\u8d77\u6784\u601d\u4e00\u6bb5 AI \u65f6\u4ee3\u7684\u76f8\u8bc6\u6545\u4e8b\uff0c\u5148\u804a\u804a\u80cc\u666f\u8bbe\u5b9a\u3002",
            "\u8ba8\u8bba\u4e00\u4e0b\u5267\u672c\u91cc\u6211\u548c AI \u642d\u6863\u600e\u4e48\u8ba4\u8bc6\uff0c\u4e0d\u7528\u5f00\u59cb\u5199\u6b63\u6587\u3002",
        )
        for text in examples:
            with self.subTest(text=text):
                self.assertFalse(has_action_intent(text))
                self.assertEqual(classify_turn(text), "qa")

    def test_action_announcement_is_not_a_lexical_retry_trigger(self) -> None:
        with temporary_agent_paths() as paths:
            reply = LLMResponse(model="mock", content="I'll create the tables now.",
                                tool_calls=[], finish_reason="stop", usage=None, raw={})
            session = Session(
                conversation_id="_announce_plain_text",
                session_dir=paths.data / "sessions" / "_announce_plain_text",
                goal="plain response",
                meta=SessionMeta(topics=[], llm_model="mock", updated_at=utc_now_iso(),
                                 phase="S4", turn_mode="agent"),
                messages=[], paths=paths,
            )
            session.save()
            mock = _MockLLM([reply])
            agent = Agent.create(session, llm=mock, confirm_fn=lambda *_args: "y")

            result = agent.run_turn("Explain this wording without making changes.", spawn_explore=False)

            self.assertEqual(mock.calls, 1)
            self.assertEqual(result.assistant_text, "I'll create the tables now.")
            self.assertFalse(any(message.get("role") == "tool" for message in session.messages))

    def test_model_selected_tool_executes_without_interstitial_nudge(self) -> None:
        with temporary_agent_paths() as paths:
            with_tools = LLMResponse(
                model="mock", content=None,
                tool_calls=[{"id": "call_list", "type": "function",
                             "function": {"name": "list_dir", "arguments": json.dumps({"path": "docs"})}}],
                finish_reason="tool_calls", usage=None, raw={},
            )
            final = LLMResponse(model="mock", content="Checked docs.", tool_calls=[],
                                 finish_reason="stop", usage=None, raw={})
            session = Session(
                conversation_id="_announce_nudge",
                session_dir=paths.data / "sessions" / "_announce_nudge",
                goal="model-selected tool",
                meta=SessionMeta(topics=[], llm_model="mock", updated_at=utc_now_iso(),
                                 phase="S4", turn_mode="agent"),
                messages=[], paths=paths,
            )
            session.save()
            mock = _MockLLM([with_tools, final])
            agent = Agent.create(session, llm=mock, confirm_fn=lambda _p, _a: "y")
            result = agent.run_turn("fix the database", spawn_explore=False)

            self.assertEqual(mock.calls, 2)
            self.assertIn("never claim an action completed without that evidence", str(mock.requests[0][0]))
            self.assertTrue(any(m.get("role") == "tool" for m in session.messages))
            self.assertTrue(result.assistant_text)

    def test_ask_mode_does_not_force_a_tool_for_action_words(self) -> None:
        with temporary_agent_paths() as paths:
            answer = LLMResponse(model="mock", content="This is only a phrasing example.",
                                 tool_calls=[], finish_reason="stop", usage=None, raw={})
            session = Session(
                conversation_id="_announce_ask",
                session_dir=paths.data / "sessions" / "_announce_ask",
                goal="ask",
                meta=SessionMeta(topics=[], llm_model="mock", updated_at=utc_now_iso(),
                                 phase="S4", turn_mode="ask"),
                messages=[], paths=paths,
            )
            session.save()
            mock = _MockLLM([answer])
            agent = Agent.create(session, llm=mock, confirm_fn=lambda _p, _a: "y")
            agent.run_turn("Explain the phrase 'I will create the table'.", spawn_explore=False)

            self.assertEqual(mock.calls, 1)
            self.assertFalse(any(m.get("role") == "tool" for m in session.messages))


if __name__ == "__main__":
    unittest.main()
