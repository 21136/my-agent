"""Phase 50 — explore scope rails (IT-5001～5004)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from explore_scope import (
    build_explore_continue_task,
    build_kernel_auto_explore_task,
    explore_scope_rail,
)
from subagent import (
    SubagentResult,
    merge_explore_results,
    synthesize_cap_summary,
)


class TestExploreScopeRail(unittest.TestCase):
    def test_it5001_general_rail(self) -> None:
        self.assertEqual(explore_scope_rail(), "general")
        task = build_kernel_auto_explore_task("找代码和文档不符")
        self.assertIn("scope_rail=general", task)
        self.assertIn("agent 仓库内核", task)
        self.assertIn("找代码和文档不符", task)

    def test_it5001_grow_rail(self) -> None:
        self.assertEqual(explore_scope_rail(active_shell="grow"), "grow")
        task = build_kernel_auto_explore_task(
            "按 run_demo 造工具",
            active_shell="grow",
        )
        self.assertIn("scope_rail=grow", task)
        self.assertIn("evolve/tools", task)

    def test_it5001_project_rail_template(self) -> None:
        task = build_kernel_auto_explore_task(
            "看看",
            project_id="huiyi",
            active_shell="grow",
        )
        self.assertIn("scope_rail=grow", task)

    def test_build_explore_continue_task_lists_paths(self) -> None:
        text = build_explore_continue_task(
            "原任务",
            ["docs/TOOLS.md", "agent-core/tools/registry.py"],
        )
        self.assertIn("docs/TOOLS.md", text)
        self.assertIn("续跑", text)


class TestMergeExploreResults(unittest.TestCase):
    def test_it5002_merge_paths_and_summary(self) -> None:
        first = SubagentResult(
            kind="explore",
            summary="第一轮",
            paths_cited=["a.md"],
            tool_rounds=16,
            truncated=False,
            task="t",
            hit_cap=True,
        )
        second = SubagentResult(
            kind="explore",
            summary="第二轮补充",
            paths_cited=["b.md"],
            tool_rounds=8,
            truncated=False,
            task="continue",
            hit_cap=False,
        )
        merged = merge_explore_results(first, second)
        self.assertTrue(merged.explore_continued)
        self.assertEqual(merged.tool_rounds, 24)
        self.assertIn("a.md", merged.paths_cited)
        self.assertIn("b.md", merged.paths_cited)
        self.assertIn("续跑补充", merged.summary)


class TestExploreContinueRunner(unittest.TestCase):
    def test_it5002_continue_when_hit_cap(self) -> None:
        from subagent import SubagentRunner

        paths = MagicMock()
        runner = SubagentRunner(paths=paths)
        session = MagicMock()
        session.meta = MagicMock()
        session.conversation_id = "t"
        session.scaffold_tool_turn = False

        cap_result = SubagentResult(
            kind="explore",
            summary="cap",
            paths_cited=["docs/x.md"],
            tool_rounds=16,
            truncated=False,
            task="task",
            hit_cap=True,
        )
        cont_result = SubagentResult(
            kind="explore",
            summary="more",
            paths_cited=["docs/y.md"],
            tool_rounds=3,
            truncated=False,
            task="continue",
            hit_cap=False,
        )
        with patch.object(runner, "run_explore", side_effect=[cap_result, cont_result]):
            result, did_continue = runner.run_explore_with_continue(
                "task",
                session=session,
                llm=MagicMock(),
            )
        self.assertTrue(did_continue)
        self.assertTrue(result.explore_continued)
        self.assertIn("docs/y.md", result.paths_cited)

    def test_it5002_skip_continue_when_already_used(self) -> None:
        from subagent import SubagentRunner

        runner = SubagentRunner(paths=MagicMock())
        session = MagicMock()
        session.meta = MagicMock()
        session.conversation_id = "t"
        session.scaffold_tool_turn = False
        cap_result = SubagentResult(
            kind="explore",
            summary="cap",
            paths_cited=[],
            tool_rounds=16,
            truncated=False,
            task="task",
            hit_cap=True,
        )
        with patch.object(runner, "run_explore", return_value=cap_result) as mock_run:
            result, did_continue = runner.run_explore_with_continue(
                "task",
                session=session,
                llm=MagicMock(),
                continue_already_used=True,
            )
        self.assertFalse(did_continue)
        mock_run.assert_called_once()
        self.assertFalse(result.explore_continued)


class TestLoaderDisciplineProxy(unittest.TestCase):
    def test_it5003_cap_overlay_hint_keywords(self) -> None:
        overlay = "[子代理摘要 · explore]\n（子代理已用 16/16 轮；已达 explore 上限"
        self.assertIn("已达 explore 上限", overlay)
        text = synthesize_cap_summary([], kind="explore", cap=16, tool_rounds=16)
        self.assertIn("硬兜底", text)


if __name__ == "__main__":
    unittest.main()
