"""Project handoff guidance is present only on project-bound sessions."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from loader import format_capability_hints
from session import create_new
from tests.isolation_helpers import make_temp_agent_paths


class ProjectHandoffPromptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.paths = make_temp_agent_paths(self)

    def test_project_session_gets_on_demand_handoff_rules(self) -> None:
        session = create_new(self.paths)
        session.meta.project_id = "demo-project"

        hints = format_capability_hints(session)

        self.assertIn("用户明确要求接续", hints)
        self.assertIn("project_catalog", hints)
        self.assertIn("state=archived", hints)
        self.assertIn("当前活线不是旧线候选", hints)
        self.assertIn("歧义时先询问", hints)
        self.assertIn("须 confirm", hints)
        self.assertIn("复核当前项目状态", hints)

    def test_unbound_session_does_not_get_project_handoff_rules(self) -> None:
        session = create_new(self.paths)

        hints = format_capability_hints(session)

        self.assertNotIn("用户明确要求接续", hints)
        self.assertNotIn("歧义时先询问", hints)
        self.assertNotIn("state=archived", hints)


if __name__ == "__main__":
    unittest.main()
