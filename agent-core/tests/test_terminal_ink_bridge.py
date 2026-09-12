import io
import os
import queue
import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dataclasses import is_dataclass

from paths import AgentPaths
from terminal_ink_bridge import (
    InkCancelRequest,
    InkConfirmResponse,
    InkInputLine,
    TerminalInkBridge,
    TerminalInkConsole,
    TerminalInkEventSink,
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

    def test_ink_console_bind_context_refreshes_child_session(self):
        bridge = mock.Mock()
        console = TerminalInkConsole(
            sink=mock.Mock(),
            bridge=bridge,
            session=mock.sentinel.old_session,
            paths=mock.sentinel.old_paths,
            scope_fields=mock.sentinel.old_scope,
        )
        session = mock.sentinel.new_session
        paths = mock.sentinel.new_paths
        scope_fields = mock.sentinel.new_scope

        console.bind_context(session, paths, scope_fields)

        self.assertIs(console.session, session)
        self.assertIs(console.paths, paths)
        self.assertIs(console.scope_fields, scope_fields)
        bridge.emit_session_init.assert_called_once_with(
            session=session,
            scope_fields=scope_fields,
            resume=True,
        )

    def test_session_init_payload_includes_model_catalog(self):
        from terminal_ink_bridge import session_init_payload

        session = mock.Mock()
        session.meta.llm_model = "deepseek-v4-flash"
        paths = AgentPaths.from_root(Path(__file__).resolve().parents[2])
        payload = session_init_payload(
            session=session,
            paths=paths,
            scope_fields=mock.Mock(),
            resume=False,
        )
        self.assertIn("models", payload)
        models = payload["models"]
        self.assertIsInstance(models, list)
        self.assertGreater(len(models), 0)
        self.assertIn("id", models[0])
        self.assertIn("name", models[0])

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

    def test_error_translation_preserves_failure_level(self):
        self.assertEqual(
            translate_agent_event_to_ink({"type": "error", "message": "provider down"}),
            [{"type": "notice", "level": "error", "text": "provider down"}],
        )

    def test_cancel_input_is_dispatched_without_blocking_turn_reader(self):
        bridge = TerminalInkBridge(paths=mock.Mock())
        bridge._stdout = io.StringIO('{"type":"turn.cancel"}\n')
        listener = mock.Mock()
        bridge.cancel_listener = listener

        bridge._read_inputs()

        listener.assert_called_once_with()
        self.assertTrue(bridge._cancel_requested.is_set())

    def test_next_input_returns_none_when_ink_child_exits(self):
        bridge = TerminalInkBridge(paths=mock.Mock())
        proc = mock.Mock()
        proc.poll.return_value = 0
        bridge.process = proc

        message = bridge.next_input(timeout=0.05)

        self.assertIsNone(message)

    def test_signal_shutdown_unblocks_next_input(self):
        bridge = TerminalInkBridge(paths=mock.Mock())
        bridge._inputs.put = mock.Mock(side_effect=queue.Full)

        bridge.signal_shutdown()

        bridge._inputs.put.assert_called_once_with(None, block=False)

    def test_wait_confirm_skips_stale_response(self):
        bridge = TerminalInkBridge(paths=mock.Mock())
        bridge._inputs.put(InkConfirmResponse('stale', 'y'))
        bridge._inputs.put(InkConfirmResponse('current', 'n'))
        with mock.patch.object(bridge, 'emit_confirm_done') as done:
            choice = bridge.wait_confirm('current', False)
        self.assertEqual(choice, 'n')
        done.assert_called_once_with(request_id='current', choice='n')

    def test_terminal_repl_confirmation_routes_through_ink_console(self):
        from cli_terminal import TerminalRepl

        bridge = TerminalInkBridge(paths=mock.Mock())
        bridge.emit_confirm_request = mock.Mock(return_value='request-1')
        bridge.wait_confirm = mock.Mock(return_value='y')
        console = TerminalInkConsole(sink=mock.Mock(), bridge=bridge)
        repl = SimpleNamespace(_ink_bridge=bridge, terminal_console=console)

        choice = TerminalRepl._ink_bridge_confirm(repl, 'write file', True)

        self.assertEqual(choice, 'y')
        bridge.emit_confirm_request.assert_called_once_with(
            preview='write file', allow_approve_all=True
        )
        bridge.wait_confirm.assert_called_once_with('request-1', True)

    def test_execution_state_translation_preserves_lifecycle_payload(self):
        event = {
            "type": "execution.state",
            "run_id": "run-1",
            "state": "stopping",
            "sequence": 2,
            "cancel_requested": True,
        }
        self.assertEqual(
            translate_agent_event_to_ink(event),
            [event, {"type": "status.working", "active": True, "run_id": "run-1"}],
        )

    def test_turn_start_translation_preserves_run_id_for_stale_filtering(self):
        event = {
            "type": "turn.start",
            "run_id": "run-1",
            "turnKey": "turn-1",
        }
        self.assertEqual(
            translate_agent_event_to_ink(event),
            [
                {"type": "turn.start", "turnKey": "turn-1", "run_id": "run-1"},
                {"type": "status.working", "active": True, "run_id": "run-1"},
            ],
        )

    def test_turn_end_translation_preserves_run_id_for_stale_filtering(self):
        event = {
            "type": "turn.end",
            "run_id": "run-1",
            "ok": False,
            "finish_reason": "cancelled",
        }
        self.assertEqual(
            translate_agent_event_to_ink(event),
            [
                {"type": "tool.clear", "run_id": "run-1"},
                {"type": "status.working", "active": False, "run_id": "run-1"},
            ],
        )

    def test_ink_sink_attaches_active_run_id_to_turn_events(self):
        bridge = mock.Mock()
        sink = TerminalInkEventSink(bridge=bridge)

        sink.emit(
            {
                "type": "execution.state",
                "run_id": "run-1",
                "state": "running",
                "sequence": 1,
            }
        )
        sink.emit({"type": "turn.start", "intent": "execute", "intent_label": "执行"})
        sink.emit({"type": "turn.end", "ok": True, "finish_reason": "completed"})

        events = [call.args[0] for call in bridge.write_event.call_args_list]
        self.assertEqual(
            [event["type"] for event in events],
            ["execution.state", "status.working", "turn.start", "status.working", "tool.clear", "status.working"],
        )
        self.assertTrue(all(event.get("run_id") == "run-1" for event in events))

    def test_terminal_repl_emits_execution_state_around_agent_turn(self):
        from cli_terminal import TerminalRepl
        from execution_lifecycle import ExecutionLifecycle

        sink = mock.Mock()
        console = TerminalInkConsole(sink=sink, bridge=mock.Mock())
        repl = object.__new__(TerminalRepl)
        repl.execution_lifecycle = ExecutionLifecycle(
            run_id_factory=iter(["terminal-run-1"]).__next__
        )
        repl.terminal_console = console
        repl._turn_cancel_guard = None
        repl.agent = SimpleNamespace(
            cancel_event=threading.Event(),
            run_turn=lambda _text: SimpleNamespace(finish_reason="completed"),
        )

        TerminalRepl._run_agent_turn(repl, "hello")

        lifecycle_events = [
            call.args[0]
            for call in sink.emit.call_args_list
            if call.args and call.args[0].get("type") == "execution.state"
        ]
        self.assertEqual([event["state"] for event in lifecycle_events], ["running", "settled"])

    def test_terminal_repl_marks_returned_cancelled_result_unsuccessful(self):
        from cli_terminal import TerminalRepl
        from execution_lifecycle import ExecutionLifecycle

        sink = mock.Mock()
        console = TerminalInkConsole(sink=sink, bridge=mock.Mock())
        repl = object.__new__(TerminalRepl)
        repl.execution_lifecycle = ExecutionLifecycle(
            run_id_factory=iter(["terminal-run-cancelled"]).__next__
        )
        repl.terminal_console = console
        repl._turn_cancel_guard = None
        repl.agent = SimpleNamespace(
            cancel_event=threading.Event(),
            run_turn=lambda _text: SimpleNamespace(finish_reason="cancelled"),
        )

        TerminalRepl._run_agent_turn(repl, "stop")

        finals = [
            call.args[0]
            for call in sink.emit.call_args_list
            if call.args
            and call.args[0].get("type") == "execution.state"
            and call.args[0].get("state") in {"settled", "paused", "failed"}
        ]
        self.assertEqual(len(finals), 1)
        self.assertFalse(finals[0]["ok"])
        self.assertEqual(finals[0]["finish_reason"], "cancelled")

    def test_close_terminates_process_before_closing_stdout(self):
        bridge = TerminalInkBridge(paths=mock.Mock())
        process = mock.Mock()
        process.pid = 123
        process.poll.return_value = None
        process.wait.return_value = None
        process.terminated = False
        stdout = mock.Mock()

        def close_stdout():
            self.assertTrue(process.terminated)

        stdout.close.side_effect = close_stdout
        bridge.process = process
        bridge._stdout = stdout

        with mock.patch('terminal_ink_bridge._terminate_process_tree', create=True) as terminate:
            terminate.side_effect = lambda proc: setattr(proc, 'terminated', True)
            bridge.close()

        terminate.assert_called_once_with(process)
        stdout.close.assert_called_once_with()

    def test_start_failure_cleans_up_spawned_process(self):
        from terminal_ink_bridge import TerminalInkBridge

        process = mock.Mock()
        process.poll.return_value = None
        process.stdout = io.StringIO()
        process.wait.return_value = None
        paths = AgentPaths.from_root(Path(__file__).resolve().parents[2])

        with mock.patch('terminal_ink_bridge.resolve_cli_entry', return_value=(['fake-node'], Path('fake.js'))), mock.patch(
            'terminal_ink_bridge.subprocess.Popen', return_value=process
        ), mock.patch('terminal_ink_bridge.time.monotonic', side_effect=[0.0, 11.0]), mock.patch(
            'terminal_ink_bridge._terminate_process_tree', create=True
        ) as terminate:
            with self.assertRaises(RuntimeError):
                TerminalInkBridge.start(paths)

        terminate.assert_called_once_with(process)

    def test_ink_requires_interactive_tty(self):
        from cli_terminal import TerminalRepl
        from session import create_terminal_session
        from terminal_scope import TerminalScopeFields
        from tests.isolation_helpers import make_temp_agent_paths

        paths = make_temp_agent_paths(self)
        session = create_terminal_session(paths, terminal_scope_kind='agent', terminal_cwd='.')
        scope = TerminalScopeFields(terminal_scope_kind='agent', terminal_cwd='.')

        with mock.patch('cli_terminal.ink_ui_enabled', return_value=True), mock.patch(
            'cli_terminal._ink_allowed_on_platform', return_value=True
        ), mock.patch('cli_terminal.TerminalInkBridge.start') as start, mock.patch(
            'cli_terminal.sys.stdin.isatty', return_value=False
        ), mock.patch('cli_terminal.sys.stderr.isatty', return_value=False), mock.patch(
            'cli_terminal.prompt_toolkit_enabled', return_value=False
        ), mock.patch('cli_terminal.bottom_layout_enabled', return_value=False):
            repl = TerminalRepl.from_terminal_session(
                session,
                paths=paths,
                scope_fields=scope,
                input_fn=None,
                output_fn=lambda _text: None,
            )

        start.assert_not_called()
        self.assertIsNone(repl._ink_bridge)

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
