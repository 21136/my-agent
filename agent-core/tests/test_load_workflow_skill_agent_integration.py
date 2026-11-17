"""Registry -> LLM tool defs -> Executor -> Agent tool loop for load_workflow_skill."""

from __future__ import annotations

import contextlib
import json
import shutil
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_AGENT_CORE = Path(__file__).resolve().parents[1]
_REPO_SKILLS = Path(__file__).resolve().parents[2] / "evolve" / "skills"
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from agent import (
    BUILTIN_TOOL_NAMES,
    _BUILTIN_DESCRIPTIONS,
    _BUILTIN_PARAMETERS,
    Agent,
    build_builtin_tool_definition,
    build_llm_tools,
)
from llm_client import LLMResponse
from session import Session, SessionMeta, utc_now_iso
from tests.isolation_helpers import temporary_agent_paths
from tools.executor import ExecutorSession, ToolExecutor
from tools.registry import ToolRegistry


class _CaptureLLM:
    def __init__(self, responses: list[LLMResponse]) -> None:
        self._responses = list(responses)
        self.tool_payloads: list[list[dict]] = []

    def set_cancel_event(self, _event: Any) -> None:
        return None

    def chat(self, messages: list[dict], **kwargs) -> LLMResponse:
        tools = kwargs.get("tools")
        if tools is not None:
            self.tool_payloads.append(tools)
        if not self._responses:
            raise AssertionError("no mock responses left")
        return self._responses.pop(0)


from typing import Any


class LoadWorkflowSkillAgentIntegrationTests(unittest.TestCase):
    def test_every_registry_builtin_has_llm_schema(self) -> None:
        missing_desc = [n for n in BUILTIN_TOOL_NAMES if n not in _BUILTIN_DESCRIPTIONS]
        missing_params = [n for n in BUILTIN_TOOL_NAMES if n not in _BUILTIN_PARAMETERS]
        self.assertEqual(missing_desc, [], f"missing _BUILTIN_DESCRIPTIONS: {missing_desc}")
        self.assertEqual(missing_params, [], f"missing _BUILTIN_PARAMETERS: {missing_params}")
        for name in BUILTIN_TOOL_NAMES:
            definition = build_builtin_tool_definition(name)
            self.assertEqual(definition["function"]["name"], name)

    def test_build_llm_tools_exposes_load_workflow_skill(self) -> None:
        with temporary_agent_paths() as paths:
            from session import create_new

            session = create_new(paths, conversation_id="skill-tool-def")
            session.meta.turn_mode = "agent"
            tools = build_llm_tools(session, registry=ToolRegistry.load(paths))
            names = {item["function"]["name"] for item in tools}
            self.assertIn("load_workflow_skill", names)

    def test_agent_tool_loop_dispatches_load_workflow_skill(self) -> None:
        with temporary_agent_paths() as paths:
            shutil.copytree(_REPO_SKILLS, paths.evolve / "skills", dirs_exist_ok=True)
            session = Session(
                conversation_id="skill-tool-loop",
                session_dir=paths.data / "sessions" / "skill-tool-loop",
                goal="load skill",
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
            skill_args = json.dumps({"skill_id": "project-verification"}, ensure_ascii=False)
            llm = _CaptureLLM(
                [
                    LLMResponse(
                        model="0x567-pro",
                        content=None,
                        tool_calls=[
                            {
                                "id": "call_skill",
                                "type": "function",
                                "function": {
                                    "name": "load_workflow_skill",
                                    "arguments": skill_args,
                                },
                            }
                        ],
                        finish_reason="tool_calls",
                        usage=None,
                        raw={},
                    ),
                    LLMResponse(
                        model="0x567-pro",
                        content="已加载 project-verification，继续处理你的请求。",
                        tool_calls=[],
                        finish_reason="stop",
                        usage=None,
                        raw={},
                    ),
                ]
            )
            registry = ToolRegistry.load(paths)
            executor = ToolExecutor(
                registry=registry,
                session=ExecutorSession(session_dir=session.session_dir),
                confirm_fn=lambda _req: True,
            )
            agent = Agent(session=session, executor=executor, llm=llm)
            executor.bound_session = session

            with contextlib.nullcontext():
                with patch("turn_intent.should_spawn_explore_for_turn", return_value=False):
                    result = agent.run_turn("请加载 project-verification 技能正文")

            self.assertGreaterEqual(result.tool_rounds, 1)
            tool_msgs = [m for m in session.messages if m.get("role") == "tool"]
            self.assertTrue(tool_msgs, "expected tool role message")
            payload = json.loads(tool_msgs[0]["content"])
            self.assertTrue(payload.get("ok"), payload)
            self.assertEqual(payload.get("data", {}).get("skill_id"), "project-verification")
            self.assertFalse(
                "unknown builtin tool" in json.dumps(tool_msgs, ensure_ascii=False).casefold(),
                tool_msgs,
            )
            self.assertTrue(llm.tool_payloads)
            exposed = {t["function"]["name"] for t in llm.tool_payloads[0]}
            self.assertIn("load_workflow_skill", exposed)


if __name__ == "__main__":
    unittest.main()