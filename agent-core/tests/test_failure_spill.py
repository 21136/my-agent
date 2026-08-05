"""Phase 41 P4 — failure tool result spill (AGENT-HARNESS · IT-412)."""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from paths import AgentPaths
from tools.executor import (
    _TOOL_OUTPUTS_DIR,
    maybe_spill_result,
    preview_chars,
    spill_threshold_chars,
)
from tools.schema import tool_fail, to_json


class FailureSpillTests(unittest.TestCase):
    def setUp(self) -> None:
        self._prev_spill = os.environ.get("TOOL_OUTPUT_SPILL_CHARS")
        self._prev_preview = os.environ.get("TOOL_OUTPUT_PREVIEW_CHARS")
        os.environ["TOOL_OUTPUT_SPILL_CHARS"] = "400"
        os.environ["TOOL_OUTPUT_PREVIEW_CHARS"] = "120"
        self.paths = AgentPaths.discover()
        self.session_dir = self.paths.data / "sessions" / "_it412_failure_spill"
        self.session_dir.mkdir(parents=True, exist_ok=True)
        spill_dir = self.session_dir / _TOOL_OUTPUTS_DIR
        if spill_dir.is_dir():
            for old in spill_dir.glob("*.txt"):
                old.unlink(missing_ok=True)

    def tearDown(self) -> None:
        if self._prev_spill is None:
            os.environ.pop("TOOL_OUTPUT_SPILL_CHARS", None)
        else:
            os.environ["TOOL_OUTPUT_SPILL_CHARS"] = self._prev_spill
        if self._prev_preview is None:
            os.environ.pop("TOOL_OUTPUT_PREVIEW_CHARS", None)
        else:
            os.environ["TOOL_OUTPUT_PREVIEW_CHARS"] = self._prev_preview
        spill_dir = self.session_dir / _TOOL_OUTPUTS_DIR
        if spill_dir.is_dir():
            for old in spill_dir.glob("*.txt"):
                old.unlink(missing_ok=True)

    def test_small_failure_unchanged(self) -> None:
        result = tool_fail("run_command", "exit_nonzero", "boom", details={"stderr": "x"})
        spilled = maybe_spill_result(
            result,
            session_dir=self.session_dir,
            agent_paths=self.paths,
        )
        self.assertFalse(spilled.ok)
        self.assertFalse(spilled.truncated)
        self.assertIsNone(spilled.output_path)
        self.assertEqual(spilled.error.message if spilled.error else "", "boom")

    def test_large_failure_spills(self) -> None:
        huge = "E" * 2000
        result = tool_fail(
            "run_command",
            "exit_nonzero",
            "command failed",
            details={"stderr": huge, "stdout": "out", "exit_code": 1},
        )
        self.assertGreater(len(to_json(result)), spill_threshold_chars())

        spilled = maybe_spill_result(
            result,
            session_dir=self.session_dir,
            agent_paths=self.paths,
        )
        self.assertFalse(spilled.ok)
        self.assertTrue(spilled.truncated)
        self.assertIsNotNone(spilled.output_path)
        assert spilled.error is not None
        details = spilled.error.details or {}
        self.assertTrue(details.get("spilled"))
        self.assertIn("preview", details)
        self.assertLessEqual(len(details["preview"]), preview_chars())
        self.assertIn("read_file", str(details.get("hint", "")))
        self.assertLessEqual(len(to_json(spilled)), spill_threshold_chars() + 200)

        spill_name = Path(spilled.output_path).name  # type: ignore[arg-type]
        spill_file = self.session_dir / _TOOL_OUTPUTS_DIR / spill_name
        self.assertTrue(spill_file.is_file())
        body = spill_file.read_text(encoding="utf-8")
        self.assertIn(huge, body)
        payload = json.loads(body)
        self.assertFalse(payload["ok"])


if __name__ == "__main__":
    unittest.main()
