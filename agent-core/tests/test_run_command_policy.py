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
    load_quality_command_argv,
    matches_quality_command,
    run_command_requires_confirm,
    working_dir_under_project,
)
from tests.isolation_helpers import temporary_agent_paths

_ENV_QUALITY = """
tools:
  node: ""
quality:
  commands:
    - id: ruff
      cmd: ["python", "-m", "ruff", "check", "."]
    - id: eslint
      cmd: ["npm", "run", "lint"]
"""


class RunCommandPolicyTests(unittest.TestCase):
    def test_classes(self) -> None:
        self.assertEqual(classify_run_command("rm -rf tmp"), "danger")
        self.assertEqual(classify_run_command("npm install"), "install")
        self.assertEqual(classify_run_command("git push origin main"), "network")
        self.assertEqual(classify_run_command("gh pr create --title demo"), "network")
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

    def test_quality_whitelist_hit_from_env_text(self) -> None:
        argv = load_quality_command_argv(env_text=_ENV_QUALITY)
        self.assertTrue(matches_quality_command("python -m ruff check .", argv))
        self.assertTrue(matches_quality_command("ruff check . --fix", argv))
        needs, reason = run_command_requires_confirm(
            command="python -m ruff check .",
            working_dir="workspace/demo",
            project_root="workspace/demo",
            env_text=_ENV_QUALITY,
        )
        self.assertFalse(needs)
        self.assertEqual(reason, "skip:quality")
        needs_lint, reason_lint = run_command_requires_confirm(
            command="npm run lint",
            working_dir="workspace/demo",
            project_root="workspace/demo",
            env_text=_ENV_QUALITY,
        )
        self.assertFalse(needs_lint)
        self.assertEqual(reason_lint, "skip:quality")

    def test_quality_whitelist_reads_project_env_md(self) -> None:
        with temporary_agent_paths() as paths:
            proj = paths.workspace / "qa-demo"
            proj.mkdir(parents=True)
            (proj / "ENV.md").write_text(_ENV_QUALITY, encoding="utf-8")
            needs, reason = run_command_requires_confirm(
                command="ruff check .",
                working_dir="workspace/qa-demo",
                project_root="workspace/qa-demo",
                agent_paths=paths,
            )
            self.assertFalse(needs)
            self.assertEqual(reason, "skip:quality")

    def test_quality_whitelist_misses_still_confirm(self) -> None:
        cases = [
            ("npm install", "workspace/demo", "workspace/demo", "install"),
            ("git push origin main", "workspace/demo", "workspace/demo", "network"),
            ("gh pr create --fill", "workspace/demo", "workspace/demo", "network"),
            ("rm -rf dist", "workspace/demo", "workspace/demo", "danger"),
            ("python app.py", "workspace/demo", "workspace/demo", "other:other"),
            ("python -m ruff check .", "workspace/other", "workspace/demo", "outside_project"),
        ]
        for command, cwd, root, expected in cases:
            needs, reason = run_command_requires_confirm(
                command=command,
                working_dir=cwd,
                project_root=root,
                env_text=_ENV_QUALITY,
            )
            self.assertTrue(needs, msg=command)
            self.assertEqual(reason, expected, msg=command)

    def test_quality_list_cannot_override_danger(self) -> None:
        needs, reason = run_command_requires_confirm(
            command="rm -rf dist",
            working_dir="workspace/demo",
            project_root="workspace/demo",
            quality_commands=[["rm", "-rf", "dist"]],
        )
        self.assertTrue(needs)
        self.assertEqual(reason, "danger")

    def test_baseline_pytest_and_verify_still_skip(self) -> None:
        for command in ("pytest -q", "python verify.py", "python workspace/demo/verify.py"):
            needs, reason = run_command_requires_confirm(
                command=command,
                working_dir="workspace/demo",
                project_root="workspace/demo",
            )
            self.assertFalse(needs, msg=command)
            self.assertTrue(reason.startswith("skip:"), msg=command)


if __name__ == "__main__":
    unittest.main()
