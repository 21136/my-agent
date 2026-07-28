"""Phase 20: Task Stop Gate — IT-51 (auto_continue) + IT-52 (hard gate) + continue."""

from __future__ import annotations

import os
import secrets
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from agent import Agent, auto_continue_enabled
from loader import ensure_task_paused_text, format_task_paused_notice
from project_cli import parse_project_command, run_project_command
from project_mode import (
    first_open_task_line,
    format_project_overlay,
    is_project_continue_utterance,
    is_under_project_root,
    normalize_project_id,
    project_dir,
    project_path_rel,
    task_stop_block_reason,
)
from session import create_new
from tools.executor import ToolExecutor
from tools.registry import ToolRegistry
from tools.schema import ToolErrorCode

from tests.isolation_helpers import make_temp_agent_paths


class TaskStopAutoContinueTests(unittest.TestCase):
    """IT-51: project shell forces auto_continue off; grow follows env."""

    def test_project_shell_disables_auto_continue_even_when_env_on(self) -> None:
        with patch.dict(os.environ, {"MY_AGENT_AUTO_CONTINUE": "1"}, clear=False):
            self.assertFalse(auto_continue_enabled(active_shell="project"))

    def test_grow_shell_respects_env_on(self) -> None:
        with patch.dict(os.environ, {"MY_AGENT_AUTO_CONTINUE": "1"}, clear=False):
            self.assertTrue(auto_continue_enabled(active_shell="grow"))

    def test_grow_shell_respects_env_off(self) -> None:
        with patch.dict(os.environ, {"MY_AGENT_AUTO_CONTINUE": "0"}, clear=False):
            self.assertFalse(auto_continue_enabled(active_shell="grow"))

    def test_template_and_prompt_copy(self) -> None:
        template = (
            Path(__file__).resolve().parents[2] / "workspace" / "_template" / "TASKS.md"
        )
        text = template.read_text(encoding="utf-8")
        self.assertIn("一停", text)
        self.assertIn("继续", text)

        prompt = (
            Path(__file__).resolve().parents[2] / "evolve" / "prompts" / "project.md"
        )
        ptext = prompt.read_text(encoding="utf-8")
        self.assertIn("Task 一停门", ptext)
        self.assertIn("回复『继续』开始下一项", ptext)


class TaskStopPathAndContinueTests(unittest.TestCase):
    def test_path_forms_under_project_root(self) -> None:
        root = "workspace/demo-proj"
        self.assertEqual(project_path_rel("workspace/demo-proj/src/A.java", root), "src/A.java")
        self.assertEqual(project_path_rel("demo-proj/src/A.java", root), "src/A.java")
        self.assertEqual(project_path_rel("demo-proj/TASKS.md", root), "TASKS.md")
        self.assertTrue(is_under_project_root("demo-proj/Main.java", root))
        self.assertIsNone(project_path_rel("other/Main.java", root))

    def test_continue_utterances(self) -> None:
        self.assertTrue(is_project_continue_utterance("继续"))
        self.assertTrue(is_project_continue_utterance("下一项"))
        self.assertTrue(is_project_continue_utterance("开始编码"))
        self.assertTrue(is_project_continue_utterance("下一 task"))
        self.assertFalse(is_project_continue_utterance("帮我解释一下继续是什么意思，很长的一句话用来超过限制"))
        self.assertFalse(is_project_continue_utterance("写 pom.xml"))

    def test_first_open_task_and_overlay(self) -> None:
        text = "# t\n\n- [x] done\n- [ ] next one\n"
        self.assertEqual(first_open_task_line(text), "- [ ] next one")
        overlay = format_project_overlay(
            project_root="workspace/x",
            project_id="x",
            plan_status="confirmed",
            continue_turn=True,
            next_open_task="- [ ] next one",
        )
        self.assertIn("continue_turn", overlay)
        self.assertIn("current_task: - [ ] next one", overlay)
        self.assertIn("task_stop:", overlay)

    def test_task_paused_notice(self) -> None:
        notice = format_task_paused_notice(next_open_task="- [ ] T-2")
        self.assertIn("本项已完成", notice)
        self.assertIn("T-2", notice)
        self.assertIn("继续", ensure_task_paused_text("已写完骨架"))


class TaskStopHardGateTests(unittest.TestCase):
    """IT-52: after marking [x], same-turn product write is rejected."""

    def setUp(self) -> None:
        self.paths = make_temp_agent_paths(self, copy_tool_dirs=("common/write_text",))
        # Copy template into temp workspace for create_project
        live = Path(__file__).resolve().parents[2] / "workspace" / "_template"
        dest = self.paths.workspace / "_template"
        dest.mkdir(parents=True, exist_ok=True)
        for name in ("PROJECT.md", "MAP.md", "TASKS.md"):
            src = live / name
            if src.is_file():
                (dest / name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

        self.project_id = f"ts-{secrets.token_hex(3)}"
        self.session = create_new(
            self.paths,
            conversation_id=f"_test_task_stop_{secrets.token_hex(4)}",
        )
        run_project_command(
            self.session,
            self.paths,
            parse_project_command(f"项目 新建 {self.project_id}"),
            output_fn=lambda _l: None,
        )
        run_project_command(
            self.session,
            self.paths,
            parse_project_command("项目 确认"),
            output_fn=lambda _l: None,
        )
        self.pid = normalize_project_id(self.project_id)
        self.root = f"workspace/{self.pid}"
        tasks = project_dir(self.paths, self.pid) / "TASKS.md"
        tasks.write_text(
            "# tasks\n\n- [ ] T-001 skeleton\n- [ ] T-002 engine\n",
            encoding="utf-8",
        )

    def _executor(self) -> ToolExecutor:
        registry = ToolRegistry.load(self.paths)
        executor = ToolExecutor.create(
            paths=self.paths,
            session_dir=self.session.session_dir,
            confirm_fn=lambda _p, _a: "y",
        )
        executor.registry = registry
        executor.session.active_shell = "project"
        executor.session.project_root = self.root
        executor.session.project_plan_status = "confirmed"
        executor.begin_turn()
        return executor

    def _write_args(self, rel_under_project: str, content: str) -> dict:
        # write_text paths are workspace-relative: <id>/...
        return {
            "tool_name": "write_text",
            "arguments": {
                "path": f"{self.pid}/{rel_under_project}",
                "content": content,
                "on_conflict": "overwrite",
            },
        }

    def test_block_reason_unit(self) -> None:
        reason = task_stop_block_reason(
            active_shell="project",
            project_root=self.root,
            task_stop_armed=True,
            tool_name="run_evolved",
            arguments=self._write_args("src/Main.java", "class Main {}"),
        )
        self.assertIsNotNone(reason)
        assert reason is not None
        self.assertIn("一停", reason)

        allow_map = task_stop_block_reason(
            active_shell="project",
            project_root=self.root,
            task_stop_armed=True,
            tool_name="run_evolved",
            arguments=self._write_args("MAP.md", "# map\n"),
        )
        self.assertIsNone(allow_map)

    def test_mark_checkbox_then_product_write_rejected(self) -> None:
        executor = self._executor()
        mark = (
            "# tasks\n\n- [x] T-001 skeleton\n- [ ] T-002 engine\n"
        )
        result_tasks = executor.run("run_evolved", self._write_args("TASKS.md", mark))
        self.assertTrue(result_tasks.ok, result_tasks.error)
        self.assertTrue(executor.session.task_stop_armed)

        result_src = executor.run(
            "run_evolved",
            self._write_args("src/Main.java", "public class Main {}"),
        )
        self.assertFalse(result_src.ok)
        assert result_src.error is not None
        self.assertEqual(result_src.error.code, ToolErrorCode.VALIDATION_ERROR)
        self.assertIn("一停", result_src.error.message)
        details = result_src.error.details or {}
        self.assertEqual(details.get("guard_type"), "task_stop")

        # MAP still allowed
        result_map = executor.run(
            "run_evolved",
            self._write_args("MAP.md", "# map\nupdated\n"),
        )
        self.assertTrue(result_map.ok, result_map.error)

    def test_new_turn_clears_arm_and_allows_write(self) -> None:
        executor = self._executor()
        mark = "# tasks\n\n- [x] T-001 skeleton\n- [ ] T-002 engine\n"
        self.assertTrue(executor.run("run_evolved", self._write_args("TASKS.md", mark)).ok)
        self.assertTrue(executor.session.task_stop_armed)

        executor.begin_turn()
        self.assertFalse(executor.session.task_stop_armed)
        result_src = executor.run(
            "run_evolved",
            self._write_args("src/Engine.java", "class Engine {}"),
        )
        self.assertTrue(result_src.ok, result_src.error)

    def test_apply_task_stop_finish_reason(self) -> None:
        session = self.session
        session.meta.active_shell = "project"
        session.meta.project_root = self.root
        session.meta.project_id = self.pid
        agent = Agent.create(session, llm=MagicMock(), confirm_fn=lambda _p, _a: "y")
        agent.executor.session.task_stop_armed = True
        agent.executor.session.active_shell = "project"
        agent.executor.session.project_root = self.root
        session.append_message({"role": "assistant", "content": "骨架已落盘。"})
        text, reason = agent._apply_task_stop_finish(
            final_text="骨架已落盘。",
            finish_reason="completed",
        )
        self.assertEqual(reason, "task_paused")
        self.assertIn("继续", text)


if __name__ == "__main__":
    unittest.main()
