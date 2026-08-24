from __future__ import annotations

import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from project_cli import confirm_project_plan, parse_project_command, run_project_command
from project_mode import project_dir
from project_mode import ProjectModeError, project_mode_block_reason
from session import create_new
from tests.isolation_helpers import temporary_agent_paths


class ProjectDocumentationStageTests(unittest.TestCase):
    def test_project_shell_without_binding_blocks_side_effects_but_allows_reads(self) -> None:
        blocked = project_mode_block_reason(
            active_shell="project",
            project_root="",
            plan_status="draft",
            workflow_stage="requirements",
            tool_name="run_evolved",
            arguments={"tool_name": "run_command", "arguments": {"command": "python main.py"}},
        )
        self.assertIn("未绑定项目", blocked or "")

        allowed = project_mode_block_reason(
            active_shell="project",
            project_root="",
            plan_status="draft",
            workflow_stage="requirements",
            tool_name="run_evolved",
            arguments={"tool_name": "read_file", "arguments": {"path": "README.md"}},
        )
        self.assertIsNone(allowed)

    def test_documentation_requires_explicit_command_and_writes_core_artifacts(self) -> None:
        with temporary_agent_paths(prefix="documentation-stage-") as paths:
            session = create_new(paths, conversation_id="documentation-stage")
            outputs: list[str] = []
            command = parse_project_command("项目 新建 textbook-demo")
            assert command is not None
            run_project_command(session, paths, command, output_fn=outputs.append)

            root = project_dir(paths, "textbook-demo")
            before = {name: (root / name).read_text(encoding="utf-8") for name in ("PROJECT.md", "DESIGN.md", "TASKS.md", "VERIFY.md")}
            status_before = session.meta.project_workflow_stage

            self.assertEqual(status_before, "requirements")
            self.assertIsNone(parse_project_command("项目简介是一个音乐项目"))
            self.assertEqual(before["PROJECT.md"], (root / "PROJECT.md").read_text(encoding="utf-8"))

            organize = parse_project_command("项目 整理文档")
            assert organize is not None
            run_project_command(session, paths, organize, output_fn=outputs.append)

            self.assertEqual(session.meta.project_workflow_stage, "documentation")
            self.assertEqual(session.meta.project_plan_status, "draft")
            for name in ("PROJECT.md", "DESIGN.md", "TASKS.md", "VERIFY.md"):
                self.assertTrue((root / name).is_file())
                self.assertNotIn("待填写", (root / name).read_text(encoding="utf-8"))
            self.assertIn("四个核心制品", "\n".join(outputs))

    def test_documentation_stage_cannot_confirm_code_work(self) -> None:
        with temporary_agent_paths(prefix="documentation-gate-") as paths:
            session = create_new(paths, conversation_id="documentation-gate")
            new_command = parse_project_command("项目 新建 gated-demo")
            assert new_command is not None
            run_project_command(session, paths, new_command, output_fn=lambda _text: None)
            organize = parse_project_command("项目 整理文档")
            assert organize is not None
            run_project_command(session, paths, organize, output_fn=lambda _text: None)

            with self.assertRaisesRegex(ProjectModeError, "设计确认"):
                confirm_project_plan(session)

    def test_design_confirmation_requires_explicit_task_authorization(self) -> None:
        with temporary_agent_paths(prefix="design-confirmation-") as paths:
            session = create_new(paths, conversation_id="design-confirmation")
            new_command = parse_project_command("项目 新建 design-demo")
            assert new_command is not None
            run_project_command(session, paths, new_command, output_fn=lambda _text: None)
            organize = parse_project_command("项目 整理文档")
            assert organize is not None
            run_project_command(session, paths, organize, output_fn=lambda _text: None)

            confirm_design = parse_project_command("项目 确认设计")
            assert confirm_design is not None
            run_project_command(session, paths, confirm_design, output_fn=lambda _text: None)
            self.assertEqual(session.meta.project_workflow_stage, "design")
            self.assertEqual(session.meta.project_plan_status, "draft")

            blocked = project_mode_block_reason(
                active_shell="project",
                project_root=session.meta.project_root,
                plan_status=session.meta.project_plan_status,
                workflow_stage=session.meta.project_workflow_stage,
                tool_name="run_evolved",
                arguments={"tool_name": "run_command", "arguments": {"command": "python main.py"}},
            )
            self.assertIn("开始任务", blocked or "")

            start_task = parse_project_command("项目 开始任务 T-001")
            assert start_task is not None
            run_project_command(session, paths, start_task, output_fn=lambda _text: None)
            self.assertEqual(session.meta.project_workflow_stage, "implementation")
            self.assertEqual(session.meta.project_plan_status, "confirmed")
            self.assertEqual(session.meta.project_active_task_id, "T-001")

            allowed = project_mode_block_reason(
                active_shell="project",
                project_root=session.meta.project_root,
                plan_status=session.meta.project_plan_status,
                workflow_stage=session.meta.project_workflow_stage,
                tool_name="run_evolved",
                arguments={"tool_name": "run_command", "arguments": {"command": "python main.py"}},
            )
            self.assertIsNone(allowed)


if __name__ == "__main__":
    unittest.main()
