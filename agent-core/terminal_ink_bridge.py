"""Terminal Ink UI bridge (TERMINAL-MODE §6.6.2 stage 2 · T-5722 M0).

Python agent events → JSONL on child stdin → ``terminal-ui`` Ink renderer.
"""

from __future__ import annotations

import json
import os
import shutil
import queue
import socket
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, TextIO

from llm_client import StreamHandlers
from paths import AgentPaths
from terminal_ui import build_time_greeting_lines, build_welcome, reasoning_enabled

OutputFn = Callable[[str], None]


@dataclass(frozen=True)
class InkInputLine:
    text: str


@dataclass(frozen=True)
class InkConfirmResponse:
    request_id: str
    choice: str


@dataclass(frozen=True)
class InkCancelRequest:
    pass


InkInput = InkInputLine | InkConfirmResponse | InkCancelRequest


def terminal_ui_mode() -> str:
    """Return ``ink`` or ``legacy`` (prompt_toolkit / rich / plain)."""
    raw = os.environ.get("MY_AGENT_TERMINAL_UI", "ink").strip().casefold()
    if raw in {"legacy", "auto", "rich", "plain"}:
        return "legacy"
    return "ink"


def ink_ui_enabled(*, paths: AgentPaths) -> bool:
    if terminal_ui_mode() != "ink":
        return False
    return resolve_cli_entry(paths) is not None


def resolve_cli_entry(paths: AgentPaths) -> tuple[list[str], Path] | None:
    """Return ``(argv_prefix, entry_path)`` for the Ink CLI, or ``None`` if unavailable."""
    root = paths.agent_root
    terminal_ui = root / "terminal-ui"
    src = terminal_ui / "src" / "cli.tsx"
    node = shutil.which("node")
    if node:
        dist_candidates = (
            terminal_ui / "dist" / "cli.js",
            terminal_ui / "dist" / "src" / "cli.js",
        )
        source_mtime = src.stat().st_mtime if src.is_file() else None
        for dist in dist_candidates:
            if not dist.is_file():
                continue
            if source_mtime is not None and dist.stat().st_mtime < source_mtime:
                continue
            return ([node], dist)
    if src.is_file():
        local_tsx = terminal_ui / "node_modules" / ".bin" / (
            "tsx.cmd" if os.name == "nt" else "tsx"
        )
        if local_tsx.is_file():
            return ([str(local_tsx)], src)
        tsx = shutil.which("tsx")
        if tsx:
            return ([tsx], src)
        npx = shutil.which("npx")
        if npx:
            return ([npx, "tsx"], src)
    return None


def session_init_payload(
    *,
    session: Any,
    paths: AgentPaths,
    scope_fields: Any,
    resume: bool = False,
) -> dict[str, Any]:
    from welcome_mascot import SPRITE_LABEL, sprite_lines

    panel = build_welcome(
        session=session,
        paths=paths,
        scope_fields=scope_fields,
        resume=resume,
    )
    greet1, greet2 = build_time_greeting_lines(
        resume=resume,
        workspace=panel.workspace_name,
    )
    return {
        "type": "session.init",
        "greet": greet1,
        "greetSub": greet2,
        "model": panel.llm_model,
        "root": panel.effective_root,
        "mascotLines": list(sprite_lines()),
        "mascotLabel": SPRITE_LABEL,
    }


def translate_agent_event_to_ink(event: dict[str, Any]) -> list[dict[str, Any]]:
    """Map agent / executor events to the Ink JSONL subset (§6.6.2)."""
    event_type = event.get("type")
    if not isinstance(event_type, str):
        return []

    if event_type == "turn.start":
        out: list[dict[str, Any]] = [{"type": "turn.start"}]
        if event.get("turnKey") is not None:
            out[0]["turnKey"] = event["turnKey"]
        out.append({"type": "status.working", "active": True})
        return out

    if event_type == "turn.end":
        return [
            {"type": "tool.clear"},
            {"type": "status.working", "active": False},
        ]

    if event_type == "confirm.request":
        request_id = str(event.get("request_id", "")).strip()
        preview = str(event.get("preview", "")).strip()
        if not request_id or not preview:
            return []
        return [
            {
                "type": "confirm.request",
                "request_id": request_id,
                "preview": preview,
                "allow_approve_all": bool(event.get("allow_approve_all", False)),
            }
        ]

    if event_type == "confirm.done":
        request_id = str(event.get("request_id", "")).strip()
        if not request_id:
            return []
        return [
            {
                "type": "confirm.done",
                "request_id": request_id,
                "choice": str(event.get("choice", "n")),
            }
        ]

    if event_type == "transcript.clear":
        return [{"type": "transcript.clear"}]

    if event_type == "turn.notice":
        level = str(event.get("level", "info"))
        text = str(event.get("text", "")).strip()
        if not text:
            return []
        if level == "warn" and not text.startswith("⚠"):
            text = f"⚠ {text}"
        return [{"type": "notice", "text": text}]

    if event_type == "assistant.delta":
        return [{"type": "assistant.delta", "text": str(event.get("text", ""))}]

    if event_type == "assistant.done":
        return [{"type": "assistant.done", "text": str(event.get("text", ""))}]

    if event_type == "reasoning.delta":
        if not reasoning_enabled():
            return []
        return [{"type": "reasoning.delta", "text": str(event.get("text", ""))}]

    if event_type == "tool.start":
        tool = str(event.get("tool", "tool"))
        return [
            {"type": "tool.active", "name": tool},
            {"type": "status.working", "active": True},
        ]

    if event_type == "tool.end":
        return [{"type": "tool.clear"}]

    if event_type == "notice":
        text = str(event.get("text", "")).strip()
        return [{"type": "notice", "text": text}] if text else []

    if event_type == "error":
        text = str(event.get("message", "")).strip()
        return [{"type": "notice", "text": text}] if text else []

    return []


@dataclass
class TerminalInkBridge:
    """Spawn ``terminal-ui``; events via localhost socket, keyboard on inherited TTY."""

    paths: AgentPaths
    process: subprocess.Popen[str] | None = field(default=None, repr=False)
    _stdout: TextIO | None = field(default=None, repr=False)
    _argv: list[str] = field(default_factory=list, repr=False)
    _inputs: queue.Queue[InkInput | None] = field(default_factory=queue.Queue, repr=False)
    _reader: threading.Thread | None = field(default=None, repr=False)
    _event_server: socket.socket | None = field(default=None, repr=False)
    _event_conn: socket.socket | None = field(default=None, repr=False)
    _event_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @classmethod
    def start(cls, paths: AgentPaths) -> TerminalInkBridge:
        entry = resolve_cli_entry(paths)
        if entry is None:
            raise FileNotFoundError(
                "terminal-ui entry not found (run: cd terminal-ui && npm install && npm run build)"
            )
        prefix, entry_path = entry
        bridge = cls(paths=paths)
        bridge._argv = [*prefix, str(entry_path)]

        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        bridge._event_server = server
        event_port = server.getsockname()[1]

        env = os.environ.copy()
        env["MY_AGENT_TERMINAL_EVENT_PORT"] = str(event_port)
        env.setdefault("FORCE_COLOR", "3")
        env.setdefault("COLORTERM", "truecolor")

        bridge.process = subprocess.Popen(
            bridge._argv,
            env=env,
            stdin=None,
            stdout=subprocess.PIPE,
            stderr=None,
            bufsize=1,
            text=True,
            encoding="utf-8",
        )
        if bridge.process.stdout is None:
            raise RuntimeError("Ink child stdout pipe unavailable")

        accept_error: list[BaseException] = []

        def _accept_events() -> None:
            try:
                conn, _addr = server.accept()
                conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                bridge._event_conn = conn
            except OSError as exc:
                accept_error.append(exc)

        accept_thread = threading.Thread(
            target=_accept_events,
            name="terminal-ink-event-accept",
            daemon=True,
        )
        accept_thread.start()
        deadline = time.monotonic() + 10.0
        while bridge._event_conn is None and time.monotonic() < deadline:
            if bridge.process.poll() is not None:
                break
            time.sleep(0.05)
        accept_thread.join(timeout=0.2)
        if accept_error:
            raise RuntimeError(f"Ink event socket accept failed: {accept_error[0]}") from accept_error[0]
        if bridge._event_conn is None:
            raise RuntimeError("Ink child did not connect to event socket")

        bridge._stdout = bridge.process.stdout
        bridge._reader = threading.Thread(
            target=bridge._read_inputs,
            name="terminal-ink-input",
            daemon=True,
        )
        bridge._reader.start()
        return bridge

    def _read_inputs(self) -> None:
        stdout = self._stdout
        if stdout is None:
            return
        try:
            for line in stdout:
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue
                parsed = self._parse_input(message)
                if parsed is not None:
                    self._inputs.put(parsed)
        finally:
            self._inputs.put(None)

    @staticmethod
    def _parse_input(message: Any) -> InkInput | None:
        if not isinstance(message, dict):
            return None
        kind = message.get("type")
        if kind == "input.line":
            text = message.get("text")
            return InkInputLine(text=text) if isinstance(text, str) else None
        if kind == "confirm.response":
            request_id = message.get("request_id")
            choice = message.get("choice")
            if not isinstance(request_id, str) or not isinstance(choice, str):
                return None
            choice = choice.strip().casefold()
            if choice not in {"y", "n", "a"}:
                return None
            return InkConfirmResponse(request_id=request_id, choice=choice)
        if kind == "turn.cancel":
            return InkCancelRequest()
        return None

    def next_input(self, timeout: float | None = None) -> InkInput | None:
        try:
            return self._inputs.get(timeout=timeout)
        except KeyboardInterrupt:
            self._inputs.put(None)
            return None
        except queue.Empty:
            return None

    def wait_confirm(self, request_id: str, allow_approve_all: bool) -> str:
        while True:
            message = self.next_input()
            if message is None:
                return "n"
            if not isinstance(message, InkConfirmResponse):
                continue
            if message.request_id != request_id:
                continue
            choice = message.choice
            if choice == "a" and not allow_approve_all:
                choice = "n"
            self.emit_confirm_done(request_id=request_id, choice=choice)
            return choice

    def write_event(self, event: dict[str, Any]) -> None:
        line = json.dumps(event, ensure_ascii=False) + "\n"
        payload = line.encode("utf-8")
        with self._event_lock:
            conn = self._event_conn
            if conn is None:
                return
            try:
                conn.sendall(payload)
            except OSError:
                self.close()

    def emit(self, event: dict[str, Any]) -> None:
        for ink_event in translate_agent_event_to_ink(event):
            self.write_event(ink_event)

    def emit_session_init(
        self,
        *,
        session: Any,
        scope_fields: Any,
        resume: bool = False,
    ) -> None:
        self.write_event(
            session_init_payload(
                session=session,
                paths=self.paths,
                scope_fields=scope_fields,
                resume=resume,
            )
        )

    def emit_confirm_request(
        self,
        *,
        preview: str,
        allow_approve_all: bool,
        request_id: str | None = None,
    ) -> str:
        rid = request_id or str(uuid.uuid4())
        self.write_event(
            {
                "type": "confirm.request",
                "request_id": rid,
                "preview": preview,
                "allow_approve_all": allow_approve_all,
            }
        )
        return rid

    def emit_confirm_done(self, *, request_id: str, choice: str) -> None:
        self.write_event(
            {"type": "confirm.done", "request_id": request_id, "choice": choice}
        )

    def emit_transcript_clear(self) -> None:
        self.write_event({"type": "transcript.clear"})

    def close(self) -> None:
        if self._event_conn is not None:
            try:
                self._event_conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                self._event_conn.close()
            except OSError:
                pass
            self._event_conn = None
        if self._event_server is not None:
            try:
                self._event_server.close()
            except OSError:
                pass
            self._event_server = None
        if self._stdout is not None:
            try:
                self._stdout.close()
            except OSError:
                pass
            self._stdout = None
        if self.process is not None:
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
            self.process = None


@dataclass
class TerminalInkEventSink:
    """Forward agent events to :class:`TerminalInkBridge` (no legacy transcript render)."""

    bridge: TerminalInkBridge
    _pending_user_text: str = ""
    status_listener: Callable[[str], None] | None = field(default=None, repr=False)

    def set_pending_user_text(self, text: str) -> None:
        self._pending_user_text = text

    def emit(self, event: dict[str, Any]) -> None:
        event_type = event.get("type")
        if event_type == "turn.start":
            pending = self._pending_user_text.strip()
            if pending:
                self.bridge.write_event({"type": "user.message", "text": pending})
                self._pending_user_text = ""
            if self.status_listener is not None:
                self.status_listener("working")
        elif event_type == "turn.end" and self.status_listener is not None:
            self.status_listener("idle")

        for ink_event in translate_agent_event_to_ink(event):
            self.bridge.write_event(ink_event)

    def on_executor_event(self, event_type: str, payload: dict[str, Any]) -> None:
        if event_type in {"tool.start", "tool.end", "tool.progress"}:
            self.emit({"type": event_type, **payload})
            return
        if event_type == "guard.notice":
            text = payload.get("text")
            if isinstance(text, str) and text.strip():
                self.emit({"type": "turn.notice", "level": "info", "text": text})

    def stream_handlers(self) -> StreamHandlers:
        return StreamHandlers(
            on_content_delta=lambda text: self.emit({"type": "assistant.delta", "text": text}),
            on_reasoning_delta=lambda text: self.emit({"type": "reasoning.delta", "text": text}),
        )

    def emit_assistant_done(self, text: str) -> None:
        self.emit({"type": "assistant.done", "text": text or ""})


@dataclass
class TerminalInkConsole:
    """Ink-mode console: JSONL bridge only (M0 · one-way pipe)."""

    sink: TerminalInkEventSink
    bridge: TerminalInkBridge
    session: Any | None = field(default=None, repr=False)
    paths: AgentPaths | None = field(default=None, repr=False)
    scope_fields: Any | None = field(default=None, repr=False)

    @classmethod
    def create(
        cls,
        *,
        bridge: TerminalInkBridge,
        session: Any,
        paths: AgentPaths,
        scope_fields: Any,
    ) -> TerminalInkConsole:
        sink = TerminalInkEventSink(bridge=bridge)
        return cls(
            sink=sink,
            bridge=bridge,
            session=session,
            paths=paths,
            scope_fields=scope_fields,
        )

    @property
    def backend_kind(self) -> str:
        return "ink"

    def output_fn(self, text: str) -> None:
        stripped = text.strip()
        if not stripped or stripped.startswith("Terminal ·"):
            return
        self.sink.emit({"type": "notice", "text": stripped})

    def emit_meta_notice(self, text: str) -> None:
        self.output_fn(text)

    def wire_repl(self, repl: Any) -> None:
        repl.stream_handlers = self.sink.stream_handlers()
        repl.agent.stream_handlers = repl.stream_handlers
        repl.assistant_output_fn = self.sink.emit_assistant_done
        repl.agent.on_turn_event = self.sink.emit
        repl.agent.executor.on_event = self.sink.on_executor_event

        original_rebind = repl._rebind_agent

        def rebind() -> None:
            original_rebind()
            repl.stream_handlers = self.sink.stream_handlers()
            repl.agent.stream_handlers = repl.stream_handlers
            repl.assistant_output_fn = self.sink.emit_assistant_done
            repl.agent.on_turn_event = self.sink.emit
            repl.agent.executor.on_event = self.sink.on_executor_event

        repl._rebind_agent = rebind  # type: ignore[method-assign]
        self.sink.status_listener = lambda _status: None

    def confirm_fn(self, preview: str, allow_approve_all: bool) -> str:
        request_id = self.bridge.emit_confirm_request(
            preview=preview,
            allow_approve_all=allow_approve_all,
        )
        return self.bridge.wait_confirm(request_id, allow_approve_all)

    def begin_user_turn(self, text: str) -> None:
        self.sink.set_pending_user_text(text)

    def clear_transcript(self) -> None:
        self.bridge.emit_transcript_clear()

    def end_turn(self, *, finish_reason: str | None, ok: bool = True) -> None:
        self.sink.emit(
            {
                "type": "turn.end",
                "ok": ok,
                "finish_reason": finish_reason or "completed",
            }
        )

    def input_prompt(self) -> str:
        return "> "

    def begin_prompt_cycle(self) -> None:
        return


def open_tty_reader() -> TextIO | None:
    """Open the controlling terminal for line input while Ink owns the display."""
    if not sys.stdin.isatty():
        return None
    if sys.platform == "win32":
        try:
            return open("CONIN$", "r", encoding="utf-8", errors="replace")  # noqa: SIM115
        except OSError:
            return None
    try:
        return open("/dev/tty", "r", encoding="utf-8", errors="replace")  # noqa: SIM115
    except OSError:
        return None


def read_tty_line(prompt: str, *, tty: TextIO | None = None) -> str:
    """Read one line from the controlling TTY (M0 input until Ink PromptInput)."""
    if tty is not None:
        sys.stderr.write(prompt)
        sys.stderr.flush()
        line = tty.readline()
        return line.rstrip("\r\n")
    return input(prompt)
