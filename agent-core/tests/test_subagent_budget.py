"""Phase 49 — subagent budget policy (SUBAGENT-BUDGET · IT-4901～4906)."""

from __future__ import annotations

import json
import os
import unittest
from unittest.mock import MagicMock, patch

from subagent import (
    explore_builtin_max_per_turn,
    plan_subagent_tool_rounds,
    plan_subagent_summary_max_chars,
    resolve_subagent_max_rounds,
    review_subagent_max_rounds,
    subagent_checker_max,
    subagent_explore_max,
    subagent_hard_cap,
    synthesize_cap_summary,
)
from tools.schema import to_json, tool_ok


class TestSubagentBudgetDefaults(unittest.TestCase):
    def test_it4901_explore_default_at_least_parent_segment(self) -> None:
        self.assertGreaterEqual(subagent_explore_max(), 15)

    def test_it4903_review_default_at_least_parent_segment(self) -> None:
        self.assertGreaterEqual(review_subagent_max_rounds(), 15)

    def test_checker_default_increased(self) -> None:
        self.assertGreaterEqual(subagent_checker_max(), 10)

    def test_it4906_parent_invoke_defaults(self) -> None:
        self.assertEqual(explore_builtin_max_per_turn(), 2)

    def test_plan_tool_rounds_default(self) -> None:
        self.assertGreaterEqual(plan_subagent_tool_rounds(), 4)

    def test_plan_summary_default(self) -> None:
        self.assertGreaterEqual(plan_subagent_summary_max_chars(), 3500)

    def test_resolve_subagent_max_rounds_clamps_hard_cap(self) -> None:
        with patch.dict(os.environ, {"SUBAGENT_HARD_CAP": "12"}, clear=False):
            self.assertEqual(resolve_subagent_max_rounds(99, default=16), 12)
            self.assertEqual(resolve_subagent_max_rounds(None, default=16), 12)
            self.assertEqual(resolve_subagent_max_rounds(8, default=16), 8)

    def test_subagent_hard_cap_positive(self) -> None:
        self.assertGreaterEqual(subagent_hard_cap(), 1)


class TestSynthesizeCapSummary(unittest.TestCase):
    def test_it4902_cap_summary_lists_paths(self) -> None:
        working = [
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "1",
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "arguments": json.dumps({"path": "workspace/huiyi/TASKS.md"}),
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "1",
                "content": to_json(tool_ok("read_file", {"path": "workspace/huiyi/TASKS.md"})),
            },
        ]
        text = synthesize_cap_summary(
            working,
            kind="review",
            cap=16,
            tool_rounds=16,
        )
        self.assertIn("workspace/huiyi/TASKS.md", text)
        self.assertIn("硬兜底", text)


class TestExploreMaxRoundsPassthrough(unittest.TestCase):
    def test_it4905_executor_passes_max_rounds(self) -> None:
        from session import create_new
        from tools.executor import ToolExecutor

        from tests.isolation_helpers import temporary_agent_paths

        with temporary_agent_paths() as paths:
            session = create_new(paths)
            session.meta.project_id = "huiyi"
            session.save()

            executor = ToolExecutor.create(
                paths=paths,
                session_dir=session.session_dir,
                allowed_evolved=set(),
                confirm_fn=lambda _p, _a: "y",
            )
            executor.session.project_id = "huiyi"

            sub_result = MagicMock()
            sub_result.summary = "ok"
            sub_result.paths_cited = ["workspace/huiyi/TASKS.md"]
            sub_result.tool_rounds = 3
            sub_result.truncated = False
            sub_result.kind = "explore"
            sub_result.task = "t"
            sub_result.hit_cap = False
            sub_result.explore_continued = False

            with patch(
                "subagent.SubagentRunner.run_explore_with_continue",
                return_value=(sub_result, False),
            ) as mock_run:
                result = executor.run(
                    "explore",
                    {"task": "只读 workspace/huiyi", "max_rounds": 20},
                )

            self.assertTrue(result.ok)
            mock_run.assert_called_once()
            self.assertEqual(mock_run.call_args.kwargs.get("max_rounds"), 20)


class TestPlanMultiToolRounds(unittest.TestCase):
    def test_it4904_plan_executes_second_tool_round(self) -> None:
        from plan_agent import PlanAgent

        agent = PlanAgent.__new__(PlanAgent)
        agent.project_id = "demo"
        agent.paths = MagicMock()
        agent._plan_transcript = []
        agent._pending_gated = {}
        agent._last_partner_notices = []
        agent._degradation_level = "L0"
        agent._plan_model = "mock"

        calls = {"n": 0}

        def fake_parse(raw: str):
            calls["n"] += 1
            if calls["n"] == 1:
                return [], True, "", [{"name": "read_file", "arguments": {"path": "TASKS.md"}}]
            if calls["n"] == 2:
                return [], True, "第二轮查跑完成", []
            return [], True, "done", []

        agent._parse_operations_json = fake_parse
        agent._execute_plan_tool_calls = MagicMock(return_value=["read_file → ok"])
        agent._finalize_plan_reply = lambda out: out
        agent.append_plan_turn = MagicMock()
        agent.auto_fix = MagicMock(return_value=[])
        agent._plan_channel_fallback = MagicMock(return_value="fallback")
        agent._llm_noop_summary = MagicMock(return_value="noop")

        mock_llm = MagicMock()
        mock_llm.chat.return_value = MagicMock(content="{}")
        agent._ensure_llm = MagicMock(return_value=mock_llm)

        with patch("plan_agent.project_dir") as mock_root:
            mock_root.return_value = MagicMock()
            mock_root.return_value.__truediv__ = lambda _s, name: MagicMock(
                is_file=MagicMock(return_value=False)
            )
            with patch("subagent.plan_subagent_tool_rounds", return_value=4):
                out = agent.reason_about_intent("对照代码勾 TASKS", record_user=False)

        self.assertEqual(calls["n"], 2)
        self.assertIn("第二轮查跑完成", out)
        agent._execute_plan_tool_calls.assert_called_once()


if __name__ == "__main__":
    unittest.main()
