from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
_TOOL_PATH = _AGENT_CORE.parent / "evolve" / "tools" / "coding" / "deploy" / "main.py"
_SPEC = importlib.util.spec_from_file_location("deploy_tool_under_test", _TOOL_PATH)
assert _SPEC and _SPEC.loader
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)


class DeployToolTests(unittest.TestCase):
    def test_dry_run_plan_is_non_mutating(self) -> None:
        result = _MODULE.run_deploy({
            "action": "plan",
            "service_name": "candidate-agent",
            "command": ["python", "agent-core/server.py", "--port", "9001"],
            "working_dir": ".",
            "version": "v0.1.0",
            "dry_run": True,
        })
        self.assertTrue(result["ok"])
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["action"], "plan")

    def test_mutating_actions_require_explicit_dry_run_or_confirmation(self) -> None:
        result = _MODULE.run_deploy({"action": "start", "service_name": "x", "dry_run": True})
        self.assertTrue(result["ok"])
        self.assertTrue(result["dry_run"])


if __name__ == "__main__":
    unittest.main()
