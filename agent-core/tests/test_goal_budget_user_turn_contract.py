from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from agent import ToolLoopSegmentResult
from goal_runtime import GoalRuntime
from server import WsBridge, _build_repl
from session import create_new
from tests.isolation_helpers import temporary_agent_paths


def _agent_for(paths, session, *, confirm=lambda *_args: "y"):
    bridge = WsBridge(emit=lambda _event: None, paths=paths)
    repl = _build_repl(session, paths, bridge, work_record_controller_factory=lambda *_args: None)
    repl.agent.executor.confirm_fn = confirm
    return repl.agent


def _session(paths, conversation_id: str):
    session = create_new(paths, conversation_id=conversation_id)
    session.meta.turn_mode = "agent"
    session.meta.harness = "desktop"
    session.meta.active_shell = "daily"
    session.next_work_intent = "execute"
    session.goal = "implementation task"
    session.save()
    return session


def _over_limit_goal(session, *, pause_reason="budget_exhausted"):
    runtime = GoalRuntime(Path(session.session_dir))
    state = runtime.ensure_goal(goal_text="original task", tool_turns_limit=1, wall_seconds_limit=2)
    state.status = "paused"
    state.pause_reason = pause_reason
    if pause_reason == "repair_exhausted":
        state.budgets.repair_count = state.budgets.repair_limit
    state.budgets.tool_turns_used = 9
    state.budgets.wall_seconds_used = 901
    state.multi_project_unverified = True
    state.multi_project_ids = ["alpha", "beta"]
    state.last_verification = None
    runtime.save()
    return runtime


def test_manual_run_turn_recovers_cumulative_overage_without_resetting_evidence():
    with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
        session = _session(paths, "20261107-budget-manual")
        _over_limit_goal(session)
        agent = _agent_for(paths, session)
        entered = []

        def one_agent_loop(**kwargs):
            entered.append(kwargs["max_rounds"])
            return ToolLoopSegmentResult(
                final_text="Work continues; both project acceptance checks remain pending.",
                tool_rounds=1,
                finish_reason="stop",
            )

        agent._run_parent_tool_loop = one_agent_loop
        result = agent.run_turn("Continue implementing this task", spawn_explore=False)

        loaded = GoalRuntime(Path(session.session_dir)).load()
        assert entered and entered[0] > 1  # the ordinary external run_turn reached Agent execution
        assert result.assistant_text.startswith("Work continues")
        assert loaded is not None
        assert loaded.budgets.tool_turns_used == 10
        assert loaded.budgets.wall_seconds_used >= 901
        assert loaded.multi_project_unverified is True
        assert loaded.multi_project_ids == ["alpha", "beta"]
        assert loaded.last_verification is None
        assert loaded.status != "done"


@pytest.mark.parametrize("pause_reason", ["repair_exhausted", "authorization_required", "unknown_pause"])
def test_load_and_manual_run_turn_preserve_safety_pause_despite_cumulative_overage(pause_reason):
    with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
        session = _session(paths, f"20261107-guard-{pause_reason}")
        runtime = _over_limit_goal(session, pause_reason=pause_reason)

        # Exercise the disk-load reconciliation boundary before the external Agent path.
        loaded = GoalRuntime(Path(session.session_dir)).load()
        assert loaded is not None
        assert loaded.pause_reason == pause_reason
        assert loaded.budgets.tool_turns_used == 9
        agent = _agent_for(paths, session)
        agent._run_parent_tool_loop = lambda **_kwargs: pytest.fail("safety pause must block execution")
        result = agent.run_turn("Continue implementing this task", spawn_explore=False)

        assert result.finish_reason == "goal_safety_paused"
        after = GoalRuntime(Path(session.session_dir)).load()
        assert after is not None
        assert after.pause_reason == pause_reason
        assert after.status == "paused"
        assert after.budgets.wall_seconds_used == 901
        assert runtime.state.multi_project_unverified is True


def test_stop_requested_over_limit_goal_cannot_be_resumed_by_external_run_turn():
    with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
        session = _session(paths, "20261107-budget-stopped")
        runtime = _over_limit_goal(session)
        runtime.state.stop_requested = True
        runtime.state.status = "paused"
        runtime.state.pause_reason = "budget_exhausted"
        runtime.save()
        agent = _agent_for(paths, session)
        agent._run_parent_tool_loop = lambda **_kwargs: pytest.fail("stopped goal must not enter execution")

        result = agent.run_turn("Continue implementing this task", spawn_explore=False)

        assert result.finish_reason == "stopped"
        after = GoalRuntime(Path(session.session_dir)).load()
        assert after is not None
        assert after.stop_requested is True
        assert after.budgets.tool_turns_used == 9


def test_same_run_automatic_continuation_stops_at_independent_tool_bound(monkeypatch):
    monkeypatch.setenv("MY_AGENT_AUTO_CONTINUE", "1")
    monkeypatch.setenv("PARENT_EXECUTE_SEGMENT_MAX", "1")
    monkeypatch.setenv("PARENT_EXECUTE_TOTAL_MAX", "3")
    with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
        session = _session(paths, "20261107-budget-chain")
        runtime = _over_limit_goal(session)
        agent = _agent_for(paths, session)
        segments = []

        def exhausted_segment(**_kwargs):
            segments.append(len(segments) + 1)
            return ToolLoopSegmentResult(final_text="", tool_rounds=1, finish_reason="tool_calls", exceeded=True,
                                         had_progress=True)

        agent._run_parent_tool_loop = exhausted_segment
        result = agent.run_turn("Continue implementing this task", spawn_explore=False)

        assert len(segments) == 3  # bounded by this Run's total, not old Goal counters
        assert result.total_tool_rounds == 3
        assert result.finish_reason == "segment_cap_pause"
        assert "将自动继续下一段" not in result.assistant_text
        loaded = GoalRuntime(Path(session.session_dir)).load()
        assert loaded is not None
        assert loaded.budgets.tool_turns_used == 12
        assert loaded.multi_project_unverified is True
        assert runtime.state.status != "done"
