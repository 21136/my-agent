"""Goal Runtime P0 first-knife contract tests."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

import goal_runtime as goal_runtime_module
from execution_lifecycle import ExecutionLifecycle
from goal_runtime import (
    GoalRuntime,
    explicit_new_goal_from_request,
    explicit_wall_budget_extension,
    resolve_turn_goal_text,
    resolve_goal_action_intent,
    evaluate_acceptance_exit,
    goal_runtime_for_session,
    normalize_project_acceptance_result,
)


class _Meta:
    def __init__(self, turn_mode: str = "agent", project_id: str = "", active_shell: str = "") -> None:
        self.turn_mode = turn_mode
        self.project_id = project_id
        self.active_shell = active_shell


class _Session:
    def __init__(self, session_dir: Path, *, turn_mode: str = "agent") -> None:
        self.session_dir = session_dir
        self.meta = _Meta(turn_mode=turn_mode)
        self.goal = "demo goal"
        self.paths = None


class GoalRuntimeTests(unittest.TestCase):
    def test_cumulative_budget_not_reset_across_segments(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = GoalRuntime(
                Path(tmp),
                lifecycle=ExecutionLifecycle(run_id_factory=iter(["r1"]).__next__),
            )
            runtime.ensure_goal(goal_text="long task", tool_turns_limit=10)
            runtime.begin_run("agent")

            runtime.record_segment_usage(tool_turns=3, wall_seconds=1.5)
            runtime.record_segment_usage(tool_turns=4, wall_seconds=2.0)

            state = runtime.state
            assert state is not None
            self.assertEqual(state.budgets.tool_turns_used, 7)
            self.assertEqual(state.budgets.tool_budget_remaining(), 3)
            self.assertAlmostEqual(state.budgets.wall_seconds_used, 3.5)
            # Persist + reload must keep cumulative counters.
            reloaded = GoalRuntime(Path(tmp))
            loaded = reloaded.load()
            assert loaded is not None
            self.assertEqual(loaded.budgets.tool_turns_used, 7)
            self.assertEqual(loaded.budgets.tool_turns_limit, 10)

    def test_confirmation_wait_is_excluded_from_wall_budget(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            now = [100.0]
            runtime = GoalRuntime(Path(tmp), clock=lambda: now[0])
            runtime.ensure_goal(goal_text="wait safely", wall_seconds_limit=10)
            runtime.begin_run("agent")
            now[0] += 3
            runtime.pause_wall_clock()
            now[0] += 500
            runtime.resume_wall_clock()
            now[0] += 2
            runtime.record_segment_usage(tool_turns=1)
            self.assertAlmostEqual(runtime.state.budgets.wall_seconds_used, 5)

    def test_new_goal_archives_exhausted_goal_without_reusing_its_budget(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = GoalRuntime(Path(tmp), clock=lambda: 100.0)
            old = runtime.ensure_goal(goal_text="old task", tool_turns_limit=2)
            runtime.begin_run("agent")
            runtime.record_segment_usage(tool_turns=2, wall_seconds=4)
            new = runtime.ensure_goal(goal_text="explicitly different task", tool_turns_limit=5)
            self.assertNotEqual(new.goal_id, old.goal_id)
            self.assertEqual(new.budgets.tool_turns_used, 0)
            history = json.loads((Path(tmp) / "goal-history.json").read_text(encoding="utf-8"))
            self.assertEqual(history[0]["goal_id"], old.goal_id)
            self.assertEqual(history[0]["budgets"]["tool_turns_used"], 2)
            self.assertIsNone(history[0]["pause_reason"])
    def test_existing_goal_survives_stale_session_goal_on_later_turn(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = GoalRuntime(Path(tmp))
            state = runtime.ensure_goal(goal_text="Repair the parser")
            self.assertEqual(resolve_turn_goal_text(state, "动手任务"), "Repair the parser")
            self.assertEqual(resolve_turn_goal_text(state, "动手任务", "New independent request"),
                             "New independent request")
            self.assertEqual(resolve_turn_goal_text(None, ""), "动手任务")

    def test_new_goal_protocol_is_unambiguous(self) -> None:
        self.assertEqual(explicit_new_goal_from_request("/goal new: Fix issue B"), "Fix issue B")
        self.assertEqual(resolve_goal_action_intent("qa", "Fix issue B"), "execute")
        self.assertEqual(resolve_goal_action_intent("qa", None), "qa")
        self.assertEqual(explicit_wall_budget_extension("/goal extend: 300"), 300)
        for request in ("修好这个问题", "继续修好这个问题", "/goal new:", "new goal: task"):
            with self.subTest(request=request):
                self.assertIsNone(explicit_new_goal_from_request(request))

    def test_reload_reconciles_legacy_over_limit_state_for_explicit_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = GoalRuntime(Path(tmp))
            state = runtime.ensure_goal(goal_text="legacy task", wall_seconds_limit=900, tool_turns_limit=20)
            runtime.begin_run("agent")
            state.budgets.wall_seconds_used = 1041
            state.status = "paused"
            state.pause_reason = "budget_exhausted"
            runtime.save()
            reloaded = GoalRuntime(Path(tmp))
            loaded = reloaded.load()
            assert loaded is not None
            self.assertEqual(loaded.pause_reason, "budget_exhausted")
            self.assertFalse(reloaded.should_auto_continue())
            reloaded.extend_wall_budget(300)
            self.assertEqual(loaded.budgets.wall_seconds_used, 1041)
            self.assertEqual(loaded.budgets.wall_seconds_limit, 1200)
            self.assertEqual(loaded.pause_reason, "budget_extended")

    def test_corrupt_goal_history_refuses_replacement_without_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = GoalRuntime(Path(tmp))
            old = runtime.ensure_goal(goal_text="preserve me")
            history_path = Path(tmp) / "goal-history.json"
            history_path.write_text("{broken", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "unreadable"):
                runtime.ensure_goal(goal_text="new task")
            self.assertEqual(history_path.read_text(encoding="utf-8"), "{broken")
            self.assertEqual(runtime.path.read_text(encoding="utf-8"), json.dumps(old.to_dict(), ensure_ascii=False, indent=2) + "\n")
    def test_explicit_wall_extension_preserves_usage_and_does_not_resume(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = GoalRuntime(Path(tmp))
            state = runtime.ensure_goal(goal_text="old task", wall_seconds_limit=900, tool_turns_limit=20)
            runtime.begin_run("agent")
            runtime.record_segment_usage(tool_turns=3, wall_seconds=1041)
            # Legacy manually paused budget record; extension remains an explicit,
            # non-executing recovery action for older sessions.
            state.status = "paused"
            state.pause_reason = "budget_exhausted"
            state.last_verification = None
            runtime.save()
            self.assertEqual(state.pause_reason, "budget_exhausted")
            runtime.extend_wall_budget(300)
            self.assertEqual(state.budgets.wall_seconds_used, 1041)
            self.assertEqual(state.budgets.wall_seconds_limit, 1200)
            self.assertEqual(state.budgets.tool_turns_used, 3)
            self.assertEqual(state.pause_reason, "budget_extended")
            self.assertEqual(state.status, "paused")
            self.assertFalse(runtime.should_auto_continue())
            self.assertEqual(state.last_verification, None)

    def test_wall_extension_is_bounded_and_rejects_other_exhausted_budget(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = GoalRuntime(Path(tmp))
            state = runtime.ensure_goal(goal_text="limits", wall_seconds_limit=900, tool_turns_limit=1)
            runtime.begin_run("agent")
            runtime.record_segment_usage(tool_turns=1, wall_seconds=901)
            state.status = "paused"
            state.pause_reason = "budget_exhausted"
            with self.assertRaisesRegex(RuntimeError, "tool-turn"):
                runtime.extend_wall_budget(300)
            self.assertEqual(state.budgets.wall_seconds_limit, 900)
            runtime2 = GoalRuntime(Path(tmp) / "second")
            state2 = runtime2.ensure_goal(goal_text="cap", wall_seconds_limit=3300, tool_turns_limit=20)
            runtime2.begin_run("agent")
            runtime2.record_segment_usage(tool_turns=1, wall_seconds=3301)
            state2.status = "paused"
            state2.pause_reason = "budget_exhausted"
            with self.assertRaisesRegex(ValueError, "cannot exceed"):
                runtime2.extend_wall_budget(900)
            self.assertEqual(state2.budgets.wall_seconds_limit, 3300)
            self.assertIsNone(explicit_wall_budget_extension("/goal extend: 901"))
            self.assertIsNone(explicit_wall_budget_extension("/goal extend: 300 seconds"))
            self.assertEqual(explicit_wall_budget_extension("/goal extend: 300"), 300)

    def test_corrupt_goal_history_refuses_replacement_without_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = GoalRuntime(Path(tmp))
            current = runtime.ensure_goal(goal_text="preserve me")
            history = Path(tmp) / "goal-history.json"
            history.write_text("{broken", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "unreadable"):
                runtime.ensure_goal(goal_text="new explicit goal")
            self.assertEqual(history.read_text(encoding="utf-8"), "{broken")
            payload = json.loads(runtime.path.read_text(encoding="utf-8"))
            self.assertEqual(payload["goal_id"], current.goal_id)
            history.write_text('["malformed row"]', encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "invalid shape"):
                runtime.ensure_goal(goal_text="another new goal")
            self.assertEqual(history.read_text(encoding="utf-8"), '["malformed row"]')
            self.assertEqual(payload["goal_text"], "preserve me")

    def test_stop_does_not_auto_continue(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = GoalRuntime(
                Path(tmp),
                lifecycle=ExecutionLifecycle(run_id_factory=iter(["r-stop"]).__next__),
            )
            runtime.ensure_goal(goal_text="stop me", tool_turns_limit=20)
            runtime.begin_run("agent")
            self.assertTrue(runtime.should_auto_continue())

            runtime.request_stop("user")
            self.assertFalse(runtime.should_auto_continue())
            self.assertEqual(runtime.state.status, "stopped")
            self.assertTrue(runtime.state.stop_requested)

            # Reload after "restart" — still must not auto-continue.
            again = GoalRuntime(Path(tmp))
            loaded = again.load()
            assert loaded is not None
            self.assertTrue(loaded.stop_requested)
            self.assertEqual(loaded.status, "stopped")
            self.assertFalse(again.should_auto_continue())
            with self.assertRaises(RuntimeError):
                again.begin_run("agent")

    def test_acceptance_failure_ignores_historical_usage_but_keeps_repair_exhaustion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = GoalRuntime(Path(tmp))
            state = runtime.ensure_goal(
                goal_text="acceptance failure after historical overage",
                tool_turns_limit=2, wall_seconds_limit=5, repair_limit=3,
            )
            state.budgets.tool_turns_used = 20
            state.budgets.wall_seconds_used = 900
            runtime.begin_run("agent")
            runtime.apply_acceptance(command="check", exit_code=1)
            self.assertEqual(state.budgets.repair_count, 1)
            self.assertEqual(state.status, "active")
            self.assertIsNone(state.pause_reason)

            # Repair exhaustion remains the higher-priority safety pause.
            state.budgets.repair_count = state.budgets.repair_limit - 1
            runtime.apply_acceptance(command="check", exit_code=1)
            self.assertEqual(state.budgets.repair_count, state.budgets.repair_limit)
            self.assertEqual(state.status, "paused")
            self.assertEqual(state.pause_reason, "repair_exhausted")

    def test_acceptance_trusts_exit_code_only(self) -> None:
        verdict = evaluate_acceptance_exit(
            exit_code=0,
            expected_exit_code=0,
            command="python accept.py",
            stdout="ALL GOOD narrative that an LLM might trust",
            stderr="",
        )
        self.assertTrue(verdict["passed"])

        fail = evaluate_acceptance_exit(
            exit_code=2,
            expected_exit_code=0,
            command="python accept.py",
            stdout="looks fine to a model",
            stderr="",
        )
        self.assertFalse(fail["passed"])
        self.assertEqual(fail["exit_code"], 2)

        # Narrative-only project result without exit_code cannot pass.
        normalized = normalize_project_acceptance_result(
            {
                "ok": True,
                "passed": True,  # poisoned field — must be ignored without exit_code
                "command": "echo hi",
                "expected_exit_code": 0,
                "stdout": "success",
            }
        )
        self.assertFalse(normalized["passed"])
        self.assertEqual(normalized["exit_code"], -1)

        with tempfile.TemporaryDirectory() as tmp:
            runtime = GoalRuntime(
                Path(tmp),
                lifecycle=ExecutionLifecycle(
                    run_id_factory=iter(["r-a1", "r-a2"]).__next__
                ),
            )
            runtime.ensure_goal(
                goal_text="accept",
                acceptance_command="python accept.py",
                expected_exit_code=0,
                tool_turns_limit=10,
                repair_limit=3,
            )
            runtime.begin_run("agent")
            runtime.apply_acceptance(
                command="python accept.py",
                exit_code=1,
                stdout="failed tests",
            )
            self.assertEqual(runtime.state.status, "active")
            self.assertIn("验收失败", runtime.state.next_step)
            self.assertEqual(runtime.state.budgets.repair_count, 1)
            self.assertFalse(runtime.state.last_verification.passed)

            # Fresh run for success path after failure closed prior lifecycle.
            runtime.begin_run("agent")
            runtime.apply_acceptance(
                command="python accept.py",
                exit_code=0,
                stdout="ok",
            )
            self.assertEqual(runtime.state.status, "done")
            self.assertTrue(runtime.state.last_verification.passed)

    def test_budget_exhausted_pauses(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = GoalRuntime(
                Path(tmp),
                lifecycle=ExecutionLifecycle(run_id_factory=iter(["r-b"]).__next__),
            )
            runtime.ensure_goal(goal_text="budget", tool_turns_limit=5)
            runtime.begin_run("agent")
            runtime.record_segment_usage(tool_turns=5, wall_seconds=1.0)
            self.assertEqual(runtime.state.status, "active")
            self.assertIsNone(runtime.state.pause_reason)
            self.assertTrue(runtime.should_auto_continue())

    def test_cumulative_usage_is_telemetry_not_a_manual_run_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = GoalRuntime(Path(tmp), clock=lambda: 100.0)
            state = runtime.ensure_goal(goal_text="long task", tool_turns_limit=2, wall_seconds_limit=10)
            runtime.begin_run("agent", auto_continue_tool_turns_limit=20, auto_continue_wall_seconds_limit=2000)
            runtime.record_segment_usage(tool_turns=8, wall_seconds=901)
            self.assertEqual(state.budgets.tool_turns_used, 8)
            self.assertEqual(state.budgets.wall_seconds_used, 901)
            self.assertEqual(state.status, "active")
            self.assertTrue(runtime.should_auto_continue())

            # A later human-initiated Run remains admissible despite legacy caps.
            runtime.finish_run(ok=False, finish_reason="user_turn_finished")
            _, snapshot = runtime.begin_run("agent", auto_continue_tool_turns_limit=20, auto_continue_wall_seconds_limit=2000)
            self.assertTrue(snapshot.run_id)
            self.assertEqual(state.budgets.tool_turns_used, 8)
            self.assertEqual(state.budgets.wall_seconds_used, 901)

    def test_auto_continuation_uses_per_run_budget_not_goal_cumulative_totals(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            now = [100.0]
            runtime = GoalRuntime(Path(tmp), clock=lambda: now[0])
            state = runtime.ensure_goal(goal_text="long task", tool_turns_limit=1, wall_seconds_limit=5)
            runtime.begin_run("agent", auto_continue_tool_turns_limit=3, auto_continue_wall_seconds_limit=2000)
            runtime.record_segment_usage(tool_turns=1, wall_seconds=1000)
            self.assertEqual(state.status, "active")
            self.assertTrue(runtime.should_auto_continue())
            runtime.record_segment_usage(tool_turns=2, wall_seconds=0)
            self.assertFalse(runtime.should_auto_continue())
            self.assertEqual(state.status, "active")

            # The same exceeded cumulative counters do not block the next explicit Run.
            runtime.finish_run(ok=False, finish_reason="user_turn_finished")
            runtime.begin_run("agent", auto_continue_tool_turns_limit=3, auto_continue_wall_seconds_limit=2000)
            self.assertTrue(runtime.should_auto_continue())
            self.assertEqual(state.budgets.tool_turns_used, 3)
            self.assertEqual(state.budgets.wall_seconds_used, 1000)

    def test_auto_continuation_time_boundary_never_interrupts_first_manual_segment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            now = [100.0]
            runtime = GoalRuntime(Path(tmp), clock=lambda: now[0])
            state = runtime.ensure_goal(goal_text="active long segment")
            runtime.begin_run("agent", auto_continue_tool_turns_limit=10, auto_continue_wall_seconds_limit=5)
            now[0] += 30
            runtime.record_segment_usage(tool_turns=1, wall_seconds=30)
            # Segment work is recorded but never forcibly stopped by the auto-chain timer.
            self.assertEqual(state.status, "active")
            self.assertFalse(runtime.should_auto_continue())
            runtime.finish_run(ok=False, finish_reason="user_turn_finished")
            runtime.begin_run("agent", auto_continue_tool_turns_limit=10, auto_continue_wall_seconds_limit=5)
            self.assertTrue(runtime.should_auto_continue())

    def test_load_keeps_restart_and_terminal_reasons_ahead_of_historical_caps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = GoalRuntime(Path(tmp))
            state = runtime.ensure_goal(goal_text="active with old counters")
            runtime.begin_run("agent")
            state.budgets.tool_turns_used = state.budgets.tool_turns_limit + 5
            state.budgets.wall_seconds_used = state.budgets.wall_seconds_limit + 5
            runtime.save()

            with patch.object(goal_runtime_module, "_PROCESS_INSTANCE_ID", "simulated-next-process"):
                reloaded = GoalRuntime(Path(tmp))
                active = reloaded.load()
            assert active is not None
            self.assertEqual(active.status, "paused")
            self.assertEqual(active.pause_reason, "process_restart")
            reloaded.begin_run("agent")
            self.assertEqual(active.pause_reason, None)
            self.assertTrue(reloaded.should_auto_continue())

            active.status = "done"
            active.pause_reason = None
            reloaded.save()
            terminal = GoalRuntime(Path(tmp)).load()
            assert terminal is not None
            self.assertEqual(terminal.status, "done")
            self.assertIsNone(terminal.pause_reason)

    def test_load_does_not_downgrade_repair_pause_when_legacy_counters_exceeded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = GoalRuntime(Path(tmp))
            state = runtime.ensure_goal(goal_text="repair protected")
            state.status = "paused"
            state.pause_reason = "repair_exhausted"
            state.budgets.tool_turns_used = state.budgets.tool_turns_limit + 1
            state.budgets.wall_seconds_used = state.budgets.wall_seconds_limit + 1
            runtime.save()

            loaded_runtime = GoalRuntime(Path(tmp))
            loaded = loaded_runtime.load()
            assert loaded is not None
            self.assertEqual(loaded.status, "paused")
            self.assertEqual(loaded.pause_reason, "repair_exhausted")
            with self.assertRaisesRegex(RuntimeError, "paused goal requires explicit recovery"):
                loaded_runtime.begin_run("agent")

    def test_repair_and_unknown_pauses_are_not_manual_budget_recovery_states(self) -> None:
        for reason in ("repair_exhausted", "authorization_required", "unknown_pause"):
            with self.subTest(reason=reason), tempfile.TemporaryDirectory() as tmp:
                runtime = GoalRuntime(Path(tmp))
                state = runtime.ensure_goal(goal_text="protected pause")
                state.status = "paused"
                state.pause_reason = reason
                runtime.save()
                with self.assertRaisesRegex(RuntimeError, "paused goal requires explicit recovery"):
                    runtime.begin_run("agent", auto_continue_tool_turns_limit=20, auto_continue_wall_seconds_limit=2000)
                self.assertEqual(state.status, "paused")
                self.assertEqual(state.pause_reason, reason)

    def test_manual_resume_keeps_multi_project_verification_gate_and_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = GoalRuntime(Path(tmp), clock=lambda: 100.0)
            state = runtime.ensure_goal(goal_text="batch", tool_turns_limit=1, wall_seconds_limit=10)
            runtime.begin_run("agent")
            runtime.mark_multi_project_unverified(["alpha", "beta"])
            runtime.record_segment_usage(tool_turns=5, wall_seconds=901)
            # Reproduce a persisted legacy record paused by the old cumulative guard.
            state.status = "paused"
            state.pause_reason = "budget_exhausted"
            state.next_step = "legacy cumulative cap"
            state.last_verification = None
            runtime.save()

            reloaded = GoalRuntime(Path(tmp))
            loaded = reloaded.load()
            assert loaded is not None
            self.assertEqual(loaded.pause_reason, "budget_exhausted")
            before = loaded.to_dict()
            reloaded.begin_run("agent")
            self.assertTrue(loaded.multi_project_unverified)
            self.assertEqual(loaded.multi_project_ids, ["alpha", "beta"])
            self.assertEqual(loaded.last_verification, None)
            self.assertEqual(loaded.budgets.tool_turns_used, before["budgets"]["tool_turns_used"])
            self.assertEqual(loaded.budgets.wall_seconds_used, before["budgets"]["wall_seconds_used"])

    def test_chat_only_mode_skips_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ask_session = _Session(Path(tmp), turn_mode="ask")
            self.assertIsNone(goal_runtime_for_session(ask_session))
            agent_session = _Session(Path(tmp), turn_mode="agent")
            bound = goal_runtime_for_session(agent_session)
            self.assertIsNotNone(bound)

    def test_goal_state_json_roundtrip_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = GoalRuntime(Path(tmp))
            runtime.ensure_goal(
                goal_text="schema",
                workspace_scope="workspace/demo",
                acceptance_command="python -m pytest",
                expected_exit_code=0,
                tool_turns_limit=12,
            )
            payload = json.loads(runtime.path.read_text(encoding="utf-8"))
            self.assertEqual(payload["schema_version"], 1)
            self.assertEqual(payload["goal_text"], "schema")
            self.assertEqual(payload["workspace_scope"], "workspace/demo")
            self.assertIn("budgets", payload)
            self.assertIn("tool_turns_used", payload["budgets"])
            self.assertEqual(payload["status"], "idle")


if __name__ == "__main__":
    unittest.main()
