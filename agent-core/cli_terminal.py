"""Terminal harness REPL entry (TERMINAL-MODE §6 · T-5702)."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parent
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from agent import Agent, LLMError, ToolLoopExceededError
from boundaries import UserLineKind, classify_user_line
from host_scope_cli import HostScopeCommandError, parse_host_scope_command, run_host_scope_command
from interface_lock import InterfaceLockError, InterfaceLockGuard, read_lock
from llm_client import LLMCancelledError
from main import (
    ConversationRepl,
    ReplConfig,
    ReplOutcome,
    ReplTurnCancelGuard,
    make_confirm_fn,
    parse_exit_record_mode,
)
from paths import AgentPaths
from project_cli import ProjectCommandError, parse_project_command, try_short_plan_confirm
from session import (
    Session,
    create_terminal_session,
    parse_turn_mode_command,
    resume_terminal_session,
)
from terminal_scope import (
    TerminalScopeError,
    TerminalScopeFields,
    TerminalStartupDenied,
    TerminalStartupNeedsPrompt,
    apply_r3_choice,
    classify_terminal_startup,
    format_r3_prompt,
    resolve_terminal_cwd_candidate,
    resolve_terminal_effective_root,
    scope_fields_from_meta,
    scope_fields_to_session_kwargs,
)
from typing import Any

from terminal_ui import (
    TERMINAL_COMMANDS_LINE,
    TerminalConsole,
    build_welcome,
    format_terminal_models_list,
    format_welcome_plain,
    parse_terminal_model_command,
    parse_terminal_slash_command,
    welcome_enabled,
)
from terminal_prompt import TerminalPromptSession, prompt_toolkit_enabled
from terminal_app import BottomPinnedTerminal, bottom_layout_enabled, make_bottom_confirm_input_fn
from terminal_ink_bridge import (
    TerminalInkBridge,
    TerminalInkConsole,
    InkCancelRequest,
    InkConfirmResponse,
    InkInputLine,
    ink_ui_enabled,
)

InputFn = Callable[[str], str]
OutputFn = Callable[[str], None]

_TURN_MODE_NOTICE = "Terminal 仅 agent 模式"
_PROJECT_NOTICE = "Terminal 不支持项目命令。"


def _ink_allowed_on_platform() -> bool:
    """Ink is default on all platforms; set ``MY_AGENT_TERMINAL_INK_WINDOWS=0`` to force legacy on Windows."""
    if os.name != "nt":
        return True
    raw = os.environ.get("MY_AGENT_TERMINAL_INK_WINDOWS", "1").strip().casefold()
    return raw not in {"0", "false", "no", "off"}


@dataclass
class TerminalRepl(ConversationRepl):
    scope_fields: TerminalScopeFields = field(
        default_factory=lambda: TerminalScopeFields(terminal_scope_kind="agent", terminal_cwd=".")
    )
    _turn_cancel_guard: ReplTurnCancelGuard | None = field(default=None, repr=False)
    terminal_console: TerminalConsole | TerminalInkConsole | None = field(default=None, repr=False)
    _prompt_session: TerminalPromptSession | None = field(default=None, repr=False)
    _bottom_terminal: BottomPinnedTerminal | None = field(default=None, repr=False)
    _ink_bridge: TerminalInkBridge | None = field(default=None, repr=False)
    _ink_pending_confirm: tuple[str, Callable[[str], None]] | None = field(default=None, repr=False)
    _ink_confirm_allow_all: bool = field(default=False, repr=False)
    _tty_reader: Any | None = field(default=None, repr=False)
    _uses_builtin_input: bool = field(default=True, repr=False)

    @classmethod
    def from_terminal_session(
        cls,
        session: Session,
        *,
        paths: AgentPaths,
        scope_fields: TerminalScopeFields,
        input_fn: InputFn | None = None,
        output_fn: OutputFn | None = None,
    ) -> TerminalRepl:
        repl = cls.from_session(
            session,
            paths=paths,
            input_fn=input_fn,
            output_fn=output_fn,
            config=ReplConfig(),
        )
        repl.scope_fields = scope_fields
        repl._turn_cancel_guard = ReplTurnCancelGuard(repl)
        repl._uses_builtin_input = input_fn is None
        use_ink = (
            repl._uses_builtin_input
            and ink_ui_enabled(paths=paths)
            and _ink_allowed_on_platform()
        )
        if not use_ink and repl._uses_builtin_input and os.name == "nt":
            if os.environ.get("MY_AGENT_TERMINAL_INK_WINDOWS", "1").strip().casefold() in {
                "0",
                "false",
                "no",
                "off",
            }:
                print(
                    "提示: MY_AGENT_TERMINAL_INK_WINDOWS=0，已使用兼容输入模式（legacy）。",
                    file=sys.stderr,
                )
            elif not ink_ui_enabled(paths=paths):
                print(
                    "提示: Ink UI 不可用（需 terminal-ui 已 build）；已回退 legacy。",
                    file=sys.stderr,
                )
        if use_ink:
            try:
                bridge = TerminalInkBridge.start(paths)
            except (FileNotFoundError, RuntimeError) as exc:
                print(f"warn: Ink UI unavailable ({exc}); falling back to legacy", file=sys.stderr)
                use_ink = False
        if use_ink:
            console = TerminalInkConsole.create(
                bridge=bridge,
                session=session,
                paths=paths,
                scope_fields=scope_fields,
            )
            bridge.emit_session_init(
                session=session,
                scope_fields=scope_fields,
                resume=True,
            )
            repl._ink_bridge = bridge
            repl._tty_reader = None
            repl.terminal_console = console
            repl.output_fn = console.output_fn
            repl.input_fn = lambda _prompt: ""
        elif (
            repl._uses_builtin_input
            and prompt_toolkit_enabled()
            and bottom_layout_enabled()
        ):
            scaffold = TerminalConsole.create(
                write=None,
                session=session,
                paths=paths,
                scope_fields=scope_fields,
            )
            bottom = BottomPinnedTerminal(scaffold)
            console = TerminalConsole.create(
                write=bottom.write,
                stream_write=bottom.append_transcript_raw,
                stream_begin=bottom.begin_assistant_stream,
                stream_finalize=bottom.finalize_assistant_stream,
                kind="plain",
                session=session,
                paths=paths,
                scope_fields=scope_fields,
            )
            bottom.bind_console(console)
            repl._bottom_terminal = bottom
            repl.terminal_console = console
            repl.output_fn = console.output_fn
            repl.input_fn = make_bottom_confirm_input_fn(bottom)
        else:
            console = TerminalConsole.create(
                write=repl.output_fn,
                session=session,
                paths=paths,
                scope_fields=scope_fields,
            )
            repl.terminal_console = console
            repl.output_fn = console.output_fn
            if (
                repl._uses_builtin_input
                and console.backend_kind == "rich"
                and prompt_toolkit_enabled()
            ):
                repl._prompt_session = TerminalPromptSession(console)
            elif repl._uses_builtin_input and console.backend_kind == "rich":
                console.emit_meta_notice(
                    "提示: pip install prompt_toolkit 以启用 Claude 式输入框与底栏"
                )
        repl._rebind_agent()
        return repl

    def run(self) -> int:
        guard = self._turn_cancel_guard
        if guard is not None:
            guard.install()
        try:
            if self._ink_bridge is not None:
                return self._run_ink_layout_loop()
            if self._bottom_terminal is not None:
                return self._run_bottom_layout_loop()
            return self._run_terminal_loop()
        finally:
            if guard is not None:
                guard.uninstall()
            if self._ink_bridge is not None:
                self._ink_bridge.close()
                self._ink_bridge = None
            if self._tty_reader is not None:
                try:
                    self._tty_reader.close()
                except OSError:
                    pass
                self._tty_reader = None

    def _run_ink_layout_loop(self) -> int:
        """Consume interactive JSONL messages from Ink and run terminal commands."""
        self._log_session_start()
        self._print_corruption_notices()
        bridge = self._ink_bridge
        if bridge is None:
            return self._run_terminal_loop_legacy()

        while not self._stop:
            try:
                message = bridge.next_input()
            except KeyboardInterrupt:
                self._checkpoint_gate.on_keyboard_interrupt()
                self._stop = True
                break
            if message is None:
                self._exit_session(record_mode="off")
                break
            if isinstance(message, InkInputLine):
                line = message.text.rstrip("\r\n")
                if line.strip() and self.handle_line(line) == "stop":
                    break
            elif isinstance(message, InkConfirmResponse):
                self._deliver_ink_confirm(message)
            elif isinstance(message, InkCancelRequest):
                guard = self._turn_cancel_guard
                if guard is not None and guard.request_cancel():
                    self.output_fn("(cancelling turn…)")

        if not self._stop:
            self._exit_session(record_mode="off")
        return 0

    def _deliver_ink_confirm(self, message: InkConfirmResponse) -> None:
        bridge = self._ink_bridge
        pending = getattr(self, "_ink_pending_confirm", None)
        if bridge is None or pending is None:
            return
        request_id, resolver = pending
        if message.request_id != request_id:
            return
        choice = message.choice
        if choice == "a" and not getattr(self, "_ink_confirm_allow_all", False):
            choice = "n"
        self._ink_pending_confirm = None
        resolver(choice)
        bridge.emit_confirm_done(request_id=request_id, choice=choice)

    def _run_terminal_loop(self) -> int:
        return self._run_terminal_loop_legacy()

    def _run_terminal_loop_legacy(self) -> int:
        self._log_session_start()
        if self._bottom_terminal is not None:
            return self._run_bottom_layout_loop()
        self._print_terminal_banner()
        if self.terminal_console is None or self.terminal_console.backend_kind != "rich":
            self.output_fn(TERMINAL_COMMANDS_LINE)
        self.output_fn(
            "提示: 当前为 scroll 布局（原生滚轮/选中）。"
            "欢迎页+钉底输入: set MY_AGENT_TERMINAL_LAYOUT=bottom"
        )

        while not self._stop:
            self._checkpoint_gate.begin_line()
            try:
                if self._prompt_session is not None:
                    line = self._prompt_session.read_line()
                else:
                    if self.terminal_console is not None:
                        self.terminal_console.begin_prompt_cycle()
                    prompt = (
                        self.terminal_console.input_prompt()
                        if self.terminal_console is not None
                        else "you> "
                    )
                    line = self.input_fn(prompt)
            except KeyboardInterrupt:
                self._checkpoint_gate.on_keyboard_interrupt()
                self.output_fn("\n(cancelled)")
                continue
            except EOFError:
                self._exit_session(record_mode="off")
                break

            line = line.rstrip("\n")
            if not line.strip():
                continue

            if self.handle_line(line) == "stop":
                break

        return 0

    def _run_bottom_layout_loop(self) -> int:
        bottom = self._bottom_terminal
        if bottom is None:
            return self._run_terminal_loop()

        self._mount_bottom_welcome(bottom)
        self._print_corruption_notices()

        def on_submit(text: str) -> None:
            if bottom.should_stop or self._stop:
                return
            try:
                if self.handle_line(text) == "stop":
                    bottom.request_stop()
            except KeyboardInterrupt:
                self._checkpoint_gate.on_keyboard_interrupt()
                bottom.notify_cancelled()
            except Exception as exc:
                self.output_fn(f"error: {exc}")

        def on_cancel() -> bool:
            guard = self._turn_cancel_guard
            if guard is None or not guard.turn_busy:
                return False
            guard.request_cancel()
            bottom.write("(cancelling turn…)")
            return True

        if self.terminal_console is not None:
            console = self.terminal_console
            previous_set_status = console.set_status

            def _set_status(status: str) -> None:
                previous_set_status(status)
                if status == "working":
                    bottom.begin_turn_output()
                elif status == "idle":
                    bottom.end_turn_output()
                else:
                    bottom.request_redraw()

            console.set_status = _set_status  # type: ignore[method-assign]
            console.sink.status_listener = console.set_status
            console.set_status_change_listener(bottom.request_redraw)

        bottom.set_submit_handler(on_submit)
        bottom.set_cancel_handler(on_cancel)
        bottom.run()
        if not self._stop:
            self._exit_session(record_mode="off")
        return 0

    def _mount_bottom_welcome(self, bottom: BottomPinnedTerminal, *, resume: bool = True) -> None:
        console = self.terminal_console
        if console is None or console.session is None or console.paths is None:
            return
        if welcome_enabled():
            panel = build_welcome(
                session=console.session,
                paths=console.paths,
                scope_fields=console.scope_fields,
                resume=resume,
            )
            bottom.mount_welcome(panel)
        else:
            bottom.append_transcript_block(console.plain_banner_line())

    def _rebind_agent(self) -> None:
        self.agent = Agent.create(
            self.session,
            confirm_fn=make_confirm_fn(
                self.output_fn,
                self.input_fn,
                checkpoint_gate=self._checkpoint_gate,
            ),
            stream_handlers=self.stream_handlers,
        )
        self.agent.executor.confirm_fn = make_confirm_fn(
            self.output_fn,
            self.input_fn,
            checkpoint_gate=self._checkpoint_gate,
            cancel_event=self.agent.cancel_event,
        )
        if self._ink_bridge is not None:
            self.agent.executor.confirm_fn = self._ink_bridge_confirm
        if self.terminal_console is not None:
            self.terminal_console.wire_repl(self)
        else:
            self._wire_turn_events()

    def _ink_bridge_confirm(self, preview: str, allow_approve_all: bool) -> str:
        bridge = self._ink_bridge
        if bridge is None:
            return "n"
        return bridge.confirm_fn(preview, allow_approve_all)

        if self.terminal_console is not None:
            return
        super()._wire_turn_events()

    def _run_agent_turn(self, text: str):
        guard = self._turn_cancel_guard
        console = self.terminal_console
        if console is not None:
            console.begin_user_turn(text)
        if guard is not None:
            guard.begin_turn()
        ok = True
        finish_reason: str | None = None
        try:
            result = self.agent.run_turn(text)
            finish_reason = result.finish_reason
            return result
        except LLMCancelledError:
            ok = False
            finish_reason = "cancelled"
            raise
        except (ToolLoopExceededError, LLMError, json.JSONDecodeError):
            ok = False
            finish_reason = "error"
            raise
        finally:
            if guard is not None:
                guard.end_turn()

    def handle_line(self, line: str) -> ReplOutcome:
        stripped = line.strip()
        lower = stripped.casefold()

        if classify_user_line(stripped) == UserLineKind.EXIT:
            record_mode = parse_exit_record_mode(stripped)
            self._exit_session(record_mode=record_mode)
            return "stop"

        if lower in {"新会话", "new"}:
            self.start_new_session()
            return "continue"

        slash = parse_terminal_slash_command(stripped)
        if slash == "clear":
            if self._bottom_terminal is not None:
                self._bottom_terminal.set_transcript("")
                if self.terminal_console is not None:
                    self.terminal_console.sink.state.clear()
                self._mount_bottom_welcome(self._bottom_terminal)
            elif self._ink_bridge is not None and isinstance(self.terminal_console, TerminalInkConsole):
                self.terminal_console.clear_transcript()
            elif self.terminal_console is not None:
                self.terminal_console.clear_transcript()
            else:
                self.output_fn("— transcript cleared —")
            return "continue"
        if slash == "compact":
            return self._handle_terminal_compact()

        model_arg = parse_terminal_model_command(stripped)
        if model_arg is not None:
            return self._handle_terminal_model(model_arg)

        if parse_turn_mode_command(stripped) is not None:
            self.output_fn(_TURN_MODE_NOTICE)
            return "continue"

        if try_short_plan_confirm(self.session, stripped, self.output_fn):
            self.output_fn(_PROJECT_NOTICE)
            return "continue"

        try:
            if parse_project_command(stripped) is not None:
                self.output_fn(_PROJECT_NOTICE)
                return "continue"
        except ProjectCommandError as exc:
            self.output_fn(f"error: {exc}")
            return "continue"

        try:
            host_cmd = parse_host_scope_command(stripped)
        except HostScopeCommandError as exc:
            self.output_fn(f"error: {exc}")
            return "continue"
        if host_cmd is not None:
            run_host_scope_command(
                self.paths,
                host_cmd,
                input_fn=self.input_fn,
                output_fn=self.output_fn,
            )
            return "continue"

        if lower in {"压缩", "summarize", "compact"}:
            return self._handle_terminal_compact()

        try:
            result = self._run_agent_turn(stripped)
            self.last_turn_finish_reason = result.finish_reason
        except ToolLoopExceededError as exc:
            self.last_turn_finish_reason = None
            self.output_fn(f"error: {exc}")
            if self.terminal_console is not None:
                self.terminal_console.end_turn(finish_reason="error", ok=False)
            return "continue"
        except LLMCancelledError:
            self.last_turn_finish_reason = "cancelled"
            self.output_fn("(cancelled)")
            if self.terminal_console is not None:
                self.terminal_console.end_turn(finish_reason="cancelled", ok=False)
            return "continue"
        except LLMError as exc:
            self.last_turn_finish_reason = None
            self.output_fn(f"llm error: {exc}")
            if self.terminal_console is not None:
                self.terminal_console.end_turn(finish_reason="error", ok=False)
            return "continue"
        except json.JSONDecodeError as exc:
            self.last_turn_finish_reason = None
            self.output_fn(f"llm error: invalid provider JSON: {exc}")
            if self.terminal_console is not None:
                self.terminal_console.end_turn(finish_reason="error", ok=False)
            return "continue"

        for notice in result.notices:
            self.output_fn(notice)
        if result.assistant_text:
            if self.assistant_output_fn is not None:
                self.assistant_output_fn(result.assistant_text)
            else:
                self.output_fn(result.assistant_text)
        elif self.assistant_output_fn is not None:
            self.assistant_output_fn("")
        if self.terminal_console is not None:
            self.terminal_console.end_turn(
                finish_reason=self.last_turn_finish_reason,
                ok=True,
            )
        return "continue"

    def start_new_session(self) -> None:
        self.session = create_terminal_session(
            self.paths,
            **scope_fields_to_session_kwargs(self.scope_fields),
        )
        self.session.save()
        self._log_session_start()
        self._rebind_agent()
        if self.terminal_console is not None:
            self.terminal_console.bind_context(self.session, self.paths, self.scope_fields)
        if self._bottom_terminal is not None:
            self._mount_bottom_welcome(self._bottom_terminal, resume=False)
        else:
            self._print_terminal_banner(resume=False)

    def _print_terminal_banner(self, *, resume: bool = True) -> None:
        console = self.terminal_console
        if console is not None:
            if welcome_enabled() and console.backend_kind == "rich":
                console.show_welcome(resume=resume)
            else:
                self.output_fn(console.plain_banner_line())
        else:
            root = _format_terminal_root_label(self.session, self.paths)
            self.output_fn(
                f"Terminal · {root} · exit 结束 | session {self.session.conversation_id}"
            )
        self._print_corruption_notices()

    def _handle_terminal_compact(self) -> ReplOutcome:
        from context import compact_context

        try:
            result = compact_context(self.session, self.agent.llm, force=True)
            self.output_fn(result.message)
        except LLMCancelledError:
            self.output_fn("(cancelled)")
        except LLMError as exc:
            self.output_fn(f"compress error: {exc}")
        return "continue"

    def _emit_meta(self, text: str) -> None:
        if self.terminal_console is not None:
            self.terminal_console.emit_meta_notice(text)
        else:
            self.output_fn(text)

    def _handle_terminal_model(self, model_arg: str) -> ReplOutcome:
        from context import validate_llm_model_switch
        from llm_models import get_registry
        from session import SessionError
        from terminal_picker import interactive_choice_available, prompt_model_choice

        if model_arg == "":
            registry = get_registry(self.paths)
            current = self.session.meta.llm_model
            if self._bottom_terminal is not None:
                # In-app picker sits above the input rule — not dumped into transcript.
                picked = self._bottom_terminal.pick_model(
                    registry.models,
                    current_id=current,
                )
            elif interactive_choice_available():
                picked = prompt_model_choice(
                    registry.models,
                    current_id=current,
                )
            else:
                for line in format_terminal_models_list(
                    self.paths,
                    current_model=current,
                ):
                    self.output_fn(line)
                return "continue"
            if picked is None:
                if self._bottom_terminal is None or not self._bottom_terminal.welcome_visible:
                    self._emit_meta("(已取消)")
                return "continue"
            if picked == current:
                if self._bottom_terminal is None or not self._bottom_terminal.welcome_visible:
                    self._emit_meta(f"当前已是 {current}")
                return "continue"
            model_arg = picked

        guard = self._turn_cancel_guard
        if guard is not None and guard.turn_busy:
            self.output_fn("回合进行中，结束后再切换模型")
            return "continue"

        try:
            canonical = validate_llm_model_switch(self.session, model_arg)
            self.session.set_llm_model(canonical)
            self.session.save()
        except SessionError as exc:
            self.output_fn(f"error: {exc}")
            return "continue"

        if self.terminal_console is not None:
            self.terminal_console.bind_context(self.session, self.paths, self.scope_fields)
        bottom = self._bottom_terminal
        if bottom is not None and bottom.welcome_visible:
            self._mount_bottom_welcome(bottom)
        else:
            self._emit_meta(f"模型 → {self.session.meta.llm_model}")
        if bottom is not None:
            bottom.request_redraw()
        return "continue"


def _format_terminal_root_label(session: Session, paths: AgentPaths) -> str:
    from terminal_ui import resolve_effective_root_abs, shorten_middle_path

    root = resolve_effective_root_abs(session.meta, paths)
    return shorten_middle_path(root)


def _format_terminal_cwd_label(session: Session, paths: AgentPaths) -> str:
    return _format_terminal_root_label(session, paths)


def _prompt_r3_choice(
    cwd: Path,
    paths: AgentPaths,
    *,
    input_fn: InputFn,
    output_fn: OutputFn,
) -> TerminalScopeFields | None:
    prompt = format_r3_prompt(cwd)
    while True:
        answer = input_fn(prompt).strip()
        if answer == "3":
            return None
        if answer == "1":
            return apply_r3_choice("1", cwd, paths)
        if answer == "2":
            host_id = input_fn("托管区 id: ").strip()
            if not host_id:
                output_fn("id 不能为空。")
                continue
            label = input_fn("显示名称: ").strip() or host_id
            write_raw = input_fn("读写 [读写/只读] (默认读写): ").strip().casefold()
            host_write = write_raw not in {"只读", "ro", "read", "r"}
            try:
                fields = apply_r3_choice(
                    "2",
                    cwd,
                    paths,
                    host_id=host_id,
                    host_label=label,
                    host_write=host_write,
                )
            except TerminalScopeError as exc:
                output_fn(f"error: {exc}")
                continue
            if fields is not None:
                return fields
            continue
        output_fn("请选择 1/2/3。")


def resolve_terminal_startup(
    paths: AgentPaths,
    path_arg: str | None,
    *,
    input_fn: InputFn,
    output_fn: OutputFn,
) -> TerminalScopeFields | TerminalStartupDenied:
    try:
        cwd = resolve_terminal_cwd_candidate(path_arg, shell_cwd=Path.cwd())
    except TerminalScopeError as exc:
        return TerminalStartupDenied(message=str(exc), cwd=Path.cwd().as_posix())

    outcome = classify_terminal_startup(paths, cwd)
    if isinstance(outcome, TerminalScopeFields):
        return outcome
    if isinstance(outcome, TerminalStartupDenied):
        return outcome

    fields = _prompt_r3_choice(cwd, paths, input_fn=input_fn, output_fn=output_fn)
    if fields is None:
        return TerminalStartupDenied(
            message="terminal startup cancelled",
            cwd=cwd.resolve().as_posix(),
        )
    return fields


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="my-agent terminal",
        description="Terminal harness REPL (cwd-scoped · TM-1)",
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=None,
        help="Working directory (default: shell cwd)",
    )
    parser.add_argument("--demo", action="store_true", help="Run acceptance checks")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.demo:
        return _demo()

    paths = AgentPaths.discover()
    lock_guard = InterfaceLockGuard(paths, "terminal")
    try:
        lock_guard.acquire(takeover=False, interactive_takeover=False)
    except InterfaceLockError as exc:
        print(f"error: {exc}", file=sys.stderr)
        holder = read_lock(paths)
        if holder is not None and holder.ui == "terminal":
            print(
                "hint: 若已关掉 Terminal 仍提示占用，结束残留 python.exe 后删除 "
                "data/sessions/.interface.lock 再启动。",
                file=sys.stderr,
            )
        return 1

    try:
        scope_outcome = resolve_terminal_startup(
            paths,
            args.path,
            input_fn=input,
            output_fn=print,
        )
        if isinstance(scope_outcome, TerminalStartupDenied):
            print(f"error: {scope_outcome.message}", file=sys.stderr)
            return 0 if "cancelled" in scope_outcome.message else 1

        session = resume_terminal_session(paths, scope_outcome)
        repl = TerminalRepl.from_terminal_session(
            session,
            paths=paths,
            scope_fields=scope_fields_from_meta(session.meta),
        )
        return repl.run()
    finally:
        lock_guard.release()


def _demo() -> int:
    import secrets
    import tempfile

    from session import create_new, read_terminal_last_session_id, write_terminal_last_session_id
    from tests.isolation_helpers import temporary_agent_paths

    with temporary_agent_paths() as paths:
        workspace_repo = paths.workspace / "huiyi"
        workspace_repo.mkdir(parents=True)
        scope = classify_terminal_startup(paths, workspace_repo)
        assert isinstance(scope, TerminalScopeFields)
        session = resume_terminal_session(paths, scope)
        assert session.meta.harness == "terminal"
        session.save()
        assert read_terminal_last_session_id(paths) == session.conversation_id
        print("[PASS] T-5702: resume_terminal_session + terminal_last_session")

        desktop = create_new(paths, conversation_id=f"_d-{secrets.token_hex(3)}")
        desktop.save()
        write_terminal_last_session_id(paths, desktop.conversation_id)
        fresh = resume_terminal_session(paths, scope)
        assert fresh.meta.harness == "terminal"
        assert fresh.conversation_id != desktop.conversation_id
        print("[PASS] T-5702: skip desktop harness in terminal_last_session (IT-571)")

        with tempfile.TemporaryDirectory() as tmp:
            external = Path(tmp) / "ext"
            external.mkdir()
            classified = classify_terminal_startup(paths, external)
            assert isinstance(classified, TerminalStartupNeedsPrompt)
            fields = apply_r3_choice("1", external, paths)
            assert fields is not None
            assert fields.terminal_scope_kind == "foreign"
            print("[PASS] T-5702: R3 foreign scope helper")

        label = _format_terminal_cwd_label(session, paths)
        assert label
        print(f"[PASS] T-5702: banner cwd label -> {label}")

    print("cli_terminal demo: all passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
