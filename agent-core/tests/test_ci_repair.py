from __future__ import annotations

import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from ci_repair import CIRepairState, CIRepairStatus, decide_repair, load_ci_repair, save_ci_repair
from tests.isolation_helpers import temporary_agent_paths


class CIRepairTests(unittest.TestCase):
    def make_state(self) -> CIRepairState:
        return CIRepairState(
            repair_id="ci-1",
            repository="21136/my-agent",
            pr_number=42,
            head_sha="a" * 40,
            attempt=0,
            max_attempts=2,
            category="",
            status=CIRepairStatus.CREATED,
            log_digest="",
        )

    def test_retryable_test_failure_allows_repair(self) -> None:
        next_state = decide_repair(self.make_state(), "pytest failed: AssertionError")
        self.assertEqual(next_state.status, CIRepairStatus.REPAIR_ALLOWED)
        self.assertEqual(next_state.attempt, 1)
        self.assertEqual(len(next_state.log_digest), 64)

    def test_auth_failure_blocks_immediately(self) -> None:
        next_state = decide_repair(self.make_state(), "HTTP 403 permission denied token")
        self.assertEqual(next_state.status, CIRepairStatus.BLOCKED)
        self.assertEqual(next_state.category, "auth")

    def test_budget_exhaustion_blocks(self) -> None:
        state = self.make_state()
        state = decide_repair(state, "pytest failed")
        state = decide_repair(state, "pytest failed")
        state = decide_repair(state, "pytest failed")
        self.assertEqual(state.status, CIRepairStatus.BLOCKED)
        self.assertEqual(state.attempt, 3)

    def test_repair_allowed_can_be_completed_only_after_tests_pass(self) -> None:
        state = decide_repair(self.make_state(), "pytest failed")
        completed = state.complete_after_verification(tests_ok=True)
        self.assertEqual(completed.status, CIRepairStatus.READY_TO_DELIVER)
        with self.assertRaises(Exception):
            state.complete_after_verification(tests_ok=False)

    def test_state_persists_under_agent_data(self) -> None:
        with temporary_agent_paths() as paths:
            state = self.make_state()
            save_ci_repair(paths, state)
            loaded = load_ci_repair(paths, "ci-1")
        self.assertEqual(loaded, state)


if __name__ == "__main__":
    unittest.main()
