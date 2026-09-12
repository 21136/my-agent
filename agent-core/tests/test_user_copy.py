"""Tests for centralized user-facing copy (user_copy.py)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from user_copy import (
    format_tool_loop_exceeded_message,
    harness_hard_verify_passed_notice,
    runaway_auto_chain_notice,
    runaway_mode_toggle_notice,
)


class TestUserCopy(unittest.TestCase):
    def test_runaway_auto_chain_notice_no_harness(self) -> None:
        self.assertNotIn("Harness", runaway_auto_chain_notice())

    def test_runaway_mode_toggle_notice(self) -> None:
        self.assertIn("关闭", runaway_mode_toggle_notice(enabled=False, resumed=False))
        self.assertIn("恢复", runaway_mode_toggle_notice(enabled=True, resumed=True))
        self.assertIn("开启", runaway_mode_toggle_notice(enabled=True, resumed=False))

    def test_harness_hard_verify_passed_notice_plain(self) -> None:
        self.assertNotIn("checkpoint", harness_hard_verify_passed_notice(target="verifying"))
        self.assertIn("发布", harness_hard_verify_passed_notice(target="release_wait"))

    def test_tool_loop_zero_rounds_wording(self) -> None:
        session = SimpleNamespace(
            meta=SimpleNamespace(project_runaway_enabled=True, topics=[]),
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
        self.assertNotIn("segment", msg)
        self.assertNotIn("Harness", msg)


if __name__ == "__main__":
    unittest.main()
