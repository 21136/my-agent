"""Amber welcome card · time greeting · 忆梦 / 打工仔."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from terminal_ui import (
    WelcomeContent,
    build_time_greeting_lines,
    build_welcome_compact_formatted,
    build_welcome_formatted,
    format_welcome_plain,
    terminal_assistant_name,
    terminal_user_name,
)


class WelcomeNameDefaultsTests(unittest.TestCase):
    def test_default_names(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=False):
            for key in ("MY_AGENT_TERMINAL_USER_NAME", "MY_AGENT_TERMINAL_ASSISTANT_NAME"):
                os.environ.pop(key, None)
        self.assertEqual(terminal_user_name(), "忆梦")
        self.assertEqual(terminal_assistant_name(), "打工仔")

    def test_names_overridable_via_env(self) -> None:
        with mock.patch.dict(
            "os.environ",
            {
                "MY_AGENT_TERMINAL_USER_NAME": "测试用户",
                "MY_AGENT_TERMINAL_ASSISTANT_NAME": "小助手",
            },
            clear=False,
        ):
            self.assertEqual(terminal_user_name(), "测试用户")
            self.assertEqual(terminal_assistant_name(), "小助手")


class TimeGreetingTests(unittest.TestCase):
    def test_new_session_greeting(self) -> None:
        line1, line2 = build_time_greeting_lines(
            resume=False,
            workspace="my-agent",
            hour=10,
        )
        self.assertIn("忆梦", line1)
        self.assertIn("打工仔", line2)
        self.assertIn("我们开始", line2)

    def test_morning_resume(self) -> None:
        line1, line2 = build_time_greeting_lines(resume=True, workspace="huiyi", hour=9)
        self.assertIn("早上好", line1)
        self.assertIn("忆梦", line1)
        self.assertIn("打工仔", line2)
        self.assertIn("huiyi", line2)

    def test_late_night_resume(self) -> None:
        line1, line2 = build_time_greeting_lines(resume=True, workspace="x", hour=1)
        self.assertIn("夜深了", line1)
        self.assertIn("打工仔", line2)


class WelcomeFormattedTests(unittest.TestCase):
    def _panel(self, *, resume: bool = True) -> WelcomeContent:
        return WelcomeContent(
            effective_root="D:/my-agent",
            llm_model="deepseek-v4-flash",
            terminal_scope_kind="agent",
            harness="terminal",
            terminal_cwd=".",
            session_id="sess-1",
            resume=resume,
            workspace_name="my-agent",
            left_lines=(),
            right_lines=(),
        )

    def test_formatted_card_has_amber_box_corners(self) -> None:
        from welcome_mascot import SPRITE_LABEL, sprite_display_width, sprite_lines

        ft = build_welcome_formatted(self._panel())
        rendered = "".join(part for _style, part in ft)
        self.assertIn("╭", rendered)
        self.assertIn("╯", rendered)
        self.assertIn("my-agent", rendered)
        self.assertIn("忆梦", rendered)
        self.assertGreaterEqual(sprite_display_width(), 32)
        self.assertGreater(len(sprite_lines()), 10)
        self.assertIn(SPRITE_LABEL, rendered)
        self.assertIn("\u2580", rendered)

    def test_compact_welcome_is_single_strip(self) -> None:
        ft = build_welcome_compact_formatted(self._panel())
        rendered = "".join(part for _style, part in ft)
        self.assertIn("╭", rendered)
        self.assertIn("忆梦", rendered)
        self.assertNotIn("\u2580", rendered)

    def test_plain_fallback_includes_greeting(self) -> None:
        text = format_welcome_plain(self._panel())
        self.assertIn("忆梦", text)
        self.assertIn("打工仔", text)


if __name__ == "__main__":
    unittest.main()
