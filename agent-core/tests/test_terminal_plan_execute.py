"""Terminal Plan-and-Execute (T-5730 · IT-595～598)."""

from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock, patch

from session import create_terminal_session
from terminal_plan import (
    TerminalPlanArtifact,
    TerminalPlanStep,
    artifact_from_planner_payload,
    auto_plan_gate_rules,
    classify_terminal_plan_need,
    clear_artifact,
    goal_fingerprint,
    is_continue_turn,
    is_explicit_replan_turn,
    is_skip_plan_turn,
    load_artifact,
    resolve_auto_plan_turn,
    save_artifact,
    should_auto_plan_turn,
    should_handle_terminal_plan_turn,
    should_resume_step_execution,
)

from tests.isolation_helpers import make_temp_agent_paths


class TerminalPlanAutoGateTests(unittest.TestCase):
    """IT-595 · should_auto_plan_turn deterministic routing."""

    def setUp(self) -> None:
        self.paths = make_temp_agent_paths(self)
        self.session = create_terminal_session(
            self.paths,
            terminal_scope_kind="agent",
            terminal_cwd=".",
        )
        self.session.goal = "实现用户认证模块"

    def test_it_595_plan_mode_marker_triggers_plan(self) -> None:
        self.assertEqual(
            auto_plan_gate_rules(
                "我要测试plan模式，写个简易斗地主",
                self.session,
                "execute",
                None,
            ),
            "yes",
        )
        self.assertTrue(
            should_handle_terminal_plan_turn(
                "我要测试plan模式，写个简易斗地主",
                self.session,
                "execute",
                None,
            )
        )

    def test_it_606_ambiguous_game_classify_gate(self) -> None:
        self.assertEqual(
            auto_plan_gate_rules(
                "写个简易斗地主",
                self.session,
                "execute",
                None,
            ),
            "classify",
        )
        self.assertFalse(should_auto_plan_turn("写个简易斗地主", self.session, "execute", None))
        self.assertTrue(
            should_handle_terminal_plan_turn(
                "写个简易斗地主",
                self.session,
                "execute",
                None,
            )
        )

    def test_it_606_classifier_needs_plan_for_new_game(self) -> None:
        from unittest.mock import MagicMock

        from llm_client import LLMResponse

        mock_llm = MagicMock()
        mock_llm.chat.return_value = LLMResponse(
            model="flash",
            content='{"needs_plan": true, "reason": "从零写游戏需多步"}',
            tool_calls=[],
            finish_reason="stop",
            usage=None,
            raw={},
        )
        decision = classify_terminal_plan_need(
            "写个简易斗地主",
            llm=mock_llm,
            meta=self.session.meta,
        )
        self.assertTrue(decision.needs_plan)
        self.assertEqual(decision.source, "classifier")

        resolved = resolve_auto_plan_turn(
            "写个简易斗地主",
            self.session,
            "execute",
            None,
            llm=mock_llm,
        )
        self.assertTrue(resolved.needs_plan)
        self.assertEqual(resolved.source, "classifier")

    def test_it_606_classifier_skip_simple_edit(self) -> None:
        from unittest.mock import MagicMock

        from llm_client import LLMResponse

        mock_llm = MagicMock()
        mock_llm.chat.return_value = LLMResponse(
            model="flash",
            content='{"needs_plan": false, "reason": "单点小修"}',
            tool_calls=[],
            finish_reason="stop",
            usage=None,
            raw={},
        )
        resolved = resolve_auto_plan_turn(
            "把按钮颜色改成蓝色",
            self.session,
            "execute",
            None,
            llm=mock_llm,
        )
        self.assertFalse(resolved.needs_plan)

    def test_it_595_complex_execute_triggers_plan(self) -> None:
        self.assertTrue(
            should_auto_plan_turn(
                "实现多文件的用户认证与 JWT 刷新",
                self.session,
                "execute",
                None,
            )
        )

    def test_it_595_skip_simple_single_file_fix(self) -> None:
        self.assertFalse(
            should_auto_plan_turn(
                "修 src/foo.py 的 typo",
                self.session,
                "execute",
                None,
            )
        )

    def test_it_595_skip_direct_edit_marker(self) -> None:
        self.assertFalse(
            should_auto_plan_turn(
                "直接改 backend 接口，别计划",
                self.session,
                "execute",
                None,
            )
        )

    def test_it_595_executing_phase_lock(self) -> None:
        artifact = TerminalPlanArtifact(
            goal_fingerprint=goal_fingerprint(self.session),
            phase="executing",
            steps=[TerminalPlanStep(id="s1", title="one")],
            initial_plan_done=True,
        )
        self.assertFalse(
            should_auto_plan_turn(
                "实现全新支付子系统，多文件重构",
                self.session,
                "execute",
                artifact,
            )
        )

    def test_it_595_continue_marker_helpers(self) -> None:
        self.assertTrue(is_continue_turn("继续"))
        self.assertTrue(is_skip_plan_turn("别计划，直接改"))
        self.assertTrue(is_explicit_replan_turn("请重新规划"))


class TerminalPlanStateMachineTests(unittest.TestCase):
    """IT-596～598 · artifact + resume + budgets."""

    def setUp(self) -> None:
        self.paths = make_temp_agent_paths(
            self,
            copy_tool_dirs=("common/write_text", "common/run_command"),
        )
        self.session = create_terminal_session(
            self.paths,
            terminal_scope_kind="agent",
            terminal_cwd=".",
        )
        self.session.goal = "重构 API 层"
        self.fp = goal_fingerprint(self.session)

    def test_it_596_artifact_roundtrip(self) -> None:
        payload = {
            "summary": "三步骤计划",
            "steps": [
                {
                    "id": "step-1",
                    "title": "Add model",
                    "scope": ["src/model.py"],
                    "verify": ["python -m compileall src"],
                },
                {"id": "step-2", "title": "Wire routes", "verify": []},
            ],
        }
        artifact = artifact_from_planner_payload(payload, goal_fp=self.fp)
        save_artifact(self.session, artifact)
        loaded = load_artifact(self.session)
        assert loaded is not None
        self.assertEqual(loaded.phase, "executing")
        self.assertEqual(len(loaded.steps), 2)
        self.assertTrue(loaded.initial_plan_done)

    def test_it_597_resume_without_replan_on_continue(self) -> None:
        artifact = TerminalPlanArtifact(
            goal_fingerprint=self.fp,
            phase="executing",
            steps=[TerminalPlanStep(id="s1", title="one"), TerminalPlanStep(id="s2", title="two")],
            current_step=1,
            initial_plan_done=True,
        )
        self.assertTrue(should_resume_step_execution(artifact))
        self.assertTrue(
            should_handle_terminal_plan_turn("继续", self.session, "execute", artifact)
        )
        self.assertFalse(
            should_auto_plan_turn("继续", self.session, "execute", artifact)
        )

    def test_it_598_step_retry_budget_constants(self) -> None:
        from terminal_plan import replan_max, step_retry_max

        self.assertEqual(step_retry_max(), 2)
        self.assertEqual(replan_max(), 1)

    def test_it_598_goal_change_clears_artifact_mismatch(self) -> None:
        artifact = TerminalPlanArtifact(
            goal_fingerprint="old-fp",
            phase="executing",
            steps=[TerminalPlanStep(id="s1", title="one")],
            initial_plan_done=True,
        )
        save_artifact(self.session, artifact)
        loaded = load_artifact(self.session)
        assert loaded is not None
        self.assertNotEqual(loaded.goal_fingerprint, goal_fingerprint(self.session))


class TerminalPlanAgentIntegrationTests(unittest.TestCase):
    """IT-596 · planning switches model effort then executes with session model."""

    def setUp(self) -> None:
        self.paths = make_temp_agent_paths(
            self,
            copy_tool_dirs=("common/write_text", "common/run_command"),
        )
        self.session = create_terminal_session(
            self.paths,
            terminal_scope_kind="agent",
            terminal_cwd=".",
        )
        self.session.goal = "实现多文件认证模块"
        self.session.meta.llm_model = "deepseek-v4-flash"
        self.session.meta.reasoning_effort = "medium"
        self.session.save()

    @patch("subagent.SubagentRunner.run_terminal_plan")
    @patch("agent.Agent._run_parent_tool_loop")
    def test_it_596_plan_then_one_step_stop(
        self,
        mock_loop: MagicMock,
        mock_plan: MagicMock,
    ) -> None:
        from agent import Agent, ToolLoopSegmentResult
        from subagent import SubagentResult

        plan_json = json.dumps(
            {
                "summary": "两步",
                "steps": [
                    {"id": "step-1", "title": "Add file", "verify": []},
                    {"id": "step-2", "title": "Add tests", "verify": []},
                ],
            },
            ensure_ascii=False,
        )
        mock_plan.return_value = SubagentResult(
            kind="terminal_plan",
            summary=plan_json,
            paths_cited=[],
            tool_rounds=1,
            truncated=False,
            task="plan",
        )
        mock_loop.return_value = ToolLoopSegmentResult(
            final_text="已写入文件。",
            tool_rounds=2,
            finish_reason="stop",
        )

        agent = Agent.create(self.session)
        events: list[dict] = []
        agent.on_turn_event = events.append

        result = agent.run_turn(
            "实现多文件认证模块，需要重构接口层",
            spawn_explore=False,
        )

        mock_plan.assert_called_once()
        mock_loop.assert_called_once()
        self.assertEqual(result.finish_reason, "terminal_plan_step_done")
        self.assertIn("继续", result.assistant_text)
        artifact = load_artifact(self.session)
        assert artifact is not None
        self.assertEqual(artifact.current_step, 1)
        plan_notices = [
            e.get("text", "")
            for e in events
            if e.get("type") == "turn.notice" and "auto-plan" in str(e.get("text", ""))
        ]
        self.assertTrue(plan_notices)
        execute_notices = [
            e.get("text", "")
            for e in events
            if e.get("type") == "turn.notice" and "execute ·" in str(e.get("text", ""))
        ]
        self.assertTrue(execute_notices)

    @patch("agent.Agent._run_execute_segments")
    def test_it_597_simple_task_bypasses_plan_machine(self, mock_segments: MagicMock) -> None:
        from agent import Agent, TurnResult

        mock_segments.return_value = TurnResult(
            assistant_text="ok",
            tool_rounds=0,
            finish_reason="stop",
        )
        agent = Agent.create(self.session)
        agent.run_turn("修 src/foo.py 的 typo", spawn_explore=False)
        mock_segments.assert_called_once()


class TerminalPlanStatusBarTests(unittest.TestCase):
    """IT-603 · plan status in status bar."""

    def test_it_603_plan_status_segments(self) -> None:
        from terminal_plan import plan_status_segment

        self.assertEqual(
            plan_status_segment(mode="planning", model="deepseek-v4-pro", effort="max"),
            "auto-plan · pro · max",
        )
        self.assertEqual(
            plan_status_segment(
                mode="planning",
                model="deepseek-v4-flash",
                effort="high",
                degraded=True,
            ),
            "auto-plan · flash · high · 降级",
        )
        self.assertEqual(
            plan_status_segment(
                mode="executing",
                model="deepseek-v4-flash",
                effort="medium",
                step=2,
                total=5,
                retry=1,
            ),
            "step 2/5 · execute · medium · retry 1",
        )

    def test_it_603_format_status_bar_includes_plan(self) -> None:
        from terminal_ui import StatusBarContent, format_status_bar_line

        line = format_status_bar_line(
            StatusBarContent(
                llm_model="flash",
                turn_mode="agent",
                root_short="huiyi",
                session_suffix="abc",
                status="working",
                plan_status="step 1/3 · execute · medium",
            )
        )
        self.assertIn("step 1/3 · execute · medium", line)

    def test_it_603_console_applies_plan_state_event(self) -> None:
        from terminal_ui import TerminalConsole

        console = TerminalConsole.create(kind="plain")
        console.sink.emit(
            {
                "type": "terminal.plan.state",
                "mode": "planning",
                "model": "deepseek-v4-pro",
                "effort": "max",
            }
        )
        self.assertIn("auto-plan", console._plan_status)

    def test_it_603_console_applies_degraded_plan_state(self) -> None:
        from terminal_ui import TerminalConsole

        console = TerminalConsole.create(kind="plain")
        console.sink.emit(
            {
                "type": "terminal.plan.state",
                "mode": "planning",
                "model": "deepseek-v4-flash",
                "effort": "high",
                "degraded": True,
            }
        )
        self.assertIn("降级", console._plan_status)


class TerminalPlanningProfileTests(unittest.TestCase):
    """IT-604 · weak-API planning profile (Strategy A)."""

    def setUp(self) -> None:
        self.paths = make_temp_agent_paths(self)
        self.session = create_terminal_session(
            self.paths,
            terminal_scope_kind="agent",
            terminal_cwd=".",
        )

    @staticmethod
    def _vendor_registry(*, include_pro: bool = True) -> "ModelRegistry":
        from llm_models import ModelEntry, ModelRegistry

        flash = ModelEntry(
            id="vendor-flash",
            name="Flash",
            vendor="TestVendor",
            base_url="https://example.com",
            provider_model="flash-model",
            tier="flash",
            api_key="flash-key",
        )
        models: list[ModelEntry] = [flash]
        default_pro_id = "vendor-pro"
        if include_pro:
            models.append(
                ModelEntry(
                    id="vendor-pro",
                    name="Pro",
                    vendor="TestVendor",
                    base_url="https://example.com",
                    provider_model="pro-model",
                    tier="pro",
                    api_key="pro-key",
                )
            )
        else:
            default_pro_id = "other-pro"
        models.append(
            ModelEntry(
                id="other-pro",
                name="Other Pro",
                vendor="OtherVendor",
                base_url="https://example.com",
                provider_model="other-pro-model",
                tier="pro",
                api_key="other-key",
            )
        )
        return ModelRegistry(
            models=tuple(models),
            default_flash_id="vendor-flash",
            default_pro_id=default_pro_id,
            alias_map={},
        )

    def test_it_604_format_planning_notice(self) -> None:
        from terminal_plan import format_planning_notice

        self.assertIn("规划中", format_planning_notice(model_label="flash", effort="high", degraded=True))
        self.assertIn("降级", format_planning_notice(model_label="flash", effort="high", degraded=True))

    def test_it_604_flash_only_degrades_to_main_high(self) -> None:
        from terminal_plan import resolve_terminal_planning_profile

        registry = self._vendor_registry(include_pro=False)
        with patch(
            "llm_routing.resolve_model_id_for_role",
            return_value="vendor-flash",
        ):
            profile = resolve_terminal_planning_profile(
                self.session,
                registry=registry,
                paths=self.paths,
            )
        self.assertTrue(profile.degraded)
        self.assertEqual(profile.model_id, "vendor-flash")
        self.assertEqual(profile.reasoning_effort, "high")

    def test_it_604_same_vendor_pro_uses_max(self) -> None:
        from terminal_plan import resolve_terminal_planning_profile

        registry = self._vendor_registry(include_pro=True)
        with patch(
            "llm_routing.resolve_model_id_for_role",
            return_value="vendor-flash",
        ):
            profile = resolve_terminal_planning_profile(
                self.session,
                registry=registry,
                paths=self.paths,
            )
        self.assertFalse(profile.degraded)
        self.assertEqual(profile.model_id, "vendor-pro")
        self.assertEqual(profile.reasoning_effort, "max")

    def test_it_604_main_pro_without_separate_pro_still_max(self) -> None:
        from terminal_plan import resolve_terminal_planning_profile

        registry = self._vendor_registry(include_pro=True)
        with patch(
            "llm_routing.resolve_model_id_for_role",
            return_value="vendor-pro",
        ):
            profile = resolve_terminal_planning_profile(
                self.session,
                registry=registry,
                paths=self.paths,
            )
        self.assertFalse(profile.degraded)
        self.assertEqual(profile.model_id, "vendor-pro")
        self.assertEqual(profile.reasoning_effort, "max")


if __name__ == "__main__":
    unittest.main()
