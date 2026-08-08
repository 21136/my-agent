"""Phase 57 · T-5710a · Terminal TUI transcript + tool panels (IT-577)."""

from __future__ import annotations

import io
import os
import sys
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from terminal_ui import (
    PlainTerminalBackend,
    TerminalConsole,
    TerminalEventSink,
    build_terminal_backend,
    build_welcome,
    format_status_bar_line,
    format_terminal_assistant_text,
    prompt_model_short,
    format_terminal_models_list,
    make_test_console,
    parse_terminal_model_command,
    parse_terminal_slash_command,
    reasoning_enabled,
    resolve_terminal_backend_kind,
    shorten_middle_path,
    tool_panel_text,
)


class TerminalUiModeTests(unittest.TestCase):
    def test_resolve_plain_when_forced(self) -> None:
        with mock.patch.dict("os.environ", {"MY_AGENT_TERMINAL_UI": "plain"}, clear=False):
            self.assertEqual(resolve_terminal_backend_kind(stdout=io.StringIO()), "plain")

    def test_resolve_plain_when_rich_import_missing(self) -> None:
        with mock.patch.dict("os.environ", {"MY_AGENT_TERMINAL_UI": "rich"}, clear=False):
            with mock.patch("terminal_ui._rich_import_ok", return_value=False):
                self.assertEqual(resolve_terminal_backend_kind(stdout=io.StringIO()), "plain")


class TerminalTranscriptFormatTests(unittest.TestCase):
    def test_format_strips_markdown_noise(self) -> None:
        raw = "## Title\n\n**bold** and `code`\n\n---\n- item"
        out = format_terminal_assistant_text(raw)
        self.assertIn("Title", out)
        self.assertNotIn("##", out)
        self.assertNotIn("**", out)
        self.assertIn("bold", out)
        self.assertIn("item", out)

    def test_format_code_block_box_in_plain_fallback(self) -> None:
        raw = "```python\nprint(1)\n```"
        with mock.patch("terminal_ui._rich_import_ok", return_value=False):
            out = format_terminal_assistant_text(raw)
        self.assertIn("python", out)
        self.assertIn("print(1)", out)
        self.assertIn("╭", out)

    def test_buffered_assistant_writes_formatted_block(self) -> None:
        lines: list[str] = []
        streamed: list[str] = []
        backend = PlainTerminalBackend(write=lines.append, stream_write=streamed.append)
        sink = TerminalEventSink(backend=backend)
        sink.emit({"type": "assistant.delta", "text": "## Hi\n**ok**"})
        sink.emit({"type": "assistant.done", "text": ""})
        self.assertEqual("".join(streamed), "")
        combined = "\n".join(lines)
        self.assertIn("Hi", combined)
        self.assertIn("ok", combined)
        self.assertIn("◆", combined)
        self.assertNotIn("**", combined)


class TerminalToolPanelTests(unittest.TestCase):
    def test_tool_panels_hidden_by_default(self) -> None:
        lines: list[str] = []
        backend = PlainTerminalBackend(write=lines.append)
        sink = TerminalEventSink(backend=backend)
        with mock.patch.dict(os.environ, {"MY_AGENT_TERMINAL_TOOL_PANELS": "0"}, clear=False):
            sink.emit(
                {
                    "type": "tool.start",
                    "tool": "read_file",
                    "call_id": "c-hidden",
                    "summary": "foo.py",
                }
            )
            sink.emit(
                {
                    "type": "tool.end",
                    "tool": "read_file",
                    "call_id": "c-hidden",
                    "ok": True,
                    "summary": "ok",
                }
            )
        self.assertEqual(lines, [])
        self.assertEqual(sink.state.tool_panels, {})

    def test_it_577_rich_tool_panel_shows_tool_name_and_checkmark(self) -> None:
        console, buffer = make_test_console(kind="rich")
        sink = console.sink

        with mock.patch.dict(os.environ, {"MY_AGENT_TERMINAL_TOOL_PANELS": "1"}, clear=False):
            sink.emit({"type": "turn.start", "intent": "execute", "intent_label": "执行"})
            sink.emit(
                {
                    "type": "tool.start",
                    "tool": "read_file",
                    "call_id": "c-577",
                    "summary": "workspace/huiyi/backend/pom.xml",
                }
            )
            sink.emit(
                {
                    "type": "tool.end",
                    "tool": "read_file",
                    "call_id": "c-577",
                    "ok": True,
                    "summary": "12 lines · 0.4s",
                }
            )

        panel = tool_panel_text(console.state, "c-577")
        self.assertIn("read_file", panel)
        self.assertIn("✓", panel)
        rendered = buffer.getvalue()
        self.assertIn("read_file", rendered)
        self.assertIn("✓", rendered)

    def test_it_577_plain_tool_panel_shows_tool_name_and_checkmark(self) -> None:
        lines: list[str] = []

        def write(text: str) -> None:
            lines.append(text)

        backend = PlainTerminalBackend(write=write)
        sink = TerminalEventSink(backend=backend)

        with mock.patch.dict(os.environ, {"MY_AGENT_TERMINAL_TOOL_PANELS": "1"}, clear=False):
            sink.emit(
                {
                    "type": "tool.start",
                    "tool": "run_command",
                    "call_id": "c-plain",
                    "summary": "pytest -q",
                }
            )
            sink.emit(
                {
                    "type": "tool.end",
                    "tool": "run_command",
                    "call_id": "c-plain",
                    "ok": True,
                    "summary": "passed",
                }
            )

        combined = "\n".join(lines)
        self.assertIn("run_command", combined)
        self.assertIn("✓", combined)
        panel = tool_panel_text(sink.state, "c-plain")
        self.assertIn("run_command", panel)
        self.assertIn("✓", panel)

    def test_tool_panel_failure_shows_cross(self) -> None:
        lines: list[str] = []
        backend = PlainTerminalBackend(write=lines.append)
        sink = TerminalEventSink(backend=backend)
        with mock.patch.dict(os.environ, {"MY_AGENT_TERMINAL_TOOL_PANELS": "1"}, clear=False):
            sink.emit(
                {
                    "type": "tool.end",
                    "tool": "write_text",
                    "call_id": "c-fail",
                    "ok": False,
                    "summary": "permission denied",
                }
            )
        combined = "\n".join(lines)
        self.assertIn("write_text", combined)
        self.assertIn("✗", combined)

    def test_assistant_delta_streams_then_done(self) -> None:
        lines: list[str] = []
        backend = PlainTerminalBackend(write=lines.append)
        sink = TerminalEventSink(backend=backend)
        sink.emit({"type": "assistant.delta", "text": "hello "})
        sink.emit({"type": "assistant.delta", "text": "world"})
        sink.emit({"type": "assistant.done", "text": ""})
        self.assertEqual(sink.state.assistant_text, "")

    def test_turn_start_and_end_events(self) -> None:
        lines: list[str] = []
        backend = PlainTerminalBackend(write=lines.append)
        sink = TerminalEventSink(backend=backend)
        sink.set_pending_user_text("list files")
        sink.emit({"type": "turn.start", "intent": "execute", "intent_label": "执行"})
        sink.emit({"type": "turn.end", "ok": False, "finish_reason": "cancelled"})
        combined = "\n".join(lines)
        self.assertIn("list files", combined)
        self.assertNotIn("执行", combined)
        self.assertIn("(stopped)", combined)

    def test_turn_separator_optional(self) -> None:
        lines: list[str] = []
        backend = PlainTerminalBackend(write=lines.append)
        sink = TerminalEventSink(backend=backend)
        with mock.patch.dict(os.environ, {"MY_AGENT_TERMINAL_TURN_SEP": "1"}, clear=False):
            sink.emit({"type": "turn.start", "intent": "execute", "intent_label": "执行"})
        combined = "\n".join(lines)
        self.assertIn("执行", combined)

    def test_executor_event_passthrough(self) -> None:
        lines: list[str] = []
        backend = PlainTerminalBackend(write=lines.append)
        sink = TerminalEventSink(backend=backend)
        with mock.patch.dict(os.environ, {"MY_AGENT_TERMINAL_TOOL_PANELS": "1"}, clear=False):
            sink.on_executor_event(
                "tool.start",
                {
                    "tool": "glob_file_search",
                    "call_id": "exec-1",
                    "summary": "*.py",
                },
            )
            sink.on_executor_event(
                "tool.end",
                {
                    "tool": "glob_file_search",
                    "call_id": "exec-1",
                    "ok": True,
                    "summary": "3 files",
                },
            )
        combined = "\n".join(lines)
        self.assertIn("glob_file_search", combined)
        self.assertIn("✓", combined)


class TerminalReplWireTests(unittest.TestCase):
    def test_terminal_repl_wires_console_and_stream_handlers(self) -> None:
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
        scope = TerminalScopeFields(terminal_scope_kind="agent", terminal_cwd="workspace/huiyi")
        repl = TerminalRepl.from_terminal_session(
            session,
            paths=paths,
            scope_fields=scope,
            input_fn=lambda _prompt: "",
            output_fn=lambda _text: None,
        )
        self.assertIsNotNone(repl.terminal_console)
        self.assertIsNotNone(repl.stream_handlers)
        assert repl.terminal_console is not None
        sink = repl.terminal_console.sink
        self.assertIs(repl.agent.on_turn_event.__self__, sink)
        self.assertIs(repl.agent.executor.on_event.__self__, sink)
        self.assertIsNotNone(repl.assistant_output_fn)

    def test_terminal_console_stream_handlers_emit_delta(self) -> None:
        console = TerminalConsole.create(kind="plain")
        handlers = console.sink.stream_handlers()
        handlers.on_content_delta("chunk")
        self.assertEqual(console.state.assistant_text, "chunk")


class TerminalWelcomeStatusBarTests(unittest.TestCase):
    def test_it_578_build_welcome_contains_required_fields(self) -> None:
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
        session.meta.llm_model = "deepseek-v4-pro"
        scope = TerminalScopeFields(terminal_scope_kind="agent", terminal_cwd="workspace/huiyi")

        panel = build_welcome(session=session, paths=paths, scope_fields=scope)
        self.assertIn("huiyi", panel.effective_root)
        self.assertTrue(Path(panel.effective_root).is_absolute())
        self.assertEqual(panel.llm_model, "deepseek-v4-pro")
        self.assertEqual(panel.terminal_scope_kind, "agent")
        self.assertIn("huiyi", panel.left_lines[2])
        self.assertEqual(panel.workspace_name, "huiyi")

    def test_welcome_cwd_dot_uses_absolute_root(self) -> None:
        from session import create_terminal_session
        from terminal_scope import TerminalScopeFields
        from tests.isolation_helpers import make_temp_agent_paths

        paths = make_temp_agent_paths(self)
        session = create_terminal_session(
            paths,
            terminal_scope_kind="agent",
            terminal_cwd=".",
        )
        scope = TerminalScopeFields(terminal_scope_kind="agent", terminal_cwd=".")
        panel = build_welcome(session=session, paths=paths, scope_fields=scope)
        self.assertIn(paths.agent_root.resolve().as_posix(), panel.left_lines[2])

    def test_compact_tip_line_truncates_changelog_noise(self) -> None:
        from pathlib import Path

        from terminal_ui import _compact_tip_line, load_whats_new_lines

        long = "**BUG-025 fixed**：" + "x" * 120
        short = _compact_tip_line(long, max_len=48)
        self.assertLessEqual(len(short), 48)
        self.assertTrue(short.endswith("…"))
        changelog = Path(__file__).resolve().parents[2] / "docs" / "CHANGELOG.md"
        tips = load_whats_new_lines(changelog)
        self.assertLessEqual(len(tips), 6)
        for tip in tips[1:]:
            self.assertLessEqual(len(tip), 72)

    def test_shorten_middle_path_never_dot_only(self) -> None:
        long_path = "D:/my-agent/workspace/huiyi"
        short = shorten_middle_path(long_path, max_len=20)
        self.assertNotEqual(short, ".")
        self.assertIn("huiyi", short)
        self.assertIn("…", short)

    def test_status_bar_format_idle(self) -> None:
        from terminal_ui import StatusBarContent

        line = format_status_bar_line(
            StatusBarContent(
                llm_model="deepseek-v4-pro",
                turn_mode="agent",
                root_short="huiyi",
                session_suffix="…c270a524",
                status="idle",
            )
        )
        self.assertIn("deepseek-v4-pro", line)
        self.assertIn("huiyi", line)
        self.assertIn("就绪", line)
        self.assertIn("●", line)

    def test_prompt_model_short(self) -> None:
        self.assertEqual(prompt_model_short("deepseek-v4-flash"), "flash")
        self.assertEqual(prompt_model_short("deepseek-v4-pro"), "pro")
        self.assertEqual(prompt_model_short("sophnet-deepseek-v4-flash"), "flash")

    def test_turn_start_sets_working_status(self) -> None:
        console = TerminalConsole.create(kind="plain")
        console.sink.emit({"type": "turn.start", "intent": "execute", "intent_label": "执行"})
        self.assertEqual(console._status, "working")
        console.sink.emit({"type": "turn.end", "ok": True, "finish_reason": "completed"})
        self.assertEqual(console._status, "idle")

    def test_rich_welcome_renders_scope_and_workspace(self) -> None:
        from session import create_terminal_session
        from terminal_scope import TerminalScopeFields
        from tests.isolation_helpers import make_temp_agent_paths

        paths = make_temp_agent_paths(self)
        repo = paths.workspace / "huiyi"
        repo.mkdir(parents=True)
        session = create_terminal_session(
            paths,
            terminal_scope_kind="host",
            terminal_cwd="huiyi",
            terminal_host_id="projects",
        )
        session.meta.llm_model = "test-model"
        scope = TerminalScopeFields(
            terminal_scope_kind="host",
            terminal_cwd="huiyi",
            terminal_host_id="projects",
        )
        console, buffer = make_test_console(kind="rich")
        console.bind_context(session, paths, scope)
        panel = console.show_welcome(resume=True)
        assert panel is not None
        rendered = buffer.getvalue()
        self.assertIn(panel.workspace_name, rendered)
        self.assertIn(panel.effective_root.split("/")[-1], rendered)
        self.assertNotIn("test-model", rendered)

    def test_rich_refresh_welcome_is_noop(self) -> None:
        from terminal_ui import RichTerminalBackend, WelcomeContent

        backend = RichTerminalBackend(write=lambda _text: None)
        panel = WelcomeContent(
            effective_root="D:/my-agent",
            llm_model="deepseek-v4-pro",
            terminal_scope_kind="agent",
            harness="terminal",
            terminal_cwd=".",
            session_id="s1",
            resume=True,
            workspace_name="my-agent",
            left_lines=("欢迎回来", "my-agent", "D:/my-agent"),
            right_lines=("/model",),
        )
        backend.refresh_welcome(panel)

    def test_locate_welcome_model_line(self) -> None:
        from terminal_ui import locate_welcome_model_line

        rendered = "│ line │\n│   deepseek-v4-flash │"
        located = locate_welcome_model_line(rendered, "deepseek-v4-flash")
        assert located is not None
        row, prefix, suffix = located
        self.assertEqual(row, 2)
        self.assertEqual(f"{prefix}deepseek-v4-flash{suffix}", "│   deepseek-v4-flash │")

    def test_plain_banner_uses_absolute_root(self) -> None:
        from session import create_terminal_session
        from tests.isolation_helpers import make_temp_agent_paths

        paths = make_temp_agent_paths(self)
        repo = paths.workspace / "huiyi"
        repo.mkdir(parents=True)
        session = create_terminal_session(
            paths,
            terminal_scope_kind="agent",
            terminal_cwd="workspace/huiyi",
        )
        console = TerminalConsole.create(kind="plain", session=session, paths=paths)
        banner = console.plain_banner_line()
        self.assertIn(repo.resolve().as_posix(), banner)
        self.assertNotIn(" · . ·", banner)


class TerminalSlashAndPlainTests(unittest.TestCase):
    def test_parse_terminal_slash_commands(self) -> None:
        self.assertEqual(parse_terminal_slash_command("/clear"), "clear")
        self.assertEqual(parse_terminal_slash_command("/compact"), "compact")
        self.assertEqual(parse_terminal_slash_command("/compress"), "compact")
        self.assertIsNone(parse_terminal_slash_command("hello"))

    def test_parse_terminal_model_command(self) -> None:
        self.assertEqual(parse_terminal_model_command("/model"), "")
        self.assertEqual(parse_terminal_model_command("/model pro"), "pro")
        self.assertEqual(parse_terminal_model_command("/MODEL flash"), "flash")
        self.assertIsNone(parse_terminal_model_command("/clear"))

    def test_format_terminal_models_list_marks_current(self) -> None:
        from tests.isolation_helpers import make_temp_agent_paths

        paths = make_temp_agent_paths(self)
        lines = format_terminal_models_list(paths, current_model="deepseek-v4-flash")
        joined = "\n".join(lines)
        self.assertIn("可用模型", joined)
        self.assertIn("→ deepseek-v4-flash", joined)
        self.assertIn("/model flash", joined)

    def test_clear_transcript_resets_state(self) -> None:
        console = TerminalConsole.create(kind="plain")
        console.sink.emit({"type": "assistant.delta", "text": "hello"})
        self.assertTrue(console.state.lines or console.state.assistant_text)
        console.clear_transcript()
        self.assertEqual(console.state.lines, ["— transcript cleared —"])
        self.assertEqual(console.state.assistant_text, "")

    def test_it_579_plain_mode_without_rich_import(self) -> None:
        from session import create_terminal_session
        from tests.isolation_helpers import make_temp_agent_paths

        paths = make_temp_agent_paths(self)
        repo = paths.workspace / "huiyi"
        repo.mkdir(parents=True)
        session = create_terminal_session(
            paths,
            terminal_scope_kind="agent",
            terminal_cwd="workspace/huiyi",
        )
        with mock.patch.dict(
            os.environ,
            {"MY_AGENT_TERMINAL_UI": "plain", "MY_AGENT_TERMINAL_WELCOME": "0"},
            clear=False,
        ):
            with mock.patch("terminal_ui._rich_import_ok", return_value=False):
                self.assertEqual(resolve_terminal_backend_kind(stdout=io.StringIO()), "plain")
                console = TerminalConsole.create(session=session, paths=paths)
        self.assertEqual(console.backend_kind, "plain")
        banner = console.plain_banner_line()
        self.assertIn(repo.resolve().as_posix(), banner)

    def test_it_579_terminal_repl_plain_startup_banner(self) -> None:
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
        scope = TerminalScopeFields(terminal_scope_kind="agent", terminal_cwd="workspace/huiyi")
        with mock.patch.dict(
            os.environ,
            {"MY_AGENT_TERMINAL_UI": "plain", "MY_AGENT_TERMINAL_WELCOME": "0"},
            clear=False,
        ):
            with mock.patch("terminal_ui._rich_import_ok", return_value=False):
                repl = TerminalRepl.from_terminal_session(
                    session,
                    paths=paths,
                    scope_fields=scope,
                    input_fn=lambda _prompt: "",
                    output_fn=lambda _text: None,
                )
        assert repl.terminal_console is not None
        banner = repl.terminal_console.plain_banner_line()
        self.assertIn(repo.resolve().as_posix(), banner)
        self.assertEqual(repl.terminal_console.input_prompt(), "you> ")

    def test_reasoning_delta_hidden_by_default(self) -> None:
        lines: list[str] = []
        backend = PlainTerminalBackend(write=lines.append)
        sink = TerminalEventSink(backend=backend)
        with mock.patch.dict(os.environ, {"MY_AGENT_TERMINAL_REASONING": "0"}, clear=False):
            sink.emit({"type": "reasoning.delta", "text": "think"})
        self.assertEqual(lines, [])

    def test_reasoning_delta_rendered_when_enabled(self) -> None:
        lines: list[str] = []
        streamed: list[str] = []
        backend = PlainTerminalBackend(write=lines.append, stream_write=streamed.append)
        sink = TerminalEventSink(backend=backend)
        with mock.patch.dict(os.environ, {"MY_AGENT_TERMINAL_REASONING": "1"}, clear=False):
            self.assertTrue(reasoning_enabled())
            sink.emit({"type": "reasoning.delta", "text": "think"})
            sink.emit({"type": "assistant.done", "text": ""})
        self.assertIn("思考", "\n".join(lines))
        self.assertEqual("".join(streamed), "think\n")
        self.assertEqual(sink.state.reasoning_text, "think")

    def test_assistant_delta_uses_stream_write_when_provided(self) -> None:
        lines: list[str] = []
        streamed: list[str] = []
        backend = PlainTerminalBackend(write=lines.append, stream_write=streamed.append)
        sink = TerminalEventSink(backend=backend)
        with mock.patch.dict(os.environ, {"MY_AGENT_TERMINAL_MARKDOWN": "0"}, clear=False):
            sink.emit({"type": "assistant.delta", "text": "hi"})
            sink.emit({"type": "assistant.done", "text": ""})
        self.assertEqual("".join(streamed), "  hi\n")

    def test_terminal_compact_command(self) -> None:
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
        scope = TerminalScopeFields(terminal_scope_kind="agent", terminal_cwd="workspace/huiyi")
        repl = TerminalRepl.from_terminal_session(
            session,
            paths=paths,
            scope_fields=scope,
            input_fn=lambda _prompt: "",
            output_fn=lambda _text: None,
        )
        assert repl.terminal_console is not None

        class _CompactResult:
            message = "compressed ok"

        with mock.patch("context.compact_context", return_value=_CompactResult()) as compact_mock:
            outcome = repl.handle_line("/compact")
        self.assertEqual(outcome, "continue")
        compact_mock.assert_called_once()
        combined = "\n".join(repl.terminal_console.captured_lines)
        self.assertIn("compressed ok", combined)

    def test_terminal_model_list_command(self) -> None:
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
        scope = TerminalScopeFields(terminal_scope_kind="agent", terminal_cwd="workspace/huiyi")
        repl = TerminalRepl.from_terminal_session(
            session,
            paths=paths,
            scope_fields=scope,
            input_fn=lambda _prompt: "",
            output_fn=lambda _text: None,
        )
        assert repl.terminal_console is not None
        outcome = repl.handle_line("/model")
        self.assertEqual(outcome, "continue")
        combined = "\n".join(repl.terminal_console.captured_lines)
        self.assertIn("可用模型", combined)
        self.assertIn("deepseek-v4-flash", combined)

    def test_model_switch_does_not_append_second_footer(self) -> None:
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
        repl.terminal_console.begin_prompt_cycle()
        before = len(repl.terminal_console.captured_lines)
        with mock.patch.object(repl.terminal_console, "patch_prompt_footer", return_value=True):
            repl.handle_line("/model pro")
        after = len(repl.terminal_console.captured_lines)
        new_lines = "\n".join(repl.terminal_console.captured_lines[before:])
        self.assertIn("模型 →", new_lines)
        # One meta notice is fine; must not reprint another status footer.
        self.assertNotIn("就绪", new_lines)
        self.assertEqual(after, before + 1)

    def test_terminal_model_switch_command(self) -> None:
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
        outcome = repl.handle_line("/model pro")
        self.assertEqual(outcome, "continue")
        self.assertEqual(repl.session.meta.llm_model, "deepseek-v4-pro")
        with mock.patch.object(repl.terminal_console, "patch_prompt_footer", return_value=True):
            repl.terminal_console.patch_prompt_footer()
        repl.terminal_console.begin_prompt_cycle()
        combined = "\n".join(repl.terminal_console.captured_lines)
        self.assertIn("deepseek-v4-pro", combined)


if __name__ == "__main__":
    unittest.main()
