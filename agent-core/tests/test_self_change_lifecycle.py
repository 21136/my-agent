from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from loader import format_session_overlay
from tests.isolation_helpers import temporary_agent_paths
from session import create_new
from tools.executor import ToolExecutor


class SelfChangeLifecycleTests(unittest.TestCase):
    def test_session_overlay_exposes_active_self_change_transaction(self) -> None:
        with temporary_agent_paths() as paths:
            session = create_new(paths, conversation_id="self-change-overlay")
            session._self_change_transaction_id = "tx-overlay"
            overlay = format_session_overlay(session)

        self.assertIn("self_change_transaction: tx-overlay", overlay)

    def test_executor_records_self_change_tool_state(self) -> None:
        events: list[tuple[str, dict]] = []
        fake = types.SimpleNamespace(
            session=types.SimpleNamespace(self_change_transaction_id=None),
            _emit_event=lambda name, payload: events.append((name, payload)),
        )
        result = types.SimpleNamespace(
            ok=True,
            data={"transaction_id": "tx-123", "state": "snapshotted"},
        )

        ToolExecutor._maybe_record_self_change_result(
            fake,
            "run_evolved",
            {"tool_name": "self_change", "arguments": {"action": "begin"}},
            result,
        )

        self.assertEqual(fake.session.self_change_transaction_id, "tx-123")
        self.assertEqual(events[0][0], "self_change.state")
        self.assertEqual(events[0][1]["state"], "snapshotted")


if __name__ == "__main__":
    unittest.main()
