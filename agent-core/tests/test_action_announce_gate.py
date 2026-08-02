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
            result = agent.run_turn("修一下库", spawn_explore=False)

            nudge_msgs = [
                m
                for m in session.messages
                if m.get("content") == ACTION_ANNOUNCE_NUDGE_MESSAGE
            ]
            self.assertEqual(len(nudge_msgs), 1, session.messages[-8:])
            self.assertGreaterEqual(mock.calls, 2)
            self.assertTrue(result.assistant_text)

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
