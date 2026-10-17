from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
_TOOL_PATH = _AGENT_CORE.parent / "evolve" / "tools" / "coding" / "gh_release" / "main.py"
_SPEC = importlib.util.spec_from_file_location("gh_release_under_test", _TOOL_PATH)
assert _SPEC and _SPEC.loader
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)


class GhReleaseTests(unittest.TestCase):
    def test_dry_run_create_never_adds_force_or_deploy(self) -> None:
        original = _MODULE._require_gh
        _MODULE._require_gh = lambda: None
        try:
            result = _MODULE.run_gh_release({
                "action": "create",
                "tag": "v0.1.0",
                "title": "v0.1.0",
                "notes": "first release",
                "dry_run": True,
            })
        finally:
            _MODULE._require_gh = original
        self.assertTrue(result["ok"])
        self.assertTrue(result["dry_run"])
        self.assertIn("gh", result["args"])
        self.assertNotIn("--force", result["args"])

    def test_invalid_tag_is_rejected(self) -> None:
        original = _MODULE._require_gh
        _MODULE._require_gh = lambda: None
        try:
            result = _MODULE.run_gh_release({"action": "create", "tag": "-bad", "dry_run": True})
        finally:
            _MODULE._require_gh = original
        self.assertFalse(result["ok"])


if __name__ == "__main__":
    unittest.main()
