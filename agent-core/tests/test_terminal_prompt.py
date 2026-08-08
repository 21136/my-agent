"""Tests for Claude-style prompt_toolkit session."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from terminal_prompt import prompt_toolkit_enabled
from terminal_ui import TerminalConsole


class TerminalPromptTests(unittest.TestCase):
    def test_prompt_toolkit_disabled_when_forced_off(self) -> None:
        with mock.patch.dict("os.environ", {"MY_AGENT_TERMINAL_PROMPT": "0"}, clear=False):
            self.assertFalse(prompt_toolkit_enabled())

    def test_prompt_toolkit_enabled_when_import_ok_and_tty(self) -> None:
        env = {"MY_AGENT_TERMINAL_PROMPT": "auto", "NO_COLOR": ""}
        with mock.patch.dict("os.environ", env, clear=False):
            with mock.patch("sys.stdin.isatty", return_value=True):
                try:
                    import prompt_toolkit  # noqa: F401

                    self.assertTrue(prompt_toolkit_enabled())
                except ImportError:
                    self.skipTest("prompt_toolkit not installed")

    def test_terminal_prompt_session_builds(self) -> None:
        from session import create_terminal_session
        from terminal_prompt import TerminalPromptSession
        from tests.isolation_helpers import make_temp_agent_paths

        paths = make_temp_agent_paths(self)
        session = create_terminal_session(paths, terminal_scope_kind="agent")
        console = TerminalConsole.create(session=session, paths=paths, kind="rich")
        with mock.patch("prompt_toolkit.PromptSession") as prompt_cls:
            prompt_cls.return_value = mock.Mock()
            prompt = TerminalPromptSession(console)
            self.assertIs(prompt._session, prompt_cls.return_value)
            prompt_cls.assert_called_once()


if __name__ == "__main__":
    unittest.main()
