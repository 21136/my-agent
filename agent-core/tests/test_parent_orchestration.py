"""Phase 48 · thin parent orchestration (IT-4801)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from session import SessionMeta, create_new
from turn_intent import (
    project_explore_autospawn_disabled,
    should_spawn_explore,
    should_spawn_explore_for_turn,
)

from tests.isolation_helpers import temporary_agent_paths


class ProjectExploreAutospawnTests(unittest.TestCase):
  def test_project_shell_disables_auto_explore(self) -> None:
    self.assertTrue(
        project_explore_autospawn_disabled(project_id="huiyi", active_shell="project")
    )
    self.assertFalse(
        project_explore_autospawn_disabled(project_id="huiyi", active_shell="grow")
    )
    self.assertFalse(
        project_explore_autospawn_disabled(project_id="", active_shell="project")
    )

  def test_it4801_huiyi_look_phrase_no_spawn(self) -> None:
    text = "文档和代码可能脱节了，你看看"
    self.assertTrue(should_spawn_explore(text))
    self.assertFalse(
        should_spawn_explore_for_turn(
            text,
            project_id="huiyi",
            active_shell="project",
        )
    )

  def test_it4801b_grow_still_spawns(self) -> None:
    text = "按 run_demo 模式造 bar 工具"
    self.assertTrue(
        should_spawn_explore_for_turn(
            text,
            project_id="",
            active_shell="grow",
        )
    )


class RunTurnProjectExploreTests(unittest.TestCase):
  def _make_agent(self, paths, session):
    from agent import Agent
    from tools.executor import ExecutorSession, ToolExecutor
    from tools.registry import ToolRegistry

    registry = ToolRegistry.load(paths)
    executor = ToolExecutor(
      registry=registry,
      session=ExecutorSession(session_dir=session.session_dir),
      confirm_fn=lambda _req: True,
    )
    return Agent(session=session, executor=executor, llm=MagicMock())

  def test_run_turn_skips_explore_in_project_shell(self) -> None:
    with temporary_agent_paths() as paths:
      session = create_new(paths)
      session.meta.project_id = "huiyi"
      session.meta.active_shell = "project"
      session.save()

      agent = self._make_agent(paths, session)
      from agent import TurnResult

      with patch.object(agent, "_run_execute_segments") as mock_segments:
        mock_segments.return_value = TurnResult(
          assistant_text="ok",
          tool_rounds=0,
          finish_reason="stop",
        )
        with patch("subagent.SubagentRunner.run_explore") as mock_explore:
          agent.run_turn("文档和代码可能脱节了，你看看")
          mock_explore.assert_not_called()

  def test_run_turn_explicit_spawn_explore_still_runs(self) -> None:
    with temporary_agent_paths() as paths:
      session = create_new(paths)
      session.meta.project_id = "huiyi"
      session.meta.active_shell = "project"
      session.save()

      agent = self._make_agent(paths, session)
      from agent import TurnResult
      from subagent import SubagentResult

      explore_result = SubagentResult(
        kind="explore",
        summary="test",
        paths_cited=[],
        tool_rounds=0,
        truncated=False,
        task="看看",
      )
      with patch.object(agent, "_run_execute_segments") as mock_segments:
        mock_segments.return_value = TurnResult(
          assistant_text="ok",
          tool_rounds=0,
          finish_reason="stop",
        )
        with patch("subagent.SubagentRunner.run_explore", return_value=explore_result) as mock_explore:
          agent.run_turn("看看", spawn_explore=True)
          mock_explore.assert_called_once()


class ExploreBuiltinTests(unittest.TestCase):
  def test_it4802_explore_builtin_sets_overlay(self) -> None:
    with temporary_agent_paths() as paths:
      session = create_new(paths)
      session.meta.project_id = "huiyi"
      session.meta.active_shell = "project"
      session.save()

      from tools.executor import ExecutorSession, ToolExecutor
      from tools.registry import ToolRegistry
      from subagent import SubagentResult

      registry = ToolRegistry.load(paths)
      executor = ToolExecutor(
        registry=registry,
        session=ExecutorSession(
          session_dir=session.session_dir,
          allowed_evolved=set(),
        ),
        confirm_fn=lambda _req, _allow: "y",
      )
      executor.session.active_shell = "project"
      executor.session.project_id = "huiyi"
      executor.begin_turn()

      sub_result = SubagentResult(
        kind="explore",
        summary="routes under workspace/huiyi",
        paths_cited=["workspace/huiyi/src"],
        tool_rounds=1,
        truncated=False,
        task="只读 workspace/huiyi",
      )

      with patch("subagent.SubagentRunner.run_explore", return_value=sub_result):
        result = executor.run("explore", {"task": "只读 workspace/huiyi"})

      self.assertTrue(result.ok)
      pending = executor.session.subagent_overlay_pending or ""
      self.assertIn("[子代理摘要 · explore]", pending)
      self.assertIn("routes under workspace/huiyi", pending)

  def test_it4802_explore_requires_task(self) -> None:
    with temporary_agent_paths() as paths:
      session = create_new(paths)
      session.save()
      from tools.executor import ExecutorSession, ToolExecutor
      from tools.registry import ToolRegistry

      registry = ToolRegistry.load(paths)
      executor = ToolExecutor(
        registry=registry,
        session=ExecutorSession(session_dir=session.session_dir),
        confirm_fn=lambda _req, _allow: "y",
      )
      result = executor.run("explore", {"task": "  "})
      self.assertFalse(result.ok)


class Phase48ProjectToolingTests(unittest.TestCase):
  def test_it4804_project_tools_include_review_not_blocked(self) -> None:
    from agent import build_llm_tools
    from paths import AgentPaths

    paths = AgentPaths.discover()
    session = create_new(paths)
    session.meta.active_shell = "project"
    session.meta.project_id = "huiyi"
    session.meta.project_root = "workspace/huiyi"
    names = {t["function"]["name"] for t in build_llm_tools(session)}
    self.assertIn("deliverable_review", names)
    self.assertIn("explore", names)
    self.assertIn("plan_partner", names)

  def test_it4804_automated_proxy_for_s480(self) -> None:
    """S-480 kernel half: no auto explore + review routing in prompt."""
    from paths import AgentPaths
    from loader import build_system_prompt

    paths = AgentPaths.discover()
    session = create_new(paths)
    session.meta.active_shell = "project"
    session.meta.project_id = "huiyi"
    session.meta.project_root = "workspace/huiyi"
    session.meta.project_delivery_profile = "solo"
    text = "文档和代码可能脱节了，你看看"
    self.assertFalse(
      should_spawn_explore_for_turn(
        text,
        project_id=session.meta.project_id,
        active_shell=session.meta.active_shell,
      )
    )
    loaded = build_system_prompt(session, paths=paths)
    self.assertIn("deliverable_review", loaded.prompt)
    self.assertIn("脱节", loaded.prompt)


if __name__ == "__main__":
  unittest.main()
