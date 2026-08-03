"""Unit tests for run_command_policy (Phase 29 Track A)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from run_command_policy import (
    classify_run_command,
    is_node_modules_wipe_command,
    run_command_requires_confirm,
    working_dir_under_project,
)


class RunCommandPolicyTests(unittest.TestCase):
    def test_classes(self) -> None:
        self.assertEqual(classify_run_command("rm -rf tmp"), "danger")
        self.assertEqual(classify_run_command("npm install"), "install")
        self.assertEqual(classify_run_command("git push origin main"), "network")
        self.assertEqual(classify_run_command("npm run build"), "build_test")
        self.assertEqual(classify_run_command("echo hi"), "readonly")

    def test_project_skip(self) -> None:
        self.assertTrue(working_dir_under_project("workspace/a/b", "workspace/a"))
        self.assertFalse(working_dir_under_project(".", "workspace/a"))
        needs, _ = run_command_requires_confirm(
            command="pytest -q",
            working_dir="workspace/a",
            project_root="workspace/a",
        )
        self.assertFalse(needs)
        needs2, reason = run_command_requires_confirm(
            command="pytest -q",
            working_dir="workspace/other",
            project_root="workspace/a",
        )
        self.assertTrue(needs2)
        self.assertEqual(reason, "outside_project")

    def test_background_always_confirm(self) -> None:
        needs, reason = run_command_requires_confirm(
            command="pytest -q",
            working_dir="workspace/a",
            project_root="workspace/a",
            background=True,
        )
        self.assertTrue(needs)
        self.assertEqual(reason, "background")

    def test_node_modules_wipe_detection(self) -> None:
        self.assertTrue(
            is_node_modules_wipe_command(
                r'cmd /c "rmdir /s /q D:\my-agent\workspace\huiyi\frontend\node_modules"'
            )
        )
        self.assertTrue(
            is_node_modules_wipe_command("Remove-Item -Recurse -Force node_modules")
        )
        self.assertFalse(is_node_modules_wipe_command("npm install"))
        self.assertFalse(
            is_node_modules_wipe_command('cmd /c "if exist node_modules (echo EXISTS)"')
        )


if __name__ == "__main__":
    unittest.main()
