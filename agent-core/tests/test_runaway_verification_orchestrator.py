"""UI-6046～6052 — verification harness orchestrator regression."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from exec_reliability import runaway_advisory_tool_may_own_checkpoint
from progress_gate import report_progress_repeat_block_reason
from project_mode import format_project_overlay
from session import create_new
from tests.isolation_helpers import temporary_agent_paths


class RunawayVerificationOrchestratorTests(unittest.TestCase):
    def test_overlay_verification_unknown_checkpoint_no_plan_write_hint(self) -> None:
        overlay = format_project_overlay(
            project_root="workspace/demo",
            project_id="demo",
            plan_status="confirmed",
            workflow_stage="verification",
            runaway_enabled=True,
            runaway_checkpoint="",
        )
        self.assertIn("Harness bootstrap", overlay)
        self.assertIn("禁止直写", overlay)
        self.assertNotIn("计划提案自动采纳，项目内安全源码写入可连续执行", overlay)

    def test_overlay_verifying_forbids_main_agent_plan_writes(self) -> None:
        overlay = format_project_overlay(
            project_root="workspace/demo",
            project_id="demo",
            plan_status="confirmed",
            workflow_stage="verification",
            runaway_enabled=True,
            runaway_checkpoint="verifying",
        )
        self.assertIn("禁止 write_text/patch_file PROJECT/ENV/TASKS", overlay)
        self.assertIn("推进 release_wait", overlay)

    def test_overlay_verifying_describes_promotion_not_finished(self) -> None:
        overlay = format_project_overlay(
            project_root="workspace/demo",
            project_id="demo",
            plan_status="confirmed",
            workflow_stage="verification",
            runaway_enabled=True,
            runaway_checkpoint="verifying",
            runaway_acceptance_passed=True,
        )
        self.assertIn("推进 release_wait", overlay)
        self.assertNotIn("Harness 已收尾", overlay)

    def test_progress_gate_skips_repeat_in_runaway_verification(self) -> None:
        reason = report_progress_repeat_block_reason(
            active_shell="project",
            task_stop_armed=True,
            tool_name="run_evolved",
            arguments={"tool_name": "report_progress", "arguments": {}},
            runaway_enabled=True,
            workflow_stage="verification",
        )
        self.assertIsNone(reason)

    def test_advisory_tools_may_not_own_checkpoint(self) -> None:
        for tool in ("deliverable_review", "plan_partner", "report_progress"):
            self.assertFalse(runaway_advisory_tool_may_own_checkpoint(tool))
        self.assertTrue(runaway_advisory_tool_may_own_checkpoint("run_command"))

    def test_hard_verification_fails_when_matrix_red_despite_acceptance(self) -> None:
        from agent import Agent

        with temporary_agent_paths() as paths:
            pid = "mx-fail-hard-pass"
            root = paths.workspace / pid
            root.mkdir(parents=True, exist_ok=True)
            (root / "PROJECT.md").write_text(
                "## 验收标准\n\n- 命令：`python verify.py` 期望退出码：0\n",
                encoding="utf-8",
            )
            (root / "TASKS.md").write_text(
                "- [x] T-001 done\n- [ ] T-002 open task\n",
                encoding="utf-8",
            )
            (root / "VERIFY.md").write_text("- V-001 T-001 pass\n", encoding="utf-8")
            session = create_new(paths, conversation_id="_mx_fail_hard_")
            session.meta.active_shell = "project"
            session.meta.project_id = pid
            session.meta.project_root = f"workspace/{pid}"
            session.meta.project_runaway_enabled = True
            session.meta.project_workflow_stage = "verification"
            agent = Agent.create(session)
            with patch.object(agent, "_rerun_runaway_harness_verification", return_value=True):
                with patch.object(agent, "_enter_runaway_repair_checkpoint", return_value=False) as repair:
                    self.assertFalse(agent._run_runaway_hard_verification())
                    repair.assert_called_once()
                    summary = repair.call_args.kwargs.get("summary", "")
                    self.assertIn("MX-1", summary)

    def test_harness_short_circuit_verification_turn(self) -> None:
        from agent import Agent

        with temporary_agent_paths() as paths:
            pid = "harness-short"
            root = paths.workspace / pid
            root.mkdir(parents=True, exist_ok=True)
            (root / "PROJECT.md").write_text(
                "## 验收标准\n\n- 命令：`python verify.py` 期望退出码：0\n",
                encoding="utf-8",
            )
            (root / "TASKS.md").write_text("- [x] T-001 done\n", encoding="utf-8")
            (root / "VERIFY.md").write_text("- V-001 T-001 pass\n", encoding="utf-8")
            (root / "ENV.md").write_text(
                "quality:\n  commands:\n    - id: t\n      cmd: [\"echo\", \"ok\"]\n",
                encoding="utf-8",
            )
            session = create_new(paths, conversation_id="_harness_short_")
            session.meta.active_shell = "project"
            session.meta.project_id = pid
            session.meta.project_root = f"workspace/{pid}"
            session.meta.project_runaway_enabled = True
            session.meta.project_workflow_stage = "verification"
            session.meta.project_runaway_checkpoint = "verifying"
            agent = Agent.create(session)
            with patch.object(agent, "_sync_runaway_harness_truth", return_value=False):
                with patch.object(agent, "_enter_runaway_repair_checkpoint", return_value=False):
                    result = agent._maybe_short_circuit_runaway_verification_harness_turn(
                        intent="execute",
                        user_text=agent.runaway_chain_user_line(),
                    )
            self.assertIsNotNone(result)
            assert result is not None
            self.assertEqual(result.finish_reason, "runaway_verification_harness")
            self.assertEqual(result.tool_rounds, 0)

    def test_runaway_chain_line_verification_exit_when_queue_done(self) -> None:
        from agent import Agent

        with temporary_agent_paths() as paths:
            pid = "chain-line"
            root = paths.workspace / pid
            root.mkdir(parents=True, exist_ok=True)
            (root / "TASKS.md").write_text("- [x] T-001 done\n", encoding="utf-8")
            session = create_new(paths, conversation_id="_chain_line_")
            session.meta.project_id = pid
            session.meta.project_root = f"workspace/{pid}"
            session.meta.project_runaway_enabled = True
            agent = Agent.create(session)
            line = agent.runaway_chain_user_line()
            self.assertIn("verification 出口", line)
            self.assertNotIn("下一项开放任务", line)


if __name__ == "__main__":
    unittest.main()
