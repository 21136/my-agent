"""BUG-024 — repeat inline_write_max guard loop (IT-98)."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from agent import Agent, _is_retryable
from exec_reliability import (
    EXEC_INLINE_WRITE_NUDGE_MESSAGE,
    clear_inline_write_guard,
    inline_write_guard_max,
    record_inline_write_guard_failure,
)
from llm_client import LLMResponse
from session import create_new
from tools.executor import ExecutorSession, ToolExecutor
from tools.registry import ToolRegistry
from tools.schema import ToolErrorCode, tool_fail

from tests.isolation_helpers import temporary_agent_paths


def _big_write_call(call_id: str) -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {
            "name": "write_text",
            "arguments": (
                '{"path":"DoctorList.vue","content":"'
                + ("x" * 9000)
                + '"}'
            ),
        },
    }


class InlineWriteGuardUnitTests(unittest.TestCase):
    def setUp(self) -> None:
        self._prev = os.environ.pop("MY_AGENT_INLINE_WRITE_GUARD_MAX", None)

    def tearDown(self) -> None:
        if self._prev is None:
            os.environ.pop("MY_AGENT_INLINE_WRITE_GUARD_MAX", None)
        else:
            os.environ["MY_AGENT_INLINE_WRITE_GUARD_MAX"] = self._prev

    def test_threshold_default_2(self) -> None:
        self.assertEqual(inline_write_guard_max(), 2)

    def test_record_blocks_on_second(self) -> None:
        session = ExecutorSession()
        clear_inline_write_guard(session)
        self.assertFalse(record_inline_write_guard_failure(session))
        self.assertEqual(session.inline_write_guard_streak, 1)
        self.assertFalse(session.inline_write_guard_blocked)
        self.assertTrue(record_inline_write_guard_failure(session))
        self.assertTrue(session.inline_write_guard_blocked)
        self.assertTrue(session.inline_write_guard_just_blocked)

    def test_retry_false_when_at_threshold(self) -> None:
        session = ExecutorSession()
        clear_inline_write_guard(session)
        record_inline_write_guard_failure(session)
        result = tool_fail(
            "run_evolved",
            ToolErrorCode.VALIDATION_ERROR,
            "too big",
            details={"guard_type": "inline_write_max", "retry": False},
        )
        self.assertFalse(_is_retryable(result))


class InlineWriteGuardLoopTests(unittest.TestCase):
    def test_it98a_stops_after_repeat_inline_guard(self) -> None:
        with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
            session = create_new(paths, conversation_id="_it98a_inline")
            session.meta.turn_mode = "agent"
            session.save()

            registry = ToolRegistry.load(paths)
            run_n = {"n": 0}

            def confirm_fn(_preview: str, allow_approve_all: bool = False) -> str:
                return "y"

            executor = ToolExecutor(
                registry=registry,
                session=ExecutorSession(
                    session_dir=session.session_dir,
                    allowed_evolved={"write_text"},
                    workspace_evolved_approved=True,
                ),
                confirm_fn=confirm_fn,
            )

            real_run = executor.run

            def counting_run(name: str, arguments: dict | None = None):
                run_n["n"] += 1
                return real_run(name, arguments)

            executor.run = counting_run  # type: ignore[method-assign]

            responses = [
                LLMResponse(
                    model="mock",
                    content=None,
                    tool_calls=[_big_write_call("c1")],
                    finish_reason="tool_calls",
                    usage=None,
                    raw={},
                ),
                LLMResponse(
                    model="mock",
                    content=None,
                    tool_calls=[_big_write_call("c2")],
                    finish_reason="tool_calls",
                    usage=None,
                    raw={},
                ),
                LLMResponse(
                    model="mock",
                    content="改用 staging 路径写入。",
                    tool_calls=[],
                    finish_reason="stop",
                    usage=None,
                    raw={},
                ),
            ]
            mock_llm = MagicMock()
            mock_llm.chat.side_effect = responses

            agent = Agent(session=session, executor=executor, llm=mock_llm)
            agent._run_parent_tool_loop(
                max_rounds=10,
                tools=[{"type": "function", "function": {"name": "write_text"}}],
                model="test",
                segment_start_index=len(session.messages),
            )

            self.assertEqual(run_n["n"], 2)
            self.assertTrue(executor.session.inline_write_guard_blocked)
            blob = "\n".join(
                str(m.get("content", ""))
                for m in session.messages
                if m.get("role") == "user"
            )
            self.assertIn(EXEC_INLINE_WRITE_NUDGE_MESSAGE, blob)

    def test_it98b_successful_write_clears_streak(self) -> None:
        with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
            session = create_new(paths, conversation_id="_it98b_staging")
            session.meta.turn_mode = "agent"
            session.save()

            registry = ToolRegistry.load(paths)

            executor = ToolExecutor(
                registry=registry,
                session=ExecutorSession(
                    session_dir=session.session_dir,
                    allowed_evolved={"write_text"},
                    workspace_evolved_approved=True,
                ),
                confirm_fn=lambda _p, allow_approve_all=False: "y",
            )

            fail = executor.run(
                "run_evolved",
                {
                    "tool_name": "write_text",
                    "arguments": {"path": "DoctorList.vue", "content": "x" * 9000},
                },
            )
            self.assertFalse(fail.ok)
            self.assertEqual(executor.session.inline_write_guard_streak, 1)
            self.assertFalse(executor.session.inline_write_guard_blocked)

            ok = executor.run(
                "run_evolved",
                {
                    "tool_name": "write_text",
                    "arguments": {
                        "path": "DoctorList.vue",
                        "content": "<template><div>fixed</div></template>\n",
                        "on_conflict": "overwrite",
                    },
                },
            )
            self.assertTrue(ok.ok, ok.error.message if ok.error else ok)
            self.assertEqual(executor.session.inline_write_guard_streak, 0)
            self.assertFalse(executor.session.inline_write_guard_blocked)

    def test_it98c_begin_turn_clears_blocked(self) -> None:
        with temporary_agent_paths() as paths:
            registry = ToolRegistry.load(paths)
            session = ExecutorSession()
            session.inline_write_guard_streak = 2
            session.inline_write_guard_blocked = True
            session.inline_write_guard_just_blocked = True

            executor = ToolExecutor(registry=registry, session=session)
            executor.begin_turn()

            self.assertEqual(session.inline_write_guard_streak, 0)
            self.assertFalse(session.inline_write_guard_blocked)
            self.assertFalse(session.inline_write_guard_just_blocked)


if __name__ == "__main__":
    unittest.main()
