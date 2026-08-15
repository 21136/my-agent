"""IT-5818: persistent release acceptance is revision-bound."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from project_release import load_release_acceptance, save_release_acceptance


class ProjectReleaseTests(unittest.TestCase):
    def test_it5818_project_api_exposes_persistent_acceptance_route(self) -> None:
        api = (_AGENT_CORE / "project_api.py").read_text(encoding="utf-8")
        self.assertIn('msg_type == "project.release.accept"', api)
        self.assertIn("save_release_acceptance", api)
        self.assertIn('"release_acceptance"', api)

    def test_it5818_acceptance_roundtrips_from_project_disk(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "demo"
            record = save_release_acceptance(
                root,
                "demo",
                release_revision="r3",
                checklist={"tasks_clear": True, "evidence_fresh": True, "human_acceptance": True},
                accepted_at="2026-08-14T08:00:00Z",
            )
            self.assertTrue(record["accepted"])
            self.assertEqual(
                load_release_acceptance(root, "demo", release_revision="r3"),
                record,
            )
            raw = json.loads((root / ".plan-agent" / "release_acceptance.json").read_text(encoding="utf-8"))
            self.assertEqual(raw["schema_version"], "0.1")

    def test_it5818_release_revision_change_invalidates_acceptance(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "demo"
            save_release_acceptance(root, "demo", release_revision="r3", checklist={"human_acceptance": True})
            result = load_release_acceptance(root, "demo", release_revision="r4")
            self.assertFalse(result["accepted"])
            self.assertIsNone(result["accepted_at"])
            self.assertEqual(result["release_revision"], "r4")


if __name__ == "__main__":
    unittest.main()
