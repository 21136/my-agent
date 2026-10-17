from __future__ import annotations

import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
_REPO_ROOT = _AGENT_CORE.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(1, str(_AGENT_CORE))

import importlib.util

_runner_spec = importlib.util.spec_from_file_location(
    "main_test_runner", _REPO_ROOT / "tools" / "run_main_tests.py"
)
assert _runner_spec and _runner_spec.loader
_runner_module = importlib.util.module_from_spec(_runner_spec)
_runner_spec.loader.exec_module(_runner_module)
build_command = _runner_module.build_command
parse_pytest_summary = _runner_module.parse_pytest_summary


class MainTestRunnerTests(unittest.TestCase):
    def test_core_command_targets_agent_core_only(self) -> None:
        command = build_command("core", ())
        self.assertEqual(command[:3], [sys.executable, "-m", "pytest"])
        self.assertIn("agent-core/tests", command)
        self.assertNotIn("workspace", " ".join(command))

    def test_collect_command_targets_agent_core_only(self) -> None:
        command = build_command("collect", ())
        self.assertEqual(command[:3], [sys.executable, "-m", "pytest"])
        self.assertIn("--collect-only", command)
        self.assertIn("agent-core/tests", command)
        self.assertNotIn("workspace", " ".join(command))

    def test_focused_command_requires_explicit_paths(self) -> None:
        command = build_command("focused", ("agent-core/tests/test_skill_registry.py",))
        self.assertIn("agent-core/tests/test_skill_registry.py", command)
        with self.assertRaises(ValueError):
            build_command("focused", ())

    def test_parse_pytest_summary_distinguishes_collection_errors(self) -> None:
        parsed = parse_pytest_summary(
            "3 passed, 1 skipped, 2 failed, 1 error in 0.4s\n",
            exit_code=1,
        )
        self.assertEqual(parsed["passed"], 3)
        self.assertEqual(parsed["skipped"], 1)
        self.assertEqual(parsed["failed"], 2)
        self.assertEqual(parsed["collection_errors"], 1)
        self.assertEqual(parsed["status"], "collection_error")


if __name__ == "__main__":
    unittest.main()
