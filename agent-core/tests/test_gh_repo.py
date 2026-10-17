from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
_TOOL_PATH = _AGENT_CORE.parent / "evolve" / "tools" / "coding" / "gh_repo" / "main.py"
_SPEC = importlib.util.spec_from_file_location("gh_repo_under_test", _TOOL_PATH)
assert _SPEC and _SPEC.loader
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)


class GhRepoTests(unittest.TestCase):
    def test_dry_run_private_repo_creation_is_explicit(self) -> None:
        original = _MODULE._require_gh
        _MODULE._require_gh = lambda: None
        try:
            result = _MODULE.run_gh_repo({
                "action": "create",
                "name": "21136/example-agent",
                "visibility": "private",
                "description": "generated agent",
                "dry_run": True,
            })
        finally:
            _MODULE._require_gh = original
        self.assertTrue(result["ok"])
        self.assertTrue(result["dry_run"])
        self.assertIn("--private", result["args"])
        self.assertNotIn("--push", result["args"])

    def test_invalid_visibility_and_name_are_rejected(self) -> None:
        original = _MODULE._require_gh
        _MODULE._require_gh = lambda: None
        try:
            visibility = _MODULE.run_gh_repo({"action": "create", "name": "demo", "visibility": "secret", "dry_run": True})
            name = _MODULE.run_gh_repo({"action": "create", "name": "-bad", "dry_run": True})
        finally:
            _MODULE._require_gh = original
        self.assertFalse(visibility["ok"])
        self.assertFalse(name["ok"])


if __name__ == "__main__":
    unittest.main()
