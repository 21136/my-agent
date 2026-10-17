"""Phase 62 — orchestration kernel wiring and injectable driver (R1)."""

from __future__ import annotations

import threading
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

from agent import Agent
from session import create_new
from tests.isolation_helpers import temporary_agent_paths
from tools.executor import ExecutorSession, ToolExecutor
from tools.registry import ToolRegistry

from runaway_v2.controller import RunawayController
from runaway_v2.kernel import RunawayRuntime
from runaway_v2.kernel.driver_adapter import AgentDriverAdapter
from runaway_v2.kernel.ports import AgentDriver


def _make_injected_driver():
    from agent import TurnResult

    agent = MagicMock()
    session = MagicMock()
    session.meta.project_id = "demo"
    session.meta.project_plan_status = "confirmed"
    session.meta.project_runaway_paused_reason = ""
    session.messages = []
    session.append_message = MagicMock()
    session.save = MagicMock()
    session.ensure_messages_loaded = MagicMock()
    agent.session = session

    driver = MagicMock(spec=AgentDriver)
    driver.session = session
    driver.cancel_event = MagicMock()
    driver.cancel_event.is_set.return_value = False
    driver.build_llm_tools.return_value = []
    driver.resolve_main_turn_model.return_value = "test-model"
    driver.run_parent_tool_loop.return_value = MagicMock(
        final_text="ok",
        tool_rounds=1,
        finish_reason="stop",
        exceeded=False,
        qa_soft_reminder_injected=False,
    )
    driver.flush_runaway_plan_proposals = MagicMock()
    driver.finish_short_tool_loop.return_value = TurnResult(
        assistant_text="ok",
        tool_rounds=1,
        finish_reason="stop",
    )
    driver.pending_runaway_proposal_ids.return_value = []
    driver.runaway_chain_user_line.return_value = "[Harness] chain"
    driver.pause_runaway = MagicMock()
    driver.sync_turn_mode = MagicMock()
    driver.sync_allowed_evolved = MagicMock()
    driver.ensure_executor_project_scope = MagicMock()
    driver.begin_executor_turn = MagicMock()
    driver.emit_turn_event = MagicMock()
    return agent, driver


def _execute_turn_patches():
    plan = MagicMock(
        mode="auto",
        phase="implement",
        max_tool_rounds=1,
        system_append="",
        user_line="",
        acceptance_item=None,
    )
    phase = MagicMock(phase="implement")
    return [
        patch("agent.prepare_session_for_s4"),
        patch("runaway_v2.continuation.repair_stale_v2_plan_status"),
        patch("runaway_v2.kernel.runtime.derive_phase", return_value=phase),
        patch("runaway_v2.kernel.runtime.build_turn_plan", return_value=plan),
        patch(
            "runaway_v2.kernel.runtime.load_or_build_checklist",
            return_value=MagicMock(items=[]),
        ),
        patch(
            "runaway_v2.kernel.project_sync.load_or_build_checklist",
            return_value=MagicMock(items=[]),
        ),
        patch("runaway_v2.kernel.runtime.append_progress"),
        patch("runaway_v2.kernel.runtime.run_acceptance", return_value=MagicMock(ok=True, tail="")),
        patch("runaway_v2.kernel.runtime.save_checklist"),
        patch("turn_intent.classify_turn", return_value="execute"),
        patch("turn_intent.intent_label", return_value="执行"),
    ]


def test_bind_cancel_event_shares_same_event_on_agent_session_executor():
    """T-6241: server bridge event must reach session for pending_runaway_work()."""
    bridge = threading.Event()
    llm = MagicMock()
    llm.set_cancel_event = MagicMock()

    with temporary_agent_paths() as paths:
        session = create_new(paths, conversation_id="_runaway_cancel_bind_")
        registry = ToolRegistry.load(paths)
        executor = ToolExecutor(
            registry=registry,
            session=ExecutorSession(session_dir=session.session_dir),
            confirm_fn=lambda _req: True,
        )
        agent = Agent(session=session, executor=executor, llm=llm)
        agent.bind_cancel_event(bridge)

        assert agent.cancel_event is bridge
        assert executor.cancel_event is bridge
        assert session.cancel_event is bridge
        session.save()
        assert session.cancel_event is bridge


def test_runtime_and_controller_share_entry():
    agent = MagicMock()
    agent.session.meta.project_id = ""
    agent._sync_turn_mode = MagicMock()
    agent._sync_allowed_evolved = MagicMock()
    agent.executor._ensure_project_scope_tools_allowed = MagicMock()
    with patch("agent.prepare_session_for_s4"), patch(
        "runaway_v2.continuation.repair_stale_v2_plan_status"
    ):
        rt = RunawayRuntime(agent)
        result = rt.run_turn("hello")
    assert result.finish_reason == "stop"
    assert "绑定项目" in result.assistant_text

    with patch("agent.prepare_session_for_s4"), patch(
        "runaway_v2.continuation.repair_stale_v2_plan_status"
    ):
        ctrl = RunawayController(agent)
        result2 = ctrl.run_turn("hello")
    assert result2.assistant_text == result.assistant_text


def test_driver_adapter_delegates_sync():
    agent = MagicMock()
    adapter = AgentDriverAdapter(agent)
    adapter.sync_turn_mode()
    agent._sync_turn_mode.assert_called_once()


def test_runtime_accepts_injected_agent_driver():
    agent = MagicMock()
    injected = MagicMock(spec=AgentDriver)
    runtime = RunawayRuntime(agent, driver=injected)
    assert runtime._driver is injected


def test_injected_driver_build_llm_tools_used_on_execute_path():
    agent, driver = _make_injected_driver()
    with ExitStack() as stack:
        for item in _execute_turn_patches():
            stack.enter_context(item)
        stack.enter_context(
            patch("runaway_v2.kernel.runtime.observe_runaway_turn", return_value=False)
        )
        runtime = RunawayRuntime(agent, driver=driver)
        result = runtime.run_turn("继续实现")

    driver.build_llm_tools.assert_called_once()
    driver.run_parent_tool_loop.assert_called_once()
    assert result.finish_reason == "stop"


def test_fatal_finish_pauses_and_does_not_chain():
    from agent import TurnResult

    agent, driver = _make_injected_driver()
    driver.finish_short_tool_loop.return_value = TurnResult(
        assistant_text="",
        tool_rounds=0,
        finish_reason="error",
    )
    with ExitStack() as stack:
        for item in _execute_turn_patches():
            stack.enter_context(item)
        stack.enter_context(
            patch("runaway_v2.kernel.runtime.observe_runaway_turn", return_value=True)
        )
        stack.enter_context(
            patch(
                "runaway_v2.kernel.runtime.should_continue_runaway",
                return_value=True,
            )
        )
        driver.pending_runaway_proposal_ids.return_value = ["proposal-1"]
        result = RunawayRuntime(agent, driver=driver).run_turn("继续实现")

    driver.pause_runaway.assert_called_once_with("狂奔运行异常")
    assert driver.run_parent_tool_loop.call_count == 1
    assert result.finish_reason == "error"


def test_cancel_blocks_stale_proposal_in_turn_chain():
    agent, driver = _make_injected_driver()
    driver.cancel_event.is_set.return_value = True
    driver.pending_runaway_proposal_ids.return_value = ["proposal-1"]
    with ExitStack() as stack:
        for item in _execute_turn_patches():
            stack.enter_context(item)
        stack.enter_context(
            patch("runaway_v2.kernel.runtime.observe_runaway_turn", return_value=True)
        )
        stack.enter_context(
            patch("runaway_v2.kernel.runtime.should_continue_runaway", return_value=False)
        )
        RunawayRuntime(agent, driver=driver).run_turn("继续实现")

    assert driver.run_parent_tool_loop.call_count == 1


def test_stale_proposals_triggers_in_turn_harness_chain():
    agent, driver = _make_injected_driver()
    driver.pending_runaway_proposal_ids.side_effect = [["proposal-1"], []]
    with ExitStack() as stack:
        for item in _execute_turn_patches():
            stack.enter_context(item)
        stack.enter_context(
            patch("runaway_v2.kernel.runtime.observe_runaway_turn", return_value=True)
        )
        stack.enter_context(
            patch("runaway_v2.kernel.runtime.should_continue_runaway", return_value=False)
        )
        RunawayRuntime(agent, driver=driver).run_turn("继续实现")

    assert driver.run_parent_tool_loop.call_count == 2


def test_cancel_blocks_pending_work_in_turn_chain():
    agent, driver = _make_injected_driver()
    driver.cancel_event.is_set.return_value = True
    pending = MagicMock(chain_user_line="[Harness] pending item")
    with ExitStack() as stack:
        for item in _execute_turn_patches():
            stack.enter_context(item)
        stack.enter_context(
            patch("runaway_v2.kernel.runtime.observe_runaway_turn", return_value=True)
        )
        stack.enter_context(
            patch("runaway_v2.kernel.runtime.should_continue_runaway", return_value=True)
        )
        stack.enter_context(
            patch("runaway_v2.kernel.runtime.pending_runaway_work", return_value=pending)
        )
        RunawayRuntime(agent, driver=driver).run_turn("继续实现")

    assert driver.run_parent_tool_loop.call_count == 1


def test_pending_work_triggers_in_turn_continue_chain():
    agent, driver = _make_injected_driver()
    pending = MagicMock(chain_user_line="[Harness] pending item")
    with ExitStack() as stack:
        for item in _execute_turn_patches():
            stack.enter_context(item)
        stack.enter_context(
            patch("runaway_v2.kernel.runtime.observe_runaway_turn", return_value=True)
        )
        stack.enter_context(
            patch(
                "runaway_v2.kernel.runtime.should_continue_runaway",
                side_effect=[True, False],
            )
        )
        stack.enter_context(
            patch("runaway_v2.kernel.runtime.pending_runaway_work", return_value=pending)
        )
        RunawayRuntime(agent, driver=driver).run_turn("继续实现")

    assert driver.run_parent_tool_loop.call_count == 2


def test_runtime_uses_injected_driver_session():
    session = MagicMock(name="driver-session")
    driver = MagicMock(spec=AgentDriver)
    driver.session = session

    runtime = RunawayRuntime(agent=object(), driver=driver)

    assert runtime.session is session


def test_runtime_delegates_session_preparation_to_driver():
    agent, driver = _make_injected_driver()
    agent.session.meta.project_id = ""
    driver.session.meta.project_id = ""
    driver.prepare_session_for_turn = MagicMock()

    with patch("agent.prepare_session_for_s4"), patch(
        "runaway_v2.continuation.repair_stale_v2_plan_status"
    ):
        RunawayRuntime(agent, driver=driver).run_turn("hello")

    driver.prepare_session_for_turn.assert_called_once_with()


def test_project_sync_routes_executor_context_through_driver_ports():
    from runaway_v2.kernel.project_sync import RunawayProjectSync

    driver = MagicMock()
    driver.session.meta.project_id = "demo"
    driver.apply_runaway_execution_context = MagicMock()
    driver.clear_runaway_runtime_workflow_stage = MagicMock()
    sync = RunawayProjectSync(driver)
    plan = MagicMock(
        phase="implement",
        active_task_id="T-1",
        allowed_write_globs=["src/**"],
        forbid_plan_partner=True,
        mode="auto",
        user_line="continue",
    )
    phase = MagicMock(phase="implement")

    sync.apply_plan(plan, phase)
    driver.apply_runaway_execution_context.assert_called_once_with(
        write_scope=["src/**"],
        forbid_plan_partner=True,
        workflow_stage="implementation",
    )

    with patch(
        "runaway_v2.kernel.project_sync.load_or_build_checklist",
        return_value=MagicMock(items=[]),
    ), patch(
        "runaway_v2.kernel.project_sync.build_v2_state_fields",
        return_value={"runaway_phase": "release_wait", "runaway_blocked": False},
    ):
        sync.mirror_runtime(phase, plan, MagicMock(items=[]))

    driver.clear_runaway_runtime_workflow_stage.assert_called_once_with()


def test_driver_adapter_owns_executor_runaway_context():
    agent = MagicMock()
    adapter = AgentDriverAdapter(agent)

    adapter.apply_runaway_execution_context(
        write_scope=["src/**"],
        forbid_plan_partner=True,
        workflow_stage="implementation",
    )

    assert agent.executor.session.runaway_v2_write_scope == ["src/**"]
    assert agent.executor.session.runaway_v2_forbid_plan_partner is True
    assert agent.executor.session.runaway_v2_runtime_workflow_stage == "implementation"

    adapter.clear_runaway_runtime_workflow_stage()

    assert agent.executor.session.runaway_v2_runtime_workflow_stage == ""
