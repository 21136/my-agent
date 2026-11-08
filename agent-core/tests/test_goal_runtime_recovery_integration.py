from __future__ import annotations

import sys
from pathlib import Path

import pytest

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from agent import ToolLoopSegmentResult, TurnResult
from goal_runtime import GoalRuntime
from server import WsBridge, _build_repl
from session import create_new
from tests.isolation_helpers import temporary_agent_paths


def test_goal_extend_command_is_persisted_uses_callback_and_does_not_execute():
    with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
        session = create_new(paths, conversation_id="20261106-goalextend")
        session.meta.turn_mode = "agent"
        session.goal = "implementation task"
        session.save()
        runtime = GoalRuntime(Path(session.session_dir))
        state = runtime.ensure_goal(goal_text="Fix the original task", wall_seconds_limit=900, tool_turns_limit=20)
        state.status = "paused"
        state.pause_reason = "budget_exhausted"
        state.budgets.wall_seconds_used = 1041
        state.last_verification = None
        runtime.save()

        bridge = WsBridge(emit=lambda _event: None, paths=paths)
        repl = _build_repl(session, paths, bridge, work_record_controller_factory=lambda *_args: None)
        repl.agent._run_turn_impl = lambda *_args, **_kwargs: pytest.fail("extension command must not execute a Goal")
        persisted_callbacks = []

        result = repl.agent.run_turn(
            "/goal extend: 300",
            on_external_user_message_persisted=lambda saved: persisted_callbacks.append(saved.conversation_id),
        )

        loaded = GoalRuntime(Path(session.session_dir)).load()
        assert loaded is not None
        assert loaded.goal_text == "Fix the original task"
        assert loaded.budgets.wall_seconds_used == 1041
        assert loaded.budgets.wall_seconds_limit == 1200
        assert loaded.status == "paused"
        assert loaded.pause_reason == "budget_extended"
        assert persisted_callbacks == [session.conversation_id]
        assert result.finish_reason == "goal_budget_extended"
        assert result.total_tool_rounds == 0
        assert any(message.get("content") == "/goal extend: 300" for message in session.messages)


def test_goal_new_control_routes_task_body_directly_to_agent_loop():
    with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
        session = create_new(paths, conversation_id="20261106-goalnew")
        session.meta.turn_mode = "agent"
        session.goal = "implementation task"
        session.save()
        bridge = WsBridge(emit=lambda _event: None, paths=paths)
        repl = _build_repl(session, paths, bridge, work_record_controller_factory=lambda *_args: None)
        seen = {}

        def capture(task_body, **kwargs):
            seen["body"] = task_body
            seen["explicit_goal"] = repl.agent._explicit_new_goal_this_turn
            return TurnResult(assistant_text="captured", tool_rounds=0, finish_reason="test",
                              turn_intent="execute", total_tool_rounds=0)

        repl.agent._run_turn_impl = capture
        result = repl.agent.run_turn("/goal new: Repair the parser")

        assert result.finish_reason == "test"
        assert seen == {"body": "Repair the parser", "explicit_goal": "Repair the parser"}
        assert "/goal new:" not in seen["body"]


def test_goal_state_reopens_in_fresh_process_as_paused_before_any_continuation(tmp_path):
    import json
    import os
    import subprocess

    session_dir = tmp_path / "synthetic-session"
    runtime = GoalRuntime(session_dir)
    state = runtime.ensure_goal(goal_text="synthetic interrupted task")
    runtime.begin_run("agent")
    state.budgets.tool_turns_used = 3
    runtime.save()

    probe = r'''
import json, sys
from pathlib import Path
from goal_runtime import GoalRuntime
runtime = GoalRuntime(Path(sys.argv[1]))
state = runtime.load()
assert state is not None
print(json.dumps({"status": state.status, "pause_reason": state.pause_reason,
                  "goal_text": state.goal_text, "tool_turns_used": state.budgets.tool_turns_used,
                  "process_instance_id": state.process_instance_id}))
'''
    completed = subprocess.run(
        [sys.executable, "-c", probe, str(session_dir)],
        cwd=_AGENT_CORE,
        env={**os.environ, "PYTHONPATH": str(_AGENT_CORE), "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=20,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    recovered = json.loads(completed.stdout.strip().splitlines()[-1])
    assert recovered["status"] == "paused"
    assert recovered["pause_reason"] == "process_restart"
    assert recovered["goal_text"] == "synthetic interrupted task"
    assert recovered["tool_turns_used"] == 3
    assert recovered["process_instance_id"] is not None


def test_goal_state_hydrates_for_session_and_is_session_scoped():
    with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
        session = create_new(paths, conversation_id="20261106-goalevent")
        session.meta.turn_mode = "agent"
        session.save()
        runtime = GoalRuntime(Path(session.session_dir))
        state = runtime.ensure_goal(goal_text="hydrated goal", wall_seconds_limit=1200)
        state.status = "paused"
        state.pause_reason = "budget_exhausted"
        runtime.save()

        events = []
        bridge = WsBridge(emit=events.append, paths=paths)
        bridge._session_provider = lambda: session
        bridge.emit_session_state(session)

        goal_events = [event for event in events if event.get("type") == "goal.state"]
        assert len(goal_events) == 1
        assert goal_events[0]["session_id"] == session.conversation_id
        assert goal_events[0]["goal_id"] == state.goal_id
        assert goal_events[0]["budgets"]["wall_seconds_limit"] == 1200


def test_agent_confirm_wrapper_excludes_wait_and_resumes_after_exception():
    with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
        session = create_new(paths, conversation_id="20261106-goalconfirm")
        session.meta.turn_mode = "agent"
        bridge = WsBridge(emit=lambda _event: None, paths=paths)
        repl = _build_repl(session, paths, bridge, work_record_controller_factory=lambda *_args: None)
        now = [10.0]
        runtime = GoalRuntime(Path(session.session_dir), clock=lambda: now[0])
        runtime.ensure_goal(goal_text="timed confirm", wall_seconds_limit=1000)
        runtime.begin_run("agent")
        repl.agent._active_goal_runtime = runtime

        def waits_then_raises(*_args, **_kwargs):
            now[0] += 600
            raise RuntimeError("confirmation cancelled")

        repl.agent.executor.confirm_fn = waits_then_raises
        repl.agent._install_goal_confirmation_timer_wrapper()
        with pytest.raises(RuntimeError, match="cancelled"):
            repl.agent.executor.confirm_fn("preview", False)
        now[0] += 2
        runtime.record_segment_usage(tool_turns=1)
        assert runtime.state.budgets.wall_seconds_used == 2
        assert runtime._wall_clock_pause_depth == 0


def test_ordinary_new_turn_routes_through_run_turn_and_preserves_unverified_legacy_goal():
    with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
        session = create_new(paths, conversation_id="20261106-goalresume")
        session.meta.turn_mode = "agent"
        session.meta.harness = "desktop"
        session.next_work_intent = "execute"
        session.goal = "implementation task"
        session.save()
        runtime = GoalRuntime(Path(session.session_dir))
        state = runtime.ensure_goal(goal_text="original multi-project task", tool_turns_limit=1, wall_seconds_limit=10)
        state.status = "paused"
        state.pause_reason = "budget_exhausted"
        state.budgets.tool_turns_used = 17
        state.budgets.wall_seconds_used = 1600
        state.multi_project_unverified = True
        state.multi_project_ids = ["alpha", "beta"]
        state.last_verification = None
        runtime.save()

        bridge = WsBridge(emit=lambda _event: None, paths=paths)
        repl = _build_repl(session, paths, bridge, work_record_controller_factory=lambda *_args: None)
        calls = []

        def one_real_agent_loop(**kwargs):
            calls.append(kwargs["max_rounds"])
            return ToolLoopSegmentResult(
                final_text="Not complete; per-project acceptance is still pending.", tool_rounds=1, finish_reason="stop",
            )

        repl.agent._run_parent_tool_loop = one_real_agent_loop
        result = repl.agent.run_turn("Continue implementing this task", spawn_explore=False)

        assert calls, "ordinary run_turn routing must enter the Agent tool loop"
        assert len(calls) == 1 and calls[0] > 1, "cumulative usage must not clamp the new human turn"
        assert result.assistant_text == "Not complete; per-project acceptance is still pending."
        loaded = GoalRuntime(Path(session.session_dir)).load()
        assert loaded is not None
        assert loaded.budgets.tool_turns_used == 18
        assert loaded.budgets.wall_seconds_used >= 1600
        assert loaded.multi_project_unverified is True
        assert loaded.multi_project_ids == ["alpha", "beta"]
        assert loaded.last_verification is None
        assert loaded.status != "done"



@pytest.mark.parametrize("pause_reason", ["repair_exhausted", "authorization_required", "unknown_pause"])
def test_ordinary_run_turn_does_not_clear_nonrecoverable_pause(pause_reason):
    with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
        session = create_new(paths, conversation_id=f"20261106-guard-{pause_reason}")
        session.meta.turn_mode = "agent"
        session.next_work_intent = "execute"
        session.goal = "protected task"
        session.save()
        runtime = GoalRuntime(Path(session.session_dir))
        state = runtime.ensure_goal(goal_text="protected task")
        state.status = "paused"
        state.pause_reason = pause_reason
        runtime.save()

        repl = _build_repl(session, paths, WsBridge(emit=lambda _event: None, paths=paths),
                           work_record_controller_factory=lambda *_args: None)
        repl.agent._run_parent_tool_loop = lambda **_kwargs: pytest.fail("protected pause must not enter execution")
        result = repl.agent.run_turn("Continue implementing this task", spawn_explore=False)

        assert result.finish_reason == "goal_safety_paused"
        loaded = GoalRuntime(Path(session.session_dir)).load()
        assert loaded is not None
        assert loaded.status == "paused"
        assert loaded.pause_reason == pause_reason


def test_auto_budget_pause_does_not_promise_or_run_ci_repair_continuation(monkeypatch):
    from agent import ToolLoopSegmentResult
    from goal_runtime import GoalRuntime

    monkeypatch.setenv("MY_AGENT_AUTO_CONTINUE", "1")
    monkeypatch.setenv("PARENT_EXECUTE_SEGMENT_MAX", "1")
    monkeypatch.setenv("PARENT_EXECUTE_TOTAL_MAX", "3")
    original_begin_run = GoalRuntime.begin_run

    def begin_with_one_auto_tool_round(self, *args, **kwargs):
        kwargs["auto_continue_tool_turns_limit"] = 1
        return original_begin_run(self, *args, **kwargs)

    monkeypatch.setattr(GoalRuntime, "begin_run", begin_with_one_auto_tool_round)
    with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
        session = create_new(paths, conversation_id="20261107-auto-budget-pause")
        session.meta.turn_mode = "agent"
        session.meta.harness = "desktop"
        session.meta.active_shell = "daily"
        session.next_work_intent = "execute"
        session.goal = "continue task"
        session._ci_repair_state = {"status": "repair_allowed"}
        session._ci_repair_auto_turns = 0
        session.save()
        bridge = WsBridge(emit=lambda _event: None, paths=paths)
        repl = _build_repl(session, paths, bridge, work_record_controller_factory=lambda *_args: None)
        calls = []

        def one_exhausted_segment(**_kwargs):
            calls.append("segment")
            return ToolLoopSegmentResult(
                final_text="", tool_rounds=1, finish_reason="tool_calls", exceeded=True, had_progress=True,
            )

        repl.agent._run_parent_tool_loop = one_exhausted_segment
        result = repl.agent.run_turn("Continue task", spawn_explore=False)

        assert calls == ["segment"]
        assert result.finish_reason == "segment_cap_pause"
        assert "请发「继续」" in result.assistant_text
        assert "将自动继续下一段" not in result.assistant_text
        assert session._ci_repair_auto_turns == 0

def test_session_status_refresh_does_not_reclassify_current_process_active_goal_as_restart():
    with temporary_agent_paths(copy_tool_dirs=("common/write_text",)) as paths:
        session = create_new(paths, conversation_id="20261106-goal-active-refresh")
        session.meta.turn_mode = "agent"
        session.save()
        runtime = GoalRuntime(Path(session.session_dir))
        state = runtime.ensure_goal(goal_text="ongoing goal")
        runtime.begin_run("agent")
        runtime.finish_run(ok=True, finish_reason="user_turn_finished")
        assert state.status == "active"

        events = []
        bridge = WsBridge(emit=events.append, paths=paths)
        bridge._session_provider = lambda: session
        bridge.emit_session_state(session)

        goal_event = next(event for event in events if event.get("type") == "goal.state")
        assert goal_event["status"] == "active"
        assert goal_event["pause_reason"] is None
        assert "process_instance_id" not in goal_event
