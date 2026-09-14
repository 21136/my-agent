"""R7-11 / R7-12 runaway reliability helpers."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from exec_reliability import (
    is_patch_anchor_param_failure,
    is_plan_gateway_tool_failure,
    is_plan_gateway_failure_summary,
    is_pool_exhausted_transport_error,
    llm_transport_backoff_seconds,
    runaway_bug_fix_enabled,
    runaway_llm_round_cooldown_seconds,
    runaway_llm_transport_retries,
    runaway_plan_gateway_fail_max,
    runaway_plan_partner_max_per_turn,
    runaway_pool_exhausted_retries,
    segment_failure_budget,
    should_count_segment_failure,
)
from tools.executor import ExecutorSession
from tools.schema import tool_fail, tool_ok
from session import create_new
from tests.isolation_helpers import temporary_agent_paths


class RunawayReliabilityTests(unittest.TestCase):
    def test_runaway_segment_failure_budget_is_higher(self) -> None:
        session = ExecutorSession(runaway_enabled=True)
        self.assertEqual(segment_failure_budget(session), 8)
        self.assertEqual(segment_failure_budget(ExecutorSession()), 3)

    def test_patch_anchor_failure_not_counted_in_runaway(self) -> None:
        session = ExecutorSession(runaway_enabled=True)
        result = tool_fail(
            "run_evolved",
            "validation_error",
            "find anchor matched 2 times; must be unique",
            details={"exit_code": 1, "retry": False},
        )
        self.assertTrue(is_patch_anchor_param_failure(result))
        self.assertFalse(should_count_segment_failure(result, session))

    def test_command_failure_still_counted_in_runaway(self) -> None:
        session = ExecutorSession(runaway_enabled=True)
        result = tool_fail(
            "run_evolved",
            "tool_error",
            "command failed",
            details={"exit_code": 1},
        )
        self.assertTrue(should_count_segment_failure(result, session))

    def test_runaway_llm_retry_defaults(self) -> None:
        self.assertEqual(runaway_llm_transport_retries(), 4)
        self.assertEqual(runaway_pool_exhausted_retries(), 2)
        self.assertEqual(llm_transport_backoff_seconds(1), 2.0)
        self.assertEqual(llm_transport_backoff_seconds(3), 8.0)
        self.assertEqual(llm_transport_backoff_seconds(9), 15.0)
        self.assertEqual(llm_transport_backoff_seconds(1, pool_exhausted=True), 30.0)
        self.assertEqual(llm_transport_backoff_seconds(3, pool_exhausted=True), 90.0)

    def test_pool_exhausted_error_detection(self) -> None:
        self.assertTrue(is_pool_exhausted_transport_error(RuntimeError("pool exhausted: all accounts")))
        self.assertTrue(is_pool_exhausted_transport_error(RuntimeError("Insufficient Balance")))
        self.assertFalse(is_pool_exhausted_transport_error(RuntimeError("validation failed")))

    def test_plan_gateway_failure_summary(self) -> None:
        self.assertTrue(
            is_plan_gateway_failure_summary("LLM 调用失败（Insufficient Balance）。计划域暂不可写")
        )
        self.assertFalse(is_plan_gateway_failure_summary("已提案新增到 Phase 1"))

    def test_plan_gateway_tool_failure(self) -> None:
        result = tool_fail(
            "plan_partner",
            "upstream_error",
            "LLM 调用失败（Insufficient Balance）。",
            details={"plan_gateway_failure": True, "retryable": True},
        )
        self.assertTrue(is_plan_gateway_tool_failure(result))
        self.assertFalse(is_plan_gateway_tool_failure(tool_ok("plan_partner", {})))

    def test_runaway_plan_gateway_fail_max_default(self) -> None:
        self.assertEqual(runaway_plan_gateway_fail_max(), 3)

    def test_runaway_cooldown_defaults(self) -> None:
        self.assertEqual(runaway_llm_round_cooldown_seconds(), 1.5)

    def test_runaway_plan_cap_with_bug_fix_enabled(self) -> None:
        import os

        keys = ("MY_AGENT_RUNAWAY_BUG_FIX_ENABLED", "MY_AGENT_RUNAWAY_PLAN_PARTNER_MAX")
        saved = {key: os.environ.get(key) for key in keys}
        os.environ["MY_AGENT_RUNAWAY_BUG_FIX_ENABLED"] = "1"
        os.environ.pop("MY_AGENT_RUNAWAY_PLAN_PARTNER_MAX", None)
        try:
            self.assertTrue(runaway_bug_fix_enabled())
            self.assertEqual(runaway_plan_partner_max_per_turn(), 0)
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_plan_partner_blocked_in_repairing(self) -> None:
        import os

        from tools.executor import ToolExecutor

        keys = ("MY_AGENT_RUNAWAY_BUG_FIX_ENABLED",)
        saved = {key: os.environ.get(key) for key in keys}
        os.environ["MY_AGENT_RUNAWAY_BUG_FIX_ENABLED"] = "1"
        try:
            with temporary_agent_paths() as paths:
                pid = "demo"
                root = paths.workspace / pid
                root.mkdir(parents=True, exist_ok=True)
                for name in ("PROJECT.md", "DESIGN.md", "TASKS.md", "VERIFY.md"):
                    (root / name).write_text("# x\n", encoding="utf-8")
                executor = ToolExecutor.create(
                    paths=paths,
                    session_dir=None,
                    allowed_evolved=set(),
                )
                executor.session.runaway_enabled = True
                executor.session.project_id = pid
                executor.session.active_shell = "project"
                executor.session.project_root = f"workspace/{pid}"
                for checkpoint, stage in (
                    ("repairing", "verification"),
                    ("release_wait", "verification"),
                    ("verifying", "verification"),
                ):
                    executor.session.project_runaway_checkpoint = checkpoint
                    executor.session.project_workflow_stage = stage
                    result = executor.run("plan_partner", {"task": "update PROJECT"})
                    self.assertFalse(result.ok, checkpoint)
                    self.assertIn(
                        "plan_partner",
                        (result.error.message if result.error else "").lower(),
                    )
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_build_llm_tools_suppresses_plan_in_release_wait(self) -> None:
        from agent import build_llm_tools
        from session import create_new

        with temporary_agent_paths() as paths:
            session = create_new(paths, conversation_id="_runaway_tool_gate_")
            session.meta.project_id = "demo"
            session.meta.project_runaway_enabled = True
            session.meta.project_workflow_stage = "verification"
            session.meta.project_runaway_checkpoint = "release_wait"
            session.meta.project_runaway_acceptance_passed = True
            names = {item["function"]["name"] for item in build_llm_tools(session)}
            self.assertNotIn("plan_partner", names)
            self.assertNotIn("deliverable_review", names)

    def test_format_project_overlay_verification_exit_gate(self) -> None:
        from project_mode import format_project_overlay

        overlay = format_project_overlay(
            project_root="workspace/demo",
            project_id="demo",
            plan_status="confirmed",
            workflow_stage="verification",
            runaway_enabled=True,
            runaway_checkpoint="release_wait",
            runaway_acceptance_passed=True,
        )
        self.assertIn("stage_gate", overlay)
        self.assertIn("plan_partner", overlay)
        self.assertIn("harness_truth", overlay)

    def test_build_llm_tools_suppresses_review_in_repairing(self) -> None:
        from agent import build_llm_tools
        from session import create_new

        with temporary_agent_paths() as paths:
            session = create_new(paths, conversation_id="_runaway_review_gate_")
            session.meta.project_id = "demo"
            session.meta.project_runaway_enabled = True
            session.meta.project_workflow_stage = "verification"
            session.meta.project_runaway_checkpoint = "repairing"
            names = {item["function"]["name"] for item in build_llm_tools(session)}
            self.assertNotIn("plan_partner", names)
            self.assertNotIn("deliverable_review", names)

    def test_format_project_overlay_repairing_gate(self) -> None:
        from project_mode import format_project_overlay

        overlay = format_project_overlay(
            project_root="workspace/demo",
            project_id="demo",
            plan_status="confirmed",
            workflow_stage="verification",
            runaway_enabled=True,
            runaway_checkpoint="repairing",
            milestone_review_suggested="phase:2",
        )
        self.assertIn("repairing", overlay)
        self.assertIn("bug-fix", overlay)
        self.assertNotIn("milestone_review_suggested:", overlay)

    def test_runaway_kernel_plan_spawn_blocked_at_exit(self) -> None:
        from exec_reliability import runaway_kernel_plan_spawn_blocked

        self.assertTrue(
            runaway_kernel_plan_spawn_blocked(
                runaway_enabled=True,
                workflow_stage="verification",
                checkpoint="release_wait",
            )
        )

    def test_runaway_plan_gateway_nudge_verification_exit(self) -> None:
        from exec_reliability import runaway_plan_gateway_nudge_message

        msg = runaway_plan_gateway_nudge_message(
            checkpoint="release_wait",
            workflow_stage="verification",
            acceptance_passed=True,
        )
        self.assertIn("deliverable_review", msg)
        self.assertNotIn("write_text", msg.lower())

    def test_bug_fix_lane_allows_project_write(self) -> None:
        from project_mode import main_agent_plan_domain_write_block

        blocked_main = main_agent_plan_domain_write_block(
            project_root="workspace/demo",
            tool_name="run_evolved",
            arguments={
                "tool_name": "write_text",
                "path": "workspace/demo/PROJECT.md",
            },
        )
        self.assertIsNotNone(blocked_main)
        allowed_bug_fix = main_agent_plan_domain_write_block(
            project_root="workspace/demo",
            tool_name="run_evolved",
            arguments={
                "tool_name": "write_text",
                "path": "workspace/demo/PROJECT.md",
            },
            bug_fix_lane=True,
        )
        self.assertIsNone(allowed_bug_fix)
        blocked_map = main_agent_plan_domain_write_block(
            project_root="workspace/demo",
            tool_name="run_evolved",
            arguments={
                "tool_name": "write_text",
                "path": "workspace/demo/MAP.md",
            },
            bug_fix_lane=True,
        )
        self.assertIn("MAP", blocked_map or "")

    def test_bug_fix_lane_allows_env_write(self) -> None:
        from project_mode import main_agent_plan_domain_write_block, project_mode_block_reason

        allowed = main_agent_plan_domain_write_block(
            project_root="workspace/demo",
            tool_name="run_evolved",
            arguments={
                "tool_name": "write_text",
                "arguments": {
                    "path": "workspace/demo/ENV.md",
                    "content": "quality.commands:\n  - id: q1\n",
                },
            },
            bug_fix_lane=True,
        )
        self.assertIsNone(allowed)
        blocked_main = main_agent_plan_domain_write_block(
            project_root="workspace/demo",
            tool_name="run_evolved",
            arguments={
                "tool_name": "write_text",
                "arguments": {
                    "path": "workspace/demo/ENV.md",
                    "content": "quality.commands:\n",
                },
            },
            bug_fix_lane=False,
        )
        self.assertIsNotNone(blocked_main)
        stage_reason = project_mode_block_reason(
            active_shell="project",
            project_root="workspace/demo",
            plan_status="confirmed",
            workflow_stage="verification",
            runaway_enabled=True,
            bug_fix_lane=True,
            tool_name="run_evolved",
            arguments={
                "tool_name": "write_text",
                "arguments": {
                    "path": "workspace/demo/ENV.md",
                    "content": "quality.commands:\n",
                },
            },
        )
        self.assertIsNone(stage_reason)

    def test_bug_fix_patch_file_env_allowed_in_verification(self) -> None:
        from project_mode import project_mode_block_reason

        reason = project_mode_block_reason(
            active_shell="project",
            project_root="workspace/demo",
            plan_status="confirmed",
            workflow_stage="verification",
            runaway_enabled=True,
            bug_fix_lane=True,
            tool_name="run_evolved",
            arguments={
                "tool_name": "patch_file",
                "arguments": {
                    "path": "workspace/demo/ENV.md",
                    "find": "tools:",
                    "replace": "tools:\n  node: \"\"\n",
                },
            },
        )
        self.assertIsNone(reason)

    def test_verification_patch_file_to_artifact_not_implementation_block(self) -> None:
        from project_mode import project_mode_block_reason

        reason = project_mode_block_reason(
            active_shell="project",
            project_root="workspace/demo",
            plan_status="confirmed",
            workflow_stage="verification",
            runaway_enabled=True,
            bug_fix_lane=False,
            tool_name="run_evolved",
            arguments={
                "tool_name": "patch_file",
                "arguments": {
                    "path": "workspace/demo/ENV.md",
                    "find": "x",
                    "replace": "y",
                },
            },
        )
        self.assertIsNotNone(reason)
        self.assertNotIn("implementation", reason or "")

    def test_bug_fix_executor_allows_env_write_in_verification(self) -> None:
        from tools.executor import ToolExecutor

        with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
            pid = "bugfix-env"
            root = paths.workspace / pid
            root.mkdir(parents=True, exist_ok=True)
            env_md = root / "ENV.md"
            env_md.write_text("# env\n", encoding="utf-8")
            executor = ToolExecutor.create(
                paths=paths,
                session_dir=None,
                allowed_evolved=None,
            )
            executor.session.active_shell = "project"
            executor.session.project_id = pid
            executor.session.project_root = f"workspace/{pid}"
            executor.session.project_workflow_stage = "verification"
            executor.session.project_plan_status = "confirmed"
            executor.session.runaway_enabled = True
            executor.session.bug_fix_lane = True
            result = executor.run(
                "write_text",
                {
                    "path": f"workspace/{pid}/ENV.md",
                    "content": "quality.commands:\n  - id: smoke\n    command: echo ok\n",
                    "on_conflict": "overwrite",
                },
            )
            self.assertTrue(result.ok, result.error)
            self.assertIn("quality.commands", env_md.read_text(encoding="utf-8"))

    def test_bug_fix_executor_allows_proxy_writes(self) -> None:
        from tools.executor import ToolExecutor
        from tools.registry import ToolRegistry

        with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
            pid = "bugfix-write"
            root = paths.workspace / pid
            root.mkdir(parents=True, exist_ok=True)
            project_md = root / "PROJECT.md"
            project_md.write_text("# before\n", encoding="utf-8")
            registry = ToolRegistry.load(paths)
            executor = ToolExecutor.create(
                paths=paths,
                session_dir=None,
                allowed_evolved=None,
            )
            executor.session.active_shell = "project"
            executor.session.project_id = pid
            executor.session.project_root = f"workspace/{pid}"
            executor.session.runaway_enabled = True
            executor.session.bug_fix_lane = True
            executor.session.project_workflow_stage = "verification"
            result = executor.run(
                "write_text",
                {
                    "path": f"workspace/{pid}/PROJECT.md",
                    "content": "# after\n",
                    "on_conflict": "overwrite",
                },
            )
            self.assertTrue(result.ok, result.error)
            self.assertIn("after", project_md.read_text(encoding="utf-8"))

    def test_bug_fix_executor_blocks_empty_allowed_evolved_writes(self) -> None:
        """Regression: allowed_evolved=set() hid common/coding tools from bug-fix."""
        from tools.executor import ToolExecutor

        with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
            pid = "bugfix-blocked"
            root = paths.workspace / pid
            root.mkdir(parents=True, exist_ok=True)
            (root / "PROJECT.md").write_text("# x\n", encoding="utf-8")
            executor = ToolExecutor.create(
                paths=paths,
                session_dir=None,
                allowed_evolved=set(),
            )
            executor.session.active_shell = "project"
            executor.session.project_id = pid
            executor.session.project_root = f"workspace/{pid}"
            executor.session.runaway_enabled = True
            executor.session.bug_fix_lane = True
            result = executor.run(
                "write_text",
                {
                    "path": f"workspace/{pid}/PROJECT.md",
                    "content": "# y\n",
                    "on_conflict": "overwrite",
                },
            )
            self.assertFalse(result.ok)
            self.assertIn("write_text", (result.error.message if result.error else ""))

    def test_runaway_verification_exit_short_circuit_helper(self) -> None:
        from exec_reliability import runaway_verification_exit_short_circuit

        self.assertTrue(
            runaway_verification_exit_short_circuit(
                runaway_enabled=True,
                workflow_stage="verification",
                checkpoint="release_wait",
                acceptance_passed=True,
                turn_intent="execute",
            )
        )
        self.assertTrue(
            runaway_verification_exit_short_circuit(
                runaway_enabled=True,
                workflow_stage="verification",
                checkpoint="release_wait",
                acceptance_passed=True,
                turn_intent="qa",
                user_text="狂奔模式：正式任务已全部完成。",
            )
        )
        self.assertFalse(
            runaway_verification_exit_short_circuit(
                runaway_enabled=True,
                workflow_stage="verification",
                checkpoint="release_wait",
                acceptance_passed=True,
                turn_intent="qa",
                user_text="这个项目现在能发布了吗？",
            )
        )
        self.assertFalse(
            runaway_verification_exit_short_circuit(
                runaway_enabled=True,
                workflow_stage="verification",
                checkpoint="verifying",
                acceptance_passed=True,
                turn_intent="execute",
            )
        )


if __name__ == "__main__":
    unittest.main()
