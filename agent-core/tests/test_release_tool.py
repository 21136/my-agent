from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
_TOOL_PATH = _AGENT_CORE.parent / "evolve" / "tools" / "coding" / "release" / "main.py"
_SPEC = importlib.util.spec_from_file_location("release_tool_under_test", _TOOL_PATH)
assert _SPEC and _SPEC.loader
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)


class ReleaseToolTests(unittest.TestCase):
    def test_build_dry_run_reports_artifact_plan(self) -> None:
        result = _MODULE.run_release({
            "action": "build",
            "working_dir": ".",
            "version": "0.1.0",
            "dry_run": True,
        })
        self.assertTrue(result["ok"])
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["action"], "build")

    def test_unknown_action_is_rejected(self) -> None:
        result = _MODULE.run_release({"action": "deploy"})
        self.assertFalse(result["ok"])


if __name__ == "__main__":
    unittest.main()
