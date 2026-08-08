"""Tests for terminal interactive picker."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from terminal_picker import (
    interactive_choice_available,
    prompt_model_choice,
)


class _Entry:
    def __init__(self, model_id: str, name: str, tier: str = "flash") -> None:
        self.id = model_id
        self.name = name
        self.tier = tier


class TerminalPickerTests(unittest.TestCase):
    def test_interactive_choice_requires_tty(self) -> None:
        with mock.patch("sys.stdin.isatty", return_value=False):
            self.assertFalse(interactive_choice_available())
        with mock.patch("sys.stdin.isatty", return_value=True):
            self.assertTrue(interactive_choice_available())

    def test_interactive_choice_env_disable(self) -> None:
        with mock.patch.dict("os.environ", {"MY_AGENT_TERMINAL_INTERACTIVE": "0"}):
            with mock.patch("sys.stdin.isatty", return_value=True):
                self.assertFalse(interactive_choice_available())

    def test_prompt_model_choice_arrow_and_enter(self) -> None:
        models = [
            _Entry("deepseek-v4-flash", "Flash"),
            _Entry("deepseek-v4-pro", "Pro", tier="pro"),
        ]
        keys = iter(["down", "enter"])

        def read_key() -> str | None:
            return next(keys)

        picked = prompt_model_choice(
            models,
            current_id="deepseek-v4-flash",
            read_key=read_key,
        )
        self.assertEqual(picked, "deepseek-v4-pro")

    def test_prompt_model_choice_esc_cancels(self) -> None:
        models = [_Entry("deepseek-v4-flash", "Flash")]
        picked = prompt_model_choice(
            models,
            current_id="deepseek-v4-flash",
            read_key=lambda: "esc",
        )
        self.assertIsNone(picked)

    def test_render_model_menu_highlight_follows_index(self) -> None:
        from terminal_picker import _format_model_menu_lines

        models = [
            _Entry("deepseek-v4-flash", "Flash"),
            _Entry("deepseek-v4-pro", "Pro", tier="pro"),
        ]
        flash_menu = _format_model_menu_lines(
            models,
            0,
            current_id="deepseek-v4-pro",
        )
        pro_menu = _format_model_menu_lines(
            models,
            1,
            current_id="deepseek-v4-pro",
        )
        self.assertIn("› Flash", flash_menu[2])
        self.assertNotIn(_STYLE_SEL := "\033[36m", flash_menu[3][:20])  # pro row not selected
        self.assertTrue(pro_menu[3].startswith(_STYLE_SEL) or "› Pro" in pro_menu[3])
        joined = "\n".join(flash_menu)
        self.assertNotIn("●", joined)
        self.assertIn("\033[36m", joined)

    def test_prompt_model_choice_redraws_on_arrow(self) -> None:
        import io
        from unittest import mock

        from terminal_picker import _redraw_menu

        models = [
            _Entry("deepseek-v4-flash", "Flash"),
            _Entry("deepseek-v4-pro", "Pro", tier="pro"),
        ]
        keys = iter(["down", "enter"])
        buf = io.StringIO()

        with mock.patch("terminal_picker._menu_stream", return_value=buf):
            with mock.patch("terminal_picker._print_menu"):
                with mock.patch("terminal_picker._erase_menu_lines"):
                    with mock.patch("terminal_picker._redraw_menu", wraps=_redraw_menu) as redraw:
                        picked = prompt_model_choice(
                            models,
                            current_id="deepseek-v4-flash",
                            read_key=lambda: next(keys),
                        )
        self.assertEqual(picked, "deepseek-v4-pro")
        redraw.assert_called_once()

    def test_erase_menu_lines_writes_cursor_moves(self) -> None:
        import io

        from terminal_picker import _erase_menu_lines

        buf = io.StringIO()
        _erase_menu_lines(["a", "b", "c"], stream=buf)
        written = buf.getvalue()
        self.assertIn("\033[3A", written)
        self.assertIn("\033[3M", written)

    def test_terminal_model_interactive_switch(self) -> None:
        from cli_terminal import TerminalRepl
        from session import create_terminal_session
        from terminal_scope import TerminalScopeFields
        from tests.isolation_helpers import make_temp_agent_paths

        paths = make_temp_agent_paths(self)
        repo = paths.workspace / "huiyi"
        repo.mkdir(parents=True)
        session = create_terminal_session(
            paths,
            terminal_scope_kind="agent",
            terminal_cwd="workspace/huiyi",
        )
        session.set_llm_model("deepseek-v4-flash")
        scope = TerminalScopeFields(terminal_scope_kind="agent", terminal_cwd="workspace/huiyi")
        repl = TerminalRepl.from_terminal_session(
            session,
            paths=paths,
            scope_fields=scope,
            input_fn=lambda _prompt: "",
            output_fn=lambda _text: None,
        )
        assert repl.terminal_console is not None
        with mock.patch("terminal_picker.interactive_choice_available", return_value=True):
            with mock.patch(
                "terminal_picker.prompt_model_choice",
                return_value="deepseek-v4-pro",
            ):
                outcome = repl.handle_line("/model")
        self.assertEqual(outcome, "continue")
        self.assertEqual(repl.session.meta.llm_model, "deepseek-v4-pro")


if __name__ == "__main__":
    unittest.main()
