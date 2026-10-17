# -*- coding: utf-8 -*-
"""Request-scoped ordinary work intent contract."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

import turn_intent


class OrdinaryWorkIntentTests(unittest.TestCase):
    def test_implementation_wins_over_plan_words(self) -> None:
        self.assertEqual(
            turn_intent.resolve_ordinary_work_intent("先整理方案，再实现登录修复", "plan"),
            "execute",
        )

    def test_explicit_plan_only_is_not_an_execute_request(self) -> None:
        self.assertEqual(
            turn_intent.resolve_ordinary_work_intent("只给方案，不改代码", "plan"),
            "plan_only",
        )

    def test_questions_and_recall_are_read_only(self) -> None:
        for text, classified in (
            ("这个项目现在进度怎么样？", "qa"),
            ("刚刚我们说了什么", "recall"),
            ("查一下 docs/TOOLS.md", "research"),
        ):
            with self.subTest(text=text):
                self.assertEqual(
                    turn_intent.resolve_ordinary_work_intent(text, classified),
                    "read_only",
                )

    def test_imperative_implementation_overrides_classified_plan(self) -> None:
        self.assertEqual(
            turn_intent.resolve_ordinary_work_intent("计划确认后，直接修改 auth.py", "plan"),
            "execute",
        )

    def test_execute_ignores_legacy_stage_and_plan_status_for_safe_in_root_write(self) -> None:
        from project_mode import project_mode_block_reason

        for stage in ("requirements", "documentation", "design", "implementation", "verification", "release"):
            for status in ("draft", "confirmed", "plan_dirty"):
                with self.subTest(stage=stage, status=status):
                    blocked = project_mode_block_reason(
                        active_shell="project",
                        project_root="workspace/demo",
                        plan_status=status,
                        workflow_stage=stage,
                        tool_name="run_evolved",
                        arguments={
                            "tool_name": "write_text",
                            "arguments": {"path": "workspace/demo/app.py", "content": "pass"},
                        },
                        ordinary_work_intent="execute",
                    )
                    self.assertIsNone(blocked)

    def test_plan_only_allows_planning_artifact_but_not_source_write(self) -> None:
        from project_mode import project_mode_block_reason

        source_block = project_mode_block_reason(
            active_shell="project",
            project_root="workspace/demo",
            plan_status="draft",
            workflow_stage="requirements",
            tool_name="run_evolved",
            arguments={
                "tool_name": "write_text",
                "arguments": {"path": "workspace/demo/PROJECT.md", "content": "# plan"},
            },
            ordinary_work_intent="plan_only",
        )
        self.assertIsNone(source_block)
        code_block = project_mode_block_reason(
            active_shell="project",
            project_root="workspace/demo",
            plan_status="draft",
            workflow_stage="requirements",
            tool_name="run_evolved",
            arguments={
                "tool_name": "write_text",
                "arguments": {"path": "workspace/demo/app.py", "content": "pass"},
            },
            ordinary_work_intent="plan_only",
        )
        self.assertIn("规划", code_block or "")

    def test_read_only_denies_writes_and_commands_but_allows_reads(self) -> None:
        from project_mode import project_mode_block_reason

        common = dict(
            active_shell="project",
            project_root="workspace/demo",
            plan_status="draft",
            workflow_stage="requirements",
            ordinary_work_intent="read_only",
        )
        write_block = project_mode_block_reason(
            **common,
            tool_name="run_evolved",
            arguments={
                "tool_name": "write_text",
                "arguments": {
                    "path": "workspace/demo/app.py",
                    "content": "pass",
                },
            },
        )
        command_block = project_mode_block_reason(
            **common,
            tool_name="run_evolved",
            arguments={
                "tool_name": "run_command",
                "arguments": {"command": "pytest"},
            },
        )
        read_allowed = project_mode_block_reason(
            **common,
            tool_name="run_evolved",
            arguments={
                "tool_name": "read_file",
                "arguments": {"path": "workspace/demo/app.py"},
            },
        )
        self.assertIn("只读", write_block or "")
        self.assertIn("只读", command_block or "")
        self.assertIsNone(read_allowed)

    def test_ordinary_writes_stay_inside_project_root(self) -> None:
        from project_mode import project_mode_block_reason

        blocked = project_mode_block_reason(
            active_shell="project",
            project_root="workspace/demo",
            plan_status="draft",
            workflow_stage="requirements",
            tool_name="run_evolved",
            arguments={
                "tool_name": "write_text",
                "arguments": {
                    "path": "workspace/other/app.py",
                    "content": "pass",
                },
            },
            ordinary_work_intent="execute",
        )
        self.assertIn("项目目录", blocked or "")

    def test_stale_active_task_does_not_narrow_new_execute_request(self) -> None:
        from project_mode import project_mode_block_reason

        common = dict(
            active_shell="project",
            project_root="workspace/demo",
            plan_status="draft",
            workflow_stage="design",
            tool_name="run_evolved",
            arguments={
                "tool_name": "write_text",
                "arguments": {
                    "path": "workspace/demo/other.py",
                    "content": "pass",
                },
            },
            ordinary_work_intent="execute",
        )
        self.assertIsNone(project_mode_block_reason(**common, ordinary_task_scope="T-001"))
        self.assertIsNone(project_mode_block_reason(**common, ordinary_task_scope=""))

    def test_one_shot_override_can_open_execution_for_this_request(self) -> None:
        self.assertEqual(
            turn_intent.resolve_ordinary_work_intent(
                "继续处理这个项目",
                "plan",
                explicit_override="execute",
            ),
            "execute",
        )

    def test_request_override_is_ephemeral_and_not_in_session_meta(self) -> None:
        from agent import Agent
        from llm_client import LLMResponse
        from session import Session, create_new
        from tests.isolation_helpers import make_temp_agent_paths

        paths = make_temp_agent_paths(self, prefix="ordinary-ephemeral-")
        session = create_new(paths, conversation_id="ordinary-ephemeral")
        session.next_work_intent = "execute"
        session.next_task_scope = "T-001"
        self.assertNotIn("next_work_intent", session.meta.to_dict())
        self.assertNotIn("next_task_scope", session.meta.to_dict())
        session.save()
        reloaded = Session.load(paths, session.conversation_id)
        self.assertIsNone(reloaded.next_work_intent)
        self.assertIsNone(reloaded.next_task_scope)

        reloaded.next_work_intent = "execute"
        reloaded.next_task_scope = "T-001"

        seen: list[str] = []

        class CaptureLlm:
            def set_cancel_event(self, _event) -> None:
                return None

            def chat(self, _messages, **kwargs):
                seen.append(str(kwargs.get("ordinary_work_intent", "")))
                return LLMResponse(
                    model="mock",
                    content="ok",
                    tool_calls=[],
                    finish_reason="stop",
                    usage=None,
                    raw={},
                )

        agent = Agent.create(reloaded, llm=CaptureLlm())
        agent.run_turn("继续处理这个项目", spawn_explore=False)
        self.assertIsNone(reloaded.next_work_intent)
        self.assertIsNone(reloaded.next_task_scope)
        self.assertEqual(agent.executor.session.ordinary_work_intent, "execute")

        agent.run_turn("只查看状态，不改任何文件", spawn_explore=False)
        self.assertEqual(agent.executor.session.ordinary_work_intent, "read_only")

    def test_explicit_read_only_text_discards_stale_execute_override(self) -> None:
        self.assertEqual(
            turn_intent.resolve_ordinary_work_intent(
                "只查看状态，不改任何文件",
                "execute",
                explicit_override="execute",
            ),
            "read_only",
        )


if __name__ == "__main__":
    unittest.main()

