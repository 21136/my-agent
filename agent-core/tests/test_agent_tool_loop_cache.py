"""Parent tool loop freezes static system across rounds (M0)."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from agent import Agent
from llm_client import LLMResponse
from session import Session, SessionMeta, utc_now_iso
from tests.isolation_helpers import temporary_agent_paths
from tools.executor import ExecutorSession, ToolExecutor
from tools.registry import ToolRegistry


class _CaptureLLM:
    def __init__(self, responses: list[LLMResponse]) -> None:
        self._responses = list(responses)
        self.payloads: list[list[dict[str, Any]]] = []

    def set_cancel_event(self, _event: Any) -> None:
        return None

    def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> LLMResponse:
        self.payloads.append(messages)
        if not self._responses:
            raise AssertionError("no mock responses left")
        return self._responses.pop(0)


class AgentToolLoopCacheTests(unittest.TestCase):
    def _make_agent(self, paths, session, llm: _CaptureLLM) -> Agent:
        registry = ToolRegistry.load(paths)
        executor = ToolExecutor(
            registry=registry,
            session=ExecutorSession(session_dir=session.session_dir),
            confirm_fn=lambda _req: True,
        )
        return Agent(session=session, executor=executor, llm=llm)

    def test_static_system_identical_across_tool_rounds(self) -> None:
        with temporary_agent_paths() as paths:
            session = Session(
                conversation_id="tool-loop-cache",
                session_dir=paths.data / "sessions" / "tool-loop-cache",
                goal="list docs",
                meta=SessionMeta(
                    topics=[],
                    llm_model="0x567-pro",
                    updated_at=utc_now_iso(),
                    phase="S4",
                    turn_mode="agent",
                ),
                messages=[],
                paths=paths,
            )
            session.save()
            session.turn_intent = "qa"

            list_args = json.dumps({"path": "docs"}, ensure_ascii=False)
            llm = _CaptureLLM(
                [
                    LLMResponse(
                        model="0x567-pro",
                        content=None,
                        tool_calls=[
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": "list_dir",
                                    "arguments": list_args,
                                },
                            }
                        ],
                        finish_reason="tool_calls",
                        usage=None,
                        raw={},
                    ),
                    LLMResponse(
                        model="0x567-pro",
                        content="done",
                        tool_calls=[],
                        finish_reason="stop",
                        usage=None,
                        raw={},
                    ),
                ]
            )
            agent = self._make_agent(paths, session, llm)

            with patch.object(agent, "_maybe_pre_spawn_plan", return_value=None):
                with patch(
                    "turn_intent.should_spawn_explore_for_turn",
                    return_value=False,
                ):
                    result = agent.run_turn("list docs folder")

            self.assertEqual(len(llm.payloads), 2)
            first_static = llm.payloads[0][0]
            second_static = llm.payloads[1][0]
            self.assertEqual(first_static, second_static)
            self.assertEqual(first_static["role"], "system")
            self.assertIsInstance(first_static.get("content"), str)
            self.assertEqual(result.tool_rounds, 1)

    def test_emits_llm_usage_event_after_chat(self) -> None:
        with temporary_agent_paths() as paths:
            session = Session(
                conversation_id="tool-loop-usage",
                session_dir=paths.data / "sessions" / "tool-loop-usage",
                goal="hi",
                meta=SessionMeta(
                    topics=[],
                    llm_model="0x567-pro",
                    updated_at=utc_now_iso(),
                    phase="S4",
                    turn_mode="agent",
                ),
                messages=[],
                paths=paths,
            )
            session.save()
            session.turn_intent = "qa"

            llm = _CaptureLLM(
                [
                    LLMResponse(
                        model="0x567-pro",
                        content="ok",
                        tool_calls=[],
                        finish_reason="stop",
                        usage={
                            "prompt_tokens": 2000,
                            "completion_tokens": 10,
                            "prompt_tokens_details": {"cached_tokens": 1500},
                        },
                        raw={},
                    ),
                ]
            )
            agent = self._make_agent(paths, session, llm)
            events: list[dict[str, Any]] = []
            agent.on_turn_event = events.append

            with patch.object(agent, "_maybe_pre_spawn_plan", return_value=None):
                with patch(
                    "turn_intent.should_spawn_explore_for_turn",
                    return_value=False,
                ):
                    agent.run_turn("hello")

            usage_events = [e for e in events if e.get("type") == "llm.usage"]
            self.assertEqual(len(usage_events), 1)
            self.assertEqual(usage_events[0]["cached_tokens"], 1500)
            self.assertEqual(usage_events[0]["cache_ratio"], 0.75)


if __name__ == "__main__":
    unittest.main()
