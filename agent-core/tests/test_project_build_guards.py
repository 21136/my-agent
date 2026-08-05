"""PROJECT-MODE §0d E7–E9: cwd alias, repl build bypass, node_modules install guard."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from project_npm_guard import coalesce_working_dir, redundant_npm_install_error
from tools.executor import (
    ExecutorSession,
    ToolExecutor,
    _validate_project_repl_build_bypass,
)
from tools.registry import ToolRegistry
from tools.schema import ToolErrorCode

from tests.isolation_helpers import make_temp_agent_paths, temporary_agent_paths


def _load_run_command_module():
    path = _AGENT_CORE.parent / "evolve" / "tools" / "common" / "run_command" / "main.py"
    spec = importlib.util.spec_from_file_location("run_command_guard_test", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class CoalesceWorkingDirTests(unittest.TestCase):
    def test_cwd_alias_when_working_dir_empty(self) -> None:
        self.assertEqual(
            coalesce_working_dir({"cwd": "workspace/demo/frontend"}),
            "workspace/demo/frontend",
        )

    def test_working_dir_wins_over_cwd(self) -> None:
        self.assertEqual(
            coalesce_working_dir(
                {"working_dir": "workspace/a", "cwd": "workspace/b"},
            ),
            "workspace/a",
        )


class ReplBuildBypassTests(unittest.TestCase):
    def test_blocks_npm_in_project_repl(self) -> None:
        session = ExecutorSession(active_shell="project", project_root="workspace/demo")
        err = _validate_project_repl_build_bypass(
            session,
            "run_evolved",
            {
                "tool_name": "repl",
                "arguments": {
                    "code": "import subprocess; subprocess.run(['npm','install'], cwd='frontend')"
                },
            },
        )
        self.assertIsNotNone(err)
        assert err is not None
        self.assertFalse(err.ok)
        self.assertEqual(err.error.code if err.error else None, ToolErrorCode.VALIDATION_ERROR)
        self.assertEqual(
            (err.error.details or {}).get("guard_type") if err.error else None,
            "project_repl_build_bypass",
        )
        self.assertIn("run_command", err.error.message if err.error else "")

    def test_allows_repl_without_pkg_manager(self) -> None:
        session = ExecutorSession(active_shell="project", project_root="workspace/demo")
        err = _validate_project_repl_build_bypass(
            session,
            "run_evolved",
            {"tool_name": "repl", "arguments": {"code": "print(1+1)"}},
        )
        self.assertIsNone(err)

    def test_non_project_shell_allows_npm_in_repl(self) -> None:
        session = ExecutorSession(active_shell="grow", project_root="")
        err = _validate_project_repl_build_bypass(
            session,
            "run_evolved",
            {
                "tool_name": "repl",
                "arguments": {"code": "subprocess.run(['npm','--version'])"},
            },
        )
        self.assertIsNone(err)

    def test_executor_validate_rejects_archived_repl(self) -> None:
        """Archived repl is rejected before E8 bypass guard (E8 covered by unit tests above)."""
        paths = make_temp_agent_paths(self, copy_tool_dirs=("common/repl",))
        session_dir = paths.data / "sessions" / "_e8"
        session_dir.mkdir(parents=True, exist_ok=True)
        executor = ToolExecutor(
            registry=ToolRegistry.load(paths),
            session=ExecutorSession(
                session_dir=session_dir,
                active_shell="project",
                project_root="workspace/demo",
                allowed_evolved={"repl"},
            ),
            confirm_fn=lambda _p, _a: "y",
        )
        result = executor.validate(
            "run_evolved",
            {
                "tool_name": "repl",
                "arguments": {"code": r"os.system(r'C:\maven\bin\mvn.cmd -q compile')"},
            },
        )
        self.assertIsNotNone(result)
        assert result is not None
        self.assertFalse(result.ok)
        msg = result.error.message if result.error else ""
        self.assertIn("不可执行", msg)
        self.assertIn("archived", msg)


class NpmInstallGuardTests(unittest.TestCase):
    def test_policy_rejects_when_node_modules_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            frontend = Path(tmp) / "frontend"
            (frontend / "node_modules").mkdir(parents=True)
            err = redundant_npm_install_error(frontend, "npm install")
            self.assertIsNotNone(err)
            assert err is not None
            self.assertIn("node_modules", err)
            self.assertIn("force_install", err)

    def test_force_install_skips_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            frontend = Path(tmp) / "frontend"
            (frontend / "node_modules").mkdir(parents=True)
            self.assertIsNone(
                redundant_npm_install_error(frontend, "npm install", force_install=True)
            )

    def test_run_command_dry_run_applies_e9_guard(self) -> None:
        with temporary_agent_paths(copy_tool_dirs=("common/run_command",)) as paths:
            frontend = paths.workspace / "fe-guard"
            frontend.mkdir(parents=True)
            (frontend / "node_modules").mkdir()
            mod = _load_run_command_module()
            mod._agent_root = lambda: paths.agent_root  # type: ignore[method-assign]
            out = mod.run_command(
                {
                    "command": "npm install",
                    "working_dir": "workspace/fe-guard",
                    "dry_run": True,
                }
            )
            self.assertFalse(out.get("ok"))
            self.assertIn("node_modules", str(out.get("error", "")))


if __name__ == "__main__":
    unittest.main()
