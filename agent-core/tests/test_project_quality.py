"""Tests for Phase 45 project quality + db migrate status."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from paths import AgentPaths
from project_env import ensure_project_env, find_env_path
from project_quality import (
    detect_migration_backend,
    parse_eslint_violations,
    parse_quality_commands_from_env_text,
    parse_ruff_violations,
    run_quality,
)
from subagent import _build_test_fail_hard_checklist, _parse_project_test_checker_command, CheckerTask
from tests.isolation_helpers import temporary_agent_paths
from tools.registry import ToolRegistry


class ProjectQualityTests(unittest.TestCase):
    def test_parse_quality_commands(self) -> None:
        text = """
tools:
  node: ""
quality:
  commands:
    - id: ruff
      cmd: ["python", "-m", "ruff", "check", "."]
      cwd: backend
"""
        cmds = parse_quality_commands_from_env_text(text)
        self.assertEqual(len(cmds), 1)
        self.assertEqual(cmds[0]["id"], "ruff")
        self.assertEqual(cmds[0]["cwd"], "backend")

    def test_ruff_eslint_parsers(self) -> None:
        ruff = parse_ruff_violations("src/a.py:10:5: E999 SyntaxError: oops\n")
        self.assertEqual(ruff[0]["line"], 10)
        eslint = parse_eslint_violations("src/b.ts:3:1: Missing semicolon\n")
        self.assertEqual(eslint[0]["line"], 3)

    def test_run_quality_dry_run(self) -> None:
        with temporary_agent_paths() as paths:
            proj = paths.workspace / "q-demo"
            proj.mkdir(parents=True)
            (proj / "ENV.md").write_text(
                "tools:\n  node: \"\"\nquality:\n  commands:\n"
                '    - id: echo\n      cmd: ["python", "-c", "print(1)"]\n',
                encoding="utf-8",
            )
            result = run_quality(paths, working_dir="workspace/q-demo", dry_run=True)
            self.assertTrue(result.get("ok"))
            self.assertTrue(result.get("dry_run"))

    def test_ensure_project_env_preserves_quality(self) -> None:
        with temporary_agent_paths() as paths:
            proj = paths.workspace / "env-q"
            proj.mkdir(parents=True)
            (proj / "ENV.md").write_text(
                "tools:\n  node: OLD\nprefer:\n  package_manager: npm\n\n"
                "quality:\n  commands:\n    - id: t\n      cmd: [\"echo\"]\n",
                encoding="utf-8",
            )
            ensure_project_env(paths, "env-q")
            text = (proj / "ENV.md").read_text(encoding="utf-8")
            self.assertIn("quality:", text)
            self.assertIn("id: t", text)
            self.assertNotIn("OLD", text)

    def test_detect_migration_backend(self) -> None:
        with temporary_agent_paths() as paths:
            alembic_dir = paths.workspace / "alembic-proj"
            alembic_dir.mkdir(parents=True)
            (alembic_dir / "alembic.ini").write_text("[alembic]\n", encoding="utf-8")
            self.assertEqual(detect_migration_backend(alembic_dir), "alembic")

    def test_db_migrate_status_dry_run(self) -> None:
        with temporary_agent_paths() as paths:
            proj = paths.workspace / "mig-demo"
            proj.mkdir(parents=True)
            (proj / "alembic.ini").write_text("[alembic]\n", encoding="utf-8")
            from project_quality import db_migrate_status

            result = db_migrate_status(paths, working_dir="workspace/mig-demo", dry_run=True)
            self.assertTrue(result.get("ok"))
            self.assertEqual(result.get("backend"), "alembic")

    def test_registry_loads_phase45_tools(self) -> None:
        live = AgentPaths.discover()
        registry = ToolRegistry.load(live)
        for name in ("db_migrate_status", "run_quality"):
            tool = registry.get_evolved(name)
            self.assertIsNotNone(tool)
            assert tool is not None
            self.assertEqual(tool.status, "active")


class ProjectTestCheckerTests(unittest.TestCase):
    def test_parse_project_test_checker_command(self) -> None:
        task = _parse_project_test_checker_command("验收测试 workspace/foo/backend")
        self.assertIsNotNone(task)
        assert task is not None
        self.assertEqual(task.kind, "project_test_fail")
        self.assertEqual(task.working_dir, "workspace/foo/backend")

    def test_build_test_fail_hard_checklist(self) -> None:
        items = _build_test_fail_hard_checklist(
            CheckerTask(
                kind="project_test_fail",
                working_dir="workspace/x",
                test_result={
                    "ok": False,
                    "failures": [{"file": "a.py", "line": 1, "message": "boom"}],
                    "failure_summary": "- a.py:1 — boom",
                },
            )
        )
        self.assertEqual(items[0].status, "fail")
        self.assertIn("1 failure", items[0].note)


if __name__ == "__main__":
    unittest.main()
