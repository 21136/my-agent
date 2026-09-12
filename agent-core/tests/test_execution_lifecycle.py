"""Execution lifecycle state machine contract tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from execution_lifecycle import ExecutionLifecycle


class ExecutionLifecycleTests(unittest.TestCase):
    def test_start_creates_running_snapshot_with_monotonic_sequence(self) -> None:
        lifecycle = ExecutionLifecycle(run_id_factory=iter(["run-1"]).__next__)

        snapshot = lifecycle.start_or_activate("ordinary")

        self.assertEqual(snapshot.run_id, "run-1")
        self.assertEqual(snapshot.mode, "ordinary")
        self.assertEqual(snapshot.state, "running")
        self.assertFalse(snapshot.cancel_requested)
        self.assertEqual(snapshot.sequence, 1)
        self.assertEqual(snapshot.to_event()["type"], "execution.state")

    def test_queued_run_activates_with_the_same_run_id(self) -> None:
        lifecycle = ExecutionLifecycle(run_id_factory=iter(["run-queued"]).__next__)

        queued = lifecycle.enqueue("runaway")
        running = lifecycle.start_or_activate("runaway")

        self.assertEqual(queued.state, "queued")
        self.assertEqual(running.state, "running")
        self.assertEqual(running.run_id, queued.run_id)
        self.assertGreater(running.sequence, queued.sequence)

    def test_stop_is_idempotent_and_records_reason(self) -> None:
        lifecycle = ExecutionLifecycle(run_id_factory=iter(["run-stop"]).__next__)
        lifecycle.start_or_activate("ordinary")

        first, first_changed = lifecycle.request_stop()
        second, second_changed = lifecycle.request_stop()

        self.assertTrue(first_changed)
        self.assertIsNotNone(first)
        self.assertEqual(first.state, "stopping")
        self.assertTrue(first.cancel_requested)
        self.assertEqual(first.cancel_reason, "user")
        self.assertFalse(second_changed)
        self.assertEqual(second, first)

    def test_finish_is_idempotent_and_carries_final_reason(self) -> None:
        lifecycle = ExecutionLifecycle(run_id_factory=iter(["run-finish"]).__next__)
        lifecycle.start_or_activate("ordinary")

        first, first_changed = lifecycle.finish("completed", ok=True)
        second, second_changed = lifecycle.finish("error", ok=False)

        self.assertTrue(first_changed)
        self.assertIsNotNone(first)
        self.assertEqual(first.state, "settled")
        self.assertEqual(first.finish_reason, "completed")
        self.assertTrue(first.ok)
        self.assertFalse(second_changed)
        self.assertEqual(second, first)

    def test_cancelled_queue_cannot_be_activated_and_can_be_finalized(self) -> None:
        lifecycle = ExecutionLifecycle(run_id_factory=iter(["run-queued-stop"]).__next__)
        lifecycle.enqueue("runaway")
        stopped, changed = lifecycle.request_stop("user")

        self.assertTrue(changed)
        self.assertEqual(stopped.state, "stopping")

        with self.assertRaises(RuntimeError):
            lifecycle.start_or_activate("runaway")

        final, finalized = lifecycle.finish("cancelled", ok=False)
        self.assertTrue(finalized)
        self.assertEqual(final.state, "settled")
        self.assertEqual(final.finish_reason, "cancelled")

    def test_old_run_cannot_finish_a_new_run(self) -> None:
        lifecycle = ExecutionLifecycle(
            run_id_factory=iter(["run-old", "run-new"]).__next__
        )
        old = lifecycle.start_or_activate("runaway")
        lifecycle.finish("completed", ok=True, run_id=old.run_id)
        new = lifecycle.enqueue("runaway")

        finished, changed = lifecycle.finish(
            "continuation_finished",
            ok=True,
            run_id=old.run_id,
        )

        self.assertFalse(changed)
        self.assertEqual(finished, new)


if __name__ == "__main__":
    unittest.main()
