"""Local WebSocket API for Electron desktop shell (DESKTOP.md §4–5, TASKS T-904a)."""

from __future__ import annotations

import argparse
import asyncio
import atexit
import json
import os
import queue
import sys
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_AGENT_CORE = Path(__file__).resolve().parent
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from agent import ToolLoopExceededError  # noqa: F401 — reserved for future turn error mapping
from evolve import (
    EvolveError,
    ProposalRecord,
    accept_proposal,
    list_pending_proposals,
    reject_proposal,
)
from llm_client import LLMError  # noqa: F401
from llm_client import StreamHandlers
from context import session_memory_event
from main import ConversationRepl, ReplConfig
from paths import AgentPaths
from sidecar_logging import configure_sidecar_logging, log_sidecar_exception, log_sidecar_ws_error
from host_scope import load_host_scope
from runtime_guards import TurnWatchdog, stall_watchdog_sec, turn_wall_sec
from session import Session, create_new, emit_corruption_notices, list_session_summaries, resume_or_create, session_banner_event, session_history_event, turn_mode_label
from tools.executor import build_confirm_preview

try:
    from websockets.asyncio.server import ServerConnection, serve
    from websockets.exceptions import ConnectionClosed
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "websockets package required for server.py; pip install websockets>=12"
    ) from exc

EmitFn = Callable[[dict[str, Any]], None]
DEFAULT_HOST = "127.0.0.1"
DEFAULT_CONFIRM_TIMEOUT_SEC = 90.0
TURN_LOCK = asyncio.Lock()


def confirm_timeout_sec() -> float:
    raw = os.environ.get("CONFIRM_TIMEOUT_SEC", str(int(DEFAULT_CONFIRM_TIMEOUT_SEC)))
    try:
        value = float(raw)
    except ValueError:
        value = DEFAULT_CONFIRM_TIMEOUT_SEC
    return max(0.1, value)


def _proposal_item(record: ProposalRecord) -> dict[str, Any]:
    target_path = str(record.target.get("path", "")).strip()
    return {
        "proposal_id": record.proposal_id,
        "type": record.type,
        "mode": record.mode,
        "summary": record.summary,
        "target_path": target_path or None,
        "topics": list(record.topics),
    }


def _session_banner_payload(session: Session) -> dict[str, Any]:
    return session_banner_event(session)


def _proposals_payload(paths: AgentPaths) -> dict[str, Any]:
    items = [_proposal_item(record) for record in list_pending_proposals(paths)]
    return {"type": "evolve.proposals", "items": items}


@dataclass
class WsBridge:
    """Thread-safe bridge between WebSocket events and ConversationRepl I/O."""

    emit: EmitFn
    paths: AgentPaths
    _input_queue: queue.Queue[str] = field(default_factory=queue.Queue)
    _confirm_queue: queue.Queue[tuple[str, str]] = field(default_factory=queue.Queue)
    _awaiting_input: threading.Event = field(default_factory=threading.Event)
    _turn_busy: threading.Event = field(default_factory=threading.Event)
    cancel_event: threading.Event = field(default_factory=threading.Event)
    confirm_timeout: float = field(default_factory=confirm_timeout_sec)
    turn_watchdog: TurnWatchdog | None = field(default=None, repr=False)
    _pending_confirm_id: str | None = field(default=None, init=False)
    _cancel_turn: Callable[[], None] | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self.turn_watchdog = TurnWatchdog(
            cancel_event=self.cancel_event,
            on_auto_timeout=lambda message: self._on_auto_timeout(message),
            wall_sec=turn_wall_sec(),
            stall_sec=stall_watchdog_sec(),
        )

    def _on_auto_timeout(self, message: str) -> None:
        self.emit({"type": "notice", "text": message})
        if self._cancel_turn is not None:
            self._cancel_turn()

    def _touch_progress(self) -> None:
        if self.turn_watchdog is not None:
            self.turn_watchdog.touch_progress()

    def begin_turn(self) -> None:
        self.cancel_event.clear()
        if self.turn_watchdog is not None:
            self.turn_watchdog.begin()

    def end_turn(self) -> None:
        if self.turn_watchdog is not None:
            self.turn_watchdog.end()

    def resolve_turn_finish_reason(self, agent_finish_reason: str | None = None) -> str:
        if self.turn_watchdog is not None:
            return self.turn_watchdog.resolve_finish_reason(agent_finish_reason)
        if agent_finish_reason:
            return agent_finish_reason
        if self.cancel_event.is_set():
            return "cancelled"
        return "completed"

    def resolve_cancel_finish_reason(self) -> str | None:
        if not self.cancel_event.is_set():
            return None
        if self.turn_watchdog is not None:
            return self.turn_watchdog.resolve_interrupt_reason()
        return "cancelled"

    def output_fn(self, text: str) -> None:
        stripped = text.strip()
        if not stripped:
            return
        if stripped.startswith("--- session "):
            return
        if stripped.startswith("error:") or stripped.startswith("llm error:"):
            emit_error(self, stripped)
            return
        self.emit({"type": "notice", "text": text})

    def input_fn(self, prompt: str) -> str:
        self.emit({"type": "prompt.request", "prompt": prompt})
        self._awaiting_input.set()
        try:
            return self._input_queue.get(timeout=3600)
        except queue.Empty:
            return ""
        finally:
            self._awaiting_input.clear()

    def confirm_fn(self, preview: str, allow_approve_all: bool) -> str:
        from tools.executor import CONTEXT_SWITCH_CONFIRM_PREFIX

        context_switch = preview.startswith(CONTEXT_SWITCH_CONFIRM_PREFIX)
        if context_switch:
            request_id = preview[len(CONTEXT_SWITCH_CONFIRM_PREFIX) :].strip()
            if not request_id:
                request_id = str(uuid.uuid4())
        else:
            request_id = str(uuid.uuid4())
        self._pending_confirm_id = request_id
        if not context_switch:
            self.emit(
                {
                    "type": "confirm.request",
                    "request_id": request_id,
                    "preview": preview,
                    "allow_approve_all": allow_approve_all,
                }
            )
        self._touch_progress()
        deadline = time.monotonic() + self.confirm_timeout
        try:
            while True:
                if self.cancel_event.is_set():
                    if not context_switch:
                        self.emit(
                            {
                                "type": "confirm.done",
                                "request_id": request_id,
                                "choice": "cancelled",
                            }
                        )
                    self._touch_progress()
                    return "n"
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise queue.Empty
                try:
                    resp_id, choice = self._confirm_queue.get(timeout=min(0.25, remaining))
                except queue.Empty:
                    if time.monotonic() < deadline:
                        continue
                    raise
                if resp_id == request_id:
                    if self.cancel_event.is_set():
                        choice = "cancelled"
                    if not context_switch:
                        self.emit(
                            {
                                "type": "confirm.done",
                                "request_id": request_id,
                                "choice": choice,
                            }
                        )
                    self._touch_progress()
                    return "n" if choice == "cancelled" else choice
                # C1: never put stale ids back — that spins forever (BUG-008).
                self.emit(
                    {
                        "type": "notice",
                        "text": "忽略过期确认（request_id 不匹配当前请求）",
                    }
                )
                self.emit(
                    {
                        "type": "confirm.done",
                        "request_id": resp_id,
                        "choice": "stale",
                    }
                )
        except queue.Empty:
            # C2: always pair confirm.done with confirm_fn return (BUG-008b).
            self.emit({"type": "notice", "text": "确认超时，已跳过"})
            if not context_switch:
                self.emit(
                    {
                        "type": "confirm.done",
                        "request_id": request_id,
                        "choice": "timeout",
                    }
                )
            self._touch_progress()
            return "n"
        finally:
            self._pending_confirm_id = None

    def on_executor_event(self, event_type: str, payload: dict[str, Any]) -> None:
        if event_type in {"tool.start", "tool.end", "tool.progress"}:
            if self.turn_watchdog is not None and event_type in {"tool.start", "tool.end"}:
                self.turn_watchdog.note_progress_event(event_type)
            self.emit({"type": event_type, **payload})
            return
        if event_type == "turn.evidence":
            self.emit({"type": event_type, **payload})
            return
        if event_type == "services.state":
            self.emit({"type": event_type, **payload})
            return
        if event_type == "session.workspace_evolved_approved":
            self.emit({"type": "notice", "text": "本会话 workspace evolved 均已允许。"})
            return
        if event_type == "guard.notice":
            text = payload.get("text")
            if isinstance(text, str) and text.strip():
                self.emit({"type": "notice", "text": text})
            return
        if event_type in {
            "context.switch.request",
            "context.switch.done",
            "session.banner",
            "session.history",
            "session.memory",
            "project.state",
            "notice",
        }:
            self.emit({"type": event_type, **payload})
            return
        self.emit({"type": "notice", "text": f"{event_type}: {json.dumps(payload, ensure_ascii=False)}"})

    def try_route_input(self, text: str) -> bool:
        if self._awaiting_input.is_set():
            self._input_queue.put(text)
            return True
        return False

    def request_cancel(self) -> bool:
        if not self._turn_busy.is_set():
            self.emit({"type": "notice", "text": "当前无进行中的回合"})
            return False
        if self.cancel_event.is_set():
            return True
        if self.turn_watchdog is not None:
            self.turn_watchdog.request_user_cancel()
        self.cancel_event.set()
        if self._awaiting_input.is_set():
            self._input_queue.put("")
        if self._cancel_turn is not None:
            self._cancel_turn()
        self.emit({"type": "notice", "text": "正在停止当前回合…"})
        return True

    def deliver_confirm(self, request_id: str, choice: str) -> bool:
        if not request_id:
            return False
        if self.cancel_event.is_set():
            return False
        pending = self._pending_confirm_id
        if pending is not None and request_id != pending:
            # C1 ingress: reject stale cards before they enter the queue.
            self.emit(
                {
                    "type": "notice",
                    "text": "忽略过期确认（请点最新一张工具确认卡）",
                }
            )
            self.emit(
                {
                    "type": "confirm.done",
                    "request_id": request_id,
                    "choice": "stale",
                }
            )
            return False
        self._confirm_queue.put((request_id, choice))
        return True

    def emit_session_state(self, session: Session) -> None:
        self.emit(_session_banner_payload(session))
        self.emit(session_memory_event(session))
        self.emit(session_history_event(session))
        self.emit(_proposals_payload(self.paths))
        if session.meta.project_id:
            from project_api import project_state_payload

            self.emit(project_state_payload(session, self.paths))

    def emit_assistant(self, text: str) -> None:
        # C9: allow empty text so desktop always gets assistant.done.
        self.emit({"type": "assistant.done", "text": text or ""})

    def emit_content_delta(self, text: str) -> None:
        if text:
            self._touch_progress()
            self.emit({"type": "assistant.delta", "text": text})

    def emit_reasoning_delta(self, text: str) -> None:
        if text:
            self.emit({"type": "reasoning.delta", "text": text})

    def stream_handlers(self) -> StreamHandlers:
        return StreamHandlers(
            on_content_delta=self.emit_content_delta,
            on_reasoning_delta=self.emit_reasoning_delta,
        )


def _patch_repl(repl: ConversationRepl, bridge: WsBridge) -> None:
    repl.stream_handlers = bridge.stream_handlers()
    repl.agent.stream_handlers = repl.stream_handlers
    repl.agent.executor.confirm_fn = bridge.confirm_fn
    repl.agent.executor.on_event = bridge.on_executor_event
    repl.agent.on_turn_event = bridge.emit
    repl.agent.bind_cancel_event(bridge.cancel_event)
    repl.agent.bind_cancel_finish_reason(bridge.resolve_cancel_finish_reason)
    bridge._cancel_turn = repl.agent.request_cancel

    original_rebind = repl._rebind_agent

    def rebind() -> None:
        original_rebind()
        repl.stream_handlers = bridge.stream_handlers()
        repl.agent.stream_handlers = repl.stream_handlers
        repl.agent.executor.confirm_fn = bridge.confirm_fn
        repl.agent.executor.on_event = bridge.on_executor_event
        repl.agent.on_turn_event = bridge.emit
        repl.agent.bind_cancel_event(bridge.cancel_event)
        repl.agent.bind_cancel_finish_reason(bridge.resolve_cancel_finish_reason)
        bridge._cancel_turn = repl.agent.request_cancel

    repl._rebind_agent = rebind  # type: ignore[method-assign]


def _build_repl(session: Session, paths: AgentPaths, bridge: WsBridge) -> ConversationRepl:
    repl = ConversationRepl.from_session(
        session,
        paths=paths,
        input_fn=bridge.input_fn,
        output_fn=bridge.output_fn,
        config=ReplConfig(),
        stream_handlers=bridge.stream_handlers(),
    )
    repl.assistant_output_fn = bridge.emit_assistant
    _patch_repl(repl, bridge)
    return repl


def _emit_session_list(bridge: WsBridge, paths: AgentPaths) -> None:
    """S-1: proactively push updated session list to keep dropdown fresh."""
    summaries = list_session_summaries(paths)
    bridge.emit({"type": "session.list", "sessions": summaries})


def _repl_refreshes_session_state(line: str) -> bool:
    """REPL meta-commands that replace session overlay / chat history on desktop."""
    lower = line.strip().casefold()
    return lower in {"新会话", "new", "换主题", "压缩", "summarize", "compact"}


async def _run_line(repl: ConversationRepl, bridge: WsBridge, line: str, paths: AgentPaths) -> None:
    repl.last_turn_finish_reason = None
    bridge.begin_turn()
    bridge._turn_busy.set()
    ok = True
    finish_reason = "completed"
    try:
        outcome = await asyncio.to_thread(repl.handle_line, line)
        agent_reason = repl.last_turn_finish_reason
        finish_reason = bridge.resolve_turn_finish_reason(agent_reason)
        if finish_reason in {"cancelled", "timeout"}:
            ok = False
            repl.session.save()
        elif outcome == "stop":
            bridge.emit({"type": "notice", "text": f"session saved: {repl.session.conversation_id}"})
            finish_reason = "cancelled"
            ok = False
        else:
            from project_api import after_turn_project_hooks

            after_turn_project_hooks(repl.session, paths, bridge.emit)
            repl.session.save()
    except Exception as exc:  # pragma: no cover
        ok = False
        finish_reason = "error"
        log_sidecar_exception(f"_run_line failed line={line!r}", exc)
        emit_error(bridge, str(exc))
    finally:
        bridge._turn_busy.clear()
        bridge.end_turn()
        # UX-019: emit updated token usage after every turn
        bridge.emit(session_memory_event(repl.session))
        # C9: always close the turn so desktop can resetTurnActivity (BUG-012).
        bridge.emit({"type": "turn.end", "ok": ok, "finish_reason": finish_reason})


class WsSessionHandler:
    def __init__(self, paths: AgentPaths) -> None:
        self.paths = paths
        self._repl_lock = threading.Lock()
        from file_stage import FileStageStore

        self._file_stage = FileStageStore()

    async def handle(self, websocket: ServerConnection) -> None:
        loop = asyncio.get_running_loop()
        outbox: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

        def emit(event: dict[str, Any]) -> None:
            loop.call_soon_threadsafe(outbox.put_nowait, event)

        bridge = WsBridge(emit=emit, paths=self.paths)
        session = resume_or_create(self.paths)
        repl = _build_repl(session, self.paths, bridge)

        sender_task = asyncio.create_task(self._sender(websocket, outbox))
        bridge.emit_session_state(repl.session)
        emit_corruption_notices(bridge.emit, repl.session)

        try:
            async for raw in websocket:
                try:
                    message = json.loads(raw)
                except json.JSONDecodeError:
                    emit_error(bridge, "invalid JSON")
                    continue
                if not isinstance(message, dict):
                    emit_error(bridge, "message must be a JSON object")
                    continue
                asyncio.create_task(self._handle_incoming(message, repl, bridge))
        finally:
            await outbox.put(None)
            await sender_task

    async def _handle_incoming(
        self,
        message: dict[str, Any],
        repl: ConversationRepl,
        bridge: WsBridge,
    ) -> None:
        """Process one client frame without blocking the websocket read loop."""
        if self._dispatch_inline(message, bridge):
            return

        msg_type = message.get("type")
        if msg_type == "user.message":
            text = message.get("text")
            if isinstance(text, str) and text.strip() and bridge.try_route_input(text):
                return

        if msg_type in {"user.message", "command"}:
            async with TURN_LOCK:
                try:
                    await self._dispatch(message, repl, bridge)
                except Exception as exc:  # pragma: no cover
                    emit_error(bridge, str(exc))
            return

        try:
            await self._dispatch(message, repl, bridge)
        except Exception as exc:  # pragma: no cover
            emit_error(bridge, str(exc))

    async def _sender(
        self,
        websocket: ServerConnection,
        outbox: asyncio.Queue[dict[str, Any] | None],
    ) -> None:
        while True:
            event = await outbox.get()
            if event is None:
                break
            try:
                await websocket.send(json.dumps(event, ensure_ascii=False))
            except ConnectionClosed:
                break

    def _dispatch_inline(self, message: dict[str, Any], bridge: WsBridge) -> bool:
        """Handle messages that must not wait behind an in-flight turn.

        While a user turn runs in a worker thread (blocked on confirm/input queues),
        the websocket loop must still process confirm.response and routed user input.
        """
        msg_type = message.get("type")
        if not isinstance(msg_type, str):
            return False

        if msg_type == "confirm.response":
            request_id = message.get("request_id")
            choice = message.get("choice")
            if not isinstance(request_id, str) or not isinstance(choice, str):
                emit_error(bridge, "confirm.response requires request_id and choice")
                return True
            bridge.deliver_confirm(request_id, choice.strip().casefold())
            return True

        if msg_type == "context.switch.response":
            request_id = message.get("request_id")
            choice = message.get("choice")
            if not isinstance(request_id, str) or not isinstance(choice, str):
                emit_error(bridge, "context.switch.response requires request_id and choice")
                return True
            normalized = choice.strip().casefold()
            if normalized in {"confirm", "yes", "y"}:
                normalized = "y"
            elif normalized in {"reject", "no", "n"}:
                normalized = "n"
            bridge.deliver_confirm(request_id, normalized)
            return True

        if msg_type == "turn.cancel":
            bridge.request_cancel()
            return True

        return False

    async def _dispatch(
        self,
        message: dict[str, Any],
        repl: ConversationRepl,
        bridge: WsBridge,
    ) -> None:
        msg_type = message.get("type")
        if not isinstance(msg_type, str):
            emit_error(bridge, "missing type")
            return

        if msg_type == "user.message":
            text = message.get("text")
            if not isinstance(text, str):
                emit_error(bridge, "user.message requires text")
                return
            raw_attachments = message.get("attachments")
            attachment_ids: list[str] = []
            if raw_attachments is not None:
                if not isinstance(raw_attachments, list) or not all(
                    isinstance(item, str) and item.strip() for item in raw_attachments
                ):
                    emit_error(bridge, "user.message attachments must be string ids")
                    return
                attachment_ids = [item.strip() for item in raw_attachments]
            if not text.strip() and not attachment_ids:
                emit_error(bridge, "user.message requires text or attachments")
                return
            if text.strip() and bridge.try_route_input(text):
                return
            from file_stage import compose_user_message

            attachments = self._file_stage.take(repl.session.conversation_id, attachment_ids)
            if attachment_ids and len(attachments) != len(attachment_ids):
                emit_error(bridge, "unknown or expired attachment id")
                return

            line = compose_user_message(text=text, attachments=attachments)
            # TURN_LOCK is held by _handle_incoming for user.message / command.
            await _run_line(repl, bridge, line, self.paths)
            if _repl_refreshes_session_state(line):
                bridge.emit_session_state(repl.session)
                _emit_session_list(bridge, self.paths)
            return

        if msg_type == "file.stage":
            raw_paths = message.get("paths")
            if not isinstance(raw_paths, list) or not raw_paths:
                emit_error(bridge, "file.stage requires non-empty paths array")
                return
            if not all(isinstance(item, str) and item.strip() for item in raw_paths):
                emit_error(bridge, "file.stage paths must be non-empty strings")
                return
            if len(raw_paths) > 20:
                emit_error(bridge, "file.stage allows at most 20 paths")
                return
            shell_raw = message.get("shell") or repl.session.meta.active_shell or "grow"
            if (
                not message.get("shell")
                and repl.session.meta.active_shell == "project"
                and (repl.session.meta.project_root or "").strip()
            ):
                shell_raw = "project"
            if shell_raw not in {"grow", "daily", "project", "govern", "pet"}:
                shell_raw = "grow"
            from file_stage import FileStageError, stage_absolute_path

            config = load_host_scope(self.paths)
            staged_items: list[dict[str, Any]] = []
            for raw_path in raw_paths:
                path_text = str(raw_path).strip()
                try:
                    item = stage_absolute_path(
                        path_text,
                        paths=self.paths,
                        session=repl.session,
                        shell=shell_raw,  # type: ignore[arg-type]
                        config=config,
                    )
                    self._file_stage.register(repl.session.conversation_id, item)
                    staged_items.append(item.to_item())
                except FileStageError as exc:
                    bridge.emit({"type": "file.error", "message": str(exc), "path": path_text})
            if staged_items:
                bridge.emit({"type": "file.staged", "items": staged_items})
            return

        if msg_type == "file.unstage":
            attachment_id = message.get("attachment_id")
            if not isinstance(attachment_id, str) or not attachment_id.strip():
                emit_error(bridge, "file.unstage requires attachment_id")
                return
            removed = self._file_stage.unstage(repl.session.conversation_id, attachment_id.strip())
            if removed:
                bridge.emit({"type": "file.unstaged", "attachment_id": attachment_id.strip()})
            else:
                emit_error(bridge, "attachment not found")
            return

        if msg_type == "command":
            name = message.get("name")
            if not isinstance(name, str) or not name.strip():
                emit_error(bridge, "command requires name")
                return
            await _run_line(repl, bridge, name.strip(), self.paths)
            bridge.emit_session_state(repl.session)
            return

        if msg_type == "session.list":
            summaries = list_session_summaries(self.paths)
            bridge.emit({"type": "session.list", "sessions": summaries})
            return

        if msg_type == "session.open":
            session_id = message.get("session_id")
            if not isinstance(session_id, str) or not session_id.strip():
                emit_error(bridge, "session.open requires session_id")
                return
            try:
                repl.session = Session.load(self.paths, session_id.strip())
                repl._rebind_agent()
                bridge.emit_session_state(repl.session)
                emit_corruption_notices(bridge.emit, repl.session)
                _emit_session_list(bridge, self.paths)
            except Exception as exc:
                emit_error(bridge, str(exc))
            return

        if msg_type == "session.refresh":
            bridge.emit_session_state(repl.session)
            return

        if msg_type == "proposal.accept":
            proposal_id = message.get("proposal_id")
            if not isinstance(proposal_id, str):
                emit_error(bridge, "proposal.accept requires proposal_id")
                return
            try:
                result = accept_proposal(
                    proposal_id,
                    paths=self.paths,
                    conversation_id=repl.session.conversation_id,
                )
                bridge.emit({"type": "notice", "text": result.message})
                bridge.emit(_proposals_payload(self.paths))
            except EvolveError as exc:
                emit_error(bridge, str(exc))
            return

        if msg_type == "proposal.reject":
            proposal_id = message.get("proposal_id")
            if not isinstance(proposal_id, str):
                emit_error(bridge, "proposal.reject requires proposal_id")
                return
            try:
                result = reject_proposal(
                    proposal_id,
                    paths=self.paths,
                    conversation_id=repl.session.conversation_id,
                )
                bridge.emit({"type": "notice", "text": result.message})
                bridge.emit(_proposals_payload(self.paths))
            except EvolveError as exc:
                emit_error(bridge, str(exc))
            return

        if isinstance(msg_type, str) and msg_type.startswith("host_scope."):
            await self._dispatch_host_scope(message, bridge)
            return

        if isinstance(msg_type, str) and msg_type.startswith("services."):
            await self._dispatch_services(message, bridge)
            return

        if isinstance(msg_type, str) and msg_type.startswith("project.doc."):
            await self._dispatch_doc(message, repl, bridge)
            return

        if isinstance(msg_type, str) and msg_type == "project.task.add":
            await self._dispatch_task_add(message, repl, bridge)
            return

        if isinstance(msg_type, str) and (
            msg_type.startswith("project.") or msg_type == "plan.response"
        ):
            await self._dispatch_project(message, repl, bridge)
            return

        emit_error(bridge, f"unknown type: {msg_type}")

    async def _dispatch_doc(
        self,
        message: dict[str, Any],
        repl: ConversationRepl,
        bridge: WsBridge,
    ) -> None:
        from project_api import ProjectApiError, dispatch_doc_message

        try:
            payload = await asyncio.to_thread(
                dispatch_doc_message, repl.session, self.paths, message
            )
            if isinstance(payload, dict) and "_events" in payload:
                for event in payload["_events"]:
                    bridge.emit(event)
                return
            bridge.emit(payload)
        except ProjectApiError as exc:
            emit_error(bridge, str(exc))

    async def _dispatch_task_add(
        self,
        message: dict[str, Any],
        repl: ConversationRepl,
        bridge: WsBridge,
    ) -> None:
        from project_api import ProjectApiError, dispatch_task_add

        try:
            payload = await asyncio.to_thread(
                dispatch_task_add, repl.session, self.paths, message
            )
            if isinstance(payload, dict) and "_events" in payload:
                if "_session" in payload:
                    repl.session = payload["_session"]
                    repl._rebind_agent()
                for event in payload["_events"]:
                    bridge.emit(event)
                return
            bridge.emit(payload)
        except ProjectApiError as exc:
            emit_error(bridge, str(exc))

    async def _dispatch_project(
        self,
        message: dict[str, Any],
        repl: ConversationRepl,
        bridge: WsBridge,
    ) -> None:
        from project_api import ProjectApiError, dispatch_project_message, handle_plan_response

        try:
            if message.get("type") == "plan.response":
                await asyncio.to_thread(
                    handle_plan_response,
                    repl.session,
                    self.paths,
                    message,
                    bridge.emit,
                )
                repl.session.save()
                return
            payload = await asyncio.to_thread(
                dispatch_project_message,
                repl.session,
                self.paths,
                message,
            )
            if isinstance(payload, dict) and "_events" in payload:
                if "_session" in payload:
                    repl.session = payload["_session"]
                    repl._rebind_agent()
                for event in payload["_events"]:
                    bridge.emit(event)
                return
            bridge.emit(payload)
        except ProjectApiError as exc:
            emit_error(bridge, str(exc))

    async def _dispatch_host_scope(
        self,
        message: dict[str, Any],
        bridge: WsBridge,
    ) -> None:
        from host_scope import HostScopeConfigError
        from host_scope_api import dispatch_host_scope_message

        try:
            payload = await asyncio.to_thread(
                dispatch_host_scope_message,
                self.paths,
                message,
            )
            bridge.emit(payload)
        except HostScopeConfigError as exc:
            emit_error(bridge, str(exc))

    async def _dispatch_services(
        self,
        message: dict[str, Any],
        bridge: WsBridge,
    ) -> None:
        from services_api import ServicesApiError, dispatch_services_message

        try:
            payload = await asyncio.to_thread(
                dispatch_services_message,
                self.paths,
                message,
            )
            bridge.emit(payload)
        except ServicesApiError as exc:
            emit_error(bridge, str(exc))


def emit_error(bridge: WsBridge, message: str) -> None:
    log_sidecar_ws_error(message)
    bridge.emit({"type": "error", "message": message})


async def run_server(host: str, port: int, *, takeover: bool = False) -> int:
    paths = AgentPaths.discover()
    configure_sidecar_logging(paths)
    from interface_lock import (
        AcquireStatus,
        InterfaceLockError,
        InterfaceLockGuard,
        acquire_lock,
        lock_conflict_payload,
    )

    lock_guard = InterfaceLockGuard(paths, "electron")
    result = acquire_lock(paths, "electron", takeover=False)
    if result.status == AcquireStatus.CONFLICT and result.holder is not None:
        if not takeover:
            print(json.dumps(lock_conflict_payload(result.holder), ensure_ascii=False), flush=True)
            return 2
        try:
            lock_guard.acquire(takeover=True, interactive_takeover=False)
        except InterfaceLockError as exc:
            print(
                json.dumps({"ready": False, "error": "lock_conflict", "message": str(exc)}),
                flush=True,
            )
            return 2
    else:
        lock_guard._held = True
        atexit.register(lock_guard.release)

    handler = WsSessionHandler(paths)

    try:
        async with serve(handler.handle, host, port) as server:
            sockets = server.sockets
            if not sockets:
                raise RuntimeError("server failed to bind")
            actual_port = sockets[0].getsockname()[1]
            print(json.dumps({"ready": True, "host": host, "port": actual_port}), flush=True)
            await server.serve_forever()
    except OSError as exc:
        # S-52: dual bind / stale listener — clear JSON, do not silent-fail or hang.
        print(
            json.dumps(
                {
                    "ready": False,
                    "error": "port_in_use",
                    "port": port,
                    "message": f"端口 {port} 已被占用（{exc}）。请关闭另一实例，或先结束占用进程后再启动。",
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        lock_guard.release()
        return 3
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="my-agent WebSocket sidecar for Electron")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=8765, help="0 = OS-assigned port")
    parser.add_argument(
        "--takeover",
        action="store_true",
        help="Take over session lock from CLI when starting Electron sidecar",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return asyncio.run(run_server(args.host, args.port, takeover=args.takeover))
    except KeyboardInterrupt:
        return 0


def _demo() -> int:
    """Lightweight self-check without starting a live socket."""
    import shutil

    paths = AgentPaths.discover()
    demo_dir = paths.data / "sessions" / "_repl_ws_demo"
    if demo_dir.is_dir():
        shutil.rmtree(demo_dir)

    events: list[dict[str, Any]] = []

    bridge = WsBridge(emit=events.append, paths=paths)
    session = create_new(paths, conversation_id="_repl_ws_demo")
    repl = _build_repl(session, paths, bridge)
    assert callable(repl.agent.executor.confirm_fn)
    assert callable(repl.agent.executor.on_event)
    print("[PASS] T-904a: WsBridge patches ConversationRepl")

    bridge.emit_session_state(session)
    assert events[0]["type"] == "session.banner"
    assert events[1]["type"] == "session.memory"
    assert events[2]["type"] == "session.history"
    assert events[3]["type"] == "evolve.proposals"
    print("[PASS] T-904a: session.banner + memory + history + proposals")

    delivered: list[str] = []

    def fake_confirm(preview: str, allow: bool) -> str:
        delivered.append(preview)
        return "n"

    repl.agent.executor.confirm_fn = fake_confirm
    preview = build_confirm_preview("run_evolved", {"tool_name": "write_text", "arguments": {}})
    choice = repl.agent.executor._ask_confirm("run_evolved", {"tool_name": "write_text", "arguments": {}}, None)
    assert choice == "n"
    print("[PASS] T-904a: confirm_fn wired to executor")

    import threading
    import time

    bridge2 = WsBridge(emit=lambda _e: None, paths=paths)
    confirm_result: list[str] = []
    pending_id: list[str] = []

    def wait_confirm() -> None:
        choice = bridge2.confirm_fn("tool preview", False)
        confirm_result.append(choice)

    worker = threading.Thread(target=wait_confirm, daemon=True)
    worker.start()
    for _ in range(50):
        if bridge2._pending_confirm_id:
            pending_id.append(bridge2._pending_confirm_id)
            break
        time.sleep(0.01)
    assert pending_id, "confirm.request id not issued"
    assert bridge2.deliver_confirm(pending_id[0], "y")
    worker.join(timeout=2)
    assert confirm_result == ["y"]
    print("[PASS] T-904a: deliver_confirm unblocks confirm_fn thread")

    # Phase 14 C1: stale request_id must not spin (BUG-008).
    bridge_stale = WsBridge(emit=lambda _e: None, paths=paths)
    stale_events: list[dict[str, Any]] = []
    bridge_stale.emit = stale_events.append  # type: ignore[method-assign]
    stale_result: list[str] = []

    def wait_confirm_stale() -> None:
        stale_result.append(bridge_stale.confirm_fn("stale preview", False))

    worker_stale = threading.Thread(target=wait_confirm_stale, daemon=True)
    worker_stale.start()
    for _ in range(50):
        if bridge_stale._pending_confirm_id:
            break
        time.sleep(0.01)
    pending_stale = bridge_stale._pending_confirm_id
    assert pending_stale, "confirm.request id not issued for stale test"
    assert bridge_stale.deliver_confirm("not-the-pending-id", "y") is False
    assert bridge_stale.deliver_confirm(pending_stale, "y") is True
    worker_stale.join(timeout=2)
    assert stale_result == ["y"], stale_result
    stale_choices = [
        e.get("choice") for e in stale_events if e.get("type") == "confirm.done"
    ]
    assert "stale" in stale_choices
    assert "y" in stale_choices
    print("[PASS] confirm stale id does not spin")

    # Phase 14 C1 defense-in-depth: wrong id already in queue is discarded.
    bridge_spin = WsBridge(emit=lambda _e: None, paths=paths)
    spin_events: list[dict[str, Any]] = []
    bridge_spin.emit = spin_events.append  # type: ignore[method-assign]
    spin_result: list[str] = []
    bridge_spin._confirm_queue.put(("orphan-id", "y"))

    def wait_confirm_spin() -> None:
        spin_result.append(bridge_spin.confirm_fn("spin preview", False))

    worker_spin = threading.Thread(target=wait_confirm_spin, daemon=True)
    worker_spin.start()
    for _ in range(50):
        if bridge_spin._pending_confirm_id:
            break
        time.sleep(0.01)
    pending_spin = bridge_spin._pending_confirm_id
    assert pending_spin
    time.sleep(0.05)  # let loop drain orphan
    assert bridge_spin.deliver_confirm(pending_spin, "n")
    worker_spin.join(timeout=2)
    assert spin_result == ["n"], spin_result
    assert any(
        e.get("type") == "confirm.done" and e.get("choice") == "stale" for e in spin_events
    )
    print("[PASS] confirm orphan queue entry discarded without spin")

    # Phase 15: Stop must wake a pending confirm immediately.
    cancel_events: list[dict[str, Any]] = []
    bridge_cancel = WsBridge(
        emit=cancel_events.append,
        paths=paths,
        confirm_timeout=1.0,
    )
    bridge_cancel._turn_busy.set()
    cancel_result: list[str] = []
    worker_cancel = threading.Thread(
        target=lambda: cancel_result.append(
            bridge_cancel.confirm_fn("cancel preview", False)
        ),
        daemon=True,
    )
    worker_cancel.start()
    for _ in range(50):
        if bridge_cancel._pending_confirm_id:
            break
        time.sleep(0.01)
    assert bridge_cancel.request_cancel()
    worker_cancel.join(timeout=1)
    assert cancel_result == ["n"]
    assert any(
        event.get("type") == "confirm.done"
        and event.get("choice") == "cancelled"
        for event in cancel_events
    )
    print("[PASS] turn.cancel unblocks pending confirm")

    from runtime_guards import stall_watchdog_sec, turn_wall_sec

    assert turn_wall_sec() == 900.0
    assert stall_watchdog_sec() == 0.0
    print("[PASS] T-1513/T-1510: TURN_WALL_SEC + STALL_WATCHDOG_SEC defaults")

    async def _check_user_message_no_nested_lock() -> None:
        """Regression: _handle_incoming holds TURN_LOCK; _dispatch must not re-acquire."""
        handler = WsSessionHandler(paths)
        demo_session = create_new(paths, conversation_id="_repl_ws_lock_demo")
        demo_repl = _build_repl(demo_session, paths, WsBridge(emit=lambda _e: None, paths=paths))

        async def _dispatch_under_lock() -> None:
            async with TURN_LOCK:
                await handler._dispatch(
                    {"type": "user.message", "text": "新会话"},
                    demo_repl,
                    WsBridge(emit=lambda _e: None, paths=paths),
                )

        await asyncio.wait_for(_dispatch_under_lock(), timeout=5)

    asyncio.run(_check_user_message_no_nested_lock())
    lock_demo_dir = paths.data / "sessions" / "_repl_ws_lock_demo"
    if lock_demo_dir.is_dir():
        shutil.rmtree(lock_demo_dir)
    print("[PASS] T-904a: user.message dispatch does not nest TURN_LOCK")

    async def _check_new_session_refreshes_state() -> None:
        refresh_events: list[dict[str, Any]] = []
        refresh_bridge = WsBridge(emit=refresh_events.append, paths=paths)
        refresh_session = create_new(paths, conversation_id="_repl_ws_new_session_demo")
        refresh_repl = _build_repl(refresh_session, paths, refresh_bridge)
        handler = WsSessionHandler(paths)

        await handler._dispatch(
            {"type": "user.message", "text": "新会话"},
            refresh_repl,
            refresh_bridge,
        )

        types = [event["type"] for event in refresh_events]
        assert refresh_repl.session.conversation_id != "_repl_ws_new_session_demo"
        assert "session.banner" in types
        assert "session.history" in types
        return refresh_session.conversation_id, refresh_repl.session.conversation_id

    old_id, new_id = asyncio.run(_check_new_session_refreshes_state())
    for cid in {old_id, new_id}:
        demo_path = paths.data / "sessions" / cid
        if demo_path.is_dir():
            shutil.rmtree(demo_path)
    print("[PASS] T-904a: user.message 新会话 emits session.banner + session.history")

    from host_scope_api import run_t1008_demo

    run_t1008_demo(paths)

    if demo_dir.is_dir():
        shutil.rmtree(demo_dir)
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--demo":
        raise SystemExit(_demo())
    raise SystemExit(main())
