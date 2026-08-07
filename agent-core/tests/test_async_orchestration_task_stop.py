"""Pack 6 T-5603 · Task-stop vs orchestration boundary (IT-561)."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from agent import Agent, auto_continue_enabled, segment_messages_show_progress
from project_mode import format_project_overlay
from session import Session, SessionMeta
from tools.executor import ExecutorSession, ToolExecutor
from tools.registry import ToolRegistry
from tools.schema import tool_ok

from tests.isolation_helpers import temporary_agent_paths


class OrchestrationTaskStopBoundaryTests(unittest.TestCase):
    """IT-561 · run_service chain does not arm task_stop / task_paused."""

    def test_it561_run_service_wait_does_not_arm_task_stop(self) -> None:
        with temporary_agent_paths(copy_tool_dirs=("common/run_service",)) as paths:
            demo = paths.workspace / "demo"
            demo.mkdir(parents=True)
            (demo / "TASKS.md").write_text("## Phase 1\n- [ ] T-001 start gateway\n", encoding="utf-8")
            registry = ToolRegistry.load(paths)
            executor = ToolExecutor(
                registry=registry,
                session=ExecutorSession(
                    allowed_evolved={"run_service"},
                    active_shell="project",
                    project_root="workspace/demo",
                    project_id="demo",
                    project_delivery_profile="ritual",
                    task_done_baseline=0,
                ),
            )
            result = tool_ok("run_evolved", {"action": "wait", "name": "gateway", "ready": True})
            executor._maybe_arm_task_stop(
                "run_evolved",
                {
                    "tool_name": "run_service",
                    "arguments": {"action": "wait", "name": "gateway", "timeout_sec": 5},
                },
                result,
            )
            self.assertFalse(executor.session.task_stop_armed)
            self.assertFalse(executor.session.report_progress_done_this_turn)

    def test_it561_apply_task_stop_skipped_when_not_armed(self) -> None:
        with temporary_agent_paths() as paths:
            session = Session(
                conversation_id="_it561",
                session_dir=paths.data / "sessions" / "_it561",
                goal="orch",
                meta=SessionMeta(
                    topics=[],
                    llm_model="mock",
                    active_shell="project",
                    project_root="workspace/demo",
                    project_id="demo",
                    project_delivery_profile="ritual",
                ),
                messages=[],
                paths=paths,
            )
            agent = Agent.create(session, confirm_fn=lambda _p, _a: "y")
            text, reason = agent._apply_task_stop_finish(
                final_text="gateway 已 wait。",
                finish_reason="stop",
            )
            self.assertEqual(text, "gateway 已 wait。")
            self.assertEqual(reason, "stop")

    def test_it561_run_service_tool_counts_as_segment_progress(self) -> None:
        payload = json.dumps(
            {"tool": "run_evolved", "ok": True, "data": {"action": "wait"}},
            ensure_ascii=False,
        )
        messages = [
            {"role": "user", "content": "起服"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{"id": "1", "function": {"name": "run_evolved", "arguments": "{}"}}],
            },
            {"role": "tool", "tool_call_id": "1", "content": payload},
        ]
        self.assertTrue(segment_messages_show_progress(messages, 1))

    def test_it561_project_shell_no_auto_continue_on_segment_cap(self) -> None:
        self.assertFalse(auto_continue_enabled(active_shell="project"))

    def test_it561_overlay_includes_orch_boundary(self) -> None:
        solo = format_project_overlay(
            project_root="workspace/huiyi",
            project_id="huiyi",
            plan_status="confirmed",
            delivery_profile="solo",
        )
        self.assertIn("orch_boundary:", solo)
        self.assertIn("run_service", solo)

        ritual = format_project_overlay(
            project_root="workspace/huiyi",
            project_id="huiyi",
            plan_status="confirmed",
            delivery_profile="ritual",
        )
        self.assertIn("orch_boundary:", ritual)
        self.assertIn("report_progress", ritual)


if __name__ == "__main__":
    unittest.main()
