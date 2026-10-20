"""Display-only project identity must not replace logical IDs or external bindings."""
from __future__ import annotations
import sys
import tempfile
import unittest
from pathlib import Path

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))
from external_project import apply_external_project, scan_external_project, validate_external_binding
from project_api import project_state_payload
from project_mode import create_project
from session import create_new
from tests.isolation_helpers import temporary_agent_paths

class ProjectDisplayIdentityTests(unittest.TestCase):
    def test_internal_project_exposes_display_directory_without_changing_logical_identity(self):
        with temporary_agent_paths() as paths:
            root = create_project(paths, "music-dreamer")
            session = create_new(paths, conversation_id="_display_normal_")
            session.meta.project_id = "music-dreamer"
            session.meta.project_root = "workspace/music-dreamer"
            payload = project_state_payload(session, paths)
            self.assertEqual(payload.get("project_display_name"), "music-dreamer")
            self.assertEqual(payload.get("project_directory"), str(root))
            self.assertEqual(payload["project_id"], "music-dreamer")
            self.assertEqual(payload["project_root"], "workspace/music-dreamer")

    def test_imported_project_exposes_real_folder_name_and_keeps_generated_id_and_binding(self):
        with temporary_agent_paths() as paths, tempfile.TemporaryDirectory(prefix="dock-external-") as temp:
            external = Path(temp) / "毕业设计"
            external.mkdir()
            (external / "README.md").write_text("# Original source\n", encoding="utf-8")
            binding = apply_external_project(paths, scan_external_project(paths, str(external), project_id="imported-b1f2b097"))
            session = create_new(paths, conversation_id="_display_external_")
            session.meta.project_id = "imported-b1f2b097"
            session.meta.project_root = "workspace/imported-b1f2b097"
            payload = project_state_payload(session, paths)
            self.assertEqual(payload.get("project_display_name"), "毕业设计")
            self.assertEqual(payload.get("project_directory"), str(external.resolve()))
            self.assertEqual(payload["project_id"], "imported-b1f2b097")
            self.assertEqual(payload["project_root"], "workspace/imported-b1f2b097")
            self.assertEqual(session.meta.project_id, "imported-b1f2b097")
            self.assertEqual(validate_external_binding(paths, "imported-b1f2b097"), binding)

    def test_free_chat_has_no_project_display_metadata(self):
        with temporary_agent_paths() as paths:
            session = create_new(paths, conversation_id="_display_free_")
            payload = project_state_payload(session, paths)
            self.assertIn("project_display_name", payload)
            self.assertIn("project_directory", payload)
            self.assertIsNone(payload["project_display_name"])
            self.assertIsNone(payload["project_directory"])

if __name__ == "__main__":
    unittest.main()
