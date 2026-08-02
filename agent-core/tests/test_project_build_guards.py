"""PROJECT-MODE §0d E7–E9: cwd alias, repl build bypass, node_modules install guard."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
_AGENT_ROOT = _AGENT_CORE.parent
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from tools.executor import (
    ExecutorSession,
    ToolExecutor,
    _validate_project_repl_build_bypass,
)
from tools.registry import ToolRegistry
from tools.schema import ToolErrorCode

from tests.isolation_helpers import make_temp_agent_paths


def _load_npm_exec_module():
    path = _AGENT_ROOT / "evolve" / "tools" / "common" / "npm_exec" / "main.py"
    spec = importlib.util.spec_from_file_location("npm_exec_under_test", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class CoalesceWorkingDirTests(unittest.TestCase):
    def test_cwd_alias_when_working_dir_empty(self) -> None:
        mod = _load_npm_exec_module()
        self.assertEqual(
            mod._coalesce_working_dir({"cwd": "workspace/demo/frontend"}),
            "workspace/demo/frontend",
        )

    def test_working_dir_wins_over_cwd(self) -> None:
        mod = _load_npm_exec_module()
        self.assertEqual(
            mod._coalesce_working_dir(
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
        self.assertIn("npm_exec", err.error.message if err.error else "")

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

    def test_executor_validate_blocks_mvn_cmd(self) -> None:
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
        self.assertEqual(
            (result.error.details or {}).get("guard_type") if result.error else None,
            "project_repl_build_bypass",
        )


class NpmInstallGuardTests(unittest.TestCase):
    def test_rejects_install_when_node_modules_exists(self) -> None:
        mod = _load_npm_exec_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frontend = root / "frontend"
            (frontend / "node_modules").mkdir(parents=True)
            # Patch resolve to use our temp dir without full AgentPaths dance
            orig_resolve = mod._resolve_working_dir

            def fake_resolve(_paths, path_arg: str | None):
                return frontend

            mod._resolve_working_dir = fake_resolve  # type: ignore[method-assign]
            try:
                out = mod.npm_exec(
                    {
                        "args": ["install"],
                        "working_dir": "frontend",
                        "dry_run": True,
                    }
                )
            finally:
                mod._resolve_working_dir = orig_resolve  # type: ignore[method-assign]
            self.assertFalse(out.get("ok"))
            self.assertIn("node_modules", str(out.get("error", "")))
            self.assertIn("force_install", str(out.get("error", "")))

    def test_force_install_allows_dry_run(self) -> None:
        mod = _load_npm_exec_module()
        with tempfile.TemporaryDirectory() as tmp:
            frontend = Path(tmp) / "frontend"
            (frontend / "node_modules").mkdir(parents=True)
            orig_resolve = mod._resolve_working_dir
            mod._resolve_working_dir = lambda _p, _a: frontend  # type: ignore[method-assign]
            try:
                out = mod.npm_exec(
                    {
                        "args": ["install"],
                        "working_dir": "frontend",
                        "force_install": True,
                        "dry_run": True,
                    }
                )
            finally:
                mod._resolve_working_dir = orig_resolve  # type: ignore[method-assign]
            self.assertTrue(out.get("ok"), out)
            self.assertTrue(out.get("dry_run"))


if __name__ == "__main__":
    unittest.main()
