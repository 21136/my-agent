"""T-6204 / IT-6204: structured_test v1 protocol adapter."""

from __future__ import annotations

import sys
import importlib.util
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from project_verify import run_project_tests
from structured_test import normalize_test_result
from tests.isolation_helpers import temporary_agent_paths
from tools.registry import ToolRegistry


class StructuredTestTests(unittest.TestCase):
    def test_tool_rejects_invalid_numeric_arguments_as_structured_failure(self) -> None:
        script = _AGENT_CORE.parent / "evolve" / "tools" / "project" / "structured_test" / "main.py"
        spec = importlib.util.spec_from_file_location("structured_test_tool_under_test", script)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        result = module.run({"working_dir": "workspace/test", "timeout_sec": "fast"})
        self.assertEqual(result["status"], "failed")
        self.assertIn("timeout_sec", result["error"])

    def test_passed_result_has_stable_fields_and_no_rerun(self) -> None:
        result = normalize_test_result(
            {
                "ok": True,
                "suite": "pytest",
                "working_dir": "workspace/demo",
                "exit_code": 0,
                "duration_ms": 12,
                "stdout": "2 passed\n",
                "stderr": "",
                "failures": [],
            }
        )
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["duration_ms"], 12)
        self.assertEqual(result["failed"], [])
        self.assertIsNone(result["rerun"])
        self.assertEqual(result["stdout_tail"], "2 passed\n")

    def test_failure_preserves_file_line_and_builds_rerun_hint(self) -> None:
        result = normalize_test_result(
            {
                "ok": False,
                "suite": "pytest",
                "exit_code": 1,
                "failures": [
                    {
                        "file": "tests/test_demo.py",
                        "line": 42,
                        "test": "tests/test_demo.py::test_fail",
                        "message": "boom",
                    }
                ],
                "stdout": "FAILED tests/test_demo.py::test_fail\n",
            }
        )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["failed"][0]["line"], 42)
        self.assertEqual(
            result["rerun"],
            {"file": "tests/test_demo.py", "test": "tests/test_demo.py::test_fail"},
        )

    def test_timeout_and_missing_dependency_are_blocked_with_null_exit(self) -> None:
        timeout = normalize_test_result(
            {"ok": False, "error": "command timed out after 5s", "exit_code": None}
        )
        self.assertEqual(timeout["status"], "timeout")
        self.assertIsNone(timeout["exit_code"])
        missing = normalize_test_result(
            {"ok": False, "error": "No module named pytest", "exit_code": 1}
        )
        self.assertEqual(missing["status"], "blocked")
        self.assertEqual(missing["reason"], "missing_dependency")

    def test_dry_run_is_blocked_and_active_tool_is_listed(self) -> None:
        dry = normalize_test_result(
            {"ok": True, "dry_run": True, "suite": "pytest", "command": "pytest -q"}
        )
        self.assertEqual(dry["status"], "blocked")
        self.assertEqual(dry["reason"], "dry_run")

        with temporary_agent_paths() as paths:
            project = paths.workspace / "protocol-demo"
            project.mkdir(parents=True)
            (project / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
            planned = run_project_tests(
                paths,
                working_dir="workspace/protocol-demo",
                suite="pytest",
                dry_run=True,
            )
            self.assertIn("-m pytest", planned["command"])

        with temporary_agent_paths(copy_tool_dirs=("project/structured_test",)) as paths:
            registry = ToolRegistry.load(paths)
            tool = registry.get_evolved("structured_test")
            self.assertIsNotNone(tool)
            assert tool is not None
            self.assertEqual(tool.status, "active")
            self.assertIn(tool, registry.session_evolved(["project", "coding"]))


if __name__ == "__main__":
    unittest.main()
