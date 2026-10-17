from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from external_project import (
    ExternalProjectError,
    apply_external_project,
    detach_external_project,
    scan_external_project,
    validate_external_binding,
)
from project_mode import normalize_project_id
from tests.isolation_helpers import make_temp_agent_paths


class ExternalProjectAdoptionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.paths = make_temp_agent_paths(self, prefix="external-adopt-")
        self.external = Path(tempfile.mkdtemp(prefix="external-source-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.external, ignore_errors=True))
        (self.external / ".git").mkdir()
        (self.external / "README.md").write_text("# Existing project\n", encoding="utf-8")
        (self.external / "app.py").write_text("print('partial')\n", encoding="utf-8")
        (self.external / ".env").write_text("SECRET=must-not-be-read\n", encoding="utf-8")

    def test_scan_is_read_only_and_reports_existing_project_facts(self) -> None:
        scan = scan_external_project(self.paths, str(self.external), project_id="partial-app")
        self.assertEqual(scan.project_id, "partial-app")
        self.assertEqual(scan.external_root, str(self.external.resolve()))
        self.assertIn("README.md", scan.markers)
        self.assertIn(".git", scan.markers)
        self.assertTrue(scan.has_git)
        self.assertFalse(scan.has_tasks)
        self.assertNotIn("SECRET=must-not-be-read", repr(scan.to_dict()))
        self.assertFalse((self.paths.workspace / "partial-app").exists())

    def test_scan_rejects_protected_roots_and_missing_paths(self) -> None:
        for root in (self.paths.agent_root, self.paths.workspace, self.paths.data):
            with self.subTest(root=root), self.assertRaises(ExternalProjectError):
                scan_external_project(self.paths, str(root), project_id="bad-root")
        with self.assertRaises(ExternalProjectError):
            scan_external_project(self.paths, str(self.external / "missing"), project_id="missing")
        with self.assertRaises(ExternalProjectError):
            scan_external_project(self.paths, str(self.external / "app.py"), project_id="file")

    def test_apply_creates_logical_binding_and_detach_keeps_original_files(self) -> None:
        scan = scan_external_project(self.paths, str(self.external), project_id="partial-app")
        binding = apply_external_project(self.paths, scan)
        logical = self.paths.workspace / "partial-app"
        self.assertEqual(binding.project_id, "partial-app")
        self.assertTrue(logical.exists())
        self.assertEqual(logical.resolve(), self.external.resolve())
        self.assertEqual((logical / "app.py").read_text(encoding="utf-8"), "print('partial')\n")
        self.assertEqual(validate_external_binding(self.paths, "partial-app"), binding)

        (logical / "continued.py").write_text("print('continued')\n", encoding="utf-8")
        self.assertTrue((self.external / "continued.py").is_file())
        detach_external_project(self.paths, "partial-app")
        self.assertFalse(logical.exists())
        self.assertTrue((self.external / "app.py").is_file())
        self.assertTrue((self.external / "continued.py").is_file())
        self.assertIsNone(validate_external_binding(self.paths, "partial-app"))

    def test_apply_does_not_replace_an_existing_logical_project(self) -> None:
        project_id = normalize_project_id("partial-app")
        (self.paths.workspace / project_id).mkdir(parents=True)
        scan = scan_external_project(self.paths, str(self.external), project_id=project_id)
        with self.assertRaises(ExternalProjectError):
            apply_external_project(self.paths, scan)


if __name__ == "__main__":
    unittest.main()
