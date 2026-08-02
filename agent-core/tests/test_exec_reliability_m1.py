"""EXEC-RELIABILITY M1: failure class + playbooks (IT-162)."""

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
from exec_reliability import (
    PLAYBOOK_NPM_CORRUPT,
    PLAYBOOK_NUDGES,
    PLAYBOOK_SQL_MISSING,
    classify_failure,
    match_playbook,
)
from llm_client import LLMResponse
from session import Session, SessionMeta, utc_now_iso
from tests.isolation_helpers import temporary_agent_paths
from tools.schema import tool_fail, tool_ok


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


class FailureClassUnitTests(unittest.TestCase):
    def test_npm_corrupt_is_b(self) -> None:
        result = tool_fail(
            "run_evolved",
            "execution_error",
            "esbuild failed",
            details={
                "exit_code": 1,
                "logs_tail": "Error: Unexpected end of file in node_modules/@esbuild/win32-x64",
            },
        )
        insight = classify_failure(result)
        self.assertEqual(insight.failure_class, "B")
        self.assertEqual(insight.playbook_id, PLAYBOOK_NPM_CORRUPT)

    def test_sql_missing_is_c(self) -> None:
        result = tool_fail(
            "run_evolved",
            "execution_error",
            "query failed",
            details={
                "exit_code": 1,
                "stderr": "Table 'huiyi.user' doesn't exist",
            },
        )
        insight = classify_failure(result)
        self.assertEqual(insight.failure_class, "C")
        self.assertEqual(insight.playbook_id, PLAYBOOK_SQL_MISSING)

    def test_auth_is_d(self) -> None:
        result = tool_fail(
            "run_evolved",
            "execution_error",
            "login failed",
            details={"exit_code": 1, "stderr": "HTTP 401 Unauthorized"},
        )
        self.assertEqual(classify_failure(result).failure_class, "D")

    def test_schema_is_a(self) -> None:
        result = tool_fail(
            "run_evolved",
            "validation_error",
            "missing command",
            details={"retry": True},
        )
        self.assertEqual(classify_failure(result).failure_class, "A")

    def test_match_playbook_helpers(self) -> None:
        self.assertEqual(
            match_playbook("Unexpected end of file reading node_modules/esbuild"),
            PLAYBOOK_NPM_CORRUPT,
        )
        self.assertEqual(
            match_playbook("Table 'demo.orders' doesn't exist"),
            PLAYBOOK_SQL_MISSING,
        )


class IT162PlaybookNudgeTests(unittest.TestCase):
    def test_it163_npm_corrupt_does_not_inject_playbook(self) -> None:
        """D1/M3a: playbook auto-nudge abolished (was IT-162)."""
        with temporary_agent_paths() as paths:
            list_args = json.dumps({"path": "workspace"}, ensure_ascii=False)
            tool_resp = LLMResponse(
                model="mock",
                content=None,
                tool_calls=[
                    {
                        "id": "c1",
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
                content="依赖可能坏了，我来换招处理。",
                tool_calls=[],
                finish_reason="stop",
                usage=None,
                raw={},
            )
            session = Session(
                conversation_id="_it163",
                session_dir=paths.data / "sessions" / "_it163",
                goal="it163",
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
            mock = _MockLLM(responses=[tool_resp, final])
            agent = Agent.create(session, llm=mock, confirm_fn=lambda _p, _a: "y")
            fail = tool_fail(
                "list_dir",
                "execution_error",
                "vite crashed",
                details={
                    "exit_code": 1,
                    "logs_tail": (
                        "Error: Unexpected end of file\n"
                        "    at node_modules/@esbuild/win32-x64/esbuild.exe"
                    ),
                },
            )
            with patch.object(agent.executor, "_execute_builtin", return_value=fail):
                agent.run_turn("前端起不来", spawn_explore=False)

            for msg in PLAYBOOK_NUDGES.values():
                self.assertFalse(
                    any(m.get("content") == msg for m in session.messages),
                    "playbook nudge must not be injected",
                )
            self.assertEqual(agent.executor.session.playbook_nudged, set())
            self.assertEqual(agent.executor.session.last_failure_class, "B")

    def test_it163_sql_missing_no_playbook_nudge(self) -> None:
        with temporary_agent_paths() as paths:
            list_args = json.dumps({"path": "workspace"}, ensure_ascii=False)
            tool_resp = LLMResponse(
                model="mock",
                content=None,
                tool_calls=[
                    {
                        "id": "c1",
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
                content="数据库报错显示缺表，先停下说明。",
                tool_calls=[],
                finish_reason="stop",
                usage=None,
                raw={},
            )
            session = Session(
                conversation_id="_it163_sql",
                session_dir=paths.data / "sessions" / "_it163_sql",
                goal="sql",
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
            mock = _MockLLM(responses=[tool_resp, final])
            agent = Agent.create(session, llm=mock, confirm_fn=lambda _p, _a: "y")
            fail = tool_fail(
                "list_dir",
                "execution_error",
                "db error",
                details={
                    "exit_code": 1,
                    "stderr": "Table 'huiyi.sys_user' doesn't exist",
                },
            )
            with patch.object(agent.executor, "_execute_builtin", return_value=fail):
                agent.run_turn("动手修一下表不存在的报错", spawn_explore=False)

            for msg in PLAYBOOK_NUDGES.values():
                self.assertFalse(any(m.get("content") == msg for m in session.messages))
            self.assertEqual(agent.executor.session.last_failure_class, "C")


if __name__ == "__main__":
    unittest.main()
