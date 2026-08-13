import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dataclasses import is_dataclass

from paths import AgentPaths
from terminal_ink_bridge import (
    InkCancelRequest,
    InkConfirmResponse,
    InkInputLine,
    TerminalInkBridge,
    resolve_cli_entry,
    translate_agent_event_to_ink,
)


class TerminalInkBridgeStage3Tests(unittest.TestCase):
    def test_terminal_repl_is_dataclass(self):
        from cli_terminal import TerminalRepl

        self.assertTrue(is_dataclass(TerminalRepl))

    def test_parse_input_line(self):
        parsed = TerminalInkBridge._parse_input({'type': 'input.line', 'text': '/clear'})
        self.assertEqual(parsed, InkInputLine('/clear'))

    def test_ink_is_enabled_on_windows_by_default(self):
        from cli_terminal import _ink_allowed_on_platform

        with mock.patch("cli_terminal.os.name", "nt"), mock.patch.dict(
            "os.environ", {}, clear=True
        ):
            self.assertTrue(_ink_allowed_on_platform())

    def test_ink_windows_opt_out(self):
        from cli_terminal import _ink_allowed_on_platform

        with mock.patch("cli_terminal.os.name", "nt"), mock.patch.dict(
            "os.environ", {"MY_AGENT_TERMINAL_INK_WINDOWS": "0"}, clear=True
        ):
            self.assertFalse(_ink_allowed_on_platform())

    def test_ink_allowed_on_non_windows(self):
        from cli_terminal import _ink_allowed_on_platform

        with mock.patch("cli_terminal.os.name", "posix"), mock.patch.dict(
            "os.environ", {}, clear=True
        ):
            self.assertTrue(_ink_allowed_on_platform())

    def test_invalid_messages_are_ignored(self):
        self.assertIsNone(TerminalInkBridge._parse_input({'type': 'input.line', 'text': 3}))
        self.assertIsNone(
            TerminalInkBridge._parse_input(
                {'type': 'confirm.response', 'request_id': 'r1', 'choice': 'maybe'}
            )
        )

    def test_confirm_translation(self):
        event = {
            'type': 'confirm.request',
            'request_id': 'r1',
            'preview': 'write file',
            'allow_approve_all': True,
        }
        self.assertEqual(translate_agent_event_to_ink(event), [event])

    def test_confirm_done_translation(self):
        self.assertEqual(
            translate_agent_event_to_ink(
                {'type': 'confirm.done', 'request_id': 'r1', 'choice': 'y'}
            ),
            [{'type': 'confirm.done', 'request_id': 'r1', 'choice': 'y'}],
        )

    def test_transcript_clear_translation(self):
        self.assertEqual(
            translate_agent_event_to_ink({'type': 'transcript.clear'}),
            [{'type': 'transcript.clear'}],
        )

    def test_reasoning_delta_translation(self):
        with mock.patch.dict(os.environ, {"MY_AGENT_TERMINAL_REASONING": "1"}, clear=False):
            self.assertEqual(
                translate_agent_event_to_ink({"type": "reasoning.delta", "text": "trace"}),
                [{"type": "reasoning.delta", "text": "trace"}],
            )

    def test_reasoning_delta_hidden_when_disabled(self):
        with mock.patch.dict(os.environ, {"MY_AGENT_TERMINAL_REASONING": "0"}, clear=False):
            self.assertEqual(translate_agent_event_to_ink({"type": "reasoning.delta", "text": "x"}), [])

    def test_tool_progress_translates_to_activity(self):
        self.assertEqual(
            translate_agent_event_to_ink(
                {
                    "type": "tool.progress",
                    "tool": "run_command",
                    "text": "仍在执行… 5s",
                }
            ),
            [{"type": "activity.update", "text": "run_command · 仍在执行… 5s"}],
        )

    def test_llm_pending_translates_to_activity(self):
        self.assertEqual(
            translate_agent_event_to_ink({"type": "llm.pending"}),
            [{"type": "activity.update", "text": "等待模型响应…"}],
        )

    def test_wait_confirm_skips_stale_response(self):
        bridge = TerminalInkBridge(paths=mock.Mock())
        bridge._inputs.put(InkConfirmResponse('stale', 'y'))
        bridge._inputs.put(InkConfirmResponse('current', 'n'))
        with mock.patch.object(bridge, 'emit_confirm_done') as done:
            choice = bridge.wait_confirm('current', False)
        self.assertEqual(choice, 'n')
        done.assert_called_once_with(request_id='current', choice='n')

    def test_resolve_cli_entry_prefers_live_source_over_dist(self):
        root = Path(__file__).resolve().parents[2]
        paths = AgentPaths.from_root(root)
        entry = resolve_cli_entry(paths)
        self.assertIsNotNone(entry)
        if entry is not None:
            _, entry_path = entry
            self.assertEqual(entry_path.suffix, ".tsx")
            self.assertTrue(entry_path.as_posix().endswith("terminal-ui/src/cli.tsx"))

    def test_resolve_cli_entry_can_force_dist(self):
        root = Path(__file__).resolve().parents[2]
        paths = AgentPaths.from_root(root)
        with mock.patch.dict(os.environ, {"MY_AGENT_TERMINAL_USE_DIST": "1"}, clear=False):
            entry = resolve_cli_entry(paths)
        self.assertIsNotNone(entry)
        if entry is not None:
            _, entry_path = entry
            self.assertEqual(entry_path.suffix, ".js")


if __name__ == '__main__':
    unittest.main()
