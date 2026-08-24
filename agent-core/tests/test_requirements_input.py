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
from turn_intent import classify_turn, is_requirement_input, should_spawn_explore
from tests.isolation_helpers import temporary_agent_paths


PROJECT_BRIEF = (
    "1.项目简介\n\n"
    "随着网络的发展，市场上出现了许多音乐网站，但大部分平台注册成为歌手的门槛较高，"
    "导致音乐创作者无法方便地分享自己的作品。系统希望降低发布门槛，为普通用户提供播放、"
    "推荐、收藏、歌单、历史和通知能力，同时为创作者提供上传、审核和发布流程。"
    "后台管理员负责用户、歌手和歌曲管理。"
)


class RequirementsInputTests(unittest.TestCase):
    def test_project_brief_is_read_only_requirements_input(self) -> None:
        self.assertTrue(is_requirement_input(PROJECT_BRIEF))
        self.assertEqual(classify_turn(PROJECT_BRIEF), "requirements")
        self.assertFalse(should_spawn_explore(PROJECT_BRIEF))
        self.assertEqual(classify_turn("请根据这份简介创建项目脚手架"), "execute")

    def test_requirements_turn_does_not_expose_tools(self) -> None:
        with temporary_agent_paths() as paths:
            session = create_new(paths, conversation_id="_requirements_input")
            calls: list[dict] = []

            class FakeLlm:
                def set_cancel_event(self, _event) -> None:
                    return None

                def chat(self, messages, **kwargs):
                    calls.append(kwargs)
                    return LLMResponse(
                        model="mock",
                        content="已收到项目简介，目前先停留在需求阶段，不执行写入或创建工具。",
                        tool_calls=[],
                        finish_reason="stop",
                        usage=None,
                        raw={},
                    )

            agent = Agent.create(session, llm=FakeLlm(), confirm_fn=Mock())
            result = agent.run_turn(PROJECT_BRIEF, spawn_explore=True)

            self.assertEqual(result.turn_intent, "requirements")
            self.assertEqual(result.tool_rounds, 0)
            self.assertTrue(calls)
            self.assertIsNone(calls[0].get("tools"))
            self.assertFalse(any(message.get("role") == "tool" for message in session.messages))

    def test_requirements_turn_blocks_even_hallucinated_tool_calls(self) -> None:
        with temporary_agent_paths() as paths:
            session = create_new(paths, conversation_id="_requirements_hallucinated_tool")

            class FakeLlm:
                def set_cancel_event(self, _event) -> None:
                    return None

                def chat(self, messages, **kwargs):
                    return LLMResponse(
                        model="mock",
                        content=None,
                        tool_calls=[
                            {
                                "id": "call-forbidden",
                                "type": "function",
                                "function": {
                                    "name": "write_text",
                                    "arguments": '{"path":"workspace/should-not-exist.txt","content":"x"}',
                                },
                            }
                        ],
                        finish_reason="tool_calls",
                        usage=None,
                        raw={},
                    )

            agent = Agent.create(session, llm=FakeLlm(), confirm_fn=Mock())
            result = agent.run_turn(PROJECT_BRIEF)

            self.assertEqual(result.tool_rounds, 0)
            self.assertIn("只读对话", result.assistant_text)
            self.assertFalse(any(message.get("role") == "tool" for message in session.messages))


if __name__ == "__main__":
    unittest.main()
