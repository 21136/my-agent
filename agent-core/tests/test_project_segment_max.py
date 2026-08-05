"""Phase 41 P2 — project segment max 15 (AGENT-HARNESS · IT-411)."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from agent import (
    _DEFAULT_EXECUTE_SEGMENT_MAX,
    _DEFAULT_PROJECT_EXECUTE_SEGMENT_MAX,
    parent_execute_segment_max,
)
from loader import format_turn_discipline_overlay
from session import create_new

from tests.isolation_helpers import make_temp_agent_paths


class ProjectSegmentMaxTests(unittest.TestCase):
    def setUp(self) -> None:
        self._prev = os.environ.pop("PARENT_EXECUTE_SEGMENT_MAX", None)

    def tearDown(self) -> None:
        if self._prev is None:
            os.environ.pop("PARENT_EXECUTE_SEGMENT_MAX", None)
        else:
            os.environ["PARENT_EXECUTE_SEGMENT_MAX"] = self._prev

    def test_project_defaults_to_15(self) -> None:
        self.assertEqual(
            parent_execute_segment_max(active_shell="project"),
            _DEFAULT_PROJECT_EXECUTE_SEGMENT_MAX,
        )
        self.assertEqual(_DEFAULT_PROJECT_EXECUTE_SEGMENT_MAX, 15)

    def test_non_project_defaults_to_50(self) -> None:
        for shell in ("grow", "daily", "govern", ""):
            self.assertEqual(
                parent_execute_segment_max(active_shell=shell),
                _DEFAULT_EXECUTE_SEGMENT_MAX,
                msg=shell,
            )

    def test_env_overrides_project(self) -> None:
        os.environ["PARENT_EXECUTE_SEGMENT_MAX"] = "7"
        self.assertEqual(parent_execute_segment_max(active_shell="project"), 7)
        self.assertEqual(parent_execute_segment_max(active_shell="grow"), 7)

    def test_overlay_shows_project_budget(self) -> None:
        paths = make_temp_agent_paths(self)
        session = create_new(paths, conversation_id="_it411_overlay")
        session.meta.phase = "S4"
        session.meta.turn_mode = "agent"
        session.meta.active_shell = "project"
        session.save()
        text = format_turn_discipline_overlay(session)
        assert text is not None
        self.assertIn("≤15", text)
        self.assertIn("项目模式", text)


if __name__ == "__main__":
    unittest.main()
