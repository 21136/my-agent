"""Local WebSocket API for Electron desktop shell (DESKTOP.md §4–5, TASKS T-904a)."""

from __future__ import annotations

import argparse
import asyncio
import atexit
import json
import logging
import os
import queue
import re
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
from llm_client import LLMError, resolve_model_entry  # noqa: F401
from llm_client import StreamHandlers
from context import session_memory_event, validate_llm_model_switch
from execution_lifecycle import ExecutionLifecycle
from llm_models import models_list_event
from main import ConversationRepl, ReplConfig
from paths import AgentPaths
from sidecar_logging import SIDECAR_LOGGER_NAME, configure_sidecar_logging, log_sidecar_exception, log_sidecar_ws_error
from host_scope import load_host_scope
from runtime_guards import TurnWatchdog, stall_watchdog_sec, turn_wall_sec
from session import Session, SessionError, HarnessMismatchError, create_new, create_sandbox_evolution_session, emit_corruption_notices, list_session_summaries, load_session_for_harness, normalize_turn_mode, resume_desktop_or_create, session_banner_event, session_history_event, turn_mode_label
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
T9_T4_OWNED_PATHS = ("evolve/tool-catalog/INDEX.md", "evolve/tool-catalog/recovery/main.cjs", "evolve/tool-catalog/recovery/service.cjs", "evolve/tool-catalog/recovery/manifest.json")


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

    _RUN_SCOPED_EVENT_TYPES = frozenset(
        {
            "turn.start",
            "turn.end",
            "turn.notice",
            "llm.pending",
            "assistant.delta",
            "assistant.done",
            "reasoning.delta",
            "activity.update",
            "confirm.request",
            "confirm.done",
            "tool.start",
            "tool.end",
            "tool.progress",
            "turn.evidence",
            "terminal.plan.state",
        }
    )

    emit: EmitFn
    paths: AgentPaths
    _input_queue: queue.Queue[str] = field(default_factory=queue.Queue)
    _confirm_queue: queue.Queue[tuple[str, str]] = field(default_factory=queue.Queue)
    _awaiting_input: threading.Event = field(default_factory=threading.Event)
    _turn_busy: threading.Event = field(default_factory=threading.Event)
    cancel_event: threading.Event = field(default_factory=threading.Event)
    confirm_timeout: float = field(default_factory=confirm_timeout_sec)
    turn_watchdog: TurnWatchdog | None = field(default=None, repr=False)
    lifecycle: ExecutionLifecycle = field(default_factory=ExecutionLifecycle, repr=False)
    lifecycle_conversation_id: str = field(default="", init=False, repr=False)
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

    def _scope_run_event(self, event: dict[str, Any]) -> dict[str, Any]:
        """Attach the authoritative run identity to execution-owned events."""
        if event.get("type") not in self._RUN_SCOPED_EVENT_TYPES:
            return event
        if event.get("run_id"):
            return event
        snapshot = self.lifecycle.snapshot()
        if snapshot is None:
            return event
        payload = dict(event)
        payload["run_id"] = snapshot.run_id
        return payload

    def begin_turn(self, *, mode: str | None = None) -> bool:
        mode = mode or "ordinary"
        try:
            snapshot = self.lifecycle.start_or_activate(mode)
        except RuntimeError as exc:
            self.emit({"type": "notice", "text": f"执行未启动：{exc}"})
            return False
        self.cancel_event.clear()
        if self.turn_watchdog is not None:
            self.turn_watchdog.wall_sec = turn_wall_sec()
            self.turn_watchdog.begin()
        self.emit(snapshot.to_event())
        return True

    def begin_continuation(self, mode: str = "continuation") -> str | None:
        try:
            snapshot = self.lifecycle.enqueue(mode)
        except RuntimeError as exc:
            self.emit({"type": "notice", "text": f"续接未排队：{exc}"})
            return None
        self.emit(snapshot.to_event())
        return snapshot.run_id

    def finish_execution(
        self,
        finish_reason: str,
        *,
        ok: bool,
        final_state: str | None = None,
        run_id: str | None = None,
    ) -> bool:
        snapshot, changed = self.lifecycle.finish(
            finish_reason,
            ok=ok,
            final_state=final_state,
            run_id=run_id,
        )
        if changed and snapshot is not None:
            self.emit(snapshot.to_event())
        return changed

    def emit_turn_event(self, event: dict[str, Any]) -> None:
        self.emit(self._scope_run_event(dict(event)))

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
        return self.confirm_with_receipt(preview, allow_approve_all)[0]

    def confirm_with_receipt(self, preview: str, allow_approve_all: bool) -> tuple[str, str]:
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
                self._scope_run_event(
                    {
                        "type": "confirm.request",
                        "request_id": request_id,
                        "preview": preview,
                        "allow_approve_all": allow_approve_all,
                    }
                )
            )
        self._touch_progress()
        deadline = time.monotonic() + self.confirm_timeout
        try:
            while True:
                if self.cancel_event.is_set():
                    if not context_switch:
                        self.emit(
                            self._scope_run_event(
                                {
                                    "type": "confirm.done",
                                    "request_id": request_id,
                                    "choice": "cancelled",
                                }
                            )
                        )
                    self._touch_progress()
                    return "n", request_id
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
                            self._scope_run_event(
                                {
                                    "type": "confirm.done",
                                    "request_id": request_id,
                                    "choice": choice,
                                }
                            )
                        )
                    self._touch_progress()
                    return ("n" if choice == "cancelled" else choice), request_id
                # C1: never put stale ids back — that spins forever (BUG-008).
                self.emit(
                    {
                        "type": "notice",
                        "text": "忽略过期确认（request_id 不匹配当前请求）",
                    }
                )
                self.emit(
                    self._scope_run_event(
                        {
                            "type": "confirm.done",
                            "request_id": resp_id,
                            "choice": "stale",
                        }
                    )
                )
        except queue.Empty:
            # C2: always pair confirm.done with confirm_fn return (BUG-008b).
            self.emit({"type": "notice", "text": "确认超时，已跳过"})
            if not context_switch:
                self.emit(
                    self._scope_run_event(
                        {
                            "type": "confirm.done",
                            "request_id": request_id,
                            "choice": "timeout",
                        }
                    )
                )
            self._touch_progress()
            return "n", request_id
        finally:
            self._pending_confirm_id = None

    def on_executor_event(self, event_type: str, payload: dict[str, Any]) -> None:
        if event_type in {"tool.start", "tool.end", "tool.progress"}:
            if self.turn_watchdog is not None and event_type in {"tool.start", "tool.end"}:
                self.turn_watchdog.note_progress_event(event_type)
                if event_type == "tool.start":
                    self.turn_watchdog.pause_wall()
                else:
                    self.turn_watchdog.resume_wall()
            self.emit(self._scope_run_event({"type": event_type, **payload}))
            return
        if event_type == "turn.evidence":
            self.emit(self._scope_run_event({"type": event_type, **payload}))
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
            "review.subagent.start",
            "review.subagent.done",
            "notice",
        }:
            self.emit({"type": event_type, **payload})
            return
        logging.getLogger(SIDECAR_LOGGER_NAME).debug(
            "unhandled executor event %s: %s",
            event_type,
            json.dumps(payload, ensure_ascii=False),
        )

    def try_route_input(self, text: str) -> bool:
        if self._awaiting_input.is_set():
            self._input_queue.put(text)
            return True
        return False

    def request_cancel(self) -> bool:
        current = self.lifecycle.snapshot()
        lifecycle_active = current is not None and current.state in {
            "queued",
            "running",
            "stopping",
        }
        turn_busy = self._turn_busy.is_set()
        if not turn_busy and not lifecycle_active:
            self.emit({"type": "notice", "text": "当前无进行中的回合"})
            return False

        snapshot, changed = self.lifecycle.request_stop("user")
        if changed and snapshot is not None:
            self.emit(snapshot.to_event())
        if self.cancel_event.is_set():
            return True
        if self.turn_watchdog is not None:
            if turn_busy:
                self.turn_watchdog.request_user_cancel()
        self.cancel_event.set()
        if turn_busy and self._awaiting_input.is_set():
            self._input_queue.put("")
        if turn_busy and self._cancel_turn is not None:
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
                self._scope_run_event(
                    {
                        "type": "confirm.done",
                        "request_id": request_id,
                        "choice": "stale",
                    }
                )
            )
            return False
        self._confirm_queue.put((request_id, choice))
        return True

    def emit_session_state(self, session: Session) -> None:
        quick_memory = not session._messages_fully_loaded
        self.emit(_session_banner_payload(session))
        # History before heavy project.state so chat hydrates first (UI-5972).
        self.emit(session_history_event(session))
        self.emit(
            session_memory_event(session, quick=quick_memory)
        )
        self.emit(models_list_event(self.paths))
        self.emit(_proposals_payload(self.paths))
        if session.meta.project_id:
            from project_api import project_state_payload

            self.emit(project_state_payload(session, self.paths))

    def emit_assistant(self, text: str) -> None:
        # C9: allow empty text so desktop always gets assistant.done.
        self.emit(self._scope_run_event({"type": "assistant.done", "text": text or ""}))

    def emit_content_delta(self, text: str) -> None:
        if text:
            self._touch_progress()
            self.emit(self._scope_run_event({"type": "assistant.delta", "text": text}))

    def emit_reasoning_delta(self, text: str) -> None:
        if text:
            self.emit(self._scope_run_event({"type": "reasoning.delta", "text": text}))

    def stream_handlers(self) -> StreamHandlers:
        return StreamHandlers(
            on_content_delta=self.emit_content_delta,
            on_reasoning_delta=self.emit_reasoning_delta,
        )


def _patch_repl(
    repl: ConversationRepl,
    bridge: WsBridge,
    *,
    evolution_client: Any | None = None,
) -> None:
    repl.stream_handlers = bridge.stream_handlers()
    repl.agent.stream_handlers = repl.stream_handlers
    repl.agent.executor.confirm_fn = bridge.confirm_fn
    repl.agent.executor.session.evolution_confirm_with_receipt = bridge.confirm_with_receipt
    repl.agent.executor.on_event = bridge.on_executor_event
    repl.agent.on_turn_event = bridge.emit_turn_event
    repl.agent.bind_cancel_event(bridge.cancel_event)
    repl.agent.bind_cancel_finish_reason(bridge.resolve_cancel_finish_reason)
    if evolution_client is not None:
        repl.agent.executor.evolution_client = evolution_client

    def _turn_wall_pause() -> None:
        if bridge.turn_watchdog is not None:
            bridge.turn_watchdog.pause_wall()

    def _turn_wall_resume() -> None:
        if bridge.turn_watchdog is not None:
            bridge.turn_watchdog.resume_wall()

    repl.agent.bind_turn_wall_hooks(_turn_wall_pause, _turn_wall_resume)
    bridge._cancel_turn = repl.agent.request_cancel

    original_rebind = repl._rebind_agent

    def rebind() -> None:
        original_rebind()
        repl.stream_handlers = bridge.stream_handlers()
        if evolution_client is not None:
            repl.agent.executor.evolution_client = evolution_client
        repl.agent.stream_handlers = repl.stream_handlers
        repl.agent.executor.confirm_fn = bridge.confirm_fn
        repl.agent.executor.session.evolution_confirm_with_receipt = bridge.confirm_with_receipt
        repl.agent.executor.on_event = bridge.on_executor_event
        repl.agent.on_turn_event = bridge.emit_turn_event
        repl.agent.bind_cancel_event(bridge.cancel_event)
        repl.agent.bind_cancel_finish_reason(bridge.resolve_cancel_finish_reason)

        def _turn_wall_pause() -> None:
            if bridge.turn_watchdog is not None:
                bridge.turn_watchdog.pause_wall()

        def _turn_wall_resume() -> None:
            if bridge.turn_watchdog is not None:
                bridge.turn_watchdog.resume_wall()

        repl.agent.bind_turn_wall_hooks(_turn_wall_pause, _turn_wall_resume)
        bridge._cancel_turn = repl.agent.request_cancel

    repl._rebind_agent = rebind  # type: ignore[method-assign]


def _build_repl(
    session: Session,
    paths: AgentPaths,
    bridge: WsBridge,
    *,
    evolution_client: Any | None = None,
) -> ConversationRepl:
    repl = ConversationRepl.from_session(
        session,
        paths=paths,
        input_fn=bridge.input_fn,
        output_fn=bridge.output_fn,
        config=ReplConfig(),
        stream_handlers=bridge.stream_handlers(),
    )
    repl.assistant_output_fn = bridge.emit_assistant
    _patch_repl(repl, bridge, evolution_client=evolution_client)
    return repl


def _emit_session_list(bridge: WsBridge, paths: AgentPaths) -> None:
    """S-1: proactively push updated session list to keep dropdown fresh."""
    summaries = list_session_summaries(paths)
    bridge.emit({"type": "session.list", "sessions": summaries})


def _repl_refreshes_session_state(line: str) -> bool:
    """REPL meta-commands that replace session overlay / chat history on desktop."""
    lower = line.strip().casefold()
    return lower in {"新会话", "new", "换主题"}


def _refresh_repl_project_binding(repl: ConversationRepl) -> None:
    """Keep the long-lived executor in sync with project controls changed by the UI."""
    repl.agent.executor.session.refresh_bound_project_meta()


async def _run_line(
    repl: ConversationRepl,
    bridge: WsBridge,
    line: str,
    paths: AgentPaths,
    *,
    lock_held: bool = False,
) -> None:
    repl.last_turn_finish_reason = None
    from session import is_terminal_harness

    execution_mode = "terminal" if is_terminal_harness(repl.session.meta) else "ordinary"
    if not bridge.begin_turn(mode=execution_mode):
        return
    run_snapshot = bridge.lifecycle.snapshot()
    if run_snapshot is not None:
        bridge.lifecycle_conversation_id = repl.session.conversation_id
    bind_host_run_context = getattr(repl.agent, "bind_host_run_context", None)
    if run_snapshot is not None and callable(bind_host_run_context):
        # The lifecycle is authoritative; never manufacture or persist a host run id.
        executor_session = getattr(getattr(repl.agent, "executor", None), "session", None)
        authorization_id = getattr(executor_session, "evolution_authorization_id", "")
        bind_host_run_context(run_snapshot.run_id, authorization_id)
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
            bridge.emit({"type": "notice", "text": "会话已保存"})
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
        if bridge.finish_execution(finish_reason, ok=ok):
            bridge.emit_turn_event(
                {"type": "turn.end", "ok": ok, "finish_reason": finish_reason}
            )


class WsSessionHandler:
    def __init__(
        self,
        paths: AgentPaths,
        *,
        evolution_client: Any | None = None,
        evolution_scope_id: str = "",
        evolution_owned_paths: tuple[str, ...] = (),
        managed_readonly_context: Any | None = None,
    ) -> None:
        self.paths = paths
        self.evolution_client = evolution_client
        self.evolution_scope_id = evolution_scope_id
        self.evolution_owned_paths = tuple(evolution_owned_paths)
        self.managed_readonly_context = managed_readonly_context
        self.evolution_confirmation_ttl_seconds = 600
        self._repl_lock = threading.Lock()
        if managed_readonly_context is None:
            from file_stage import FileStageStore

            self._file_stage = FileStageStore()
        else:
            self._file_stage = None

    async def handle(self, websocket: ServerConnection) -> None:
        if self.managed_readonly_context is not None:
            await self._handle_managed_readonly(websocket)
            return
        loop = asyncio.get_running_loop()
        outbox: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

        def emit(event: dict[str, Any]) -> None:
            loop.call_soon_threadsafe(outbox.put_nowait, event)

        bridge = WsBridge(emit=emit, paths=self.paths)
        session = resume_desktop_or_create(self.paths)
        repl = _build_repl(
            session, self.paths, bridge, evolution_client=self.evolution_client
        )

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

    async def _handle_managed_readonly(self, websocket: ServerConnection) -> None:
        context = self.managed_readonly_context
        try:
            context.revalidate()
        except Exception:
            await websocket.send(json.dumps({"type": "error", "code": "managed_readonly_unavailable"}))
            return
        async for raw in websocket:
            try:
                message = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                await websocket.send(json.dumps({"type": "error", "code": "managed_readonly_rejected"}))
                continue
            if type(message) is not dict or type(message.get("type")) is not str:
                await websocket.send(json.dumps({"type": "error", "code": "managed_readonly_rejected"}))
                continue
            try:
                context.revalidate()
            except Exception:
                await websocket.send(json.dumps({"type": "error", "code": "managed_readonly_unavailable"}))
                return
            request_type = message["type"]
            request_id = message.get("request_id")
            if request_id is not None and (type(request_id) is not str or len(request_id) > 128):
                await websocket.send(json.dumps({"type": "error", "code": "managed_readonly_rejected"}))
                continue
            if request_type == "managed.health" and set(message) <= {"type", "request_id"}:
                response = {"type": "managed.health", "ready": True, "access": "read_only",
                            "installation_id": context.installation_id, "release_id": context.release_id,
                            "binding_digest": context.binding_digest, "runtime_digest": context.runtime_digest}
                if hasattr(context, "host_nonce"):
                    response.update(context.revalidate())
                    response.update(nonce=context.nonce, host_nonce=context.host_nonce, profile_intent_digest=context.profile_intent_digest)
                if request_id is not None:
                    response["request_id"] = request_id
            elif request_type == "managed.ping" and set(message) <= {"type", "request_id"}:
                response = {"type": "managed.pong"}
                if request_id is not None:
                    response["request_id"] = request_id
            elif request_type == "session.list" and set(message) <= {"type", "request_id"}:
                response = {"type": "session.list", "sessions": []}
                if request_id is not None:
                    response["request_id"] = request_id
            else:
                response = {"type": "error", "code": "managed_readonly_rejected"}
                if request_id is not None:
                    response["request_id"] = request_id
            await websocket.send(json.dumps(response, ensure_ascii=False, separators=(",", ":")))

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

        if msg_type == "evolution.scripted":
            async with TURN_LOCK:
                try:
                    await self._dispatch(message, repl, bridge)
                except Exception as exc:  # pragma: no cover
                    emit_error(bridge, str(exc))
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
        if (
            self.paths._fixed_code_root is not None
            and msg_type in _FIXED_HOST_E0_DISABLED_MESSAGES
        ):
            emit_error(bridge, "fixed Host E0 does not support chat or evolve mutations")
            return

        if msg_type in {"evolution.begin", "evolution.inspect", "evolution.stop"}:
            await self._dispatch_evolution(message, repl, bridge)
            return
        if msg_type == "evolution.scripted":
            await self._dispatch_scripted_evolution(message, repl, bridge)
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
            if any(item.vision_input for item in attachments):
                model_entry = resolve_model_entry(repl.session.meta.llm_model)
                if model_entry is None or not model_entry.supports_image_input:
                    bridge.emit(
                        {
                            "type": "turn.notice",
                            "level": "warn",
                            "text": "当前模型不支持识图，请切换到支持视觉的模型。",
                        }
                    )
            active_doc = message.get("active_doc")
            repl.session._turn_active_doc = (
                active_doc.strip() if isinstance(active_doc, str) else ""
            )
            active_sel = message.get("active_doc_selection")
            repl.session._turn_active_doc_selection = (
                active_sel if isinstance(active_sel, str) else ""
            )
            turn_mode_raw = message.get("turn_mode")
            if isinstance(turn_mode_raw, str) and turn_mode_raw.strip():
                try:
                    repl.session.set_turn_mode(normalize_turn_mode(turn_mode_raw.strip()))
                    repl.agent.executor.session.turn_mode = repl.session.meta.turn_mode
                except SessionError as exc:
                    emit_error(bridge, str(exc))
                    return
            from companion_world_context import (
                format_companion_world_context,
                is_companion_world_context_allowed,
            )
            from session import is_terminal_harness

            world_context = None
            if is_companion_world_context_allowed(
                active_shell=repl.session.meta.active_shell,
                project_root=repl.session.meta.project_root,
                terminal=is_terminal_harness(repl.session.meta),
            ):
                world_context = format_companion_world_context(
                    message.get("companion_world_context")
                )
            repl.session._turn_companion_world_context = world_context
            # TURN_LOCK is held by _handle_incoming for user.message / command.
            try:
                await _run_line(repl, bridge, line, self.paths, lock_held=True)
            finally:
                repl.session._turn_companion_world_context = None
            repl.session._turn_active_doc = ""
            repl.session._turn_active_doc_selection = ""
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
            previous_session_id = repl.session.conversation_id
            previous_project_id = (repl.session.meta.project_id or "").strip()
            await _run_line(repl, bridge, name.strip(), self.paths, lock_held=True)
            current_session_id = repl.session.conversation_id
            current_project_id = (repl.session.meta.project_id or "").strip()
            if (
                current_session_id != previous_session_id
                or current_project_id != previous_project_id
            ):
                bridge.emit(
                    {
                        "type": "context.switch.done",
                        "request_id": f"command-{uuid.uuid4()}",
                        "choice": "y",
                        "applied": True,
                        "action": "project.create" if current_project_id else "session.new",
                        "target": current_project_id or "current",
                        "project_id": current_project_id or None,
                        "session_id": current_session_id,
                        "session_replaced": current_session_id != previous_session_id,
                        "message": "命令已切换当前工作上下文",
                    }
                )
            if _repl_refreshes_session_state(name.strip()):
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
            sid = session_id.strip()

            def _open_session() -> Session:
                return load_session_for_harness(
                    self.paths,
                    sid,
                    expected="desktop",
                )

            try:
                repl.session = await asyncio.to_thread(_open_session)
                repl._rebind_agent()
                bridge.emit_session_state(repl.session)
                emit_corruption_notices(bridge.emit, repl.session)
            except (SessionError, HarnessMismatchError) as exc:
                emit_error(bridge, str(exc))
            return

        if msg_type == "session.create_sandbox":
            if bridge._turn_busy.is_set():
                emit_error(bridge, "回合进行中，结束后再开实验会话")
                return

            def _create_sandbox() -> Session:
                return create_sandbox_evolution_session(self.paths)

            try:
                repl.session = await asyncio.to_thread(_create_sandbox)
                repl._rebind_agent()
                bridge.emit_session_state(repl.session)
                emit_corruption_notices(bridge.emit, repl.session)
            except SessionError as exc:
                emit_error(bridge, str(exc))
            return

        if msg_type == "session.refresh":
            bridge.emit_session_state(repl.session)
            return

        if msg_type == "companion.sync":
            from loader import sync_companion_profile_to_evolve

            partner_name = str(message.get("partner_name") or "小魔王")
            raw_stickers = message.get("stickers")
            stickers = [str(s) for s in raw_stickers] if isinstance(raw_stickers, list) else []
            partner_note = str(message.get("partner_note") or "")
            days_together = int(message.get("days_together") or 1) if isinstance(message.get("days_together"), (int, float)) else 1
            late_night_count = int(message.get("late_night_count") or 0) if isinstance(message.get("late_night_count"), (int, float)) else 0
            care_count = int(message.get("care_count") or 0) if isinstance(message.get("care_count"), (int, float)) else 0
            sync_companion_profile_to_evolve(
                self.paths.evolve,
                partner_name=partner_name,
                stickers=stickers,
                partner_note=partner_note,
                days_together=days_together,
                late_night_count=late_night_count,
                care_count=care_count,
            )
            return

        if msg_type == "session.set_model":
            model_raw = message.get("model")
            if not isinstance(model_raw, str) or not model_raw.strip():
                emit_error(bridge, "session.set_model requires model")
                return
            if bridge._turn_busy.is_set():
                emit_error(bridge, "回合进行中，结束后再切换模型")
                return
            try:
                canonical = validate_llm_model_switch(repl.session, model_raw.strip())
                repl.session.set_llm_model(canonical)
                repl.session.save()
            except SessionError as exc:
                emit_error(bridge, str(exc))
                return
            bridge.emit_session_state(repl.session)
            bridge.emit({"type": "notice", "text": f"已切换模型：{repl.session.meta.llm_model}"})
            return

        if msg_type == "session.models":
            bridge.emit(models_list_event(self.paths))
            return

        if msg_type == "session.set_turn_mode":
            mode_raw = message.get("mode")
            if mode_raw not in {"agent", "ask"}:
                emit_error(bridge, "session.set_turn_mode requires mode='agent'|'ask'")
                return
            if bridge._turn_busy.is_set():
                emit_error(bridge, "回合进行中，结束后再切换模式")
                return
            repl.session.set_turn_mode(mode_raw)
            repl.agent.executor.session.turn_mode = repl.session.meta.turn_mode
            repl.session.save()
            bridge.emit_session_state(repl.session)
            label = "动手 (Agent)" if repl.session.meta.turn_mode == "agent" else "只聊 (Ask)"
            bridge.emit({"type": "notice", "text": f"已切换模式：{label}"})
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

        if isinstance(msg_type, str) and msg_type.startswith("llm_keys."):
            await self._dispatch_llm_keys(message, bridge)
            return

        if isinstance(msg_type, str) and msg_type.startswith("services."):
            await self._dispatch_services(message, bridge)
            return

        if msg_type in {"terminal.list", "terminal.output", "terminal.close", "terminal.prune"}:
            await self._dispatch_terminal(message, bridge)
            return

        if isinstance(msg_type, str) and msg_type.startswith("project.doc."):
            await self._dispatch_doc(message, repl, bridge)
            return

        if isinstance(msg_type, str) and (
            msg_type.startswith("project.")
        ):
            await self._dispatch_project(message, repl, bridge)
            return

        emit_error(bridge, f"unknown type: {msg_type}")

    async def _dispatch_evolution(
        self,
        message: dict[str, Any],
        repl: ConversationRepl,
        bridge: WsBridge,
    ) -> None:
        from evolution.context import attach_evolution, normalize_host_run_id
        from session import normalize_evolution_id

        msg_type = message.get("type")
        client = self.evolution_client
        conversation_id = repl.session.conversation_id
        if bridge.lifecycle_conversation_id != conversation_id:
            emit_error(bridge, "当前执行生命周期不属于此会话；请先在当前会话完成一轮")
            return
        snapshot = bridge.lifecycle.snapshot()
        host_run_id = normalize_host_run_id(snapshot.run_id if snapshot else "")
        if not host_run_id:
            emit_error(bridge, "稳定执行生命周期身份不可用；未创建演化绑定")
            return
        if client is None:
            emit_error(bridge, "可信演化控制客户端未连接")
            return

        if msg_type == "evolution.begin":
            if repl.session.meta.harness != "desktop":
                emit_error(bridge, "仅当前桌面会话可以绑定演化")
                return
            if repl.session.meta.evolution_id:
                emit_error(bridge, "当前会话已经绑定演化；请先查看现有状态")
                return
            scope_id = normalize_evolution_id(self.evolution_scope_id)
            owned_paths = self.evolution_owned_paths
            begin_binding = getattr(client, "begin_binding", None)
            binder = getattr(repl.agent, "bind_host_run_context", None)
            executor = getattr(repl.agent, "executor", None)
            executor_session = getattr(executor, "session", None)
            if (
                not scope_id
                or not callable(begin_binding)
                or not isinstance(owned_paths, (tuple, list))
                or not 1 <= len(owned_paths) <= 256
                or any(
                    not isinstance(item, str)
                    or not item
                    or item.startswith(("/", "\\"))
                    or "\\" in item
                    or ":" in item
                    or any(part in {"", ".", ".."} for part in item.split("/"))
                    for item in owned_paths
                )
                or len({item.casefold() for item in owned_paths}) != len(owned_paths)
            ):
                emit_error(bridge, "可信演化范围/owned_paths/绑定服务未配置；未创建演化绑定")
                return
            if not callable(binder) or executor is None or executor_session is None:
                emit_error(bridge, "当前 Agent 不支持安全演化上下文注入")
                return
            from datetime import datetime, timedelta, timezone

            expiry = datetime.now(timezone.utc) + timedelta(seconds=self.evolution_confirmation_ttl_seconds)
            expires_at = expiry.isoformat(timespec="seconds").replace("+00:00", "Z")
            preview = (
                "确认将当前会话绑定到 local_managed 演化范围。候选运行与当前用户权限同级，"
                "不是操作系统隔离；不会复制真实会话数据。只允许修改下列可信配置路径。"
                f"\nowned_paths: {', '.join(owned_paths)}"
                f"\n授权期限: 10 分钟，截止 {expires_at} (UTC)"
                f"\nconversation_id: {conversation_id}\nscope_id: {scope_id}"
            )
            try:
                choice, confirmation_id = await asyncio.to_thread(
                    bridge.confirm_with_receipt, preview, False
                )
                if not isinstance(choice, str) or choice.strip().casefold() not in {"y", "yes", "confirm", "allow"}:
                    bridge.emit({"type": "evolution.denied", "conversation_id": conversation_id})
                    return
                binding = await asyncio.to_thread(
                    begin_binding,
                    conversation_id,
                    scope_id,
                    confirmation_id,
                    expires_at,
                )
                if not isinstance(binding, dict) or set(binding) != {"evolution_id", "authorization_id"}:
                    raise ValueError("trusted supervisor returned an invalid binding")
                evolution_id = normalize_evolution_id(binding.get("evolution_id"))
                authorization_id = normalize_evolution_id(binding.get("authorization_id"))
                if not evolution_id or not authorization_id:
                    raise ValueError("trusted supervisor returned invalid binding identities")
                await asyncio.to_thread(
                    attach_evolution,
                    repl.session,
                    evolution_id,
                    client=client,
                    host_run_id=host_run_id,
                )
                executor.evolution_client = client
                executor_session.evolution_id = evolution_id
                binder(host_run_id, authorization_id)
                executor_session.evolution_stop_authorization_id = ""
                executor_session.evolution_stop_instance_id = ""
                bridge.emit(
                    {
                        "type": "evolution.bound",
                        "evolution_id": evolution_id,
                        "conversation_id": conversation_id,
                        "host_run_id": host_run_id,
                    }
                )
            except Exception as exc:
                emit_error(bridge, f"演化绑定失败：{exc}")
            return

        evolution_id = normalize_evolution_id(repl.session.meta.evolution_id)
        if not evolution_id:
            emit_error(bridge, "当前会话尚未绑定演化")
            return

        executor = getattr(repl.agent, "executor", None)
        executor_session = getattr(executor, "session", None)
        inspect_response = await asyncio.to_thread(
            client.inspect, evolution_id, conversation_id, host_run_id
        )
        projection = (
            inspect_response.get("result")
            if isinstance(inspect_response, dict)
            and inspect_response.get("status") == "succeeded"
            else None
        )
        if (
            not isinstance(projection, dict)
            or projection.get("evolution_id") != evolution_id
            or projection.get("conversation_id") != conversation_id
        ):
            emit_error(bridge, "可信监督器未返回当前会话的有效演化状态")
            return

        if msg_type == "evolution.stop":
            current_instance_id = projection.get("current_instance_id")
            if current_instance_id is not None:
                request_method = getattr(client, "request", None)
                resolve_stop_authorization = getattr(client, "resolve_stop_authorization", None)
                if not callable(request_method) or not callable(resolve_stop_authorization):
                    emit_error(bridge, "可信精确实例 stop grant 恢复服务不可用；未发送停止请求")
                    return
                try:
                    authorization_id = normalize_evolution_id(
                        await asyncio.to_thread(resolve_stop_authorization, conversation_id, evolution_id, current_instance_id)
                    )
                except Exception:
                    authorization_id = ""
                if not authorization_id:
                    emit_error(bridge, "Supervisor 未能恢复当前实例的 stop grant；未发送停止请求")
                    return
                if executor_session is not None:
                    executor_session.evolution_stop_authorization_id = authorization_id
                    executor_session.evolution_stop_instance_id = current_instance_id
                import uuid

                request = {
                    "protocol": "evolution/1.0",
                    "operation_id": str(uuid.uuid4()),
                    "evolution_id": evolution_id,
                    "conversation_id": conversation_id,
                    "host_run_id": host_run_id,
                    "expected_state_revision": projection.get("state_revision"),
                    "authorization_id": authorization_id,
                    "action": "stop",
                    "payload": {"instance_id": current_instance_id},
                }
                result = await asyncio.to_thread(request_method, request)
                if not isinstance(result, dict) or result.get("status") not in {"succeeded", "failed", "unknown", "cancelled"}:
                    emit_error(bridge, "可信监督器停止结果格式无效")
                    return
                if result.get("status") != "succeeded":
                    bridge.emit({"type": "evolution.stop.result", "conversation_id": conversation_id, "host_run_id": host_run_id, "result": result})
                    return
                refreshed = await asyncio.to_thread(client.inspect, evolution_id, conversation_id, host_run_id)
                projection = refreshed.get("result") if isinstance(refreshed, dict) and refreshed.get("status") == "succeeded" else None
                if not isinstance(projection, dict) or projection.get("evolution_id") != evolution_id or projection.get("conversation_id") != conversation_id:
                    emit_error(bridge, "停止后无法重新核对可信监督器状态")
                    return
                if projection.get("candidate_status") == "stopped" and executor_session is not None:
                    executor_session.evolution_stop_authorization_id = ""
                    executor_session.evolution_stop_instance_id = ""

        allowed_projection = {
            key: projection[key]
            for key in (
                "evolution_id", "conversation_id", "state_revision",
                "current_candidate_id", "current_instance_id",
                "pending_operation_ids", "unknown_operation_ids",
                "isolation_mode", "candidate_status",
                "prototype_control_verified", "scripted_recovery_verified",
                "agent_recovery_verified", "independent_review",
                "main_host_replaced", "late_event_count",
            )
            if key in projection
        }
        bridge.emit(
            {
                "type": "evolution.state",
                "evolution_id": evolution_id,
                "conversation_id": conversation_id,
                "host_run_id": host_run_id,
                "projection": allowed_projection,
            }
        )

    async def _dispatch_scripted_evolution(
        self,
        message: dict[str, Any],
        repl: ConversationRepl,
        bridge: WsBridge,
    ) -> None:
        """T9-only deterministic driver. It uses the current Session, Host client and confirmation queue; no model turn is started."""
        from evolution.contracts import ContractError, validate_action_payload
        from evolution.client import _UUID_RE

        fields = {"type", "request_id", "conversation_id", "operation"}
        operation = message.get("operation")
        if operation == "action":
            fields |= {"action", "payload"}
        elif operation == "late_ready":
            fields |= {"evolution_id", "candidate_id", "instance_id", "instance_sequence"}
        if set(message) != fields or message.get("type") != "evolution.scripted":
            return
        request_id = message.get("request_id")
        conversation_id = message.get("conversation_id")
        if not isinstance(request_id, str) or not _UUID_RE.fullmatch(request_id) or conversation_id != repl.session.conversation_id:
            return
        if len(json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > 65_536:
            bridge.emit({"type": "evolution.scripted.result", "request_id": request_id, "conversation_id": conversation_id, "status": "failed", "error": {"code": "budget_exceeded", "message": "T9 driver frame exceeds 64 KiB"}})
            return
        if self.evolution_client is None or tuple(self.evolution_owned_paths) != T9_T4_OWNED_PATHS or not self.evolution_scope_id:
            bridge.emit({"type": "evolution.scripted.result", "request_id": request_id, "conversation_id": conversation_id, "status": "failed", "error": {"code": "permission_denied", "message": "T9 scripted driver is available only in the fixed T4 H1 profile"}})
            return
        if bridge._turn_busy.is_set():
            bridge.emit({"type": "evolution.scripted.result", "request_id": request_id, "conversation_id": conversation_id, "status": "failed", "error": {"code": "permission_denied", "message": "T9 scripted operations cannot overlap a model turn"}})
            return
        result: Any = None
        try:
            if operation == "lifecycle.begin":
                if bridge.lifecycle_conversation_id:
                    raise ContractError("identity_mismatch", "scripted lifecycle already belongs to a conversation")
                if not bridge.begin_turn(mode="scripted_recovery"):
                    raise ContractError("outcome_unknown", "scripted lifecycle did not start")
                bridge.lifecycle_conversation_id = conversation_id
                snapshot = bridge.lifecycle.snapshot()
                if snapshot is None:
                    raise ContractError("outcome_unknown", "scripted lifecycle identity is unavailable")
                result = {"conversation_id": conversation_id, "host_run_id": snapshot.run_id}
            else:
                snapshot = bridge.lifecycle.snapshot()
                if bridge.lifecycle_conversation_id != conversation_id or snapshot is None:
                    raise ContractError("permission_denied", "T9 scripted lifecycle is not active for this Session")
                if operation == "bind":
                    await self._dispatch_evolution({"type": "evolution.begin"}, repl, bridge)
                    evolution_id = str(getattr(repl.session.meta, "evolution_id", ""))
                    if not _UUID_RE.fullmatch(evolution_id):
                        raise ContractError("permission_denied", "current Session was not bound to the fixed T4 scope")
                    result = {"conversation_id": conversation_id, "evolution_id": evolution_id, "host_run_id": snapshot.run_id}
                elif operation == "action":
                    action = message.get("action")
                    payload = message.get("payload")
                    if not isinstance(action, str) or action not in {"inspect", "patch", "snapshot", "build", "launch", "stop"} or not isinstance(payload, dict):
                        raise ContractError("invalid_request", "T9 scripted action or payload is invalid")
                    checked = validate_action_payload(action, payload)
                    executor = getattr(repl.agent, "executor", None)
                    session = getattr(executor, "session", None)
                    client = self.evolution_client
                    if executor is None or session is None or not getattr(session, "evolution_id", ""):
                        raise ContractError("permission_denied", "fixed T4 Session binding is unavailable")
                    client_run = getattr(session, "host_run_id", "")
                    if client_run != snapshot.run_id:
                        raise ContractError("identity_mismatch", "scripted action host_run_id changed")
                    if action == "snapshot":
                        source_list = await asyncio.to_thread(client.request, {
                            "protocol": "evolution/1.0", "operation_id": str(uuid.uuid4()), "evolution_id": session.evolution_id,
                            "conversation_id": conversation_id, "host_run_id": snapshot.run_id, "expected_state_revision": 0,
                            "authorization_id": str(uuid.uuid4()), "action": "inspect", "payload": {"query": "source_list"},
                        })
                        if source_list.get("status") != "succeeded" or not isinstance(source_list.get("result"), dict):
                            raise ContractError("outcome_unknown", "trusted source inventory is unavailable")
                        checked = {**checked, "workspace_revision": source_list["result"].get("workspace_revision")}
                        checked = validate_action_payload(action, checked)
                    if action == "inspect":
                        request = {"protocol": "evolution/1.0", "operation_id": str(uuid.uuid4()), "evolution_id": session.evolution_id, "conversation_id": conversation_id, "host_run_id": snapshot.run_id, "expected_state_revision": 0, "authorization_id": str(uuid.uuid4()), "action": action, "payload": checked}
                        result = await asyncio.to_thread(client.request, request)
                    else:
                        from tools.builtin.evolution import run as run_evolution
                        tool_result = await asyncio.to_thread(run_evolution, {"action": action, "payload": checked}, client=client, session=session)
                        result = tool_result.to_dict()
                elif operation == "late_ready":
                    evolution_id = message.get("evolution_id")
                    candidate_id = message.get("candidate_id")
                    instance_id = message.get("instance_id")
                    sequence = message.get("instance_sequence")
                    if not isinstance(evolution_id, str) or not _UUID_RE.fullmatch(evolution_id) or not isinstance(candidate_id, str) or not isinstance(instance_id, str) or not _UUID_RE.fullmatch(instance_id) or type(sequence) is not int or sequence < 1:
                        raise ContractError("invalid_request", "T9 stale-ready identity is invalid")
                    result = await asyncio.to_thread(self.evolution_client.deliver_t9_late_ready, conversation_id, evolution_id, candidate_id, instance_id, sequence)
                elif operation == "lifecycle.finish":
                    bridge.finish_execution("scripted_recovery", ok=True)
                    bridge.end_turn()
                    bridge.lifecycle_conversation_id = ""
                    result = {"conversation_id": conversation_id, "finished": True}
                else:
                    raise ContractError("unsupported_capability", "T9 scripted operation is unsupported")
            bridge.emit({"type": "evolution.scripted.result", "request_id": request_id, "conversation_id": conversation_id, "status": "succeeded", "result": result, "error": None})
        except Exception as exc:
            code = getattr(exc, "code", "invalid_request")
            bridge.emit({"type": "evolution.scripted.result", "request_id": request_id, "conversation_id": conversation_id, "status": "failed", "result": None, "error": {"code": str(code), "message": str(exc)[:512]}})
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
            _refresh_repl_project_binding(repl)
            if isinstance(payload, dict) and "_events" in payload:
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
        from project_api import ProjectApiError, dispatch_project_message

        try:
            payload = await asyncio.to_thread(
                dispatch_project_message,
                repl.session,
                self.paths,
                message,
            )
            _refresh_repl_project_binding(repl)
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
        except Exception as exc:
            log_sidecar_exception("project dispatch failed", exc)
            emit_error(bridge, f"project request failed: {exc}")

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

    async def _dispatch_llm_keys(
        self,
        message: dict[str, Any],
        bridge: WsBridge,
    ) -> None:
        from llm_models_api import dispatch_llm_keys_message
        from llm_secrets import LlmSecretsError

        try:
            payloads = await asyncio.to_thread(
                dispatch_llm_keys_message,
                self.paths,
                message,
            )
            for payload in payloads:
                bridge.emit(payload)
        except LlmSecretsError as exc:
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

    async def _dispatch_terminal(
        self,
        message: dict[str, Any],
        bridge: WsBridge,
    ) -> None:
        from terminal_api import TerminalApiError, dispatch_terminal_message

        try:
            payload = await asyncio.to_thread(
                dispatch_terminal_message,
                self.paths,
                message,
                confirm_fn=bridge.confirm_fn,
            )
            bridge.emit(payload)
        except TerminalApiError as exc:
            message_type = message.get("type")
            done_type = {
                "terminal.output": "terminal.output.done",
                "terminal.close": "terminal.close.done",
            }.get(message_type, "terminal.list.done")
            payload: dict[str, Any] = {
                "type": done_type,
                "ok": False,
                "error": str(exc),
            }
            if done_type == "terminal.list.done":
                payload["sessions"] = []
            if done_type == "terminal.output.done":
                payload.update(
                    {
                        "output": "",
                        "cursor": 0,
                        "next_cursor": 0,
                        "cursor_reset": False,
                        "truncated": False,
                    }
                )
            request_id = message.get("request_id")
            if isinstance(request_id, str) and request_id.strip():
                payload["request_id"] = request_id.strip()
            session_id = message.get("session_id")
            if isinstance(session_id, str) and session_id.strip():
                payload["session_id"] = session_id.strip()
            bridge.emit(payload)


_MAX_EVOLUTION_BOOTSTRAP_BYTES = 65_536
_EVOLUTION_BOOTSTRAP_PROTOCOL = "my-agent-evolution-sidecar/1.0"
_EVOLUTION_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
_EVOLUTION_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_EVOLUTION_TOKEN_RE = re.compile(r"^[0-9a-f]{64}$")
_FIXED_HOST_E0_DISABLED_MESSAGES = frozenset(
    {
        "user.message",
        "command",
        "proposal.accept",
        "proposal.reject",
        "companion.sync",
    }
)


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("private bootstrap contains duplicate fields")
        value[key] = item
    return value


def parse_evolution_sidecar_bootstrap(raw: bytes) -> dict[str, Any]:
    """Parse the one-shot parent-to-sidecar private stdin frame; never log its contents."""
    if not isinstance(raw, bytes) or not raw or len(raw) > _MAX_EVOLUTION_BOOTSTRAP_BYTES:
        raise ValueError("private bootstrap is empty or exceeds the frame limit")
    if not raw.endswith(b"\n") or b"\n" in raw[:-1] or b"\r" in raw:
        raise ValueError("private bootstrap must be one newline-terminated frame")
    try:
        payload = json.loads(raw[:-1].decode("utf-8", errors="strict"), object_pairs_hook=_reject_duplicate_json_keys)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("private bootstrap is invalid JSON") from exc
    from evolution.contracts import canonical_json

    if type(payload) is not dict or canonical_json(payload) + b"\n" != raw:
        raise ValueError("private bootstrap must use canonical JSON")
    fields = {"protocol", "control_release", "pipe_name", "host_instance_id", "host_token", "scope_id", "owned_paths"}
    if set(payload) != fields or payload.get("protocol") != _EVOLUTION_BOOTSTRAP_PROTOCOL:
        raise ValueError("private bootstrap fields or protocol are invalid")
    release = payload.get("control_release")
    release_fields = {"release_id", "root", "node_executable", "python_executable", "manifest_path", "manifest_digest"}
    if type(release) is not dict or set(release) != release_fields:
        raise ValueError("private bootstrap control release is malformed")
    if not isinstance(release.get("release_id"), str) or not _EVOLUTION_HASH_RE.fullmatch(release["release_id"]) or release.get("manifest_digest") != release["release_id"]:
        raise ValueError("private bootstrap control release identity is invalid")
    for key in ("root", "node_executable", "python_executable", "manifest_path"):
        if not isinstance(release.get(key), str) or not Path(release[key]).is_absolute():
            raise ValueError("private bootstrap control release paths must be absolute")
    pipe_name = payload.get("pipe_name")
    pipe_prefix = "\\\\.\\pipe\\"
    if not isinstance(pipe_name, str) or not pipe_name.startswith(pipe_prefix) or len(pipe_name) > 240 or "\\" in pipe_name[len(pipe_prefix):]:
        raise ValueError("private bootstrap pipe identity is invalid")
    for key in ("host_instance_id", "scope_id"):
        if not isinstance(payload.get(key), str) or not _EVOLUTION_UUID_RE.fullmatch(payload[key]):
            raise ValueError("private bootstrap identity is invalid")
    if not isinstance(payload.get("host_token"), str) or not _EVOLUTION_TOKEN_RE.fullmatch(payload["host_token"]):
        raise ValueError("private bootstrap credential is malformed")
    owned_paths = payload.get("owned_paths")
    if type(owned_paths) is not list or not 1 <= len(owned_paths) <= 256:
        raise ValueError("private bootstrap owned_paths is outside its limit")
    folded: set[str] = set()
    for item in owned_paths:
        if not isinstance(item, str) or not item or item.startswith(("/", "\\")) or "\\" in item or ":" in item or any(part in {"", ".", ".."} for part in item.split("/")):
            raise ValueError("private bootstrap owned_paths contains an unsafe path")
        key = item.casefold()
        if key in folded:
            raise ValueError("private bootstrap owned_paths contains a case collision")
        folded.add(key)
    return payload


def read_evolution_sidecar_bootstrap(stream: Any) -> dict[str, Any]:
    raw = stream.readline(_MAX_EVOLUTION_BOOTSTRAP_BYTES + 1)
    if len(raw) > _MAX_EVOLUTION_BOOTSTRAP_BYTES or stream.read(1):
        raise ValueError("private bootstrap must contain exactly one bounded frame")
    return parse_evolution_sidecar_bootstrap(raw)


def emit_error(bridge: WsBridge, message: str) -> None:
    log_sidecar_ws_error(message)
    bridge.emit({"type": "error", "message": message})


async def _run_managed_application_server(host, port, app, control_stream):
    """Closed application transport; never construct a legacy Session/REPL/model."""
    from managed_application import MAX_MESSAGE_BYTES, canonical
    from managed_host_entry import _reject_duplicate_keys

    async def handle(websocket):
        async for raw in websocket:
            try:
                if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_MESSAGE_BYTES:
                    raise ValueError("bounded JSON object required")
                request = json.loads(raw, object_pairs_hook=_reject_duplicate_keys,
                                     parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite JSON")))
                response = app.handle(request)
            except (ValueError, TypeError, UnicodeError):
                response = {"type": "error", "code": "managed_application_rejected"}
            await websocket.send(json.dumps(response, ensure_ascii=False, separators=(",", ":")))

    loop = asyncio.get_running_loop()
    controls = asyncio.Queue(maxsize=8)

    def reader():
        try:
            raw = control_stream.readline(16 * 1024 + 1)
            loop.call_soon_threadsafe(controls.put_nowait, raw)
        except Exception:
            loop.call_soon_threadsafe(lambda: controls.put_nowait(b""))

    try:
        async with serve(handle, host, port, max_size=MAX_MESSAGE_BYTES) as listener:
            if not listener.sockets:
                raise OSError("managed listener is unavailable")
            ready = {"ready": True, "mode": "managed_application", "access": "fenced_application",
                     "host": host, "port": listener.sockets[0].getsockname()[1], **app.metadata()}
            print(json.dumps(ready, separators=(",", ":")), flush=True)
            while True:
                control_reader = threading.Thread(target=reader, daemon=True)
                control_reader.start()
                raw = await controls.get()
                # Each reader owns one bounded frame. Never leave a buffered
                # stdin read alive when a clean shutdown exits the interpreter.
                control_reader.join(timeout=1)
                try:
                    if not raw or len(raw) > 16 * 1024 or not raw.endswith(b"\n") or b"\n" in raw[:-1] or b"\r" in raw:
                        raise ValueError("managed control stream ended or exceeded bounds")
                    frame = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
                    if canonical(frame) + b"\n" != raw:
                        raise ValueError("managed control must be canonical")
                    response = app.control(frame)
                    print(json.dumps(response, separators=(",", ":")), flush=True)
                    if response["type"] == "managed.application.stopped":
                        return 0
                except Exception:
                    app.abandon()
                    print('{"ready":false,"error":"managed_application_maintenance_required"}', flush=True)
                    return 4
    except Exception:
        app.abandon()
        print('{"ready":false,"error":"managed_application_startup_rejected"}', flush=True)
        return 4


async def run_server(
    host: str,
    port: int,
    *,
    takeover: bool = False,
    evolution_bootstrap: dict[str, Any] | None = None,
    fixed_host_layout: dict[str, Any] | None = None,
    managed_readonly_context: Any | None = None,
    managed_application: Any | None = None,
    managed_control_stream: Any | None = None,
) -> int:
    if managed_application is not None:
        from managed_application import ManagedApplication
        if (not isinstance(managed_application, ManagedApplication) or managed_control_stream is None
                or host not in {"127.0.0.1", "::1"} or type(port) is not int or not 0 <= port <= 65535
                or takeover or evolution_bootstrap is not None or fixed_host_layout is not None or managed_readonly_context is not None):
            return 4
        return await _run_managed_application_server(host, port, managed_application, managed_control_stream)
    lock_guard = None
    if managed_readonly_context is not None:
        if (host not in {"127.0.0.1", "::1"} or type(port) is not int or not 0 <= port <= 65535
                or takeover or fixed_host_layout is not None):
            print(json.dumps({"ready": False, "error": "managed_readonly_startup_rejected"}), flush=True)
            return 4
        try:
            paths = AgentPaths.from_managed_readonly(managed_readonly_context)
            if getattr(managed_readonly_context, "_source_probe", False):
                from managed_readonly import ManagedReadonlyAuditGuard, read_roots_for_context

                runtime_roots = [Path(sys.base_prefix).resolve(strict=True)]
                if Path(sys.prefix).resolve(strict=True) != runtime_roots[0]:
                    runtime_roots.append(Path(sys.prefix).resolve(strict=True))
                ManagedReadonlyAuditGuard(read_roots_for_context(managed_readonly_context, runtime_roots)).install()
        except Exception:
            print(json.dumps({"ready": False, "error": "managed_readonly_startup_rejected"}), flush=True)
            return 4
        if evolution_bootstrap is not None:
            print(json.dumps({"ready": False, "error": "managed_readonly_startup_rejected"}), flush=True)
            return 4
    else:
        paths = (
            AgentPaths.from_fixed_layout(
                fixed_host_layout["code_root"], fixed_host_layout["instance_root"]
            )
            if fixed_host_layout is not None
            else AgentPaths.discover()
        )
        configure_sidecar_logging(paths)
        from interface_lock import (
            AcquireStatus,
            InterfaceLockGuard,
            acquire_lock,
            lock_conflict_payload,
        )

        if takeover:
            print(
                json.dumps(
                    {
                        "ready": False,
                        "error": "takeover_disabled",
                        "message": "interface lock takeover is disabled (TM-9)",
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            return 2

        lock_guard = InterfaceLockGuard(paths, "electron")
        result = acquire_lock(paths, "electron", takeover=False)
        if result.status == AcquireStatus.CONFLICT and result.holder is not None:
            print(json.dumps(lock_conflict_payload(result.holder), ensure_ascii=False), flush=True)
            return 2
        lock_guard._held = True
        atexit.register(lock_guard.release)

    evolution_client = None
    evolution_scope_id = ""
    evolution_owned_paths: tuple[str, ...] = ()
    if evolution_bootstrap is not None:
        if managed_readonly_context is not None:
            return 4
        from evolution.client import EvolutionClient

        evolution_client = EvolutionClient(
            evolution_bootstrap["control_release"],
            evolution_bootstrap["pipe_name"],
            evolution_bootstrap["host_instance_id"],
            evolution_bootstrap["host_token"],
        )
        evolution_scope_id = evolution_bootstrap["scope_id"]
        evolution_owned_paths = tuple(evolution_bootstrap["owned_paths"])
    handler = WsSessionHandler(
        paths,
        evolution_client=evolution_client,
        evolution_scope_id=evolution_scope_id,
        evolution_owned_paths=evolution_owned_paths,
        managed_readonly_context=managed_readonly_context,
    )

    try:
        async with serve(handler.handle, host, port) as server:
            sockets = server.sockets
            if not sockets:
                raise RuntimeError("server failed to bind")
            actual_port = sockets[0].getsockname()[1]
            if managed_readonly_context is None:
                ready = {
                    "ready": True,
                    "host": host,
                    "port": actual_port,
                    "features": {"tokeness_tools_reasoning_none": True},
                }
            else:
                ready = {
                    "ready": True,
                    "mode": "managed_readonly",
                    "access": "read_only",
                    "installation_id": managed_readonly_context.installation_id,
                    "release_id": managed_readonly_context.release_id,
                    "binding_digest": managed_readonly_context.binding_digest,
                    "runtime_digest": managed_readonly_context.runtime_digest,
                    "nonce": managed_readonly_context.nonce,
                    "host": host,
                    "port": actual_port,
                }
                if hasattr(managed_readonly_context, "host_nonce"):
                    ready.update(managed_readonly_context.revalidate())
                    ready.update(mode="managed_host_readonly", host_nonce=managed_readonly_context.host_nonce,
                                 profile_intent_digest=managed_readonly_context.profile_intent_digest)
            print(json.dumps(ready, separators=(",", ":")), flush=True)
            if managed_readonly_context is not None and hasattr(managed_readonly_context, "host_nonce"):
                from managed_application import canonical
                from managed_host_entry import _reject_duplicate_keys
                if managed_control_stream is None:
                    return 4
                loop = asyncio.get_running_loop()
                result = loop.create_future()

                def read_readonly_control():
                    try:
                        raw = managed_control_stream.readline(16 * 1024 + 1)
                    except Exception:
                        raw = b""
                    loop.call_soon_threadsafe(result.set_result, raw)

                reader = threading.Thread(target=read_readonly_control, daemon=True)
                reader.start()
                raw = await result
                reader.join(timeout=1)
                try:
                    if not raw or len(raw) > 16 * 1024 or not raw.endswith(b"\n") or b"\n" in raw[:-1] or b"\r" in raw:
                        raise ValueError("readonly private control frame rejected")
                    control = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
                    if canonical(control) + b"\n" != raw:
                        raise ValueError("readonly control must be canonical")
                    stopped = managed_readonly_context.control(control)
                    print(json.dumps(stopped, separators=(",", ":")), flush=True)
                    return 0
                except Exception:
                    print('{"ready":false,"error":"managed_host_readonly_control_rejected"}', flush=True)
                    return 4
            await server.serve_forever()
    except OSError as exc:
        if managed_readonly_context is not None:
            print(json.dumps({"ready": False, "error": "managed_readonly_bind_failed"}), flush=True)
            return 3
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
        if lock_guard is not None:
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
    bootstrap = parser.add_mutually_exclusive_group()
    bootstrap.add_argument(
        "--evolution-bootstrap-stdin",
        action="store_true",
        help="Read one private, bounded evolution bootstrap frame from parent stdin",
    )
    bootstrap.add_argument(
        "--fixed-host-layout-stdin",
        action="store_true",
        help="Read one private, bounded fixed Host layout frame from parent stdin",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.fixed_host_layout_stdin:
        # This module imports non-stdlib dependencies before main(); fixed Host
        # startup is supported only through the cold -I -B -S entry wrapper.
        print(json.dumps({"ready": False, "error": "fixed_host_entry_required"}), flush=True)
        return 4
    try:
        evolution_bootstrap = None
        fixed_host_layout = None
        if args.evolution_bootstrap_stdin:
            try:
                evolution_bootstrap = read_evolution_sidecar_bootstrap(sys.stdin.buffer)
            except Exception:
                print(json.dumps({"ready": False, "error": "evolution_bootstrap_invalid"}), flush=True)
                return 4
        elif args.fixed_host_layout_stdin:
            try:
                from fixed_host import read_fixed_host_layout

                fixed_host_layout = read_fixed_host_layout(sys.stdin.buffer)
            except Exception:
                print(json.dumps({"ready": False, "error": "fixed_host_layout_invalid"}), flush=True)
                return 4
        return asyncio.run(
            run_server(
                args.host,
                args.port,
                takeover=args.takeover,
                evolution_bootstrap=evolution_bootstrap,
                fixed_host_layout=fixed_host_layout,
            )
        )
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
