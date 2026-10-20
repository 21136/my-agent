"""User-facing copy helpers."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from user_copy import format_tool_loop_exceeded_message


class TestUserCopy(unittest.TestCase):
    def test_tool_loop_zero_rounds_wording(self) -> None:
        session = SimpleNamespace(
            meta=SimpleNamespace(topics=[]),
            paths=SimpleNamespace(),
        )
        with (
            patch("loader.session_evolved_allowlist", return_value=[]),
            patch("user_copy.ToolRegistry.load", return_value=SimpleNamespace()),
        ):
            msg = format_tool_loop_exceeded_message(
                session,
                tool_rounds=0,
                tool_loop_max=8,
            )
        self.assertIn("未成功调用工具", msg)
        self.assertNotIn("狂奔", msg)
        self.assertNotIn("自动模式", msg)
        self.assertNotIn("segment", msg)
        self.assertNotIn("Harness", msg)

    def test_qa_zero_rounds_no_tool_budget_wording(self) -> None:
        session = SimpleNamespace(
            meta=SimpleNamespace(topics=[], turn_mode="agent"),
            paths=SimpleNamespace(),
            turn_intent="qa",
        )
        with (
            patch("loader.session_evolved_allowlist", return_value=["write_text"]),
            patch("user_copy.ToolRegistry.load", return_value=SimpleNamespace()),
        ):
            msg = format_tool_loop_exceeded_message(
                session,
                tool_rounds=0,
                tool_loop_max=1,
                turn_intent="qa",
            )
        self.assertIn("自动重试", msg)
        self.assertNotIn("工具预算", msg)
        self.assertNotIn("write_text", msg)


if __name__ == "__main__":
    unittest.main()
