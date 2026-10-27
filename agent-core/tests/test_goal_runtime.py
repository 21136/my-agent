"""Goal Runtime P0 first-knife contract tests."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from execution_lifecycle import ExecutionLifecycle
from goal_runtime import (
    GoalRuntime,
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
            self.assertEqual(runtime.state.status, "paused")
            self.assertEqual(runtime.state.pause_reason, "budget_exhausted")
            self.assertFalse(runtime.should_auto_continue())

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
