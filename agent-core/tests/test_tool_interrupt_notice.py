"""Interrupt notices must not be confused with tool-budget caps."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from agent import _tool_interrupt_kind, _tool_interrupt_label
from loader import format_tool_interrupt_kernel_message, format_tool_interrupt_notice
from tools.schema import ToolErrorCode, tool_fail, tool_ok


class ToolInterruptNoticeTests(unittest.TestCase):
    def test_cancelled_copy_denies_budget_cap(self) -> None:
        text = format_tool_interrupt_notice("cancelled", tool_label="run_command")
        self.assertIn("不是工具回合上限", text)
        self.assertIn("run_command", text)
        self.assertNotIn("segment", text.lower())

    def test_confirm_rejected_points_to_repair_node_modules(self) -> None:
        text = format_tool_interrupt_notice(
            "confirm_rejected", tool_label="repair_node_modules"
        )
        self.assertIn("确认被拒绝", text)
        self.assertIn("repair_node_modules", text)
        kernel = format_tool_interrupt_kernel_message(
            "confirm_rejected", tool_label="npm install"
        )
        self.assertTrue(kernel.startswith("[内核]"))

    def test_timeout_copy_says_system_not_user(self) -> None:
        text = format_tool_interrupt_notice("timeout", tool_label="run_command")
        self.assertIn("墙钟", text)
        self.assertIn("不是你点了", text)
        self.assertIn("不是工具回合上限", text)
        self.assertIn("repair_node_modules", text)

    def test_interrupt_kind_from_result(self) -> None:
        rejected = tool_fail(
            "run_evolved",
            ToolErrorCode.CONFIRM_REJECTED,
            "tool call rejected by user",
        )
        self.assertEqual(_tool_interrupt_kind(rejected), "confirm_rejected")

        cancelled = tool_fail(
            "run_evolved",
            ToolErrorCode.VALIDATION_ERROR,
            "tool run_command cancelled",
            details={"tool_name": "run_command"},
        )
        self.assertEqual(_tool_interrupt_kind(cancelled), "cancelled")
        self.assertEqual(_tool_interrupt_label(cancelled, "run_evolved"), "run_command")

        ok = tool_ok("run_evolved", {"tool_name": "run_command"})
        self.assertIsNone(_tool_interrupt_kind(ok))


if __name__ == "__main__":
    unittest.main()
