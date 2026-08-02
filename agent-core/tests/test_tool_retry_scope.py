"""TOOL-RETRY must not free-retry shell exit_code failures."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from agent import _is_retryable
from tools.schema import ToolErrorCode, tool_fail


class ToolRetryScopeTests(unittest.TestCase):
    def test_param_validation_is_retryable(self) -> None:
        result = tool_fail(
            "run_evolved",
            ToolErrorCode.VALIDATION_ERROR,
            "path is required",
            details={"retry": True, "expected": {"path": "string"}},
        )
        self.assertTrue(_is_retryable(result))

    def test_exit_code_failure_consumes_quota(self) -> None:
        result = tool_fail(
            "run_evolved",
            ToolErrorCode.VALIDATION_ERROR,
            "exit_code=1",
            details={
                "exit_code": 1,
                "stderr": "ERROR 1146",
                "tool_name": "run_command",
            },
        )
        self.assertFalse(_is_retryable(result))

    def test_cancelled_consumes_quota(self) -> None:
        result = tool_fail(
            "run_evolved",
            ToolErrorCode.VALIDATION_ERROR,
            "tool run_service cancelled",
            details={"tool_name": "run_service"},
        )
        self.assertFalse(_is_retryable(result))


if __name__ == "__main__":
    unittest.main()
