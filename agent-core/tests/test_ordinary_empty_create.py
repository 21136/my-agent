"""Ordinary / light create must not leak plan chrome, T-001, or template docs."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from context_switch import create_project_with_session_isolation
from project_api import project_state_payload
from project_cli import parse_project_command, run_project_command
from project_mode import create_project, list_project_docs, project_dir
from session import create_new
from tests.isolation_helpers import temporary_agent_paths


class OrdinaryEmptyCreateTests(unittest.TestCase):
    def test_light_create_has_no_seeded_tasks(self) -> None:
        with temporary_agent_paths() as paths:
            root = create_project(paths, "lite-empty", project_template="light")
            self.assertFalse((root / "TASKS.md").is_file())
            self.assertFalse((root / "DESIGN.md").is_file())
            self.assertFalse((root / "SCOPE.md").is_file())

    def test_light_create_hides_placeholder_docs(self) -> None:
        with temporary_agent_paths() as paths:
            create_project(paths, "lite-docs", project_template="light")
            docs = list_project_docs(paths, "lite-docs")
            names = {item["name"] for item in docs}
            self.assertNotIn("PROJECT.md", names)
            self.assertNotIn("TASKS.md", names)
            self.assertNotIn("VERIFY.md", names)
            # User-written docs still show.
            (project_dir(paths, "lite-docs") / "NOTES.md").write_text("# notes\n", encoding="utf-8")
            names2 = {item["name"] for item in list_project_docs(paths, "lite-docs")}
            self.assertIn("NOTES.md", names2)


if __name__ == "__main__":
    unittest.main()
