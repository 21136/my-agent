"""Tests for user-facing tool display aliases."""

from __future__ import annotations

import unittest

from tool_display import (
    display_tool_name,
    format_tool_action_label,
    resolve_effective_tool_name,
)


class ToolDisplayTests(unittest.TestCase):
    def test_resolve_run_evolved_inner_from_summary(self) -> None:
        self.assertEqual(
            resolve_effective_tool_name("run_evolved", "write_text: workspace/music/VERIFY.md"),
            "write_text",
        )

    def test_format_action_write_text(self) -> None:
        label = format_tool_action_label(
            "run_evolved",
            "write_text: workspace/music/VERIFY.md",
        )
        self.assertEqual(label, "写入 VERIFY.md")

    def test_format_action_patch_fail(self) -> None:
        label = format_tool_action_label(
            "run_evolved",
            "patch_file: workspace/music/VERIFY.md",
            "find anchor not found",
        )
        self.assertIn("修补", label)
        self.assertIn("VERIFY.md", label)

    def test_display_builtin_name(self) -> None:
        self.assertEqual(display_tool_name("grep"), "搜索内容")
        self.assertEqual(display_tool_name("plan_partner"), "整理计划")


if __name__ == "__main__":
    unittest.main()
