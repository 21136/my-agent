"""Tests for runaway v2 controller (IT-6102 / IT-6103 / IT-6104)."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from agent import Agent, ToolLoopSegmentResult, TurnResult
from project_mode import create_project
from runaway_v2.acceptance import HookResult
from runaway_v2.checklist import Checklist, ChecklistItem, build_checklist, save_checklist
from runaway_v2.controller import RunawayController
from runaway_v2.plan import build_turn_plan
from runaway_v2.phase import PhaseResult, derive_phase
from session import Session, create_new
from tests.isolation_helpers import temporary_agent_paths


class RunawayV2ControllerTests(unittest.TestCase):
    def _bind_session(self, paths, pid: str = "demo"):
        session = create_new(paths, conversation_id="_runaway_v2_ctrl_")
        session.meta.project_id = pid
        session.meta.project_root = f"workspace/{pid}"
        session.meta.active_shell = "project"
        session.meta.project_plan_status = "confirmed"
        session.meta.project_runaway_enabled = True
        session.meta.project_workflow_stage = "verification"
        return session

    def _write_project(self, root: Path) -> None:
        root.mkdir(parents=True, exist_ok=True)
        (root / "PROJECT.md").write_text(
            "# Demo\n\nREQ-001\nAC-001\n\n## 验收标准\n\n- 命令：`python verify.py` 期望退出码：0\n",
            encoding="utf-8",
        )
        (root / "DESIGN.md").write_text("UX-001\nTD-001\n", encoding="utf-8")
        (root / "TASKS.md").write_text(
            "- [x] T-001 done\n- [ ] 后续任务待规划\n",
            encoding="utf-8",
        )
        (root / "VERIFY.md").write_text("- V-001 T-001 pass\nAC-001\n", encoding="utf-8")
        # Omit ENV.md so MX-5 stays red.

    def _make_agent(self, paths, session):
        from tools.executor import ExecutorSession, ToolExecutor
        from tools.registry import ToolRegistry

        registry = ToolRegistry.load(paths)
        executor = ToolExecutor(
            registry=registry,
            session=ExecutorSession(session_dir=session.session_dir),
            confirm_fn=lambda _req: True,
        )
        return Agent(session=session, executor=executor, llm=MagicMock())

    @patch.dict(os.environ, {"MY_AGENT_RUNAWAY_V2": "1"}, clear=False)
    def test_it_6102_controller_does_not_call_v1_bugfix_or_short_circuit(self) -> None:
        with temporary_agent_paths() as paths:
            self._write_project(paths.workspace / "demo")
            session = self._bind_session(paths)
            agent = self._make_agent(paths, session)
            with patch.object(agent, "_maybe_short_circuit_runaway_verification_harness_turn") as short_circuit:
                with patch.object(agent, "_maybe_run_runaway_repair_lane_at_turn_start") as repair_lane:
                    with patch("subagent.SubagentRunner.run_bug_fix") as run_bug_fix:
                        with patch.object(
                            agent,
                            "_run_parent_tool_loop",
                            return_value=MagicMock(
                                final_text="ok",
                                tool_rounds=0,
                                finish_reason="stop",
                                exceeded=False,
                                qa_soft_reminder_injected=False,
                            ),
                        ):
                            with patch.object(
                                agent,
                                "_finish_short_tool_loop",
                                return_value=TurnResult(
                                    assistant_text="ok",
                                    tool_rounds=0,
                                    finish_reason="stop",
                                ),
                            ):
                                result = agent.run_turn("继续狂奔")
            short_circuit.assert_not_called()
            repair_lane.assert_not_called()
            run_bug_fix.assert_not_called()
            self.assertIsInstance(result, TurnResult)

    def test_implementation_plan_allows_verify_and_requires_progress_report(self) -> None:
        with temporary_agent_paths() as paths:
            root = paths.workspace / "demo"
            self._write_project(root)
            (root / "TASKS.md").write_text("- [ ] T-001 implement API\n", encoding="utf-8")
            checklist = build_checklist(paths, "demo")
            phase = derive_phase(paths, "demo", plan_status="confirmed", checklist=checklist)

            plan = build_turn_plan(
                paths=paths,
                project_id="demo",
                phase=phase,
                checklist=checklist,
                user_text="继续",
                intent="execute",
            )

            self.assertEqual(plan.phase, "implement")
            self.assertIn("VERIFY.md", plan.allowed_write_globs)
            self.assertIn("report_progress", plan.system_append)
            self.assertIn("硬验收", plan.system_append)

    @patch.dict(os.environ, {"MY_AGENT_RUNAWAY_V2": "1", "MY_AGENT_RUNAWAY_V2_DIRECTED_AFTER": "2"}, clear=False)
    def test_it_6103_hook_failure_does_not_mark_passed(self) -> None:
        with temporary_agent_paths() as paths:
            self._write_project(paths.workspace / "demo")
            checklist = build_checklist(paths, "demo")
            mx5 = next(item for item in checklist.items if item.id == "MX-5")
            mx5.status = "pending"
            save_checklist(paths, checklist)
            phase = derive_phase(paths, "demo", plan_status="confirmed", checklist=checklist)
            plan = build_turn_plan(
                paths=paths,
                project_id="demo",
                phase=phase,
                checklist=checklist,
                user_text="继续",
                intent="execute",
            )
            controller = RunawayController(MagicMock())
            controller.agent = MagicMock()
            hook = __import__("runaway_v2.acceptance", fromlist=["run_acceptance"]).run_acceptance(
                paths, "demo", plan.acceptance_item
            )
            self.assertFalse(hook.ok)
            controller._commit_acceptance(checklist, plan, hook)
            updated = next(item for item in checklist.items if item.id == "MX-5")
            self.assertEqual(updated.status, "failed")
            self.assertNotEqual(updated.status, "passed")

    def test_successful_hook_clears_previous_failure(self) -> None:
        with temporary_agent_paths() as paths:
            self._write_project(paths.workspace / "demo")
            checklist = build_checklist(paths, "demo")
            mx5 = next(item for item in checklist.items if item.id == "MX-5")
            mx5.status = "failed"
            mx5.last_failure = {"command": "lint MX-5", "tail": "旧错误"}
            plan = MagicMock(phase="verify", acceptance_item=mx5, mode="auto")
            controller = RunawayController(MagicMock())
            controller.agent = MagicMock()

            controller._commit_acceptance(checklist, plan, HookResult(ok=True))

            self.assertEqual(mx5.status, "passed")
            self.assertIsNone(mx5.last_failure)

    def test_prepare_acceptance_advances_bound_project(self) -> None:
        with temporary_agent_paths() as paths:
            session = self._bind_session(paths, "demo")
            agent = MagicMock()
            agent.session = session
            controller = RunawayController(agent)
            item = ChecklistItem(
                id="PREPARE",
                kind="matrix",
                title="四件套就绪",
                acceptance={"type": "prepare_ready"},
            )
            checklist = Checklist(
                version=1,
                project_id="demo",
                generated_at="",
                source_fingerprint="",
                items=[item],
            )
            plan = MagicMock(phase="prepare", acceptance_item=item)
            with patch.object(controller, "_advance_after_prepare_ready") as advance:
                controller._commit_acceptance(checklist, plan, HookResult(ok=True))
            advance.assert_called_once_with("demo")
            self.assertEqual(item.status, "passed")

    def test_prepare_acceptance_advances_without_persisted_item(self) -> None:
        with temporary_agent_paths() as paths:
            session = self._bind_session(paths, "demo")
            agent = MagicMock()
            agent.session = session
            controller = RunawayController(agent)
            item = ChecklistItem(
                id="PREPARE",
                kind="matrix",
                title="四件套就绪",
                acceptance={"type": "prepare_ready"},
            )
            checklist = Checklist(1, "demo", "", "", items=[])
            plan = MagicMock(phase="prepare", acceptance_item=item)
            with patch.object(controller, "_advance_after_prepare_ready") as advance:
                controller._commit_acceptance(checklist, plan, HookResult(ok=True))
            advance.assert_called_once_with("demo")

    def test_prepare_transition_refreshes_plan_fingerprints(self) -> None:
        """v2 document preparation must not re-open legacy plan_dirty on turn end."""
        with temporary_agent_paths() as paths:
            root = paths.workspace / "demo"
            self._write_project(root)
            session = self._bind_session(paths, "demo")
            session.meta.project_plan_status = "plan_dirty"
            session.meta.project_phase_fingerprint = "stale-phase"
            session.meta.project_doc_fingerprint = "stale-doc"
            agent = MagicMock()
            agent.session = session
            controller = RunawayController(agent)

            controller._advance_after_prepare_ready("demo")

            from project_mode import (
                phase_fingerprint_from_text,
                project_doc_fingerprint,
                sync_plan_dirty_if_structure_changed,
            )

            self.assertEqual(session.meta.project_plan_status, "confirmed")
            self.assertEqual(
                session.meta.project_phase_fingerprint,
                phase_fingerprint_from_text((root / "TASKS.md").read_text(encoding="utf-8")),
            )
            self.assertEqual(
                session.meta.project_doc_fingerprint,
                project_doc_fingerprint(root / "PROJECT.md"),
            )
            self.assertFalse(sync_plan_dirty_if_structure_changed(session, paths))

    @patch.dict(os.environ, {"MY_AGENT_RUNAWAY_V2": "1"}, clear=False)
    def test_run_turn_recovers_stale_prepare_metadata_before_model_loop(self) -> None:
        with temporary_agent_paths() as paths:
            root = paths.workspace / "demo"
            self._write_project(root)
            (root / "TASKS.md").write_text("- [x] T-001 done\n", encoding="utf-8")
            (root / "ENV.md").write_text(
                "quality:\n  commands:\n    - id: smoke\n      cmd: [\"echo\", \"ok\"]\n",
                encoding="utf-8",
            )

            session = self._bind_session(paths)
            session.meta.project_plan_status = "draft"
            session.meta.project_runaway_checkpoint = "preparing"
            agent = self._make_agent(paths, session)
            checklist = build_checklist(paths, "demo")
            for item in checklist.items:
                item.status = "passed"
            save_checklist(paths, checklist)

            with patch.object(agent, "_run_parent_tool_loop") as model_loop:
                result = agent.run_turn("继续狂奔")

            self.assertEqual(result.finish_reason, "stop")
            self.assertIn("等待发布确认", result.assistant_text)
            self.assertEqual(session.meta.project_plan_status, "confirmed")
            self.assertEqual(session.meta.project_runaway_checkpoint, "release_wait")
            self.assertEqual(session.meta.project_runaway_v2_phase, "release_wait")
            model_loop.assert_not_called()

    @patch.dict(os.environ, {"MY_AGENT_RUNAWAY_V2": "1"}, clear=False)
    def test_release_wait_honors_explicit_user_request(self) -> None:
        with temporary_agent_paths() as paths:
            root = paths.workspace / "demo"
            self._write_project(root)
            (root / "TASKS.md").write_text("- [x] T-001 done\n", encoding="utf-8")
            (root / "ENV.md").write_text(
                "quality:\n  commands:\n    - id: smoke\n      cmd: [\"echo\", \"ok\"]\n",
                encoding="utf-8",
            )

            session = self._bind_session(paths)
            session.meta.project_workflow_stage = "release"
            session.meta.project_runaway_checkpoint = "release_wait"
            session.meta.project_runaway_v2_phase = "release_wait"
            checklist = build_checklist(paths, "demo")
            for item in checklist.items:
                item.status = "passed"
            save_checklist(paths, checklist)
            agent = self._make_agent(paths, session)

            with patch.object(
                agent,
                "_run_parent_tool_loop",
                return_value=MagicMock(
                    final_text="已检查并修复",
                    tool_rounds=1,
                    finish_reason="stop",
                    exceeded=False,
                    qa_soft_reminder_injected=False,
                ),
            ) as model_loop, patch.object(
                agent,
                "_finish_short_tool_loop",
                return_value=TurnResult(
                    assistant_text="已检查并修复",
                    tool_rounds=1,
                    finish_reason="stop",
                ),
            ):
                result = agent.run_turn("去寻找bug并修复")

            self.assertEqual(result.assistant_text, "已检查并修复")
            model_loop.assert_called_once()
            self.assertEqual(session.meta.project_runaway_checkpoint, "release_wait")
            self.assertEqual(session.meta.project_runaway_v2_phase, "release_wait")

    @patch.dict(os.environ, {"MY_AGENT_RUNAWAY_V2": "1"}, clear=False)
    def test_release_wait_maintenance_turn_exposes_implementation_context(self) -> None:
        """A user-requested repair must not inherit the release write gate."""
        with temporary_agent_paths() as paths:
            root = paths.workspace / "demo"
            self._write_project(root)
            (root / "TASKS.md").write_text("- [x] T-001 done\n", encoding="utf-8")
            (root / "ENV.md").write_text(
                "quality:\n  commands:\n    - id: smoke\n      cmd: [\"echo\", \"ok\"]\n",
                encoding="utf-8",
            )

            session = self._bind_session(paths)
            session.meta.project_workflow_stage = "release"
            session.meta.project_runaway_checkpoint = "release_wait"
            session.meta.project_runaway_v2_phase = "release_wait"
            checklist = build_checklist(paths, "demo")
            for item in checklist.items:
                item.status = "passed"
            save_checklist(paths, checklist)
            phase = derive_phase(
                paths,
                "demo",
                plan_status="confirmed",
                checklist=checklist,
            )
            plan = build_turn_plan(
                paths=paths,
                project_id="demo",
                phase=phase,
                checklist=checklist,
                user_text="去寻找 bug 并修复",
                intent="execute",
                allow_user_request=True,
            )
            agent = self._make_agent(paths, session)
            controller = RunawayController(agent)

            controller._apply_plan(plan, phase)

            self.assertEqual(plan.phase, "implement")
            self.assertIn("implementation/implementing", plan.system_append)
            self.assertIn("write_text/patch_file", plan.system_append)
            self.assertEqual(session.meta.project_workflow_stage, "implementation")
            self.assertEqual(session.meta.project_runaway_checkpoint, "implementing")
            self.assertEqual(
                agent.executor.session.project_runaway_checkpoint,
                "implementing",
            )

            # The temporary maintenance context must not become a persisted
            # phase change after the turn is reconciled from disk.
            controller._mirror_runtime(phase, plan, checklist)
            self.assertEqual(session.meta.project_runaway_checkpoint, "release_wait")
            self.assertEqual(session.meta.project_workflow_stage, "release")
            self.assertEqual(
                agent.executor.session.runaway_v2_runtime_workflow_stage,
                "",
            )

    @patch.dict(os.environ, {"MY_AGENT_RUNAWAY_V2": "1"}, clear=False)
    def test_release_wait_maintenance_write_survives_tool_meta_refresh(self) -> None:
        """The real tool validator must retain the temporary maintenance stage."""
        with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
            root = paths.workspace / "demo"
            self._write_project(root)
            (root / "TASKS.md").write_text("- [x] T-001 done\n", encoding="utf-8")
            (root / "ENV.md").write_text(
                "quality:\n  commands:\n    - id: smoke\n      cmd: [\"echo\", \"ok\"]\n",
                encoding="utf-8",
            )

            session = self._bind_session(paths)
            session.meta.project_workflow_stage = "release"
            session.meta.project_runaway_checkpoint = "release_wait"
            session.meta.project_runaway_v2_phase = "release_wait"
            session.save()
            checklist = build_checklist(paths, "demo")
            for item in checklist.items:
                item.status = "passed"
            save_checklist(paths, checklist)
            phase = derive_phase(
                paths,
                "demo",
                plan_status="confirmed",
                checklist=checklist,
            )
            plan = build_turn_plan(
                paths=paths,
                project_id="demo",
                phase=phase,
                checklist=checklist,
                user_text="修复 app.py 的 bug",
                intent="execute",
                allow_user_request=True,
            )
            agent = self._make_agent(paths, session)
            controller = RunawayController(agent)

            controller._apply_plan(plan, phase)
            error = agent.executor.validate(
                "run_evolved",
                {
                    "tool_name": "write_text",
                    "arguments": {
                        "path": "workspace/demo/app.py",
                        "content": "fixed = True\n",
                    },
                },
            )

            self.assertIsNone(
                error,
                error.error.message if error is not None and error.error else error,
            )
            self.assertEqual(
                agent.executor.session.project_workflow_stage,
                "implementation",
            )

    @patch.dict(os.environ, {"MY_AGENT_RUNAWAY_V2": "1"}, clear=False)
    def test_fatal_model_error_does_not_auto_chain_and_pauses(self) -> None:
        """A transport error must stop the v2 owner before retrying another turn."""
        with temporary_agent_paths() as paths:
            root = paths.workspace / "demo"
            root.mkdir(parents=True, exist_ok=True)
            (root / "PROJECT.md").write_text(
                "# Demo\n\nREQ-001\nAC-001\n\n## 验收标准\n\n"
                "- 命令：`python verify.py` 期望退出码：0\n",
                encoding="utf-8",
            )
            (root / "DESIGN.md").write_text("UX-001\nTD-001\n", encoding="utf-8")
            (root / "TASKS.md").write_text("- [ ] T-001 implement\n", encoding="utf-8")
            (root / "VERIFY.md").write_text("V-001 T-001\nAC-001\n", encoding="utf-8")
            (root / "ENV.md").write_text(
                "quality:\n  commands:\n    - id: smoke\n      cmd: [\"echo\", \"ok\"]\n",
                encoding="utf-8",
            )
            session = self._bind_session(paths)
            agent = self._make_agent(paths, session)
            with patch.object(
                agent,
                "_run_parent_tool_loop",
                return_value=MagicMock(
                    final_text="",
                    tool_rounds=0,
                    finish_reason="error",
                    exceeded=False,
                    qa_soft_reminder_injected=False,
                ),
            ) as model_loop, patch.object(
                agent,
                "_finish_short_tool_loop",
                return_value=TurnResult(
                    assistant_text="",
                    tool_rounds=0,
                    finish_reason="error",
                ),
            ):
                result = agent.run_turn("开始实现")

            self.assertEqual(result.finish_reason, "error")
            model_loop.assert_called_once()
            self.assertTrue(session.meta.project_runaway_paused_reason)
            self.assertIn("异常", session.meta.project_runaway_paused_reason)

    def test_short_tool_loop_preserves_model_error_finish_reason(self) -> None:
        """An empty transport error must not become a retryable budget exhaustion."""
        with temporary_agent_paths() as paths:
            session = self._bind_session(paths)
            agent = self._make_agent(paths, session)

            result = agent._finish_short_tool_loop(
                loop_result=ToolLoopSegmentResult(
                    final_text="",
                    tool_rounds=0,
                    finish_reason="error",
                ),
                loop_max=12,
                intent="execute",
                spawn_explore_flag=False,
                subagent_tool_rounds=0,
            )

            self.assertEqual(result.finish_reason, "error")
            self.assertFalse(result.tool_loop_exceeded)

    def test_mirror_uses_fresh_phase_after_acceptance(self) -> None:
        with temporary_agent_paths() as paths:
            session = self._bind_session(paths, "demo")
            agent = MagicMock()
            agent.session = session
            controller = RunawayController(agent)
            plan = MagicMock(mode="auto", user_line="准备中")
            checklist = Checklist(1, "demo", "", "")
            with patch(
                "runaway_v2.controller.build_v2_state_fields",
                return_value={
                    "runaway_phase": "implement",
                    "runaway_user_line": "正在实现 T-001",
                    "runaway_blocked": False,
                },
            ):
                controller._mirror_runtime(
                    PhaseResult(phase="prepare"), plan, checklist
                )
            self.assertEqual(session.meta.project_runaway_v2_phase, "implement")
            self.assertEqual(session.meta.project_runaway_v2_user_line, "正在实现 T-001")
            self.assertEqual(session.meta.project_runaway_checkpoint, "implementing")

    def test_mirror_reloads_checklist_after_tool_changes(self) -> None:
        with temporary_agent_paths() as paths:
            session = self._bind_session(paths, "demo")
            agent = MagicMock()
            agent.session = session
            controller = RunawayController(agent)
            initial = Checklist(1, "demo", "old", "old", items=[])
            refreshed = Checklist(1, "demo", "new", "new", items=[])
            plan = MagicMock(mode="auto", user_line="准备中")

            with patch(
                "runaway_v2.controller.load_or_build_checklist",
                return_value=refreshed,
            ) as reload_checklist, patch(
                "runaway_v2.controller.build_v2_state_fields",
                return_value={
                    "runaway_phase": "verify",
                    "runaway_user_line": "验收中",
                    "runaway_blocked": False,
                },
            ) as build_state:
                controller._mirror_runtime(
                    PhaseResult(phase="prepare"), plan, initial
                )

            reload_checklist.assert_called_once_with(paths, "demo")
            self.assertIs(build_state.call_args.kwargs["checklist"], refreshed)

    def test_mirror_clears_active_task_at_release_wait(self) -> None:
        with temporary_agent_paths() as paths:
            session = self._bind_session(paths, "demo")
            session.meta.project_active_task_id = "T-001"
            agent = MagicMock()
            agent.session = session
            controller = RunawayController(agent)
            plan = MagicMock(mode="auto", user_line="验收中")
            checklist = Checklist(1, "demo", "", "")

            with patch(
                "runaway_v2.controller.build_v2_state_fields",
                return_value={
                    "runaway_phase": "release_wait",
                    "runaway_user_line": "验收清单已全部通过，等待发布确认",
                    "runaway_blocked": False,
                },
            ):
                controller._mirror_runtime(
                    PhaseResult(phase="verify"), plan, checklist
                )

            self.assertEqual(session.meta.project_runaway_checkpoint, "release_wait")
            self.assertEqual(session.meta.project_workflow_stage, "release")
            self.assertEqual(session.meta.project_active_task_id, "")

    def test_v2_runtime_mirror_survives_session_reload(self) -> None:
        with temporary_agent_paths() as paths:
            session = self._bind_session(paths)
            session.meta.project_runaway_v2_phase = "human"
            session.meta.project_runaway_v2_mode = "human"
            session.meta.project_runaway_v2_user_line = "验收项 MX-5 需要人工处理"
            session.meta.project_runaway_v2_blocked = True
            session.save()

            reloaded = Session.load(paths, session.conversation_id)

            self.assertEqual(reloaded.meta.project_runaway_v2_phase, "human")
            self.assertEqual(reloaded.meta.project_runaway_v2_mode, "human")
            self.assertEqual(
                reloaded.meta.project_runaway_v2_user_line,
                "验收项 MX-5 需要人工处理",
            )
            self.assertTrue(reloaded.meta.project_runaway_v2_blocked)

    @patch.dict(os.environ, {"MY_AGENT_RUNAWAY_V2_DIRECTED_AFTER": "2"}, clear=False)
    def test_it_6104_escalation_directed_then_human(self) -> None:
        with temporary_agent_paths() as paths:
            self._write_project(paths.workspace / "demo")
            checklist = build_checklist(paths, "demo")
            item = next(entry for entry in checklist.items if entry.id == "MX-5")
            item.status = "failed"
            item.attempts = 2
            item.directed_used = False
            phase = derive_phase(paths, "demo", plan_status="confirmed", checklist=checklist)
            directed_plan = build_turn_plan(
                paths=paths,
                project_id="demo",
                phase=phase,
                checklist=checklist,
                user_text="继续",
                intent="execute",
            )
            self.assertEqual(directed_plan.mode, "directed")

            item.directed_used = True
            item.attempts = 3
            human_plan = build_turn_plan(
                paths=paths,
                project_id="demo",
                phase=phase,
                checklist=checklist,
                user_text="继续",
                intent="execute",
            )
            self.assertEqual(human_plan.mode, "human")
            from runaway_v2.plan import should_chain

            self.assertFalse(
                should_chain(
                    plan=human_plan,
                    hook_ok=False,
                    runaway_enabled=True,
                    cancelled=False,
                    intent="execute",
                    checklist=checklist,
                    phase=phase,
                )
            )

    @patch.dict(os.environ, {"MY_AGENT_RUNAWAY_V2": "1"}, clear=False)
    def test_prepare_hook_still_chains_until_docs_ready(self) -> None:
        with temporary_agent_paths() as paths:
            root = paths.workspace / "demo"
            root.mkdir(parents=True, exist_ok=True)
            (root / "PROJECT.md").write_text("# Demo\n", encoding="utf-8")
            phase = derive_phase(paths, "demo", plan_status="draft", checklist=build_checklist(paths, "demo"))
            plan = build_turn_plan(
                paths=paths,
                project_id="demo",
                phase=phase,
                checklist=build_checklist(paths, "demo"),
                user_text="继续",
                intent="execute",
            )
            from runaway_v2.acceptance import run_acceptance
            from runaway_v2.plan import should_chain

            hook = run_acceptance(paths, "demo", plan.acceptance_item)
            self.assertFalse(hook.ok)
            self.assertIn("PROJECT.md", hook.tail)
            self.assertTrue(
                should_chain(
                    plan=plan,
                    hook_ok=hook.ok,
                    runaway_enabled=True,
                    cancelled=False,
                    intent="execute",
                    checklist=build_checklist(paths, "demo"),
                    phase=phase,
                )
            )

    @patch.dict(os.environ, {"MY_AGENT_RUNAWAY_V2": "1"}, clear=False)
    def test_controller_passes_llm_tools_to_parent_loop(self) -> None:
        with temporary_agent_paths() as paths:
            self._write_project(paths.workspace / "demo")
            session = self._bind_session(paths)
            agent = self._make_agent(paths, session)
            captured: dict[str, object] = {}

            def _capture_tools(*, tools, **kwargs):
                captured["tools"] = tools
                return MagicMock(
                    final_text="ok",
                    tool_rounds=0,
                    finish_reason="stop",
                    exceeded=False,
                    qa_soft_reminder_injected=False,
                )

            with patch.object(agent, "_run_parent_tool_loop", side_effect=_capture_tools):
                with patch.object(
                    agent,
                    "_finish_short_tool_loop",
                    return_value=TurnResult(
                        assistant_text="ok",
                        tool_rounds=0,
                        finish_reason="stop",
                    ),
                ):
                    agent.run_turn("继续狂奔")
            tools = captured.get("tools")
            self.assertIsInstance(tools, list)
            assert isinstance(tools, list)
            self.assertGreater(len(tools), 0)
            names = {item["function"]["name"] for item in tools}
            self.assertIn("run_evolved", names)

    @patch.dict(os.environ, {"MY_AGENT_RUNAWAY_V2": "1"}, clear=False)
    def test_v2_server_chains_when_active(self) -> None:
        with temporary_agent_paths() as paths:
            pid = "demo"
            create_project(paths, pid)
            session = create_new(paths, conversation_id="_runaway_v2_chain_")
            session.meta.active_shell = "project"
            session.meta.project_id = pid
            session.meta.project_root = f"workspace/{pid}"
            session.meta.project_runaway_enabled = True
            session.meta.project_runaway_v2_phase = "prepare"
            session.meta.project_runaway_v2_blocked = False
            agent = self._make_agent(paths, session)
            self.assertTrue(agent.should_chain_runaway_after_turn("completed"))
            # The disk-derived phase is authoritative; an old runtime mirror
            # must not stop a continuation after a session reload.
            session.meta.project_runaway_v2_blocked = True
            self.assertTrue(agent.should_chain_runaway_after_turn("completed"))

            root = paths.workspace / pid
            self._write_project(root)
            (root / "TASKS.md").write_text("- [x] T-001 done\n", encoding="utf-8")
            (root / "VERIFY.md").write_text("V-001 T-001 pass\nAC-001\n", encoding="utf-8")
            (root / "ENV.md").write_text(
                "quality:\n  commands:\n    - id: smoke\n      cmd: [\"echo\", \"ok\"]\n",
                encoding="utf-8",
            )
            final_checklist = build_checklist(paths, pid)
            for item in final_checklist.items:
                item.status = "passed"
            save_checklist(paths, final_checklist)
            session.meta.project_runaway_v2_blocked = False
            session.meta.project_runaway_v2_phase = "human"
            session.meta.project_plan_status = "confirmed"
            self.assertFalse(agent.should_chain_runaway_after_turn("completed"))

    @patch.dict(os.environ, {"MY_AGENT_RUNAWAY_V2": "1"}, clear=False)
    def test_should_chain_ignores_qa_intent_for_prepare(self) -> None:
        with temporary_agent_paths() as paths:
            root = paths.workspace / "demo"
            root.mkdir(parents=True, exist_ok=True)
            (root / "PROJECT.md").write_text("# Demo\n", encoding="utf-8")
            phase = derive_phase(paths, "demo", plan_status="draft", checklist=build_checklist(paths, "demo"))
            plan = build_turn_plan(
                paths=paths,
                project_id="demo",
                phase=phase,
                checklist=build_checklist(paths, "demo"),
                user_text="继续",
                intent="qa",
            )
            from runaway_v2.plan import should_chain

            self.assertTrue(plan.chain_after_ok)
            self.assertTrue(
                should_chain(
                    plan=plan,
                    hook_ok=False,
                    runaway_enabled=True,
                    cancelled=False,
                    intent="qa",
                    checklist=build_checklist(paths, "demo"),
                    phase=phase,
                )
            )

    @patch.dict(os.environ, {"MY_AGENT_RUNAWAY_V2": "1"}, clear=False)
    def test_continue_utterance_classifies_as_execute_in_controller(self) -> None:
        from exec_reliability import is_runaway_continue_utterance

        self.assertTrue(is_runaway_continue_utterance("继续"))
        self.assertTrue(is_runaway_continue_utterance("继续狂奔"))
        self.assertFalse(is_runaway_continue_utterance("为什么停下来了？"))


if __name__ == "__main__":
    unittest.main()
