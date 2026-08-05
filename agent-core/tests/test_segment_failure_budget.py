"""Phase 41 P5 — segment failure budget (AGENT-HARNESS · IT-413)."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from agent import Agent
from exec_reliability import (
    EXEC_SEGMENT_FAILURE_NUDGE_MESSAGE,
    clear_segment_failure_budget,
    record_segment_failure,
    segment_failure_budget,
)
from llm_client import LLMResponse
from session import create_new
from tools.executor import ExecutorSession, ToolExecutor, _format_guard_notice
from tools.registry import ToolRegistry
from tools.schema import tool_fail

from tests.isolation_helpers import temporary_agent_paths


class SegmentFailureBudgetUnitTests(unittest.TestCase):
    def setUp(self) -> None:
        self._prev = os.environ.pop("MY_AGENT_SEGMENT_FAILURE_BUDGET", None)

    def tearDown(self) -> None:
        if self._prev is None:
            os.environ.pop("MY_AGENT_SEGMENT_FAILURE_BUDGET", None)
        else:
            os.environ["MY_AGENT_SEGMENT_FAILURE_BUDGET"] = self._prev

    def test_budget_default_3(self) -> None:
        self.assertEqual(segment_failure_budget(), 3)

    def test_record_hits_on_third(self) -> None:
        session = ExecutorSession()
        clear_segment_failure_budget(session)
        self.assertFalse(record_segment_failure(session))
        self.assertFalse(record_segment_failure(session))
        self.assertTrue(record_segment_failure(session))
        self.assertTrue(session.segment_failure_budget_hit)
        self.assertTrue(session.segment_failure_budget_just_hit)
        # further failures do not re-fire just_hit
        session.segment_failure_budget_just_hit = False
        self.assertFalse(record_segment_failure(session))

    def test_failure_class_notice_silenced(self) -> None:
        text = _format_guard_notice(
            "exec_failure_class",
            {"failure_class": "E", "tool": "run_command"},
        )
        self.assertIsNone(text)


class SegmentFailureBudgetLoopTests(unittest.TestCase):
    def test_it413_stops_after_budget(self) -> None:
        with temporary_agent_paths(copy_tool_dirs=("common/run_command",)) as paths:
            session = create_new(paths, conversation_id="_it413_budget")
            session.meta.turn_mode = "agent"
            session.meta.active_shell = "project"
            session.save()

            registry = ToolRegistry.load(paths)
            events: list[tuple[str, dict]] = []

            def confirm_fn(_preview: str, allow_approve_all: bool = False) -> str:
                return "y"

            executor = ToolExecutor(
                registry=registry,
                session=ExecutorSession(
                    session_dir=session.session_dir,
                    allowed_evolved={"run_command"},
                    workspace_evolved_approved=True,
                    active_shell="project",
                ),
                confirm_fn=confirm_fn,
                on_event=lambda et, p: events.append((et, p)),
            )
            # Force countable failures without real shell
            fail = tool_fail(
                "run_evolved",
                "execution_error",
                "boom",
                details={"exit_code": 1, "stderr": "error"},
            )
            call_n = {"n": 0}

            def fake_run(name: str, arguments: dict | None = None):
                call_n["n"] += 1
                # route through update path
                from tools.executor import ToolExecutor as TE

                result = fail
                # manually mirror what run() does after execute
                executor._update_exec_circuit(name, arguments or {}, result)
                return result

            executor.run = fake_run  # type: ignore[method-assign]

            # LLM: 3 tool rounds then text close
            responses = [
                LLMResponse(
                    model="mock",
                    content=None,
                    tool_calls=[
                        {
                            "id": f"c{i}",
                            "type": "function",
                            "function": {
                                "name": "run_command",
                                "arguments": f'{{"command":"echo fail-{i}"}}',
                            },
                        }
                    ],
                    finish_reason="tool_calls",
                    usage=None,
                    raw={},
                )
                for i in range(1, 4)
            ]
            responses.append(
                LLMResponse(
                    model="mock",
                    content="停下说明：环境未就绪。",
                    tool_calls=[],
                    finish_reason="stop",
                    usage=None,
                    raw={},
                )
            )
            mock_llm = MagicMock()
            mock_llm.chat.side_effect = responses

            agent = Agent(session=session, executor=executor, llm=mock_llm)
            result = agent._run_parent_tool_loop(
                max_rounds=10,
                tools=[{"type": "function", "function": {"name": "run_command"}}],
                model="test",
                segment_start_index=len(session.messages),
            )

            self.assertEqual(call_n["n"], 3)
            self.assertTrue(executor.session.segment_failure_budget_hit)
            blob = "\n".join(
                str(m.get("content", ""))
                for m in session.messages
                if m.get("role") == "user"
            )
            self.assertIn(EXEC_SEGMENT_FAILURE_NUDGE_MESSAGE, blob)
            # no chat spam for failure_class
            notice_texts = [
                p.get("text", "")
                for et, p in events
                if et == "guard.notice" or (et == "notice")
            ]
            self.assertFalse(any("失败分型" in t for t in notice_texts))
            self.assertIn("停下说明", result.final_text or "")


if __name__ == "__main__":
    unittest.main()
