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
    def test_keyword_plan_classification_does_not_block_ordinary_agent_tools(self) -> None:
        self.assertEqual(
            turn_intent.resolve_ordinary_work_intent("please modify auth.py", "plan"),
            "execute",
        )
    def test_only_structured_plan_mode_is_a_hard_nonwrite_boundary(self) -> None:
        self.assertEqual(
            turn_intent.resolve_ordinary_work_intent("only give me a plan", "plan"),
            "execute",
        )
        self.assertEqual(
            turn_intent.resolve_ordinary_work_intent(
                "implement the feature", "execute", explicit_override="plan_only"
            ),
            "read_only",
        )
    def test_natural_language_and_classifier_do_not_set_hard_plan_mode(self) -> None:
        for text in ("please read PROJECT.md", "please inspect PROJECT.md"):
            with self.subTest(text=text):
                self.assertEqual(
                    turn_intent.resolve_ordinary_work_intent(text, "plan", turn_mode="agent"),
                    "execute",
                )
    def test_classified_execute_is_preserved_without_resolving_prose_again(self) -> None:
        for text in ("do not write NOTES.md", "The phrase 'write NOTES.md' is a quote"):
            with self.subTest(text=text):
                self.assertEqual(
                    turn_intent.resolve_ordinary_work_intent(text, "execute", turn_mode="agent"),
                    "execute",
                )

    def test_prose_and_requirement_labels_do_not_set_hard_read_only_mode(self) -> None:
        self.assertEqual(
            turn_intent.intent_label("requirements"),
            "需求交流，按需选择工具",
        )
        for text in ("read only", "please do not write", "do not modify"):
            with self.subTest(text=text):
                self.assertEqual(
                    turn_intent.resolve_ordinary_work_intent(text, "requirements", turn_mode="agent"),
                    "execute",
                )
        self.assertEqual(
            turn_intent.resolve_ordinary_work_intent(
                "write the file", "execute", explicit_override="read_only", turn_mode="agent"
            ),
            "read_only",
        )
        self.assertEqual(
            turn_intent.resolve_ordinary_work_intent(
                "write the file", "execute", turn_mode="ask"
            ),
            "read_only",
        )
    def test_ask_mode_questions_and_recall_are_read_only(self) -> None:
        for text, classified in (
            ("这个项目现在进度怎么样？", "qa"),
            ("刚刚我们说了什么", "recall"),
            ("查一下 docs/TOOLS.md", "research"),
        ):
            with self.subTest(text=text):
                self.assertEqual(
                    turn_intent.resolve_ordinary_work_intent(
                        text, classified, turn_mode="ask"
                    ),
                    "read_only",
                )

    def test_agent_mode_short_lines_default_execute(self) -> None:
        for text, classified in (
            ("现在呢", "qa"),
            ("现在应该可以了，去生成吧", "qa"),
            ("这个项目现在进度怎么样？", "qa"),
        ):
            with self.subTest(text=text):
                self.assertEqual(
                    turn_intent.resolve_ordinary_work_intent(
                        text, classified, turn_mode="agent"
                    ),
                    "execute",
                )

    def test_explicit_image_generation_is_execute_even_when_classified_qa(self) -> None:
        for text in (
            "现在应该可以了，去生成吧",
            "帮我生成图片",
            "用附件再生成一张",
        ):
            with self.subTest(text=text):
                self.assertTrue(turn_intent.is_explicit_image_generation_request(text))
                self.assertEqual(
                    turn_intent.resolve_ordinary_work_intent(text, "qa"),
                    "execute",
                )

    def test_plan_keyword_does_not_block_main_model_tool_choice(self) -> None:
        cases = (
            "do not generate an image",
            "discuss how image generation works",
            "only discuss; do not generate an image",
            "the phrase 'generate an image' is only an example",
        )
        for text in cases:
            with self.subTest(text=text):
                self.assertEqual(
                    turn_intent.resolve_ordinary_work_intent(text, "plan", turn_mode="agent"),
                    "execute",
                )
    def test_agent_mode_keeps_general_execute_for_main_llm(self) -> None:
        for text in (
            "请帮我生成图片",
            "先讨论方案，现在请生成一张图片",
            "这个项目现在进度怎么样？",
        ):
            with self.subTest(text=text):
                self.assertEqual(
                    turn_intent.resolve_ordinary_work_intent(text, "qa", turn_mode="agent"),
                    "execute",
                )

    def test_image_generation_words_do_not_override_ask_mode(self) -> None:
        for text in ("请帮我生成图片", "不要生成图片", "先讨论怎么生成图片"):
            with self.subTest(text=text):
                self.assertEqual(
                    turn_intent.resolve_ordinary_work_intent(text, "qa", turn_mode="ask"),
                    "read_only",
                )

    def test_plan_classifier_is_not_a_tool_permission_boundary(self) -> None:
        self.assertEqual(
            turn_intent.resolve_ordinary_work_intent("plan confirmed, directly modify auth.py", "plan"),
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

    def test_request_intent_applies_to_project_and_grow_shells(self) -> None:
        from project_mode import project_mode_block_reason

        for shell in ("project", "grow"):
            with self.subTest(shell=shell):
                common = dict(
                    active_shell=shell,
                    project_root="workspace/demo",
                    ordinary_work_intent="read_only",
                )
                for evolved_name, inner in (
                    ("write_text", {"path": "workspace/demo/app.py", "content": "x"}),
                    ("run_command", {"command": "pytest"}),
                    ("write_evolve", {"path": "evolve/tools/common/x/tool.toml"}),
                    ("git_clone", {"target": "evolve_tools", "dest": "common/x"}),
                ):
                    reason = project_mode_block_reason(
                        **common,
                        tool_name="run_evolved",
                        arguments={"tool_name": evolved_name, "arguments": inner},
                    )
                    self.assertIn("只读", reason or "", evolved_name)

                read_reason = project_mode_block_reason(
                    **common,
                    tool_name="run_evolved",
                    arguments={"tool_name": "read_file", "arguments": {"path": "workspace/demo/README.md"}},
                )
                self.assertIsNone(read_reason)

    def test_plan_only_checks_sources_commands_and_planning_writes_in_both_shells(self) -> None:
        from project_mode import project_mode_block_reason

        for shell in ("project", "grow"):
            with self.subTest(shell=shell):
                common = dict(
                    active_shell=shell,
                    project_root="workspace/demo",
                    ordinary_work_intent="plan_only",
                )
                for evolved_name, inner in (
                    ("write_text", {"path": "workspace/demo/app.py", "content": "x"}),
                    ("run_command", {"command": "pytest"}),
                ):
                    reason = project_mode_block_reason(
                        **common,
                        tool_name="run_evolved",
                        arguments={"tool_name": evolved_name, "arguments": inner},
                    )
                    self.assertIsNotNone(reason, evolved_name)

                if shell == "project":
                    plan_reason = project_mode_block_reason(
                        **common,
                        tool_name="run_evolved",
                        arguments={
                            "tool_name": "write_text",
                            "arguments": {"path": "workspace/demo/PROJECT.md", "content": "# plan"},
                        },
                    )
                    self.assertIsNone(plan_reason)

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

    def test_terminal_keeps_specialized_keyword_permission_policy(self) -> None:
        self.assertEqual(
            turn_intent.resolve_ordinary_work_intent(
                "only give me a plan", "plan", terminal_harness=True
            ),
            "plan_only",
        )

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
        self.assertEqual(agent.executor.session.ordinary_work_intent, "execute")

    def test_explicit_override_is_not_reinterpreted_from_text(self) -> None:
        self.assertEqual(
            turn_intent.resolve_ordinary_work_intent(
                "只查看状态，不改任何文件",
                "execute",
                explicit_override="execute",
            ),
            "execute",
        )


    def test_ordinary_classification_is_ignored_but_ask_remains_hard_read_only(self) -> None:
        cases = (
            ("please modify auth.py", "plan", "agent", "execute"),
            ("do not modify auth.py", "execute", "agent", "execute"),
            ("The phrase 'please modify auth.py' is only an example.", "plan", "agent", "execute"),
            ("What does 'delete the file' mean?", "qa", "ask", "read_only"),
        )
        for text, classified, mode, expected in cases:
            with self.subTest(text=text, classified=classified, mode=mode):
                self.assertEqual(
                    turn_intent.resolve_ordinary_work_intent(text, classified, turn_mode=mode),
                    expected,
                )

if __name__ == "__main__":
    unittest.main()

