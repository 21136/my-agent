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
    def test_project_shell_allows_explicit_evolve_tool_operations(self) -> None:
        for evolved_name, inner in (
            (
                "write_evolve",
                {"path": "evolve/tools/common/example/tool.toml", "content_base64": "W3Rvb2xd"},
            ),
            (
                "git_clone",
                {"target": "evolve_tools", "dest": "common/example", "url": "https://example.invalid/repo"},
            ),
        ):
            with self.subTest(evolved_name=evolved_name):
                reason = project_mode_block_reason(
                    active_shell="project",
                    project_root="workspace/demo",
                    tool_name="run_evolved",
                    arguments={"tool_name": evolved_name, "arguments": inner},
                )
                self.assertIsNone(reason)

                unbound_reason = project_mode_block_reason(
                    active_shell="project",
                    project_root="",
                    tool_name="run_evolved",
                    arguments={"tool_name": evolved_name, "arguments": inner},
                )
                self.assertIsNone(unbound_reason)

    def test_unbound_project_shell_keeps_workspace_targets_blocked(self) -> None:
        for evolved_name, inner in (
            ("run_command", {"command": "pytest", "working_dir": "workspace/demo"}),
            (
                "git_clone",
                {"target": "workspace", "dest": "workspace/demo/vendor/repo", "url": "https://example.invalid/repo"},
            ),
        ):
            with self.subTest(evolved_name=evolved_name):
                reason = project_mode_block_reason(
                    active_shell="project",
                    project_root="",
                    tool_name="run_evolved",
                    arguments={"tool_name": evolved_name, "arguments": inner},
                )
                self.assertIn("未绑定项目", reason or "")

    def test_bound_project_workspace_clone_stays_under_project_root(self) -> None:
        reason = project_mode_block_reason(
            active_shell="project",
            project_root="workspace/demo",
            tool_name="run_evolved",
            arguments={
                "tool_name": "git_clone",
                "arguments": {
                    "target": "workspace",
                    "dest": "workspace/other/vendor/repo",
                    "url": "https://example.invalid/repo",
                },
            },
        )
        self.assertIn("项目目录", reason or "")

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

        unbound_patch = project_mode_block_reason(
            active_shell="project",
            project_root="",
            tool_name="run_evolved",
            arguments={
                "tool_name": "patch_file",
                "arguments": {"path": "workspace/demo/src/a.py", "find": "x", "replacement": "y"},
            },
        )
        self.assertIsNone(unbound_patch)

    def test_organize_writes_core_artifacts_without_advancing_ordinary_stage(self) -> None:
        with temporary_agent_paths(prefix="documentation-stage-") as paths:
            session = create_new(paths, conversation_id="documentation-stage")
            outputs: list[str] = []
            command = parse_project_command("项目 新建 textbook-demo")
            assert command is not None
            run_project_command(session, paths, command, output_fn=outputs.append)

            root = project_dir(paths, "textbook-demo")
            before = {
                name: (root / name).read_text(encoding="utf-8")
                for name in ("PROJECT.md", "DESIGN.md", "VERIFY.md")
            }
            organize = parse_project_command("项目 整理文档")
            assert organize is not None
            run_project_command(session, paths, organize, output_fn=outputs.append)

            self.assertEqual(session.meta.project_workflow_stage, "requirements")
            self.assertEqual(session.meta.project_plan_status, "draft")
            for name in ("PROJECT.md", "DESIGN.md", "VERIFY.md"):
                self.assertTrue((root / name).is_file())
                self.assertNotIn("待填写", (root / name).read_text(encoding="utf-8"))
            self.assertIn("文档制品", "\n".join(outputs))

    def test_plan_confirmation_is_explicit_fact_not_stage_gate(self) -> None:
        with temporary_agent_paths(prefix="documentation-gate-") as paths:
            session = create_new(paths, conversation_id="documentation-gate")
            new_command = parse_project_command("项目 新建 gated-demo")
            assert new_command is not None
            run_project_command(session, paths, new_command, output_fn=lambda _text: None)
            organize = parse_project_command("项目 整理文档")
            assert organize is not None
            run_project_command(session, paths, organize, output_fn=lambda _text: None)

            confirm_project_plan(session)
            self.assertEqual(session.meta.project_workflow_stage, "requirements")
            self.assertEqual(session.meta.project_plan_status, "confirmed")

    def test_design_confirmation_and_task_start_do_not_open_stage_wall(self) -> None:
        with temporary_agent_paths(prefix="design-confirmation-") as paths:
            session = create_new(paths, conversation_id="design-confirmation")
            new_command = parse_project_command("项目 新建 design-demo")
            assert new_command is not None
            run_project_command(session, paths, new_command, output_fn=lambda _text: None)
            organize = parse_project_command("项目 整理文档")
            assert organize is not None
            run_project_command(session, paths, organize, output_fn=lambda _text: None)

            session.meta.project_workflow_stage = "documentation"
            confirm_design = parse_project_command("项目 确认设计")
            assert confirm_design is not None
            run_project_command(session, paths, confirm_design, output_fn=lambda _text: None)
            self.assertEqual(session.meta.project_workflow_stage, "documentation")
            self.assertEqual(session.meta.project_plan_status, "draft")

            allowed = project_mode_block_reason(
                active_shell="project",
                project_root=session.meta.project_root,
                plan_status=session.meta.project_plan_status,
                workflow_stage=session.meta.project_workflow_stage,
                tool_name="run_evolved",
                arguments={"tool_name": "run_command", "arguments": {"command": "python main.py"}},
                ordinary_work_intent="execute",
            )
            self.assertIsNone(allowed)
            # 「项目 开始任务」 was removed with the task subsystem.
            self.assertIsNone(parse_project_command("项目 开始任务 T-001"))


if __name__ == "__main__":
    unittest.main()
