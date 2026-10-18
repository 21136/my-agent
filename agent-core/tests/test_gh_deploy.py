from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
_TOOL_PATH = _AGENT_CORE.parent / "evolve" / "tools" / "coding" / "gh_deploy" / "main.py"
_SPEC = importlib.util.spec_from_file_location("gh_deploy_under_test", _TOOL_PATH)
assert _SPEC and _SPEC.loader
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)


class GhDeployTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original = _MODULE._require_gh
        _MODULE._require_gh = lambda: None

    def tearDown(self) -> None:
        _MODULE._require_gh = self.original

    def test_plan_is_explicit_about_production_environment(self) -> None:
        result = _MODULE.run_gh_deploy({
            "action": "plan",
            "repository": "21136/my-agent",
            "workflow": "self-hosting-deploy.yml",
            "ref": "main",
            "environment": "production",
            "version": "255be537",
            "dry_run": True,
        })
        self.assertTrue(result["ok"])
        self.assertTrue(result["dry_run"])
        self.assertTrue(any("production" in item for item in result["args"]))
        self.assertTrue(any("255be537" in item for item in result["args"]))

    def test_dispatch_requires_confirmation(self) -> None:
        result = _MODULE.run_gh_deploy({
            "action": "dispatch",
            "repository": "21136/my-agent",
            "version": "255be537",
        })
        self.assertFalse(result["ok"])
        self.assertIn("confirmed", result["error"])

    def test_confirmed_dispatch_dry_run_is_non_mutating(self) -> None:
        result = _MODULE.run_gh_deploy({
            "action": "dispatch",
            "repository": "21136/my-agent",
            "version": "255be537",
            "confirmed": True,
            "dry_run": True,
        })
        self.assertTrue(result["ok"])
        self.assertTrue(result["dry_run"])
        self.assertIn("workflow", result["args"])


if __name__ == "__main__":
    unittest.main()
