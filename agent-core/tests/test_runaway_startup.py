"""UI-6041～6044 · Runaway startup prep and early-stage gates."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from activity_router import compute_activity_route
from agent import Agent, run_runaway_startup_prep_if_needed
from project_api import dispatch_project_message
from project_mode import create_project, format_project_overlay
from session import create_new
from tests.isolation_helpers import temporary_agent_paths
from tools.executor import ExecutorSession, ToolExecutor
from tools.registry import ToolRegistry
from turn_intent import classify_turn


class RunawayStartupPrepTests(unittest.TestCase):
    def test_execute_intent_triggers_startup_prep(self) -> None:
        with temporary_agent_paths() as paths:
            pid = "runaway-startup-exec"
            create_project(paths, pid)
            session = create_new(paths, conversation_id="_runaway_startup_exec_")
            session.meta.active_shell = "project"
            session.meta.project_id = pid
            session.meta.project_root = f"workspace/{pid}"
            session.meta.project_runaway_enabled = True
            session.meta.project_workflow_stage = "requirements"
            session.save()

            self.assertEqual(classify_turn("继续实现项目"), "execute")
            agent = Agent.create(session)
            self.assertTrue(agent._maybe_run_runaway_startup_prep())
            self.assertIn(
                session.meta.project_workflow_stage,
                {"documentation", "design", "implementation"},
            )

    def test_runaway_set_sync_prep(self) -> None:
        with temporary_agent_paths() as paths:
            pid = "runaway-startup-toggle"
            create_project(paths, pid)
            session = create_new(paths, conversation_id="_runaway_startup_toggle_")
            session.meta.active_shell = "project"
            session.meta.project_id = pid
            session.meta.project_root = f"workspace/{pid}"
            session.meta.project_workflow_stage = "requirements"
            session.save()

            dispatch_project_message(
                session,
                paths,
                {"type": "project.runaway.set", "enabled": True},
            )
            self.assertTrue(session.meta.project_runaway_enabled)
            self.assertIn(
                session.meta.project_workflow_stage,
                {"documentation", "design", "implementation"},
            )

    def test_runaway_startup_prep_helper(self) -> None:
        with temporary_agent_paths() as paths:
            pid = "runaway-startup-helper"
            create_project(paths, pid)
            session = create_new(paths, conversation_id="_runaway_startup_helper_")
            session.meta.active_shell = "project"
            session.meta.project_id = pid
            session.meta.project_root = f"workspace/{pid}"
            session.meta.project_runaway_enabled = True
            session.meta.project_workflow_stage = "requirements"
            session.save()

            self.assertTrue(run_runaway_startup_prep_if_needed(session))
            self.assertIn(
                session.meta.project_workflow_stage,
                {"documentation", "design", "implementation"},
            )


class RunawayStartupGateTests(unittest.TestCase):
    def test_begin_turn_arms_active_task_under_runaway(self) -> None:
        with temporary_agent_paths() as paths:
            pid = "runaway-arm-active"
            create_project(paths, pid)
            tasks_path = paths.workspace / pid / "TASKS.md"
            tasks_path.write_text(
                "## Phase 1\n"
                "- [ ] T-001 first\n"
                "- [ ] T-002 second verify: V-002\n",
                encoding="utf-8",
            )
            registry = ToolRegistry.load(paths)
            executor = ToolExecutor(registry=registry, session=ExecutorSession())
            executor.session.active_shell = "project"
            executor.session.project_id = pid
            executor.session.project_root = f"workspace/{pid}"
            executor.session.runaway_enabled = True
            executor.session.project_active_task_id = "T-002"
            executor.begin_turn()
            self.assertEqual(executor.session.armed_task_id, "T-002")

    def test_begin_turn_handles_empty_open_queue(self) -> None:
        with temporary_agent_paths() as paths:
            pid = "runaway-arm-empty"
            create_project(paths, pid)
            tasks_path = paths.workspace / pid / "TASKS.md"
            tasks_path.write_text("## Phase 1\n- [x] T-001 done\n", encoding="utf-8")
            registry = ToolRegistry.load(paths)
            executor = ToolExecutor(registry=registry, session=ExecutorSession())
            executor.session.active_shell = "project"
            executor.session.project_id = pid
            executor.session.project_root = f"workspace/{pid}"
            executor.session.runaway_enabled = True
            executor.begin_turn()
            self.assertEqual(executor.session.armed_task_id, "")

    def test_activity_router_skips_plan_gate_under_runaway(self) -> None:
        with temporary_agent_paths() as paths:
            session = create_new(paths, conversation_id="_runaway_route_")
            session.meta.active_shell = "project"
            session.meta.project_root = "workspace/demo"
            session.meta.project_plan_status = "draft"
            session.meta.project_runaway_enabled = True
            session.save()

            route = compute_activity_route(
                user_text="继续",
                intent="execute",
                session=session,
                paths=paths,
            )
            self.assertNotIn("计划待确认", route.reason)

    def test_overlay_requirements_stage_gate(self) -> None:
        overlay = format_project_overlay(
            project_root="workspace/demo",
            project_id="demo",
            plan_status="draft",
            workflow_stage="requirements",
            runaway_enabled=True,
        )
        self.assertIn("狂奔准备中", overlay)
        self.assertIn("禁止写业务代码", overlay)


if __name__ == "__main__":
    unittest.main()
