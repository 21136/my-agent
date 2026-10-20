from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from agent import Agent
from llm_client import LLMResponse
from session import create_new
from tests.isolation_helpers import temporary_agent_paths
from turn_intent import (
    classify_turn,
    is_attachment_qa_turn,
    is_requirement_input,
    should_use_chat_first_attachment_turn,
)


ATTACHMENT_INTERVIEW = (
    "[附件]\n"
    "- resume.pdf · workspace/_drops/s1/resume.pdf · 120 KB · application/pdf\n"
    "\n"
    "请根据简历和附件里的 Java 笔记，模拟一场面试并提问。"
)


class AttachmentQaTurnTests(unittest.TestCase):
    def test_attachment_interview_is_qa_not_requirements(self) -> None:
        self.assertTrue(is_attachment_qa_turn(ATTACHMENT_INTERVIEW))
        self.assertFalse(is_requirement_input(ATTACHMENT_INTERVIEW))
        self.assertEqual(classify_turn(ATTACHMENT_INTERVIEW), "qa")

    def test_chat_first_attachment_in_agent_mode(self) -> None:
        self.assertTrue(
            should_use_chat_first_attachment_turn(
                ATTACHMENT_INTERVIEW,
                "qa",
                turn_mode="agent",
            )
        )

    def test_agent_chat_first_uses_no_tools(self) -> None:
        with temporary_agent_paths() as paths:
            session = create_new(paths, conversation_id="_attach_qa")
            session.set_turn_mode("agent")
            calls: list[dict] = []

            class FakeLlm:
                def set_cancel_event(self, _event) -> None:
                    return None

                def chat(self, messages, **kwargs):
                    calls.append(kwargs)
                    return LLMResponse(
                        model="mock",
                        content="好的，我们从项目经历开始：请介绍你最近做的 Java 服务。",
                        tool_calls=[],
                        finish_reason="stop",
                        usage=None,
                        raw={},
                    )

            agent = Agent.create(session, llm=FakeLlm(), confirm_fn=Mock())
            result = agent.run_turn(ATTACHMENT_INTERVIEW, spawn_explore=False)

            self.assertEqual(result.turn_intent, "qa")
            self.assertEqual(result.tool_rounds, 0)
            self.assertTrue(calls)
            self.assertIsNone(calls[0].get("tools"))


if __name__ == "__main__":
    unittest.main()
