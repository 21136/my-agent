"""IT-01 / T-1803: project lifecycle — new, confirm, plan gate."""

from __future__ import annotations

import json
import secrets
import shutil
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from paths import AgentPaths
from project_cli import parse_project_command, run_project_command
from project_mode import (
    PROJECT_ARTIFACTS,
    normalize_project_id,
    plan_allows_code_writes,
    project_dir,
)
from project_switch import PROJECT_SESSIONS_KEY, read_project_sessions
from session import create_new
from tools.executor import ExecutorSession, ToolExecutor
from tools.registry import ToolRegistry
from tools.schema import ToolErrorCode, tool_ok

from tests.isolation_helpers import make_temp_agent_paths


class ProjectLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.paths = make_temp_agent_paths(self, copy_tool_dirs=("common/run_command",))
        self.project_id = f"test-lifecycle-{secrets.token_hex(4)}"
        self.session = create_new(
            self.paths,
            conversation_id=f"_test_proj_lifecycle_{secrets.token_hex(4)}",
        )

    def test_project_new_creates_workspace_and_triad(self) -> None:
        """T-1803-01: 项目 新建 creates workspace/<id>/ + PROJECT/MAP/TASKS."""
        command = parse_project_command(f"项目 新建 {self.project_id}")
        self.assertIsNotNone(command)
        assert command is not None
        self.assertEqual(command.kind, "new")
        self.assertEqual(command.project_id, self.project_id)

        outputs: list[str] = []
        result = run_project_command(
            self.session,
            self.paths,
            command,
            output_fn=outputs.append,
        )

        pid = normalize_project_id(self.project_id)
        dest = project_dir(self.paths, pid)
        self.assertTrue(dest.is_dir(), "workspace project directory should exist")
        for name in sorted(PROJECT_ARTIFACTS):
            artifact = dest / name
            self.assertTrue(artifact.is_file(), f"missing {name}")
            self.assertGreater(len(artifact.read_text(encoding="utf-8")), 0)

        self.assertTrue(result.meta_changed)
        self.assertEqual(self.session.meta.active_shell, "project")
        self.assertEqual(self.session.meta.project_id, pid)
        self.assertEqual(self.session.meta.project_root, f"workspace/{pid}")
        self.assertEqual(self.session.meta.project_plan_status, "draft")
        self.assertTrue(any("计划待确认" in line for line in outputs))
        self.assertEqual(read_project_sessions(self.paths).get(pid), self.session.conversation_id)

    def _run_project_new(self) -> None:
        command = parse_project_command(f"项目 新建 {self.project_id}")
        assert command is not None
        run_project_command(
            self.session,
            self.paths,
            command,
            output_fn=lambda _line: None,
        )
        self.assertEqual(self.session.meta.project_plan_status, "draft")

    def _run_project_confirm(self) -> None:
        command = parse_project_command("项目 确认")
        assert command is not None
        run_project_command(
            self.session,
            self.paths,
            command,
            output_fn=lambda _line: None,
        )
        self.assertEqual(self.session.meta.project_plan_status, "confirmed")

    def _run_command_arguments(self) -> dict[str, object]:
        pid = normalize_project_id(self.project_id)
        return {
            "tool_name": "run_command",
            "arguments": {
                "command": "echo hi",
                "working_dir": f"workspace/{pid}",
                "dry_run": True,
            },
        }

    def _make_executor(self) -> ToolExecutor:
        self.session.save()
        return ToolExecutor(
            registry=ToolRegistry.load(self.paths),
            session=ExecutorSession.load(
                self.session.session_dir,
                allowed_evolved={"run_command"},
            ),
            confirm_fn=lambda _preview, _allow_all: "y",
        )

    def test_project_confirm_migrates_draft_to_confirmed(self) -> None:
        """T-1803-02: 项目 确认 moves plan_status draft → confirmed."""
        self._run_project_new()

        command = parse_project_command("项目 确认")
        self.assertIsNotNone(command)
        assert command is not None
        self.assertEqual(command.kind, "confirm")

        outputs: list[str] = []
        result = run_project_command(
            self.session,
            self.paths,
            command,
            output_fn=outputs.append,
        )

        self.assertTrue(result.meta_changed)
        self.assertEqual(self.session.meta.project_plan_status, "confirmed")
        self.assertTrue(self.session.meta.project_plan_confirmed_at)
        self.assertTrue(plan_allows_code_writes(self.session.meta.project_plan_status))
        self.assertIn("confirmed", self.session.goal)
        self.assertTrue(any("计划已确认" in line for line in outputs))
        self.assertFalse(any(line.startswith("error:") for line in outputs))

    def test_draft_rejects_run_command_plan_gate(self) -> None:
        """T-1803-03: draft plan blocks run_command via executor plan gate."""
        self._run_project_new()
        executor = self._make_executor()

        result = executor.run("run_evolved", self._run_command_arguments())

        self.assertFalse(result.ok)
        assert result.error is not None
        self.assertEqual(result.error.code, ToolErrorCode.VALIDATION_ERROR)
        self.assertIn("计划未确认", result.error.message)
        details = result.error.details or {}
        self.assertEqual(details.get("project_plan_status"), "draft")

    def test_confirmed_allows_run_command(self) -> None:
        """T-1803-03: confirmed plan passes plan gate; run_command proceeds (mocked)."""
        self._run_project_new()
        self._run_project_confirm()

        mock_runner = MagicMock(
            return_value=tool_ok(
                "run_evolved",
                {"exit_code": 0, "stdout": "ok", "dry_run": True},
                duration_ms=1,
            )
        )
        executor = self._make_executor()
        with patch.dict(
            "tools.executor._BUILTIN_RUNNERS",
            {"run_evolved": mock_runner},
        ):
            result = executor.run("run_evolved", self._run_command_arguments())

        self.assertTrue(result.ok)
        mock_runner.assert_called_once()


if __name__ == "__main__":
    unittest.main()
