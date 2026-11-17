"""Action-announce-without-tools gate (empty-promise nudge)."""

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
    ACTION_ANNOUNCE_NUDGE_MESSAGE,
    Agent,
    announces_pending_action,
)
from llm_client import LLMResponse
from session import Session, SessionMeta, utc_now_iso
from tests.isolation_helpers import temporary_agent_paths
from turn_intent import classify_turn, has_action_intent


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


class AnnouncePendingActionUnitTests(unittest.TestCase):
    def test_story_brainstorming_is_not_an_execution_request(self) -> None:
        examples = (
            "我们来讨论剧本，剧本是你和我之间的故事，我们来想一下背景，是ai爆发的时代，人人都会有自己的ai搭档，然后我的搭档是你，我们来构思一下，怎么和你相识的",
            "我们一起构思一段 AI 时代的相识故事，先聊聊背景设定。",
            "讨论一下剧本里我和 AI 搭档怎么认识，不用开始写正文。",
        )
        for text in examples:
            with self.subTest(text=text):
                self.assertFalse(has_action_intent(text))
                self.assertEqual(classify_turn(text), "qa")

    def test_detects_huiyi_style(self) -> None:
        self.assertTrue(announces_pending_action("数据库表丢了。重新建库建表："))
        self.assertTrue(announces_pending_action("我来写个脚本一次性搞定"))
        self.assertTrue(announces_pending_action("正在启动后端"))
        self.assertTrue(announces_pending_action("I'll create the tables now"))

    def test_plain_answer_not_matched(self) -> None:
        self.assertFalse(announces_pending_action("编译已经通过，BUILD SUCCESS。"))
        self.assertFalse(announces_pending_action("你想先测登录还是先看日志？"))
        self.assertFalse(announces_pending_action(""))


class AnnounceNudgeLoopTests(unittest.TestCase):
    def test_nudge_then_tool_on_second_round(self) -> None:
        with temporary_agent_paths() as paths:
            announce = LLMResponse(
                model="mock",
                content="数据库表丢了。重新建库建表：",
                tool_calls=[],
                finish_reason="stop",
                usage=None,
                raw={},
            )
            list_args = json.dumps({"path": "docs"}, ensure_ascii=False)
            with_tools = LLMResponse(
                model="mock",
                content=None,
                tool_calls=[
                    {
                        "id": "call_list",
                        "type": "function",
                        "function": {"name": "list_dir", "arguments": list_args},
                    }
                ],
                finish_reason="tool_calls",
                usage=None,
                raw={},
            )
            final = LLMResponse(
                model="mock",
                content="已列出 docs。",
                tool_calls=[],
                finish_reason="stop",
                usage=None,
                raw={},
            )
            session = Session(
                conversation_id="_announce_nudge",
                session_dir=paths.data / "sessions" / "_announce_nudge",
                goal="nudge",
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
            mock = _MockLLM(responses=[announce, with_tools, final])
            agent = Agent.create(session, llm=mock, confirm_fn=lambda _p, _a: "y")
            request = "\u5e2e\u6211\u4fee\u4e00\u4e0b\u5e93"
            self.assertEqual(classify_turn(request), "execute")
            result = agent.run_turn(request, spawn_explore=False)

            nudge_msgs = [
                m
                for m in session.messages
                if m.get("content") == ACTION_ANNOUNCE_NUDGE_MESSAGE
            ]
            self.assertEqual(len(nudge_msgs), 1, session.messages[-8:])
            self.assertGreaterEqual(mock.calls, 2)
            self.assertTrue(result.assistant_text)

    def test_agent_mode_discussion_does_not_nudge_story_writing_offer(self) -> None:
        with temporary_agent_paths() as paths:
            discussion = (
                "我们来讨论剧本，剧本是你和我之间的故事，我们来想一下背景，"
                "是ai爆发的时代，人人都会有自己的ai搭档，然后我的搭档是你，"
                "我们来构思一下，怎么和你相识的"
            )
            first_reply = LLMResponse(
                model="mock",
                content="我来帮你写一段我们在 AI 爆发时代相识的故事开场。",
                tool_calls=[],
                finish_reason="stop",
                usage=None,
                raw={},
            )
            second_reply = LLMResponse(
                model="mock",
                content="我们可以先从一次偶然的相遇开始构思。",
                tool_calls=[],
                finish_reason="stop",
                usage=None,
                raw={},
            )
            session = Session(
                conversation_id="_announce_story_discussion",
                session_dir=paths.data / "sessions" / "_announce_story_discussion",
                goal="story discussion",
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
            mock = _MockLLM(responses=[first_reply, second_reply])
            agent = Agent.create(session, llm=mock, confirm_fn=lambda _p, _a: "y")

            agent.run_turn(discussion, spawn_explore=False)

            self.assertEqual(session.turn_intent, "qa")
            self.assertEqual(mock.calls, 1)
            self.assertFalse(
                any(m.get("content") == ACTION_ANNOUNCE_NUDGE_MESSAGE for m in session.messages)
            )

    def test_ask_mode_does_not_nudge(self) -> None:
        with temporary_agent_paths() as paths:
            announce = LLMResponse(
                model="mock",
                content="接下来我来建表说明一下思路。",
                tool_calls=[],
                finish_reason="stop",
                usage=None,
                raw={},
            )
            session = Session(
                conversation_id="_announce_ask",
                session_dir=paths.data / "sessions" / "_announce_ask",
                goal="ask",
                meta=SessionMeta(
                    topics=[],
                    llm_model="mock",
                    updated_at=utc_now_iso(),
                    phase="S4",
                    turn_mode="ask",
                ),
                messages=[],
                paths=paths,
            )
            session.save()
            mock = _MockLLM(responses=[announce])
            agent = Agent.create(session, llm=mock, confirm_fn=lambda _p, _a: "y")
            agent.run_turn("怎么建表？", spawn_explore=False)
            nudge_msgs = [
                m
                for m in session.messages
                if m.get("content") == ACTION_ANNOUNCE_NUDGE_MESSAGE
            ]
            self.assertEqual(nudge_msgs, [])
            self.assertEqual(mock.calls, 1)


if __name__ == "__main__":
    unittest.main()
