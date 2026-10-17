from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from project_api import dispatch_project_message
from session import create_new
from tests.isolation_helpers import make_temp_agent_paths


class ExternalProjectApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.paths = make_temp_agent_paths(self, prefix="external-api-")
        self.external = Path(tempfile.mkdtemp(prefix="external-api-source-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.external, ignore_errors=True))
        (self.external / "README.md").write_text("# Existing\n", encoding="utf-8")
        (self.external / "app.py").write_text("print('partial')\n", encoding="utf-8")
        self.session = create_new(self.paths, conversation_id="external-api-session")

    def test_scan_is_read_only_and_apply_requires_confirmation(self) -> None:
        scan = dispatch_project_message(
            self.session,
            self.paths,
            {"type": "project.import.scan", "path": str(self.external), "project_id": "api-demo"},
        )
        self.assertEqual(scan["type"], "project.import.scan")
        self.assertEqual(scan["scan"]["project_id"], "api-demo")
        self.assertFalse((self.paths.workspace / "api-demo").exists())

        cancelled = dispatch_project_message(
            self.session,
            self.paths,
            {"type": "project.import.cancel", "project_id": "api-demo"},
        )
        self.assertEqual(cancelled["type"], "project.import.cancelled")
        self.assertFalse((self.paths.workspace / "api-demo").exists())

    def test_apply_binds_original_root_and_returns_state(self) -> None:
        result = dispatch_project_message(
            self.session,
            self.paths,
            {
                "type": "project.import.apply",
                "path": str(self.external),
                "project_id": "api-demo",
                "confirm": True,
            },
        )
        self.assertIn("_events", result)
        self.assertEqual(self.session.meta.project_id, "api-demo")
        self.assertEqual(self.session.meta.project_plan_status, "draft")
        self.assertEqual(self.session.next_work_intent, "execute")
        self.assertTrue((self.external / "TASKS.md").is_file())
        event_types = [event.get("type") for event in result["_events"]]
        self.assertIn("project.import.applied", event_types)
        self.assertIn("project.state", event_types)


if __name__ == "__main__":
    unittest.main()
