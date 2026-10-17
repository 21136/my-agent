from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from agent import Agent
from loader import format_session_overlay
from session import create_new
from tests.isolation_helpers import temporary_agent_paths
from tools.executor import ToolExecutor, _ci_repair_blocks_delivery


class CIRepairLifecycleTests(unittest.TestCase):
    def test_overlay_exposes_ci_repair_state(self) -> None:
        with temporary_agent_paths() as paths:
            session = create_new(paths, conversation_id="ci-repair-overlay")
            session._ci_repair_state = {"repair_id": "ci-1", "status": "repair_allowed", "attempt": 1}
            overlay = format_session_overlay(session)
        self.assertIn("ci_repair: ci-1/repair_allowed/attempt-1", overlay)
        self.assertIn("ci_repair_policy: inspect logs, repair authorized code, test, and rerun; do not commit or push", overlay)


    def test_repair_allowed_blocks_commit_push_and_pr_create(self) -> None:
        session = types.SimpleNamespace(_ci_repair_state={"status": "repair_allowed"})
        self.assertTrue(_ci_repair_blocks_delivery(session, "git_commit", {}))
        self.assertTrue(_ci_repair_blocks_delivery(session, "git_push", {}))
        self.assertTrue(_ci_repair_blocks_delivery(session, "gh_pr", {"action": "create"}))
        self.assertFalse(_ci_repair_blocks_delivery(session, "gh_actions", {"action": "rerun"}))


    def test_repair_allowed_yields_one_bounded_continuation(self) -> None:
        fake = types.SimpleNamespace(
            session=types.SimpleNamespace(
                _ci_repair_state={"status": "repair_allowed"},
                _ci_repair_auto_turns=0,
            )
        )
        line = Agent._ci_repair_continuation_text(fake)
        self.assertIsNotNone(line)
        Agent._mark_ci_repair_continuation(fake)
        self.assertIsNone(Agent._ci_repair_continuation_text(fake))

    def test_executor_records_repair_decision_event(self) -> None:
        events: list[tuple[str, dict]] = []
        fake = types.SimpleNamespace(
            session=types.SimpleNamespace(_ci_repair_state=None),
            _emit_event=lambda name, payload: events.append((name, payload)),
        )
        result = types.SimpleNamespace(
            ok=True,
            data={
                "action": "repair_decision",
                "state": {
                    "repair_id": "ci-2",
                    "status": "repair_allowed",
                    "attempt": 1,
                    "category": "test",
                },
            },
        )
        ToolExecutor._maybe_record_ci_repair_result(
            fake,
            "run_evolved",
            {"tool_name": "gh_actions", "arguments": {"action": "repair_decision"}},
            result,
        )
        self.assertEqual(fake.session._ci_repair_state["repair_id"], "ci-2")
        self.assertEqual(events[0][0], "ci.repair.decision")
        self.assertEqual(events[0][1]["status"], "repair_allowed")


if __name__ == "__main__":
    unittest.main()
