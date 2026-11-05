"""Local WebSocket API for Electron desktop shell (DESKTOP.md §4–5, TASKS T-904a)."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import atexit
import json
import math
import hashlib
import logging
import os
import queue
import re
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
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
from llm_client import (
    HOST_INTERNAL_METADATA_KEY, LLMApiError, LLMCancelledError, LLMError,
    LLMNetworkError, LLMTimeoutError, resolve_model_entry,
)  # noqa: F401
from llm_client import StreamHandlers
from context import session_memory_event, validate_llm_model_switch
from loader import scan_memory_index
from execution_lifecycle import ExecutionLifecycle
from llm_models import models_list_event
from main import ConversationRepl, ReplConfig, WorkRecordControllerFactory
from paths import AgentPaths
from day_journal_follow import attach_session_excerpts, resolve_recall_project_id
from project_session_store import attach_project_store_pointers
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
_DUPLICATE_CAPTURE_RUN = object()
_UNCONFIRMED_CAPTURE_RUN = object()
_WORK_RECORDS_OVERVIEW_QUERY = "work-records.overview.query"
_WORK_RECORDS_OVERVIEW_RESPONSE = "work-records.overview.result"
_WORK_CARD_MESSAGE_TYPES = frozenset({
    "work-card.create", "work-card.get", "work-card.seen", "work-card.list",
    "work-card.result.view", "work-card.snapshot.view", "work-card.received", "work-card.receipt.continue",
})
PROJECT_WORK_CARD_MERGE_SECONDS = 30.0
WORK_CARD_RECEIPT_RETRY_BATCH = 20
_CAPTURE_RECONCILIATION_MESSAGES = {
    "already_recorded": "检测到本请求此前已有记录；本轮未自动执行或重执行，请核对既有记录和运行状态。",
    "request_conflict": "检测到请求编号对应内容冲突；本轮未自动执行，请核对请求内容与既有记录。",
    "stale_context": "记录或会话快照发生变化，无法确认此前是否已有对应记录；为防重复副作用，本轮未自动执行，请核对。",
    "storage_uncertain": "记录写入结果无法确认，可能已有提交；为防重复副作用，本轮未自动执行，请核对当前记录及运行状态。",
    "invalid_request": "缺少可用于安全去重的稳定请求编号，无法排除已有请求或执行；本轮未自动执行，请核对后再处理。",
    "unavailable": "无法校验请求重放身份，不能排除已有记录或执行；本轮未自动执行，请核对当前记录和运行状态。",
    "uncertain": "分类或上下文校验未能确认，不能排除并发提交；本轮未自动执行，请核对当前记录和运行状态。",
}
DEFAULT_HOST = "127.0.0.1"
DEFAULT_CONFIRM_TIMEOUT_SEC = 90.0
TURN_LOCK = asyncio.Lock()
SESSION_SWITCH_WAIT_SECONDS = 10.0
T9_T4_OWNED_PATHS = ("evolve/tool-catalog/INDEX.md", "evolve/tool-catalog/recovery/main.cjs", "evolve/tool-catalog/recovery/service.cjs", "evolve/tool-catalog/recovery/manifest.json")
_DAY_JOURNAL_QUERY_RE = re.compile(
    r"^\s*查\s*(今天|昨天|昨日|\d{4}-\d{2}-\d{2})\s*(?:的\s*)?(?:项目\s*)?日记\s*[？?。！!]*\s*$"
)
_DAY_JOURNAL_SUMMARY_RE = re.compile(r"^\s*\u603b\u7ed3\s*\u4eca\u5929\s*\u7684?\s*\u9879\u76ee?\s*\u65e5\u8bb0\s*[\u3002\uff01!\uff1f?]*\s*$")


def _is_day_journal_summary_request(text: str) -> bool:
    """Only the explicit current-day project-journal wording opts into summarizing."""
    return isinstance(text, str) and _DAY_JOURNAL_SUMMARY_RE.fullmatch(text) is not None


def _shanghai_timezone():
    """Match DayJournal's Windows fallback when neither OS tzdata nor tzdata is installed."""
    try:
        return ZoneInfo("Asia/Shanghai")
    except ZoneInfoNotFoundError:
        return timezone(timedelta(hours=8), "Asia/Shanghai")


def _parse_day_journal_request(text: str, *, now: datetime | None = None) -> str | None:
    """Accept only an explicit day-journal query; do not infer arbitrary dates."""
    if not isinstance(text, str):
        return None
    match = _DAY_JOURNAL_QUERY_RE.fullmatch(text)
    if match is None:
        return None
    token = match.group(1)
    zone = _shanghai_timezone()
    local_now = now or datetime.now(zone)
    if local_now.tzinfo is None:
        local_now = local_now.replace(tzinfo=zone)
    today = local_now.astimezone(zone).date()
    if token == "今天":
        return today.isoformat()
    if token in {"昨天", "昨日"}:
        return (today - timedelta(days=1)).isoformat()
    try:
        return datetime.strptime(token, "%Y-%m-%d").date().isoformat()
    except ValueError:
        return None


def _looks_like_unsupported_day_journal_query(text: str) -> bool:
    """Keep obvious journal lookups local even when their date phrasing is unsupported."""
    return isinstance(text, str) and "日记" in text and bool(re.search(r"查|看看|查看|找出|回顾", text))


def _format_day_journal_result(result: dict[str, Any]) -> str:
    lines = [str(result.get("message") or "日记查询未返回说明。")]
    for record in result.get("records", []):
        if not isinstance(record, dict):
            continue
        lines.append(f"\n{record.get('date', '日期未知')}：")
        projects = record.get("projects") if isinstance(record.get("projects"), list) else []
        lines.append("项目：" + ("、".join(str(item) for item in projects) if projects else "无明确项目记录"))
        summary = record.get("summary")
        summary_meta = record.get("summary_metadata")
        if summary is not None and isinstance(summary_meta, dict) and summary_meta.get("verified") is False:
            source_ids = summary_meta.get("source_ids")
            ids_text = "、".join(item for item in source_ids if isinstance(item, str)) if isinstance(source_ids, list) else "无可展示来源ID"
            lines.append(f"摘要性质：模型摘要，未经核实；来源ID：{ids_text}")
        if summary is None:
            lines.append("日摘要：未生成")
        elif isinstance(summary, str) and not summary.strip():
            lines.append("日摘要：已记录但为空，未生成可用摘要。")
        else:
            lines.append(f"日摘要：{summary}")
        for segment in record.get("segments", []):
            if not isinstance(segment, dict):
                continue
            segment_summary = segment.get("summary")
            segment_meta = segment.get("summary_metadata")
            if segment_summary is not None and isinstance(segment_meta, dict) and segment_meta.get("verified") is False:
                source_ids = segment_meta.get("source_ids")
                ids_text = "、".join(item for item in source_ids if isinstance(item, str)) if isinstance(source_ids, list) else "无可展示来源ID"
                lines.append(f"  摘要性质：模型摘要，未经核实；来源ID：{ids_text}")
            if segment_summary is None:
                detail = "未生成摘要"
            elif isinstance(segment_summary, str) and not segment_summary.strip():
                detail = "摘要为空"
            else:
                detail = segment_summary
            pointer = _format_day_journal_segment_pointer(segment)
            label = segment.get("project_id") or "普通对话"
            lines.append(f"- {label}{pointer}：{detail}")
        store = record.get("project_store")
        if isinstance(store, list) and store:
            lines.append("项目按天会话指针：")
            for entry in store:
                if not isinstance(entry, dict):
                    continue
                pid = entry.get("project_id") or "项目未知"
                day_key = entry.get("date") or record.get("date") or "日期未知"
                for item in entry.get("segments") or []:
                    if not isinstance(item, dict):
                        continue
                    seg_id = item.get("segment_id")
                    session_id = item.get("session_id")
                    if not isinstance(seg_id, str) or not seg_id or not isinstance(session_id, str) or not session_id:
                        continue
                    lines.append(f"  - {pid} {day_key} {seg_id} → {session_id}")
    for excerpt in result.get("session_excerpts") or []:
        if not isinstance(excerpt, dict):
            continue
        session_id = excerpt.get("session_id")
        messages = excerpt.get("messages")
        if not isinstance(session_id, str) or not session_id or not isinstance(messages, list) or not messages:
            continue
        lines.append(f"\n会话原文摘录（{session_id}，最近片段，非全文）：")
        for item in messages:
            if not isinstance(item, dict):
                continue
            role = item.get("role")
            text = item.get("text")
            if role not in {"user", "assistant"} or not isinstance(text, str) or not text:
                continue
            lines.append(f"- {role}：{text}")
    issues = result.get("issues", [])
    if issues:
        labels = {"corrupt": "损坏", "unsafe_path": "路径不安全", "unsafe_root": "目录不安全",
                  "oversized": "记录超出读取上限"}
        for issue in issues:
            if isinstance(issue, dict):
                lines.append(f"\n{issue.get('date') or '日记目录'}：{labels.get(issue.get('status'), '不可读取')}。")
    lines.append("\n跨天仍成立的偏好、约定不在按天日记里，请走记忆。")
    return "\n".join(lines)


def _format_day_journal_segment_pointer(segment: dict[str, Any]) -> str:
    parts: list[str] = []
    segment_id = segment.get("segment_id")
    if isinstance(segment_id, str) and segment_id.strip():
        parts.append(segment_id.strip())
    source = segment.get("source")
    if isinstance(source, dict) and source.get("kind") == "session":
        session_id = source.get("session_id")
        if isinstance(session_id, str) and session_id.strip():
            parts.append(session_id.strip())
    if not parts:
        return ""
    return f"（{' / '.join(parts)}）"


class _DayJournalQuestionPersistenceFailure(Exception):
    """Fail closed before journal work when the query itself cannot be recorded."""


def _canonical_user_message_request_id(value: Any) -> str | None:
    if type(value) is not str:
        return None
    try:
        return value if str(uuid.UUID(value)) == value else None
    except (ValueError, AttributeError):
        return None


def _emit_day_journal_user_message_persisted(
    *, bridge: WsBridge, repl: ConversationRepl, session: Session, request_id: Any,
) -> None:
    canonical_request_id = _canonical_user_message_request_id(request_id)
    closed = getattr(bridge, "_connection_closed", None)
    is_closed = getattr(closed, "is_set", None)
    emit = getattr(bridge, "emit", None)
    if (canonical_request_id is None or repl.session is not session
            or not callable(is_closed) or is_closed() or not callable(emit)):
        return
    try:
        emit({
            "type": "user.message.persisted",
            "session_id": session.conversation_id,
            "request_id": canonical_request_id,
        })
    except Exception:
        # The ACK is observational. Never retry or alter journal handling.
        pass


def _append_day_journal_question(
    session: Session, repl: ConversationRepl, text: str, *,
    on_persisted: Callable[[Session], None] | None = None,
) -> bool:
    if repl.session is not session:
        return False
    try:
        session.append_message({"role": "user", "content": text})
    except Exception:
        return False
    if repl.session is not session:
        return False
    if callable(on_persisted):
        try:
            on_persisted(session)
        except Exception:
            # ACK delivery must not affect the existing route or persistence result.
            pass
    return repl.session is session


def _day_journal_cancel_requested(bridge: WsBridge) -> bool:
    event = getattr(bridge, "cancel_event", None)
    return bool(event is not None and event.is_set())


def _raise_if_day_journal_cancelled(bridge: WsBridge) -> None:
    if _day_journal_cancel_requested(bridge):
        raise asyncio.CancelledError


def _persist_day_journal_assistant(
    session: Session, repl: ConversationRepl, response: str,
) -> str:
    """Append the exact display text once, then save; never expose storage exceptions."""
    if repl.session is not session:
        return "session_changed"
    try:
        session.append_message({"role": "assistant", "content": response})
    except Exception:
        return "assistant_append_failed"
    try:
        session.save()
    except Exception:
        # The message line was appended, but the full Session save was not confirmed.
        return "session_save_failed"
    return "saved"


def confirm_timeout_sec() -> float:
    raw = os.environ.get("CONFIRM_TIMEOUT_SEC", str(int(DEFAULT_CONFIRM_TIMEOUT_SEC)))
    try:
        value = float(raw)
    except ValueError:
        value = DEFAULT_CONFIRM_TIMEOUT_SEC
    return max(0.1, value)



def work_card_batch_confirm_timeout_sec() -> float:
    """Dedicated finite human review window, independent of normal confirms."""
    try:
        value = float(os.environ.get("WORK_CARD_BATCH_CONFIRM_TIMEOUT_SEC", "1800"))
    except (ValueError, TypeError):
        return 1800.0
    if not math.isfinite(value):
        return 1800.0
    return min(3600.0, max(90.0, value))


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


def _session_banner_payload(session: Session, paths: AgentPaths | None = None) -> dict[str, Any]:
    return session_banner_event(session, paths=paths)


def _proposals_payload(paths: AgentPaths) -> dict[str, Any]:
    items = [_proposal_item(record) for record in list_pending_proposals(paths)]
    return {"type": "evolve.proposals", "items": items}


def _memory_item(entry: Any) -> dict[str, Any]:
    return {
        "memory_id": entry.memory_id,
        "topics": list(entry.topics),
        "summary": entry.summary,
        "path": entry.path,
    }


def _memories_payload(paths: AgentPaths) -> dict[str, Any]:
    items = [_memory_item(entry) for entry in scan_memory_index(paths.evolve)]
    return {"type": "evolve.memories", "items": items}


def _role_model_preferences_payload(paths: AgentPaths) -> dict[str, Any]:
    from llm_role_model_preferences import dispatch_llm_role_models_message

    return dispatch_llm_role_models_message(paths, {"type": "llm_role_models.get"})[0]


_POST_TURN_REQUEST_CAPACITY = 4096


@dataclass
class _ParallelChatRequest:
    request_id: str
    work_run_id: str
    session: Session
    cancel_event: threading.Event = field(default_factory=threading.Event)
    client: Any | None = None


@dataclass
class _WorkInstruction:
    request_id: str
    run_id: str
    session_id: str
    text: str = field(repr=False)
    state: str = "queued"
    prepared: bool = False
    reason: str | None = None


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
    _input_request_lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _pending_input_request: tuple[str, str, str] | None = field(default=None, init=False, repr=False)
    _turn_busy: threading.Event = field(default_factory=threading.Event)
    cancel_event: threading.Event = field(default_factory=threading.Event)
    confirm_timeout: float = field(default_factory=confirm_timeout_sec)
    batch_confirm_timeout: float = field(default_factory=work_card_batch_confirm_timeout_sec)
    _session_provider: Callable[[], Any] | None = field(default=None, init=False, repr=False)
    _connection_closed: threading.Event = field(default_factory=threading.Event, init=False, repr=False)
    _post_turn_send_gate: Any | None = field(default=None, init=False, repr=False)
    _post_turn_generation: int = field(default=0, init=False, repr=False)
    _post_turn_seen_requests: set[tuple[str, str]] = field(default_factory=set, init=False, repr=False)
    _post_turn_confirmation: dict[str, Any] | None = field(default=None, init=False, repr=False)
    _post_turn_confirmation_ids: set[str] = field(default_factory=set, init=False, repr=False)
    _auto_record_confirmations: dict[str, dict[str, Any]] = field(default_factory=dict, init=False, repr=False)
    _auto_recognition_queue: Any | None = field(default=None, init=False, repr=False)
    _auto_recognition_session: Session | None = field(default=None, init=False, repr=False)
    _auto_recognition_opt_out: bool = field(default=False, init=False, repr=False)
    _auto_recognition_preference_failed: bool = field(default=False, init=False, repr=False)
    _auto_recognition_request_ids: set[str] = field(default_factory=set, init=False, repr=False)
    _auto_recognition_request_capacity_exhausted: bool = field(default=False, init=False, repr=False)
    _auto_recognition_pending: dict[str, Any] = field(default_factory=dict, init=False, repr=False)
    _auto_recognition_worker: asyncio.Task[Any] | None = field(default=None, init=False, repr=False)
    _auto_recognition_active: dict[str, Any] | None = field(default=None, init=False, repr=False)
    _auto_recognition_turn_generation: int | None = field(default=None, init=False, repr=False)
    _post_turn_approved_digest: tuple[object, str] | None = field(default=None, init=False, repr=False)
    _post_turn_tasks: set[asyncio.Task[Any]] = field(default_factory=set, init=False, repr=False)
    _batch_confirm_lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _batch_confirm_pending: tuple[str, float, queue.Queue[str]] | None = field(default=None, init=False, repr=False)
    _overview_query_lock: threading.Condition = field(default_factory=threading.Condition, init=False, repr=False)
    _result_view_write_lock: threading.RLock = field(default_factory=threading.RLock, init=False, repr=False)
    _active_overview_query: tuple[str, threading.Event] | None = field(default=None, init=False, repr=False)
    _session_binding_transitions: int = field(default=0, init=False, repr=False)
    _active_result_view_leases: dict[object, Session] = field(default_factory=dict, init=False, repr=False)
    _work_card_receipt_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)
    turn_watchdog: TurnWatchdog | None = field(default=None, repr=False)
    lifecycle: ExecutionLifecycle = field(default_factory=ExecutionLifecycle, repr=False)
    lifecycle_conversation_id: str = field(default="", init=False, repr=False)
    _pending_confirm_id: str | None = field(default=None, init=False)
    _cancel_turn: Callable[[], None] | None = field(default=None, init=False, repr=False)
    # Consent belongs to this WebSocket connection, never to the shared handler.
    project_journal_enabled: bool = field(default=False, init=False, repr=False)
    _work_card_update_coalescer: Any | None = field(default=None, init=False, repr=False)
    _work_card_reference_enqueued: set[str] = field(default_factory=set, init=False, repr=False)
    _work_card_reference_lock: threading.RLock = field(default_factory=threading.RLock, init=False, repr=False)
    _work_card_continue_requests: set[tuple[str, str]] = field(default_factory=set, init=False, repr=False)
    _parallel_chat_lock: threading.RLock = field(default_factory=threading.RLock, init=False, repr=False)
    _parallel_chats: dict[str, _ParallelChatRequest] = field(default_factory=dict, init=False, repr=False)
    _parallel_chat_seen_requests: set[str] = field(default_factory=set, init=False, repr=False)
    _work_instruction_lock: threading.RLock = field(default_factory=threading.RLock, init=False, repr=False)
    _work_instructions: dict[str, _WorkInstruction] = field(default_factory=dict, init=False, repr=False)
    _active_message_route_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)
    _active_route_slots: threading.BoundedSemaphore = field(
        default_factory=lambda: threading.BoundedSemaphore(16), init=False, repr=False,
    )
    _active_message_seen_requests: set[str] = field(default_factory=set, init=False, repr=False)

    def __post_init__(self) -> None:
        self.turn_watchdog = TurnWatchdog(
            cancel_event=self.cancel_event,
            cancel_lock=self._result_view_write_lock,
            on_auto_timeout=lambda message: self._on_auto_timeout(message),
            wall_sec=turn_wall_sec(),
            stall_sec=stall_watchdog_sec(),
        )

    def _on_auto_timeout(self, message: str) -> None:
        self.invalidate_post_turn()
        self.emit({"type": "notice", "text": message})
        if self._cancel_turn is not None:
            self._cancel_turn()

    def init_post_turn_send_gate(self, session: Any) -> None:
        """Install one connection-local gate; no post-turn candidate is enabled here."""
        from work_record_post_turn_gate import PostTurnSendGate
        with self._result_view_write_lock:
            if self._connection_closed.is_set():
                raise RuntimeError("connection is closed")
            if self._post_turn_send_gate is not None:
                if self._post_turn_send_gate.is_bound_to(session):
                    return
                raise RuntimeError("post-turn gate is bound to a different Session")
            self._post_turn_send_gate = PostTurnSendGate(session)

    def create_post_turn_ticket(self, session: Any, request_id: str, run_id: str, *, expected_generation: int | None = None, notify_failure: bool = True) -> tuple[str, object | None]:
        """Create a candidate or make bounded replay-capacity failure observable.

        The caller must already have validated the host run. This method does
        not enable the post-turn path and does not authorize outbound content.
        """
        with self._result_view_write_lock:
            try:
                if expected_generation is not None and expected_generation != self._post_turn_generation:
                    return 'stale_context', None
                if (self._post_turn_send_gate is None or self._connection_closed.is_set()
                        or not callable(self._session_provider) or self._session_provider() is not session):
                    return 'unavailable', None
                status, ticket = self._post_turn_send_gate.create_checked(session, request_id, run_id)
            except Exception:
                return 'unavailable', None
        if notify_failure and status == 'capacity_exhausted':
            self.emit({"type": "notice", "text": (
                "当前连接的事后记录安全额度已达上限，本次候选未外发、未写入；"
                "普通对话不受影响。重连前请核对已有记录；重连不会恢复此前确认，"
                "也不会自动重试原请求或重做工作。"
            )})
        elif notify_failure and status == 'busy':
            self.emit({"type": "notice", "text": (
                "已有事后记录确认正在等待或处理，本次候选未外发、未写入；"
                "不会覆盖已有确认或自动重做工作。"
            )})
        return status, ticket

    def post_turn_binding_is_valid(self, ticket: object, session: Any) -> bool:
        """Check a connection-bound ticket without trusting an external Session id."""
        with self._result_view_write_lock:
            try:
                return bool(
                    self._post_turn_send_gate is not None
                    and not self._connection_closed.is_set()
                    and not self.cancel_event.is_set()
                    and callable(self._session_provider)
                    and self._session_provider() is session
                    and self._post_turn_send_gate.is_valid(ticket)
                )
            except Exception:
                return False

    def try_start_post_turn_send(self, ticket: object, session: Any) -> bool:
        """Atomically order a previously approved send against invalidation ingress.

        This is only a permission point, not a network call or delivery claim.
        The caller must release the lock before calling the provider.
        """
        with self._result_view_write_lock:
            if not self.post_turn_binding_is_valid(ticket, session):
                return False
            return self._post_turn_send_gate.start_send(ticket)

    def confirm_post_turn(
        self, preview: str, *, session: Any, ticket: object, approve_outbound: bool = False,
        confirm_subtype: str | None = None,
    ) -> tuple[str, str]:
        """Wait outside TURN_LOCK on an independent, exact-preview confirmation.

        The in-memory confirmation is not an audit record and is never reusable
        across a connection or process restart. Call from a worker, not the WS loop.
        """
        if (type(preview) is not str or not preview or len(preview.encode('utf-8')) > 2 * 1024 * 1024
                or type(approve_outbound) is not bool
                or (confirm_subtype is not None and (type(confirm_subtype) is not str
                    or confirm_subtype != 'work_record_reply_excerpt' or approve_outbound))
                or not math.isfinite(self.confirm_timeout)
                or self.confirm_timeout <= 0 or self.confirm_timeout > 3600):
            return 'n', ''
        approved_digest = None
        if approve_outbound:
            marker = "\n精确外发消息：\n"
            try:
                if not preview.startswith("工作记录单次外发确认\n") or marker not in preview:
                    return 'n', ''
                messages = json.loads(preview.split(marker, 1)[1])
                if type(messages) is not list:
                    return 'n', ''
                approved_digest = hashlib.sha256(json.dumps(
                    messages, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False,
                ).encode('utf-8')).hexdigest()
            except (ValueError, TypeError, json.JSONDecodeError):
                return 'n', ''
        request_id = str(uuid.uuid4())
        responses: queue.Queue[str] = queue.Queue(maxsize=1)
        deadline = time.monotonic() + self.confirm_timeout
        with self._result_view_write_lock:
            if (self._post_turn_confirmation is not None or len(self._post_turn_confirmation_ids) >= 2048
                    or not self.post_turn_binding_is_valid(ticket, session)):
                return 'n', ''
            pending = {'id': request_id, 'session': session, 'ticket': ticket,
                       'responses': responses, 'claimed': False}
            self._post_turn_confirmation = pending
            self._post_turn_confirmation_ids.add(request_id)
            try:
                self.emit({'type': 'confirm.request', 'confirm_kind': 'work_record_post_turn',
                           'session_id': session.conversation_id, 'request_id': request_id,
                           'preview': preview, 'allow_approve_all': False,
                           **({'confirm_subtype': confirm_subtype} if confirm_subtype is not None else {})})
            except Exception:
                self._post_turn_confirmation = None
                self._post_turn_send_gate.cancel()
                return 'n', ''
        result, done = 'n', 'cancelled'
        try:
            while True:
                if not self.post_turn_binding_is_valid(ticket, session):
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    done = 'timeout'
                    break
                try:
                    choice = responses.get(timeout=min(.1, remaining))
                except queue.Empty:
                    continue
                with self._result_view_write_lock:
                    if (time.monotonic() >= deadline
                            or not self.post_turn_binding_is_valid(ticket, session)):
                        done = 'timeout' if time.monotonic() >= deadline else 'cancelled'
                    elif choice == 'y':
                        if not approve_outbound or self._post_turn_send_gate.approve(ticket):
                            if approve_outbound:
                                self._post_turn_approved_digest = (ticket, approved_digest)
                            result, done = 'y', 'y'
                    else:
                        done = 'n'
                break
        finally:
            with self._result_view_write_lock:
                if self._post_turn_confirmation is pending:
                    self._post_turn_confirmation = None
                try:
                    self.emit({'type': 'confirm.done', 'confirm_kind': 'work_record_post_turn',
                               'session_id': session.conversation_id, 'request_id': request_id, 'choice': done,
                               **({'confirm_subtype': confirm_subtype} if confirm_subtype is not None else {})})
                except Exception:
                    self._post_turn_send_gate.cancel()
                    result = 'n'
        return result, request_id

    def _invalidate_auto_record_confirmations(self) -> None:
        """Wake every waiter while the connection binding lock is held."""
        for pending in self._auto_record_confirmations.values():
            pending['invalidated'].set()
            try:
                pending['responses'].put_nowait('n')
            except queue.Full:
                pass

    def confirm_auto_record_candidate(
        self, preview: str, *, session: Any, binding_is_valid: Callable[[], bool],
        work_record_candidate: dict[str, Any] | None = None,
    ) -> tuple[str, str]:
        """Confirm one candidate in a worker, never under TURN_LOCK.

        Caller must revalidate its own ticket and commit under the same binding
        lock: a returned 'y' does not survive cancellation or session changes.
        IDs are connection-local tombstones and never fall through to ordinary
        turn confirmations, including after timeout or duplicate replies.
        """
        try:
            if (type(preview) is not str or not preview
                    or len(preview.encode('utf-8')) > 2 * 1024 * 1024
                    or not callable(binding_is_valid) or not math.isfinite(self.confirm_timeout)
                    or not 0 < self.confirm_timeout <= 3600):
                return 'n', ''
        except (UnicodeError, TypeError, ValueError):
            return 'n', ''
        structured = None
        if work_record_candidate is not None:
            try:
                structured = json.loads(json.dumps(work_record_candidate, ensure_ascii=False, allow_nan=False))
                base_fields = {'title', 'requirement', 'scope', 'session_id', 'request_id', 'run_id',
                               'sequence', 'terminal_state', 'finish_reason'}
                if (type(structured) is not dict or set(structured) not in
                        (base_fields, base_fields | {'operation', 'item_id'})
                        or (set(structured) != base_fields and
                            (structured['operation'] != 'append_requirement' or type(structured['item_id']) is not str))
                        or structured['session_id'] != session.conversation_id):
                    return 'n', ''
            except (TypeError, ValueError, AttributeError, UnicodeError):
                return 'n', ''
        responses: queue.Queue[str] = queue.Queue(maxsize=1)
        invalidated = threading.Event()
        deadline = time.monotonic() + self.confirm_timeout
        with self._result_view_write_lock:
            def valid() -> bool:
                try:
                    return (not invalidated.is_set() and not self._connection_closed.is_set()
                            and not self._session_binding_transitions
                            and callable(self._session_provider) and self._session_provider() is session
                            and binding_is_valid() is True)
                except Exception:
                    return False
            if (not valid() or len(self._auto_record_confirmations) >= 16
                    or len(self._post_turn_confirmation_ids) >= 2048):
                return 'n', ''
            request_id = str(uuid.uuid4())
            if request_id in self._post_turn_confirmation_ids or request_id == self._pending_confirm_id:
                return 'n', ''
            pending = {'responses': responses, 'invalidated': invalidated, 'valid': valid, 'claimed': False}
            self._auto_record_confirmations[request_id] = pending
            self._post_turn_confirmation_ids.add(request_id)
            try:
                self.emit({'type': 'confirm.request', 'confirm_kind': 'work_record_post_turn',
                           'session_id': session.conversation_id, 'request_id': request_id,
                           'preview': preview, 'allow_approve_all': False,
                           **({'confirm_subtype': 'work_record_auto_candidate',
                               'work_record_candidate': structured} if structured is not None else {})})
            except Exception:
                self._auto_record_confirmations.pop(request_id, None)
                return 'n', request_id
        result, done = 'n', 'cancelled'
        try:
            while True:
                with self._result_view_write_lock:
                    if not valid():
                        break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    done = 'timeout'
                    break
                try:
                    choice = responses.get(timeout=min(.1, remaining))
                except queue.Empty:
                    continue
                with self._result_view_write_lock:
                    if time.monotonic() >= deadline:
                        done = 'timeout'
                    elif valid():
                        result = 'y' if choice == 'y' else 'n'
                        done = result
                break
        finally:
            with self._result_view_write_lock:
                self._auto_record_confirmations.pop(request_id, None)
                if not self._connection_closed.is_set():
                    try:
                        self.emit({'type': 'confirm.done', 'confirm_kind': 'work_record_post_turn',
                                   'session_id': session.conversation_id, 'request_id': request_id,
                                   'choice': done,
                                   **({'confirm_subtype': 'work_record_auto_candidate',
                                       'work_record_candidate': structured}
                                      if structured is not None else {})})
                    except Exception:
                        result = 'n'
        return result, request_id

    def deliver_auto_record_confirm(self, request_id: str, choice: str) -> bool:
        with self._result_view_write_lock:
            pending = self._auto_record_confirmations.get(request_id)
            if (pending is None or pending['claimed'] or choice not in {'y', 'n'}
                    or not pending['valid']()):
                return False
            pending['claimed'] = True
            pending['responses'].put_nowait(choice)
            return True

    def deliver_post_turn_confirm(self, request_id: str, choice: str) -> bool:
        """Claim a response once; stale post-turn IDs never enter ordinary queues."""
        with self._result_view_write_lock:
            pending = self._post_turn_confirmation
            if (pending is None or pending['id'] != request_id or pending['claimed']
                    or choice not in {'y', 'n'}
                    or not self.post_turn_binding_is_valid(pending['ticket'], pending['session'])):
                return False
            pending['claimed'] = True
            pending['responses'].put_nowait(choice)
            return True

    def try_start_post_turn_payload(self, ticket: object, session: Any, digest: str) -> bool:
        """Require the exact digest from the user-visible outbound approval."""
        with self._result_view_write_lock:
            approved = self._post_turn_approved_digest
            if (approved is None or approved[0] is not ticket or approved[1] != digest
                    or not self.try_start_post_turn_send(ticket, session)):
                return False
            self._post_turn_approved_digest = None
            return True

    def finish_post_turn(self, ticket: object, session: Any) -> bool:
        """Release only this candidate; retain the gate's replay tombstone."""
        with self._result_view_write_lock:
            if self._post_turn_send_gate is None or not self._post_turn_send_gate.is_valid(ticket):
                return False
            self._post_turn_approved_digest = None
            self._post_turn_send_gate.cancel()
            return True

    def _cancel_auto_recognition_work_locked(self) -> None:
        """Report and release waiting content; an already-started send cannot be recalled."""
        active = self._auto_recognition_active
        if active is not None:
            active['cancel_event'].set()
        if active is not None and not active['reported']:
            active['reported'] = True
            _emit_auto_recognition_result(self, active['candidate'], 'cancelled')
        for candidate in self._auto_recognition_pending.values():
            _emit_auto_recognition_result(self, candidate, 'cancelled')
        self._auto_recognition_pending.clear()
        self._invalidate_auto_record_confirmations()

    def _revoke_auto_recognition_locked(self) -> None:
        """Invalidate this connection's permission and pending candidate confirmations."""
        session = self._auto_recognition_session
        self._auto_recognition_session = None
        if session is not None:
            self._post_turn_generation += 1
        self._cancel_auto_recognition_work_locked()
        if session is not None and self._auto_recognition_queue is not None:
            self._auto_recognition_queue.revoke(self, session)

    def _auto_recognition_preference_enabled_locked(self) -> bool:
        """Refresh the durable user choice while the bridge binding lock is held."""
        if self._auto_recognition_preference_failed:
            return False
        from work_record_auto_recognition_preference import (
            AutoRecognitionPreferenceError, AutoRecognitionPreferenceStore,
        )
        try:
            enabled = AutoRecognitionPreferenceStore(self.paths).read_enabled()
        except AutoRecognitionPreferenceError:
            self._auto_recognition_preference_failed = True
            self._auto_recognition_opt_out = True
            self._revoke_auto_recognition_locked()
            return False
        self._auto_recognition_opt_out = not enabled
        if not enabled:
            self._revoke_auto_recognition_locked()
        return enabled

    def sync_auto_recognition_session(self, session: Any) -> None:
        """Default-enable the bound session unless the durable user preference opts out.

        This only binds authorization; recognition runs after ordinary turns and does
        not issue another consent prompt. The queue remains exact-session and
        connection scoped.
        """
        from work_record_recognition_queue import QueueStatus, RecognitionQueue

        with self._result_view_write_lock:
            provider_session = self._session_provider() if callable(self._session_provider) else None
            if (not isinstance(session, Session) or provider_session is not session
                    or self._connection_closed.is_set() or self._session_binding_transitions):
                return
            try:
                preference_enabled = self._auto_recognition_preference_enabled_locked()
            except Exception:
                self._auto_recognition_preference_failed = True
                self._auto_recognition_opt_out = True
                self._revoke_auto_recognition_locked()
                preference_enabled = False
            if not preference_enabled:
                self._revoke_auto_recognition_locked()
            elif self._auto_recognition_session is not session:
                try:
                    if self._auto_recognition_queue is None:
                        self._auto_recognition_queue = RecognitionQueue()
                    result = self._auto_recognition_queue.authorize(self, session)
                    if result.status is QueueStatus.AUTHORIZED:
                        self._post_turn_generation += 1
                        self._auto_recognition_session = session
                    else:
                        self._revoke_auto_recognition_locked()
                except Exception:
                    self._revoke_auto_recognition_locked()
            enabled = self._auto_recognition_session is session
            try:
                self.emit({"type": "work-record.auto-recognition.state",
                           "session_id": session.conversation_id, "enabled": enabled})
            except Exception:
                self._auto_recognition_opt_out = True
                self._auto_recognition_preference_failed = True
                self._revoke_auto_recognition_locked()

    def set_auto_recognition(self, message: dict[str, Any], session: Any) -> dict[str, Any]:
        """Apply explicit connection/Session consent; never invoke recognition here.

        Request IDs are connection-lifetime replay tombstones. Once their bounded
        capacity is reached, new grants fail closed; revoke remains available.
        """
        from work_record_auto_recognition_preference import (
            AutoRecognitionPreferenceError, AutoRecognitionPreferenceStore,
        )
        from work_record_recognition_queue import QueueStatus, RecognitionQueue
        raw_id = message.get('request_id')
        raw_session = message.get('session_id')
        action = message.get('action')
        try:
            canonical = type(raw_id) is str and str(uuid.UUID(raw_id)) == raw_id
        except (ValueError, TypeError, AttributeError):
            canonical = False
        response = {'type': 'work-record.auto-recognition.done',
                    'session_id': raw_session if type(raw_session) is str and len(raw_session) <= 256 else '',
                    'request_id': raw_id if canonical else '',
                    'action': action if action in ('enable', 'revoke') else '',
                    'status': 'rejected', 'enabled': False}
        with self._result_view_write_lock:
            try:
                provider_session = self._session_provider() if callable(self._session_provider) else None
                if self._auto_recognition_session is not None and (
                        self._connection_closed.is_set() or provider_session is not self._auto_recognition_session):
                    self._revoke_auto_recognition_locked()
                bound = (isinstance(session, Session) and provider_session is session
                         and not self._connection_closed.is_set() and not self._session_binding_transitions
                         and type(raw_session) is str and raw_session == session.conversation_id)
                if bound:
                    response['enabled'] = self._auto_recognition_session is session
                if (not bound or not canonical or action not in ('enable', 'revoke')
                        or set(message) != {'type', 'session_id', 'request_id', 'action'}
                        or raw_id in self._auto_recognition_request_ids):
                    return response
                if len(self._auto_recognition_request_ids) >= 2048:
                    self._auto_recognition_request_capacity_exhausted = True
                    if action == 'enable':
                        return response
                else:
                    self._auto_recognition_request_ids.add(raw_id)
                if action == 'revoke':
                    self._auto_recognition_opt_out = True
                    self._revoke_auto_recognition_locked()
                    try:
                        AutoRecognitionPreferenceStore(self.paths).write_enabled(False)
                        self._auto_recognition_preference_failed = False
                    except AutoRecognitionPreferenceError:
                        self._auto_recognition_preference_failed = True
                        response.update(status='rejected', enabled=False)
                        return response
                    response.update(status='ok', enabled=False)
                    return response
                if self._auto_recognition_request_capacity_exhausted:
                    return response
                try:
                    AutoRecognitionPreferenceStore(self.paths).write_enabled(True)
                except AutoRecognitionPreferenceError:
                    self._auto_recognition_opt_out = True
                    self._auto_recognition_preference_failed = True
                    self._revoke_auto_recognition_locked()
                    return response
                self._auto_recognition_opt_out = False
                self._auto_recognition_preference_failed = False
                if self._auto_recognition_session is session:
                    response.update(status='ok', enabled=True)
                    return response
                if self._auto_recognition_queue is None:
                    self._auto_recognition_queue = RecognitionQueue()
                result = self._auto_recognition_queue.authorize(self, session)
                if result.status is QueueStatus.AUTHORIZED:
                    self._post_turn_generation += 1
                    self._auto_recognition_session = session
                    response.update(status='ok', enabled=True)
                return response
            except Exception:
                # Do not advertise permission after an uncertain state transition.
                self._auto_recognition_opt_out = True
                self._auto_recognition_preference_failed = True
                try:
                    self._revoke_auto_recognition_locked()
                except Exception:
                    self._auto_recognition_session = None
                response.update(status='rejected', enabled=False)
                return response

    def invalidate_post_turn(self) -> None:
        with self._result_view_write_lock:
            self._post_turn_generation += 1
            self._cancel_auto_recognition_work_locked()
            self._post_turn_approved_digest = None
            if self._post_turn_send_gate is not None:
                self._post_turn_send_gate.cancel()

    def register_parallel_chat(self, request: _ParallelChatRequest) -> bool:
        with self._parallel_chat_lock:
            if (self._connection_closed.is_set() or request.request_id in self._parallel_chat_seen_requests
                    or len(self._parallel_chats) >= 8 or len(self._parallel_chat_seen_requests) >= 4096):
                return False
            self._parallel_chat_seen_requests.add(request.request_id)
            self._parallel_chats[request.request_id] = request
            return True

    def cancel_parallel_chat(self, request_id: Any) -> bool:
        if type(request_id) is not str:
            return False
        with self._parallel_chat_lock:
            request = self._parallel_chats.get(request_id)
            if request is None:
                return False
            request.cancel_event.set()
            client = request.client
        if client is not None:
            cancel = getattr(client, "cancel_current_request", None)
            if callable(cancel):
                try:
                    cancel()
                except Exception:
                    pass
        return True

    def finish_parallel_chat(self, request: _ParallelChatRequest) -> None:
        with self._parallel_chat_lock:
            if self._parallel_chats.get(request.request_id) is request:
                self._parallel_chats.pop(request.request_id, None)

    def _cancel_parallel_chats(self) -> None:
        with self._parallel_chat_lock:
            requests = tuple(self._parallel_chats.values())
            for request in requests:
                request.cancel_event.set()
        for request in requests:
            client = request.client
            cancel = getattr(client, "cancel_current_request", None) if client is not None else None
            if callable(cancel):
                try:
                    cancel()
                except Exception:
                    pass

    def mark_connection_closed(self) -> None:
        self._cancel_parallel_chats()
        with self._result_view_write_lock:
            self._post_turn_generation += 1
            self._revoke_auto_recognition_locked()
            self._invalidate_auto_record_confirmations()
            self._connection_closed.set()
            self.cancel_event.set()
            self._post_turn_approved_digest = None
            if self._post_turn_send_gate is not None:
                self._post_turn_send_gate.disconnect()

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
        snapshot = self.lifecycle.snapshot()
        run_id = snapshot.run_id if snapshot is not None else ""
        session_id = self.lifecycle_conversation_id
        request_id = str(uuid.uuid4())
        pending = (request_id, run_id, session_id)
        with self._input_request_lock:
            self._pending_input_request = pending
            self._awaiting_input.set()
        self.emit({"type": "prompt.request", "prompt": prompt, "request_id": request_id,
                   "run_id": run_id, "session_id": session_id})
        try:
            return self._input_queue.get(timeout=3600)
        except queue.Empty:
            return ""
        finally:
            with self._input_request_lock:
                if self._pending_input_request == pending:
                    self._pending_input_request = None
                self._awaiting_input.clear()

    def route_prompt_response(self, request_id: Any, run_id: Any, session_id: Any, text: Any) -> bool:
        if (type(request_id) is not str or type(run_id) is not str or type(session_id) is not str
                or type(text) is not str or len(text) > 4000):
            return False
        with self._input_request_lock:
            if (not self._awaiting_input.is_set()
                    or self._pending_input_request != (request_id, run_id, session_id)
                    or self._connection_closed.is_set() or self.cancel_event.is_set()):
                return False
            snapshot = self.lifecycle.snapshot()
            if (snapshot is None or snapshot.run_id != run_id or snapshot.state != "running"
                    or self.lifecycle_conversation_id != session_id):
                return False
            self._pending_input_request = None
            self._input_queue.put(text)
            return True

    def confirm_fn(self, preview: str, allow_approve_all: bool) -> str:
        return self.confirm_with_receipt(preview, allow_approve_all)[0]

    def verify_project_worker_requirement(self, *, host: Any, session: Session, store: Any,
                                          binding: Any, outcome: Any, target: str,
                                          live_binding_check: Callable[..., bool]) -> object | None:
        """Run one explicitly authorized project self-report test; not authentication."""
        from project_worker_host import (
            ProjectWorkerHost, _ProjectWorkerSelfReportReceipt, _resolve_bound_project,
        )
        from project_worker_work_record_settlement import (
            ProjectWorkerWorkRecordBinding, _requirement_matches_bound_scope,
            project_worker_outcome_matches_binding,
        )
        from project_verify import run_requirement_pytest, requirement_pytest_input_snapshot
        if (type(host) is not ProjectWorkerHost or type(binding) is not ProjectWorkerWorkRecordBinding
                or binding.reservation is None or outcome.status != "model_turn_finished"
                or not project_worker_outcome_matches_binding(binding, outcome)
                or type(target) is not str or len(target) > 256
                or re.fullmatch(r"[A-Za-z0-9_./-]+\.py::test_[A-Za-z0-9_]+", target) is None
                or any(part in ("", ".", "..") for part in target.split("::")[0].split("/"))
                or getattr(store, "_session", None) is not session):
            return None
        reservation = binding.reservation
        rid = reservation.reservation_id
        def current() -> bool:
            try:
                with host._reservation_lock:
                    reserved = host._reservations.get(rid)
                item = store.get_item(binding.execution.item_id)
                return bool(reserved == (reservation, True)
                            and self._session_provider() is session
                            and not self.cancel_event.is_set() and not self._connection_closed.is_set()
                            and live_binding_check(session, reservation.parent_run_id, reservation.turn_epoch) is True
                            and _requirement_matches_bound_scope(store, item, binding)
                            and any(r.get("requirement_id") == binding.execution.requirement_id
                                    and r.get("current_run_id") == binding.execution.run_id
                                    for r in item["requirements"]))
            except Exception:
                return False
        try:
            with host._requirement_verification_lock:
                if rid in host._requirement_verification_attempts or not current():
                    return None
                host._requirement_verification_attempts.add(rid)
            root = _resolve_bound_project(session.paths, binding.request.project_id)
            file = root.joinpath(*target.split("::")[0].split("/"))
            if not file.is_file() or not file.resolve(strict=True).is_relative_to(root):
                return None
            identity = requirement_pytest_input_snapshot(root, target)
            payload = {"session_id": session.conversation_id, "project_id": binding.request.project_id,
                       "item_id": binding.execution.item_id, "requirement_id": binding.execution.requirement_id,
                       "project_name": binding.request.project_id, "requirement_text": binding.request.task,
                       "pytest_target": target, "worker_run_id": reservation.worker_run_id,
                       "reservation_id": rid}
            if self.confirm_project_worker_requirement(session=session, run_id=reservation.parent_run_id, payload=payload) != "y":
                return None
            if not current() or requirement_pytest_input_snapshot(root, target) != identity:
                return None
            result = run_requirement_pytest(root, target, cancel_event=self.cancel_event)
            if (not result.reported_pass or result.exit_code != 0 or result.test_count != 1
                    or not current() or _resolve_bound_project(session.paths, binding.request.project_id) != root
                    or requirement_pytest_input_snapshot(root, target) != identity):
                return None
            token = _ProjectWorkerSelfReportReceipt()
            with host._requirement_verification_lock:
                if not current():
                    return None
                host._requirement_verifications[token] = (self, store, binding, outcome, target, root, identity,
                                                          host._requirement_self_report_epoch)
            return token
        except Exception:
            return None

    def confirm_project_worker_requirement(self, *, session: Session, run_id: str,
                                           payload: dict[str, Any]) -> str:
        keys = {"session_id", "project_id", "item_id", "requirement_id", "project_name",
                "requirement_text", "pytest_target", "worker_run_id", "reservation_id"}
        snapshot = self.lifecycle.snapshot()
        if (type(payload) is not dict or set(payload) != keys
                or any(type(payload[k]) is not str or not payload[k].strip() or len(payload[k]) > 8000 for k in keys)
                or payload["session_id"] != session.conversation_id
                or self.lifecycle_conversation_id != session.conversation_id
                or snapshot is None or snapshot.run_id != run_id or snapshot.state != "running"
                or snapshot.cancel_requested):
            return "n"
        import subprocess
        command = subprocess.list2cmdline([sys.executable, "-m", "pytest", "-q", "-c", "<临时配置>",
                    "--rootdir", f"workspace/{payload['project_id']}", "-o", "xfail_strict=true",
                    "--junitxml", "<临时报告>", payload["pytest_target"]])
        preview = ("项目要求的单目标 pytest 验证确认。将运行本机 Python、pytest、conftest、插件及任意项目代码；"
                   "请确认所选项目代码及其依赖可信；目标或环境可能变化，同一系统账户的恶意项目代码可伪造结果。仅记录用户授权的项目自报告单测试通过，不认证结果，也不证明整体要求完成。确认只授权以下测试，不授予 worker shell 权限。\n"
                   "禁用插件自动加载，使用空临时 pytest 配置（不是项目默认配置）；命令：\n" + command + "\n"
                   + json.dumps(payload, ensure_ascii=False, indent=2))
        return self.confirm_work_card_batch(preview, False, session=session,
                                            _verification_payload=dict(payload), _verification_run_id=run_id,
                                            _verification_sequence=snapshot.sequence)

    def confirm_work_card_batch(self, preview: str, allow_approve_all: bool, *, session: Session,
                                _verification_payload: dict[str, Any] | None = None,
                                _verification_run_id: str | None = None,
                                _verification_sequence: int | None = None) -> str:
        """Host-only batch confirmation: exact Session, one request, finite deadline.

        This does not inspect or authorize model-controlled JSON. The caller is
        responsible for binding the exact preview and rechecking the ledger before
        outbound requests. Only this confirmation wait pauses the turn watchdog wall.
        """
        if (type(preview) is not str or not preview or len(preview.encode('utf-8')) > 2 * 1024 * 1024
                or allow_approve_all is not False or not callable(self._session_provider)):
            return "n"
        try:
            session_id = session.conversation_id
            if (type(session_id) is not str or not session_id or self._session_provider() is not session
                    or self.cancel_event.is_set() or self._connection_closed.is_set()
                    or not math.isfinite(self.batch_confirm_timeout) or self.batch_confirm_timeout <= 0
                    or self.batch_confirm_timeout > 3600):
                return "n"
        except Exception:
            return "n"
        request_id = str(uuid.uuid4())
        responses: queue.Queue[str] = queue.Queue(maxsize=1)
        deadline = time.monotonic() + self.batch_confirm_timeout
        with self._batch_confirm_lock:
            if self._batch_confirm_pending is not None or self._pending_confirm_id is not None:
                return "n"
            self._batch_confirm_pending = (request_id, deadline, responses)
            self._pending_confirm_id = request_id
        result = "n"
        done = "cancelled"
        watchdog = self.turn_watchdog
        wall_paused = False
        try:
            if watchdog is not None:
                watchdog.pause_wall()
                wall_paused = True
            event = {"type": "confirm.request", "request_id": request_id,
                     "preview": preview, "allow_approve_all": False}
            if _verification_payload is not None:
                event.update({"confirm_subtype": "project_worker_requirement_verification",
                              "project_worker_verification": _verification_payload,
                              "session_id": session_id, "run_id": _verification_run_id})
            self.emit(self._scope_run_event(event))
            self._touch_progress()
            while True:
                if (self.cancel_event.is_set() or self._connection_closed.is_set()
                        or self._session_provider() is not session or session.conversation_id != session_id):
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    done = "timeout"
                    break
                try:
                    choice = responses.get(timeout=min(.25, remaining))
                except queue.Empty:
                    continue
                if (time.monotonic() >= deadline or self.cancel_event.is_set()
                        or self._connection_closed.is_set() or self._session_provider() is not session
                        or session.conversation_id != session_id):
                    done = "timeout" if time.monotonic() >= deadline else "cancelled"
                    break
                if choice == "y":
                    result, done = "y", "y"
                else:
                    done = "n"
                break
        except Exception:
            result, done = "n", "cancelled"
        finally:
            with self._batch_confirm_lock:
                if self._batch_confirm_pending is not None and self._batch_confirm_pending[0] == request_id:
                    self._batch_confirm_pending = None
                if self._pending_confirm_id == request_id:
                    self._pending_confirm_id = None
            try:
                done_event = {"type": "confirm.done", "request_id": request_id, "choice": done}
                if _verification_payload is not None:
                    done_event.update({"confirm_subtype": "project_worker_requirement_verification",
                                       "session_id": session_id, "run_id": _verification_run_id})
                self.emit(self._scope_run_event(done_event))
                self._touch_progress()
            except Exception:
                result = "n"
            finally:
                if wall_paused:
                    try:
                        watchdog.resume_wall()
                    except Exception:
                        result = "n"
        # Recheck after emitting: a disconnect, cancel or rebind must not grant approval.
        try:
            if (self.cancel_event.is_set() or self._connection_closed.is_set()
                    or self._session_provider() is not session or session.conversation_id != session_id
                    or time.monotonic() >= deadline):
                return "n"
            if _verification_payload is not None:
                current = self.lifecycle.snapshot()
                if (current is None or current.run_id != _verification_run_id
                        or current.sequence != _verification_sequence
                        or current.state != "running" or current.cancel_requested
                        or self.lifecycle_conversation_id != session_id):
                    return "n"
        except Exception:
            return "n"
        return result

    def confirm_with_receipt(self, preview: str, allow_approve_all: bool) -> tuple[str, str]:
        with self._batch_confirm_lock:
            if self._batch_confirm_pending is not None:
                return "n", ""
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

    def begin_overview_query(self, request_id: str) -> threading.Event | None:
        """Track the serialized read-only overview so turn.cancel can suppress its result."""
        with self._overview_query_lock:
            if (self._active_overview_query is not None or self._connection_closed.is_set()
                    or self._session_binding_transitions):
                return None
            cancel = threading.Event()
            self._active_overview_query = (request_id, cancel)
            return cancel

    def finish_overview_query(self, request_id: str, cancel: threading.Event) -> None:
        with self._overview_query_lock:
            active = self._active_overview_query
            if active is not None and active[0] == request_id and active[1] is cancel:
                self._active_overview_query = None

    def cancel_overview_query(self) -> bool:
        with self._overview_query_lock:
            if self._active_overview_query is None:
                return False
            self._active_overview_query[1].set()
            return True

    def begin_session_binding_change(self) -> None:
        with self._result_view_write_lock:
            self._post_turn_generation += 1
            self._revoke_auto_recognition_locked()
            self._invalidate_auto_record_confirmations()
            if self._post_turn_send_gate is not None:
                self._post_turn_send_gate.begin_session_change()
            with self._overview_query_lock:
                self._session_binding_transitions += 1
                if self._active_overview_query is not None:
                    self._active_overview_query[1].set()

    def try_acquire_result_view_lease(self, session: Session) -> object | None:
        """Bind one explicit result-view write to a Session, not to the whole turn."""
        with self._overview_query_lock:
            if self._session_binding_transitions or self._connection_closed.is_set():
                return None
            lease = object()
            self._active_result_view_leases[lease] = session
            return lease

    def result_view_lease_is_bound(self, lease: object | None, session: Session) -> bool:
        with self._overview_query_lock:
            return lease is not None and self._active_result_view_leases.get(lease) is session

    def release_result_view_lease(self, lease: object) -> None:
        with self._overview_query_lock:
            if self._active_result_view_leases.pop(lease, None) is not None:
                self._overview_query_lock.notify_all()

    def wait_for_result_view_leases(self, timeout: float) -> bool:
        """Wait for bounded explicit ACK I/O before allowing this connection to rebind."""
        deadline = time.monotonic() + max(0.0, timeout)
        with self._overview_query_lock:
            while self._active_result_view_leases:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._overview_query_lock.wait(timeout=remaining)
            return True

    @contextlib.contextmanager
    def agent_session_binding_transition(self, expected_session: Session):
        """Coordinate a model-requested Session replacement with outstanding ACK leases."""
        self.begin_session_binding_change()
        try:
            provider = self._session_provider
            allowed = bool(
                callable(provider)
                and provider() is expected_session
                and self.wait_for_result_view_leases(SESSION_SWITCH_WAIT_SECONDS)
                and provider() is expected_session
            )
            yield allowed
        finally:
            self.end_session_binding_change()

    def end_session_binding_change(self) -> None:
        session_to_sync = None
        with self._result_view_write_lock:
            with self._overview_query_lock:
                self._session_binding_transitions = max(0, self._session_binding_transitions - 1)
            if self._post_turn_send_gate is not None:
                try:
                    session = self._session_provider() if callable(self._session_provider) else None
                    self._post_turn_send_gate.end_session_change(session)
                except Exception:
                    # Fail closed if the transition bookkeeping is inconsistent.
                    self._post_turn_send_gate.disconnect()
            if self._session_binding_transitions == 0 and callable(self._session_provider):
                try:
                    session_to_sync = self._session_provider()
                except Exception:
                    session_to_sync = None
        if session_to_sync is not None:
            self.sync_auto_recognition_session(session_to_sync)

    @staticmethod
    def _work_instruction_event(instruction: _WorkInstruction, state: str, *, reason: str | None = None) -> dict[str, Any]:
        event: dict[str, Any] = {
            "type": "work.instruction.state",
            "request_id": instruction.request_id,
            "run_id": instruction.run_id,
            "session_id": instruction.session_id,
            "state": state,
        }
        if reason or instruction.reason:
            event["reason"] = reason or instruction.reason
        if state == "delivered":
            event["delivery_stage"] = "next_model_request_context"
        return event

    def _emit_work_instruction_state(self, instruction: _WorkInstruction, state: str, *, reason: str | None = None) -> None:
        try:
            self.emit(self._work_instruction_event(instruction, state, reason=reason))
        except Exception as exc:
            _log_notification_failure("work instruction state notification failed", exc)

    def enqueue_work_instruction(
        self, *, request_id: str, run_id: str, session: Session, text: str,
    ) -> str:
        """Queue only an explicitly targeted instruction for the exact live run/session."""
        session_id = session.conversation_id if isinstance(session, Session) else ""
        request_key = request_id if type(request_id) is str else ""
        with self._work_instruction_lock:
            existing = self._work_instructions.get(request_key)
            if existing is not None:
                self._emit_work_instruction_state(existing, existing.state)
                return existing.state
            instruction = _WorkInstruction(request_key, run_id if type(run_id) is str else "", session_id,
                                           text if type(text) is str else "")
            snapshot = self.lifecycle.snapshot()
            reason = None
            if len(self._work_instructions) >= 4096 and existing is None:
                reason = "request_capacity_exhausted"
            elif (not request_key or not run_id or not isinstance(session, Session)
                    or type(text) is not str or not text.strip() or len(text) > 2000):
                reason = "invalid_request"
            elif (self._connection_closed.is_set() or not self._turn_busy.is_set()
                    or snapshot is None or snapshot.run_id != run_id or snapshot.state != "running"
                    or snapshot.cancel_requested or self.cancel_event.is_set()
                    or self.lifecycle_conversation_id != session_id):
                reason = "run_session_mismatch"
            else:
                pending = [row for row in self._work_instructions.values()
                           if row.run_id == run_id and row.session_id == session_id and row.state == "queued"]
                queued_bytes = sum(len(row.text.encode("utf-8")) for row in pending)
                incoming_bytes = len(text.encode("utf-8"))
                if len(pending) >= 8 or queued_bytes + incoming_bytes > 16000:
                    reason = "queue_full"
            if reason is not None:
                instruction.state = "undelivered"
                instruction.reason = reason
                if request_key and reason in {"queue_full", "request_capacity_exhausted"}:
                    self._work_instructions[request_key] = instruction
                self._emit_work_instruction_state(instruction, "undelivered")
                return "undelivered"
            self._work_instructions[request_key] = instruction
            self._emit_work_instruction_state(instruction, "queued")
            return "queued"

    def prepare_work_instructions_at_checkpoint(self, session: Session, run_id: str) -> int:
        """Durably stage queued text at the exact round boundary; it is not delivered until added to next prompt."""
        with self._result_view_write_lock:
            with self._work_instruction_lock:
                snapshot = self.lifecycle.snapshot()
                if (not isinstance(session, Session) or snapshot is None or snapshot.run_id != run_id
                        or snapshot.state != "running" or snapshot.cancel_requested
                        or self.cancel_event.is_set() or self._connection_closed.is_set()
                        or not self._turn_busy.is_set() or self.lifecycle_conversation_id != session.conversation_id):
                    return 0
                ready = [row for row in self._work_instructions.values()
                         if row.run_id == run_id and row.session_id == session.conversation_id
                         and row.state == "queued" and not row.prepared]
                prepared = 0
                for index, instruction in enumerate(ready):
                    try:
                        session.append_message({
                            "role": "user",
                            "content": instruction.text,
                            HOST_INTERNAL_METADATA_KEY: {
                                "work_instruction": True,
                                "work_instruction_request_id": instruction.request_id,
                                "work_instruction_run_id": run_id,
                            },
                        })
                    except Exception:
                        instruction.state = "undelivered"
                        instruction.reason = "persistence_failed"
                        self._emit_work_instruction_state(instruction, "undelivered")
                        for remainder in ready[index + 1:]:
                            remainder.state = "undelivered"
                            remainder.reason = "persistence_failed"
                            self._emit_work_instruction_state(remainder, "undelivered")
                        break
                    instruction.prepared = True
                    prepared += 1
                return prepared

    def commit_work_instruction_delivery(
        self, session: Session, run_id: str, model_context: list[dict[str, Any]],
    ) -> list[dict[str, str]]:
        """Add prepared instructions to the imminent Agent model context and then report delivery."""
        with self._result_view_write_lock:
            with self._work_instruction_lock:
                snapshot = self.lifecycle.snapshot()
                if (not isinstance(session, Session) or type(model_context) is not list
                        or snapshot is None or snapshot.run_id != run_id or snapshot.state != "running"
                        or snapshot.cancel_requested or self.cancel_event.is_set()
                        or self._connection_closed.is_set() or not self._turn_busy.is_set()
                        or self.lifecycle_conversation_id != session.conversation_id):
                    return []
                ready = [row for row in self._work_instructions.values()
                         if row.run_id == run_id and row.session_id == session.conversation_id
                         and row.state == "queued" and row.prepared]
                delivered: list[dict[str, str]] = []
                for instruction in ready:
                    message = {"role": "user", "content": instruction.text}
                    model_context.append(message)
                    instruction.state = "delivered"
                    self._emit_work_instruction_state(instruction, "delivered")
                    delivered.append(message)
                return delivered

    def finish_work_instructions(self, run_id: str, session_id: str, finish_reason: str) -> int:
        """Settle every instruction not handed to a model request before this run ended."""
        with self._work_instruction_lock:
            pending = [row for row in self._work_instructions.values()
                       if row.run_id == run_id and row.session_id == session_id and row.state == "queued"]
            for instruction in pending:
                instruction.state = "undelivered"
                instruction.reason = ("run_stopped_before_model_request" if instruction.prepared
                                      else "run_stopped_before_checkpoint") if finish_reason in {"cancelled", "stopped", "timeout"} else (
                    "run_ended_before_model_request" if instruction.prepared else "run_ended_before_checkpoint"
                )
                self._emit_work_instruction_state(instruction, "undelivered")
            return len(pending)

    def request_execution_stop(self, run_id: Any) -> bool:
        """Stop exactly one active execution; stale/missing IDs are inert."""
        if type(run_id) is not str or not run_id.strip():
            return False
        with self._result_view_write_lock:
            snapshot, changed = self.lifecycle.request_stop(
                "user", expected_run_id=run_id,
            )
            if not changed or snapshot is None:
                return False
            self.emit(snapshot.to_event())
            if self.turn_watchdog is not None and self._turn_busy.is_set():
                self.turn_watchdog.request_user_cancel()
            self.cancel_event.set()
            if self._turn_busy.is_set() and self._awaiting_input.is_set():
                self._input_queue.put("")
            if self._turn_busy.is_set() and self._cancel_turn is not None:
                self._cancel_turn()
            self.emit({"type": "notice", "text": "正在停止指定工作 run…"})
            return True

    def request_cancel(self) -> bool:
        # Serialize cancellation ingress with a result-view atomic write. Whichever
        # takes this lock first is the observable order: ACK-before-cancel or no ACK.
        with self._result_view_write_lock:
            self._post_turn_generation += 1
            self._cancel_auto_recognition_work_locked()
            if self._post_turn_send_gate is not None:
                self._post_turn_send_gate.cancel()
            current = self.lifecycle.snapshot()
            lifecycle_active = current is not None and current.state in {
                "queued", "running", "stopping",
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
            if self.turn_watchdog is not None and turn_busy:
                self.turn_watchdog.request_user_cancel()
            self.cancel_event.set()
            if turn_busy and self._awaiting_input.is_set():
                self._input_queue.put("")
            if turn_busy and self._cancel_turn is not None:
                self._cancel_turn()
            self.emit({"type": "notice", "text": "正在停止当前回合…"})
            return True
    def deliver_confirm(self, request_id: str, choice: str) -> bool:
        with self._batch_confirm_lock:
            batch = self._batch_confirm_pending
            if batch is not None:
                pending_id, deadline, responses = batch
                if (request_id != pending_id or self.cancel_event.is_set() or self._connection_closed.is_set()
                        or time.monotonic() >= deadline):
                    return False
                try:
                    responses.put_nowait(choice)
                except queue.Full:
                    return False
                return True
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
        self.emit(_session_banner_payload(session, self.paths))
        self.sync_auto_recognition_session(session)
        # History before heavy project.state so chat hydrates first (UI-5972).
        self.emit(session_history_event(session))
        self.emit(
            session_memory_event(session, quick=quick_memory)
        )
        self.emit(models_list_event(self.paths))
        try:
            self.emit(_role_model_preferences_payload(self.paths))
        except Exception as exc:
            self.emit({"type": "llm_role_models.state", "status": "rejected", "message": str(exc)})
        self.emit(_proposals_payload(self.paths))
        self.emit(_memories_payload(self.paths))
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


def _bind_multi_project_overview_cancel_event(agent: Any, cancel_event: threading.Event) -> None:
    """Keep the injected overview classifier on the Agent's current cancel boundary."""
    recognizer = getattr(agent, "multi_project_overview_recognizer", None)
    client = getattr(recognizer, "_client", None)
    set_cancel_event = getattr(client, "set_cancel_event", None)
    if callable(set_cancel_event):
        set_cancel_event(cancel_event)


# Confirmed unique work-card references are published by the trusted Session host.
_WORK_CARD_REFERENCE_PUBLICATION_ENABLED = True


@dataclass(frozen=True, slots=True)
class WorkCardReferenceIntent:
    session_id: str
    run_id: str
    presentation_id: str
    card_id: str


def _prepare_confirmed_work_card_reference(
    session: Session,
    resolution: Any,
    run_id: str,
    *,
    enabled: bool = _WORK_CARD_REFERENCE_PUBLICATION_ENABLED,
) -> WorkCardReferenceIntent | str:
    """Validate a confirmed unique selection and derive stable host-only identity.

    Deliberately performs no persistence or WS send: until the renderer can restore
    work_card messages, a prepared intent must not be represented as delivered.
    """
    if enabled is not True:
        return "disabled"
    try:
        from work_card_recall import RecallResolution
        from work_records import WorkRecordStore
        from work_card_ledger import WorkCardLedger

        if (type(session) is not Session or type(resolution) is not RecallResolution
                or resolution.status != "match" or type(resolution.records) is not tuple
                or len(resolution.records) != 1 or type(run_id) is not str):
            return "rejected"
        try:
            run_uuid = uuid.UUID(run_id)
            run_identity = run_uuid.hex
        except (ValueError, TypeError, AttributeError):
            return "rejected"
        record = resolution.records[0]
        if type(record) is not dict:
            return "rejected"
        card_id = record.get("card_id")
        store = WorkRecordStore(session)
        ledger = WorkCardLedger(session, store)
        if type(card_id) is not str or ledger.get_card(card_id) != record:
            return "rejected"
        presentation_id = str(uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"my-agent-work-card:{session.conversation_id}:{run_identity}:{card_id}",
        ))
        return WorkCardReferenceIntent(
            session.conversation_id, str(run_uuid), presentation_id, card_id,
        )
    except Exception:
        return "rejected"


def _work_card_presentation_id(session_id: str, run_id: str, card_id: str) -> str:
    run_identity = uuid.UUID(run_id).hex
    return str(uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"my-agent-work-card:{session_id}:{run_identity}:{card_id}",
    ))


def _work_card_snapshot_item_for_result(
    snapshot: Any, *, session_id: str, requirement_id: str, result_id: str,
) -> str | None:
    """Derive one exact local item only from an already-authorized ledger snapshot."""
    try:
        if type(snapshot) is not dict or snapshot.get("session_id") != session_id:
            return None
        if snapshot.get("kind") == "independent":
            cards = [snapshot.get("card")]
        elif snapshot.get("kind") in {"linked_item_copy", "linked_project_session_copy"}:
            return None
        else:
            cards = snapshot.get("cards")
        if type(cards) is not list or not cards:
            return None
        matches = []
        for card in cards:
            if type(card) is not dict or card.get("session_id") != session_id:
                return None
            item_id = card.get("item_id")
            requirements = card.get("requirements")
            if type(item_id) is not str or not item_id or type(requirements) is not list:
                return None
            if snapshot.get("kind") == "independent" and snapshot.get("item_id") != item_id:
                return None
            for requirement in requirements:
                if (type(requirement) is not dict or requirement.get("session_id") != session_id
                        or requirement.get("item_id") != item_id):
                    return None
                latest = requirement.get("latest_result")
                if latest is None:
                    continue
                if (type(latest) is not dict or latest.get("session_id") != session_id
                        or latest.get("item_id") != item_id
                        or latest.get("requirement_id") != requirement.get("requirement_id")):
                    return None
                if latest.get("result_id") == result_id:
                    matches.append((item_id, requirement.get("requirement_id")))
        if len(matches) == 1 and matches[0][1] == requirement_id:
            return matches[0][0]
    except Exception:
        pass
    return None


def _record_work_card_result_view(
    *, session: Session, store: Any, ledger: Any, presentations: Any,
    session_id: str, presentation_id: str, card_id: str, result_id: str, requirement_id: str,
    action: str, reply_binding_authority: Any = None, reply_binding: object | None = None,
    reply_text: str | None = None, write_guard: Callable[[], bool] | None = None,
) -> str:
    """Persist a trusted result-detail or host-bound reliable-reply view.

    The client protocol calls this only with the constant ``result_detail``.
    ``reliable_reply`` requires a process-local HostReplyBindingAuthority receipt
    bound to the exact persisted user message and result target. This helper adds
    no Session message and performs no networking; callers retain the result-view
    Session lease across this synchronous durable check/write.
    """
    if action not in {"result_detail", "reliable_reply"}:
        return "rejected"
    if (type(session) is not Session or session_id != session.conversation_id
            or getattr(store, "_session", None) is not session
            or getattr(ledger, "_session", None) is not session or getattr(ledger, "_store", None) is not store
            or getattr(presentations, "_session", None) is not session
            or getattr(presentations, "_store", None) is not store
            or getattr(presentations, "_ledger", None) is not ledger):
        return "rejected"
    try:
        if action == "reliable_reply":
            from work_card_reply_binding import HostReplyBindingAuthority
            if (type(reply_binding_authority) is not HostReplyBindingAuthority
                    or reply_binding is None or type(reply_text) is not str or not reply_text.strip()
                    or getattr(reply_binding_authority, "_session", None) is not session):
                return "rejected"
        elif reply_binding_authority is not None or reply_binding is not None or reply_text is not None:
            return "rejected"
        from session import _messages_unusable_for_ref, _session_messages_lock, _valid_work_card_ref
        session.ensure_messages_loaded()
        with _session_messages_lock(session.session_dir):
            if session._messages_corrupt or session._messages_unpersisted or _messages_unusable_for_ref(session):
                return "history_unavailable"
            refs = [row for row in session.messages if _valid_work_card_ref(row)
                    and row.get("presentation_id") == presentation_id and row.get("card_id") == card_id]
            if len(refs) != 1:
                return "rejected"
        presentation = presentations.get(presentation_id)
        if presentation.status in {"corrupt", "rejected"}:
            return "unavailable"
        if presentation.status != "found" or presentation.fact is None or presentation.fact.card_id != card_id:
            return "rejected"
        record = ledger.get_card(card_id)
        if type(record) is not dict:
            return "unavailable"
        from work_card_presentations import WorkCardPresentations as _WorkCardPresentations
        matches = _WorkCardPresentations._result_map(record.get("snapshot", {})).get(result_id, [])
        if matches != [requirement_id]:
            return "rejected"
        if action == "reliable_reply":
            expected_target = {"session_id": session_id, "presentation_id": presentation_id,
                               "card_id": card_id, "result_id": result_id, "requirement_id": requirement_id}
            try:
                target = reply_binding_authority.consume_persisted_user_message_binding(
                    reply_binding, expected_text=reply_text,
                )
            except Exception:
                return "rejected"
            if target != expected_target:
                return "rejected"
        ack = presentations.acknowledge_result(
            presentation_id, card_id, result_id, action=action, trusted_action=True,
            precommit_guard=write_guard,
        )
        if ack.status in {"acknowledged", "already_acknowledged"}:
            return ack.status
        return "unavailable" if ack.status in {"uncertain", "corrupt"} else "rejected"
    except Exception:
        return "unavailable"

def _public_work_card(record: Any) -> dict[str, Any] | None:
    if type(record) is not dict or not all(
        key in record for key in ("card_id", "snapshot", "saved_at", "viewed_at")
    ):
        return None
    return {key: record[key] for key in ("card_id", "snapshot", "saved_at", "viewed_at")}


def _work_card_publish_notice(bridge: WsBridge, text: str) -> None:
    try:
        bridge.emit_turn_event({"type": "turn.notice", "level": "warn", "text": text})
    except Exception as exc:
        _log_notification_failure("work-card publication notice failed", exc)


def _publish_pending_project_work_cards(
    repl: ConversationRepl, bridge: WsBridge, session: Session, store: Any,
) -> int:
    """Rebuild/replay only durable pending card intents; never re-enter project work."""
    project_id = getattr(getattr(session, "meta", None), "project_id", "")
    if (type(project_id) is not str or not project_id
            or getattr(repl, "session", None) is not session
            or getattr(getattr(repl, "agent", None), "session", None) is not session
            or getattr(store, "_session", None) is not session
            or bridge._connection_closed.is_set()):
        return 0
    try:
        from work_card_ledger import WorkCardLedger
        from work_card_publish_outbox import WorkCardPublicationOutbox
        from work_record_cards import bind_work_card_change_focus, build_project_work_record_cards

        outbox = WorkCardPublicationOutbox(session, store)
        pending_rows = outbox.list_pending()
        if not pending_rows:
            return 0
        ledger = WorkCardLedger(session, store)
        published = 0
        for original in pending_rows:
            for _attempt in range(3):
                if (bridge._connection_closed.is_set()
                        or repl.session is not session
                        or getattr(repl.agent, "session", None) is not session):
                    return published
                pending = next((row for row in outbox.list_pending()
                                if row["intent_id"] == original["intent_id"]), None)
                if pending is None:
                    break
                if (pending["project_id"] != project_id
                        or not outbox.validate_pending(pending)):
                    _work_card_publish_notice(
                        bridge, "待发布卡片的 Session/项目/run 绑定无法核实；保留待处理标记，未重做项目操作。",
                    )
                    break
                record = ledger.get_card(pending["card_id"]) if pending["card_id"] else None
                if record is None:
                    record = ledger.get_by_request_id(pending["request_id"])
                if record is None:
                    try:
                        store._validate_scope()
                        snapshot = bind_work_card_change_focus(
                            build_project_work_record_cards(store, project_id), pending,
                        )
                        saved = ledger.save_snapshot(pending["request_id"], snapshot)
                    except Exception:
                        saved = None
                    if (saved is None or saved.status not in {"saved", "already_saved"}
                            or not saved.card_id or not saved.record):
                        _work_card_publish_notice(
                            bridge, "待发布工作卡快照未能确认保存；保留待处理标记，项目操作不会重做。",
                        )
                        break
                    record = saved.record
                card_id = record.get("card_id") if type(record) is dict else None
                snapshot_payload = record.get("snapshot") if type(record) is dict else None
                expected_focus = {
                    "item_id": pending["latest_item_id"],
                    "requirement_id": pending["latest_requirement_id"],
                    "result_id": pending["result_id"],
                }
                stored_focus = snapshot_payload.get("change_focus") if type(snapshot_payload) is dict else None
                if (type(card_id) is not str or type(snapshot_payload) is not dict
                        or record.get("request_id") != pending["request_id"]
                        or record.get("card_id") != card_id
                        or snapshot_payload.get("project_id") != project_id
                        or snapshot_payload.get("session_id") != session.conversation_id
                        or (stored_focus is not None and stored_focus != expected_focus)):
                    _work_card_publish_notice(
                        bridge, "待发布卡片与当前 Session/项目不匹配；保留待处理标记，未重做项目操作。",
                    )
                    break
                bound = outbox.bind_card(
                    intent_id=pending["intent_id"], generation=pending["generation"], card_id=card_id,
                )
                if bound == "stale":
                    continue
                if bound not in {"bound", "already_bound"}:
                    _work_card_publish_notice(
                        bridge, "待发布卡片身份未能持久确认；保留待处理标记，未重做项目操作。",
                    )
                    break
                current = next((row for row in outbox.list_pending()
                                if row["intent_id"] == pending["intent_id"]), None)
                if (current is None or current["generation"] != pending["generation"]
                        or current["card_id"] != card_id):
                    continue
                presentation_id = _work_card_presentation_id(
                    session.conversation_id, pending["latest_run_id"], card_id,
                )
                if _emit_work_card_reference(
                    repl, bridge, session, presentation_id, card_id,
                    pending["latest_run_id"], active_run_id=None,
                ):
                    published += 1
                break
        return published
    except Exception:
        _work_card_publish_notice(
            bridge, "持久待发布状态无法确认；工作记录保留，未重做项目操作。",
        )
        return 0


def _resume_pending_project_work_cards(
    repl: ConversationRepl, bridge: WsBridge, session: Session, store: Any,
) -> int:
    """Resume the persisted first-update deadline, or flush it if already due."""
    project_id = getattr(getattr(session, "meta", None), "project_id", "")
    if (type(project_id) is not str or not project_id
            or getattr(repl, "session", None) is not session
            or getattr(getattr(repl, "agent", None), "session", None) is not session
            or getattr(store, "_session", None) is not session):
        return 0
    try:
        from work_card_publish_outbox import WorkCardPublicationOutbox
        from work_card_update_coalescer import ProjectWorkCardUpdateCoalescer

        outbox = WorkCardPublicationOutbox(session, store)
        pending_rows = outbox.list_pending()
        if not pending_rows:
            return 0
        pending = pending_rows[0]
        remaining = outbox.remaining_seconds(pending)
        if remaining <= 0:
            return _publish_pending_project_work_cards(repl, bridge, session, store)
        coalescer = bridge._work_card_update_coalescer
        if coalescer is None:
            coalescer = ProjectWorkCardUpdateCoalescer(delay_seconds=remaining)
            bridge._work_card_update_coalescer = coalescer
        coalescer.schedule(
            project_id,
            lambda: _publish_pending_project_work_cards(repl, bridge, session, store),
            immediate=remaining <= 0,
        )
        return 0
    except Exception:
        _work_card_publish_notice(
            bridge, "持久待发布窗口无法确认；保留卡片标记，未重做项目操作。",
        )
        return 0


def _emit_work_card_reference(
    repl: ConversationRepl,
    bridge: WsBridge,
    session: Session,
    presentation_id: str,
    card_id: str,
    run_id: str,
    *,
    active_run_id: str | None,
    after_durable_queue: Any | None = None,
    emit_event: bool = True,
    recovery_turn_run_id: str | None = None,
    recovery_turn_agent: Any | None = None,
    recover_reference_intent: bool = False,
) -> bool:
    """Queue/replay a host-authored reference; enqueue is not delivery or viewing."""
    def recovery_turn_live() -> bool:
        if recovery_turn_run_id is None:
            return True
        snapshot = bridge.lifecycle.snapshot()
        return (repl.session is session and repl.agent is recovery_turn_agent
                and getattr(repl.agent, "session", None) is session
                and not bridge.cancel_event.is_set() and not bridge._connection_closed.is_set()
                and snapshot is not None and snapshot.state == "running" and not snapshot.cancel_requested
                and snapshot.run_id == recovery_turn_run_id
                and bridge.lifecycle_conversation_id == session.conversation_id)
    try:
        if not recovery_turn_live():
            return False
        run_uuid = uuid.UUID(run_id)
        if (repl.session is not session or getattr(repl.agent, "session", None) is not session
                or session.conversation_id != getattr(repl.session, "conversation_id", None)
                or (active_run_id is not None and run_uuid.hex != uuid.UUID(active_run_id).hex)
                or _work_card_presentation_id(session.conversation_id, str(run_uuid), card_id) != presentation_id):
            return False
        if active_run_id is not None:
            snapshot = bridge.lifecycle.snapshot()
            if (bridge.cancel_event.is_set() or bridge._connection_closed.is_set()
                    or snapshot is None or snapshot.state != "running" or snapshot.cancel_requested
                    or snapshot.run_id != run_uuid.hex
                    or bridge.lifecycle_conversation_id != session.conversation_id):
                return False
        from work_records import WorkRecordStore
        from work_card_ledger import WorkCardLedger
        from work_card_presentations import WorkCardPresentations

        store = WorkRecordStore(session)
        ledger = WorkCardLedger(session, store)
        record = ledger.get_card(card_id)
        card = _public_work_card(record)
        if card is None:
            _work_card_publish_notice(
                bridge, "工作卡卡片投递状态无法确认；完整文字回查结果已保留，未标记已读。",
            )
            return False
        # Cancellation may arrive while the local ledger is being re-read.
        # Revalidate immediately before creating the durable retry reference.
        if active_run_id is not None:
            snapshot = bridge.lifecycle.snapshot()
            if (bridge.cancel_event.is_set() or bridge._connection_closed.is_set()
                    or snapshot is None or snapshot.state != "running" or snapshot.cancel_requested
                    or snapshot.run_id != run_uuid.hex
                    or bridge.lifecycle_conversation_id != session.conversation_id):
                return False
        if not recovery_turn_live():
            return False
        # Persist a receipt-tracked presentation before the history reference. If the
        # process stops between these writes, the bounded presentation queue retains
        # the run/card identity needed to recreate the same reference.
        presentations = WorkCardPresentations(session, store, ledger)
        from work_card_reference_outbox import WorkCardReferenceOutbox
        reference_outbox = WorkCardReferenceOutbox(session, store, ledger)
        existing_before = presentations.get(presentation_id)
        if (recover_reference_intent and existing_before.status == "found"
                and existing_before.fact is not None
                and existing_before.fact.receipt_state == "legacy_untracked"):
            return False
        # A trusted new host attempt is durable before presentation storage.
        if existing_before.status != "found":
            reference_outbox.begin(presentation_id, card_id, str(run_uuid))
        created = presentations.create(presentation_id, card_id, run_id=str(run_uuid))
        if created.status not in {"created", "already_created"}:
            if created.status == "uncertain" and recovery_turn_live():
                # The atomic write may have committed or failed. Preserve the exact
                # history reference only after a safe readback rules out conflicts;
                # never turn uncertainty into a delivery or receipt claim.
                snapshot = bridge.lifecycle.snapshot() if active_run_id is not None else None
                active_live = (active_run_id is None or (
                    not bridge.cancel_event.is_set() and not bridge._connection_closed.is_set()
                    and snapshot is not None and snapshot.state == "running" and not snapshot.cancel_requested
                    and snapshot.run_id == run_uuid.hex
                    and bridge.lifecycle_conversation_id == session.conversation_id))
                existing = presentations.get(presentation_id) if active_live else None
                if (existing is not None and (existing.status == "not_found" or (
                        existing.status == "found" and existing.fact is not None
                        and existing.fact.card_id == card_id and existing.fact.run_id == str(run_uuid)))):
                    session.append_sent_work_card_reference(presentation_id, card_id, run_id=str(run_uuid))
            _work_card_publish_notice(
                bridge, "工作卡卡片投递状态无法确认；完整文字回查结果已保留，引用与自动恢复未能确认，请核对当前会话历史；未标记已读。",
            )
            return False
        if emit_event and created.fact is not None and created.fact.receipt_state == "legacy_untracked":
            # Only an already persisted, exact host reference authorizes upgrading
            # old rows. Never promote unrelated legacy history into the retry queue.
            from session import _messages_unusable_for_ref, _valid_work_card_ref
            session.ensure_messages_loaded()
            if session._messages_corrupt or session._messages_unpersisted or _messages_unusable_for_ref(session):
                return False
            refs = [row for row in session.messages if _valid_work_card_ref(row)
                    and row.get("presentation_id") == presentation_id]
            if (len(refs) != 1 or refs[0].get("card_id") != card_id
                    or refs[0].get("run_id") != str(run_uuid)):
                return False
            # This rechecks persisted history and identity under the session lock.
            _, appended = session.append_sent_work_card_reference(presentation_id, card_id, run_id=str(run_uuid))
            if appended:
                return False
            tracked = presentations.begin_receipt_tracking(presentation_id, card_id, str(run_uuid))
            if tracked.status not in {"tracking", "already_tracked"}:
                return False
        else:
            session.append_sent_work_card_reference(presentation_id, card_id, run_id=str(run_uuid))
        # Clear only when a readback confirms the retryable presentation's
        # exact run binding; an untracked legacy row is never sufficient.
        confirmed = presentations.get(presentation_id)
        if (confirmed.status == "found" and confirmed.fact is not None
                and confirmed.fact.card_id == card_id and confirmed.fact.run_id == str(run_uuid)
                and confirmed.fact.receipt_state != "legacy_untracked"):
            reference_outbox.clear(presentation_id, card_id, str(run_uuid))
        elif recover_reference_intent:
            return False
        if after_durable_queue is not None:
            try:
                if after_durable_queue() not in {True, "queued", "already_queued"}:
                    _work_card_publish_notice(
                        bridge, "引用已进入持久历史，但 outbox 阶段无法确认；连接重试会复用同一 presentation id。",
                    )
                    return False
            except Exception:
                _work_card_publish_notice(
                    bridge, "引用已进入持久历史，但 outbox 阶段无法确认；连接重试会复用同一 presentation id。",
                )
                return False
        if not emit_event:
            return True
        event = {
            "type": "work-card.present", "session_id": session.conversation_id,
            "presentation_id": presentation_id, "card_id": card_id, "card": card,
        }
        # One connection may recover and publish on different threads. Reserve
        # before emit under the same lock; a definite enqueue failure rolls back
        # the reservation so the durable retry state is not stranded.
        with bridge._work_card_reference_lock:
            if not recovery_turn_live():
                return False
            if presentation_id in bridge._work_card_reference_enqueued:
                return True
            bridge._work_card_reference_enqueued.add(presentation_id)
            try:
                bridge.emit(event)
            except BaseException:
                bridge._work_card_reference_enqueued.discard(presentation_id)
                raise
        if getattr(getattr(session, "meta", None), "project_id", ""):
            try:
                from work_card_publish_outbox import WorkCardPublicationOutbox
                cleared = WorkCardPublicationOutbox(session, store).clear_matching_card(
                    card_id=card_id, run_id=str(run_uuid),
                )
                if cleared == "uncertain":
                    _work_card_publish_notice(
                        bridge, "卡片已排入当前连接，但待发布标记清理未确认；可能重发同一张卡，项目操作不会重做。",
                    )
            except Exception:
                _work_card_publish_notice(
                    bridge, "卡片已排入当前连接，但待发布标记清理无法确认；可能重发同一张卡，项目操作不会重做。",
                )
        return True
    except Exception:
        _work_card_publish_notice(
            bridge, "工作卡卡片投递状态无法确认；完整文字回查结果已保留，引用与自动恢复未能确认；未标记已读。",
        )
        return False


def _resume_linked_item_copy_cards(repl, bridge, session, store, *, binding_ids=None,
                                   emit_event=False, active_run_id=None, turn_epoch=None):
    """Recover only confirmed parent copies; never access source or execute workers."""
    if (getattr(repl, "session", None) is not session
            or getattr(getattr(repl, "agent", None), "session", None) is not session
            or getattr(store, "_session", None) is not session or bridge._connection_closed.is_set()):
        return 0
    try:
        from multi_project_work_record_bindings import ConversationWorkRecordBindings
        from work_card_ledger import WorkCardLedger, build_linked_item_copy_snapshot, linked_item_copy_request_id
        bindings = ConversationWorkRecordBindings(session)
        ledger = WorkCardLedger(session, store)
        rows = bindings.list_bindings()
    except Exception:
        _work_card_publish_notice(bridge, "已确认副本的待发布状态无法读取；未假定已送达。")
        return 0
    queued = 0
    for row in rows:
        copy = row.get("card_copy")
        if (type(copy) is not dict or copy.get("intent", {}).get("state") != "pending"
                or (binding_ids is not None and row["binding_id"] not in binding_ids)):
            continue
        try:
            snapshot = build_linked_item_copy_snapshot(session.conversation_id, copy)
            start = snapshot.host_start
            run_id = str(uuid.UUID(start["parent_run_id"]))
            if active_run_id is not None and (uuid.UUID(active_run_id).hex != uuid.UUID(run_id).hex
                                              or start["turn_epoch"] != turn_epoch):
                continue
            if active_run_id is not None:
                life = bridge.lifecycle.snapshot()
                if (life is None or life.run_id != active_run_id or life.sequence != turn_epoch
                        or life.state != "running" or life.cancel_requested or bridge.cancel_event.is_set()
                        or bridge._connection_closed.is_set() or repl.session is not session
                        or getattr(repl.agent, "session", None) is not session
                        or bridge.lifecycle_conversation_id != session.conversation_id):
                    continue
            saved = ledger.save_linked_item_copy_snapshot(
                linked_item_copy_request_id(session.conversation_id, snapshot.copy_id), snapshot)
            if saved.status not in {"saved", "already_saved"} or not saved.card_id:
                continue
            presentation_id = _work_card_presentation_id(session.conversation_id, run_id, saved.card_id)
            def current():
                if (getattr(repl, "session", None) is not session
                        or getattr(getattr(repl, "agent", None), "session", None) is not session
                        or bridge._connection_closed.is_set()):
                    return False
                if active_run_id is None:
                    return True
                life = bridge.lifecycle.snapshot()
                return (life is not None and life.run_id == active_run_id and life.sequence == turn_epoch
                        and life.state == "running" and not life.cancel_requested
                        and not bridge.cancel_event.is_set()
                        and bridge.lifecycle_conversation_id == session.conversation_id)
            if not current():
                continue
            if _emit_work_card_reference(
                    repl, bridge, session, presentation_id, saved.card_id, run_id,
                    active_run_id=active_run_id, emit_event=emit_event,
                    after_durable_queue=lambda: bindings.mark_item_copy_reference_queued(
                        binding_id=row["binding_id"], copy_id=snapshot.copy_id,
                        card_id=saved.card_id, presentation_id=presentation_id,
                        run_id=run_id, is_current=current)):
                queued += 1
        except Exception:
            _work_card_publish_notice(bridge, "已确认副本仍待安全恢复；未假定已送达。")
    return queued


def _resume_linked_project_session_copies(repl, bridge, session, store, *, binding_ids=None,
                                   emit_event=False, active_run_id=None, turn_epoch=None):
    """Recover only confirmed parent copies; never access source or execute workers."""
    if (getattr(repl, "session", None) is not session
            or getattr(getattr(repl, "agent", None), "session", None) is not session
            or getattr(store, "_session", None) is not session or bridge._connection_closed.is_set()):
        return 0
    try:
        from multi_project_work_record_bindings import ConversationWorkRecordBindings
        from work_card_ledger import WorkCardLedger, build_linked_project_session_copy_snapshot, linked_project_session_copy_request_id
        bindings = ConversationWorkRecordBindings(session)
        ledger = WorkCardLedger(session, store)
        rows = bindings.list_bindings()
    except Exception:
        _work_card_publish_notice(bridge, "已确认副本的待发布状态无法读取；未假定已送达。")
        return 0
    queued = 0
    for row in rows:
        copy = row.get("project_session_copy")
        if (type(copy) is not dict or copy.get("intent", {}).get("state") != "pending"
                or (binding_ids is not None and row["binding_id"] not in binding_ids)):
            continue
        try:
            snapshot = build_linked_project_session_copy_snapshot(session.conversation_id, row["binding_id"], copy)
            start = next(f["host_start"] for f in snapshot.source["worker_facts"] if f["binding_id"] == row["binding_id"])
            run_id = str(uuid.UUID(start["parent_run_id"]))
            if active_run_id is not None and (uuid.UUID(active_run_id).hex != uuid.UUID(run_id).hex
                                              or start["turn_epoch"] != turn_epoch):
                continue
            if active_run_id is not None:
                life = bridge.lifecycle.snapshot()
                if (life is None or life.run_id != active_run_id or life.sequence != turn_epoch
                        or life.state != "running" or life.cancel_requested or bridge.cancel_event.is_set()
                        or bridge._connection_closed.is_set() or repl.session is not session
                        or getattr(repl.agent, "session", None) is not session
                        or bridge.lifecycle_conversation_id != session.conversation_id):
                    continue
            saved = ledger.save_linked_project_session_copy_snapshot(
                linked_project_session_copy_request_id(session.conversation_id, snapshot.copy_id), snapshot)
            if saved.status not in {"saved", "already_saved"} or not saved.card_id:
                _work_card_publish_notice(bridge, "来源 Session 副本未能安全保存为工作卡；待发布意图仍保留，未假定已送达。")
                continue
            presentation_id = _work_card_presentation_id(session.conversation_id, run_id, saved.card_id)
            def current():
                if (getattr(repl, "session", None) is not session
                        or getattr(getattr(repl, "agent", None), "session", None) is not session
                        or bridge._connection_closed.is_set()):
                    return False
                if active_run_id is None:
                    return True
                life = bridge.lifecycle.snapshot()
                return (life is not None and life.run_id == active_run_id and life.sequence == turn_epoch
                        and life.state == "running" and not life.cancel_requested
                        and not bridge.cancel_event.is_set()
                        and bridge.lifecycle_conversation_id == session.conversation_id)
            if not current():
                continue
            if _emit_work_card_reference(
                    repl, bridge, session, presentation_id, saved.card_id, run_id,
                    active_run_id=active_run_id, emit_event=emit_event,
                    after_durable_queue=lambda: bindings.mark_project_session_copy_reference_queued(
                        binding_id=row["binding_id"], copy_id=snapshot.copy_id,
                        card_id=saved.card_id, presentation_id=presentation_id,
                        run_id=run_id, is_current=current)):
                queued += 1
        except Exception:
            _work_card_publish_notice(bridge, "已确认副本仍待安全恢复；未假定已送达。")
    return queued


def _resume_linked_project_session_refreshes(repl, bridge, session, store, *, binding_ids=None,
                                   emit_event=False, active_run_id=None, turn_epoch=None):
    """Recover only confirmed parent copies; never access source or execute workers."""
    if (getattr(repl, "session", None) is not session
            or getattr(getattr(repl, "agent", None), "session", None) is not session
            or getattr(store, "_session", None) is not session or bridge._connection_closed.is_set()):
        return 0
    try:
        from multi_project_work_record_bindings import ConversationWorkRecordBindings
        from work_card_ledger import WorkCardLedger, build_linked_project_session_copy_snapshot, linked_project_refresh_request_id
        bindings = ConversationWorkRecordBindings(session)
        ledger = WorkCardLedger(session, store)
        rows = bindings.list_project_session_refreshes()
    except Exception:
        _work_card_publish_notice(bridge, "已确认副本的待发布状态无法读取；未假定已送达。")
        return 0
    queued = 0
    for row in rows:
        copy = row.get("copy")
        if (type(copy) is not dict or copy.get("intent", {}).get("state") != "pending"
                or (binding_ids is not None and row["binding_id"] not in binding_ids)):
            continue
        try:
            snapshot = build_linked_project_session_copy_snapshot(session.conversation_id, row["binding_id"], copy)
            run_id = row["run_id"]
            if active_run_id is not None and (uuid.UUID(active_run_id).hex != uuid.UUID(run_id).hex
                                              or row["turn_epoch"] != turn_epoch):
                continue
            if active_run_id is not None:
                life = bridge.lifecycle.snapshot()
                if (life is None or life.run_id != active_run_id or life.sequence != turn_epoch
                        or life.state != "running" or life.cancel_requested or bridge.cancel_event.is_set()
                        or bridge._connection_closed.is_set() or repl.session is not session
                        or getattr(repl.agent, "session", None) is not session
                        or bridge.lifecycle_conversation_id != session.conversation_id):
                    continue
            saved = ledger.save_linked_project_session_copy_snapshot(
                linked_project_refresh_request_id(session.conversation_id, row["refresh_id"]), snapshot)
            if saved.status not in {"saved", "already_saved"} or not saved.card_id:
                _work_card_publish_notice(bridge, "来源 Session 副本未能安全保存为工作卡；待发布意图仍保留，未假定已送达。")
                continue
            presentation_id = _work_card_presentation_id(session.conversation_id, run_id, saved.card_id)
            def current():
                if (getattr(repl, "session", None) is not session
                        or getattr(getattr(repl, "agent", None), "session", None) is not session
                        or bridge._connection_closed.is_set()):
                    return False
                if active_run_id is None:
                    return True
                life = bridge.lifecycle.snapshot()
                return (life is not None and life.run_id == active_run_id and life.sequence == turn_epoch
                        and life.state == "running" and not life.cancel_requested
                        and not bridge.cancel_event.is_set()
                        and bridge.lifecycle_conversation_id == session.conversation_id)
            if not current():
                continue
            if _emit_work_card_reference(
                    repl, bridge, session, presentation_id, saved.card_id, run_id,
                    active_run_id=active_run_id, emit_event=emit_event,
                    after_durable_queue=lambda: bindings.mark_project_refresh_reference_queued(
                        refresh_id=row["refresh_id"], card_id=saved.card_id,
                        presentation_id=presentation_id, is_current=current)):
                queued += 1
        except Exception:
            _work_card_publish_notice(bridge, "已确认副本仍待安全恢复；未假定已送达。")
    return queued


def _emit_pending_work_card_references(repl: ConversationRepl, bridge: WsBridge) -> int:
    """Replay a bounded receipt queue; legacy references remain available through history."""
    session = getattr(repl, "session", None)
    if type(session) is not Session or getattr(repl.agent, "session", None) is not session:
        return 0
    try:
        from work_records import WorkRecordStore
        from work_card_ledger import WorkCardLedger
        from work_card_presentations import WorkCardPresentations

        store = WorkRecordStore(session)
        _resume_current_dialogue_project_work_cards(repl, bridge, session, store)
        _resume_independent_work_cards(repl, bridge, session, store)
        _resume_linked_item_copy_cards(repl, bridge, session, store)
        _resume_linked_project_session_copies(repl, bridge, session, store)
        _resume_linked_project_session_refreshes(repl, bridge, session, store)
        if store._bound_project_id:
            _resume_pending_project_work_cards(repl, bridge, session, store)
        ledger = WorkCardLedger(session, store)
        from work_card_reference_outbox import WorkCardReferenceOutbox
        reference_outbox = WorkCardReferenceOutbox(session, store, ledger)
        pending_intents = reference_outbox.claim_recovery_batch(WORK_CARD_RECEIPT_RETRY_BATCH)
        for intent in pending_intents:
            _emit_work_card_reference(
                repl, bridge, session, intent["presentation_id"], intent["card_id"], intent["run_id"],
                active_run_id=None, emit_event=False, recover_reference_intent=True,
            )
        remaining_intents = reference_outbox.list()
        if remaining_intents:
            _work_card_publish_notice(bridge, f"工作卡引用还有 {len(remaining_intents)} 条待安全恢复；未假定已送达。")
        presentations = WorkCardPresentations(session, store, ledger)
        retryable = presentations.list_retryable_receipts(limit=WORK_CARD_RECEIPT_RETRY_BATCH)
        backlog = presentations.receipt_backlog_counts()
    except Exception:
        _work_card_publish_notice(bridge, "工作卡 receipt 队列无法安全读取；旧历史保留，未假定已送达。")
        return 0

    emitted = 0
    seen: set[str] = set()
    for fact in retryable:
        if (fact.session_id != session.conversation_id or not fact.run_id
                or _work_card_presentation_id(session.conversation_id, fact.run_id, fact.card_id)
                != fact.presentation_id or fact.presentation_id in seen):
            continue
        seen.add(fact.presentation_id)
        if _emit_work_card_reference(
            repl, bridge, session, fact.presentation_id, fact.card_id, fact.run_id,
            active_run_id=None,
        ):
            emitted += 1
    retryable_count = len(retryable)
    if (backlog["pending"] > retryable_count or backlog["deferred"]
            or backlog["legacy_untracked"]):
        bridge.emit({
            "type": "work-card.receipt.backlog", "session_id": session.conversation_id,
            "pending_count": backlog["pending"], "deferred_count": backlog["deferred"],
            "legacy_untracked_count": backlog["legacy_untracked"],
            "retry_batch_size": retryable_count, "batch_limit": WORK_CARD_RECEIPT_RETRY_BATCH,
            "max_send_attempts": 3,
        })
    return emitted

def _continue_work_card_receipts(message, repl, bridge, *, active_run_id=None):
    """One user-requested bounded page, never an automatic ACK-driven drain."""
    session = getattr(repl, "session", None)
    bound_agent = getattr(repl, "agent", None)
    request_id = message.get("request_id")
    session_id = message.get("session_id")
    response = {"type": "work-card.receipt.continue.done", "request_id": request_id,
                "session_id": session_id}
    try:
        valid_id = type(request_id) is str and str(uuid.UUID(request_id)) == request_id
    except (ValueError, TypeError, AttributeError):
        valid_id = False
    if (set(message) != {"type", "session_id", "request_id"} or not valid_id
            or type(session) is not Session or getattr(repl.agent, "session", None) is not session
            or session_id != session.conversation_id):
        return {**response, "status": "rejected"}
    key = (session_id, request_id)
    if key in bridge._work_card_continue_requests:
        return {**response, "status": "already_processed"}
    if len(bridge._work_card_continue_requests) >= 512:
        return {**response, "status": "capacity", "message": "当前连接的继续请求已达上限；未自动继续。"}
    # Claim before enqueue: a retry after an uncertain/partial response cannot
    # cause a second page. Reconnect uses the ordinary receipt recovery path.
    bridge._work_card_continue_requests.add(key)
    try:
        from work_records import WorkRecordStore
        from work_card_ledger import WorkCardLedger
        from work_card_presentations import WorkCardPresentations
        store = WorkRecordStore(session)
        presentations = WorkCardPresentations(session, store, WorkCardLedger(session, store))
        with bridge._work_card_reference_lock:
            excluded = frozenset(bridge._work_card_reference_enqueued)
        selected = presentations.list_retryable_receipts(limit=5, exclude_ids=excluded)
        queued = 0
        failed = 0
        cancelled = False
        for fact in selected:
            if active_run_id is not None:
                current = bridge.lifecycle.snapshot()
                if (bridge.cancel_event.is_set() or bridge._connection_closed.is_set()
                        or current is None or current.state != "running" or current.cancel_requested
                        or current.run_id != active_run_id or repl.session is not session
                        or repl.agent is not bound_agent or getattr(bound_agent, "session", None) is not session
                        or bridge.lifecycle_conversation_id != session_id):
                    cancelled = True
                    break
            if (fact.session_id != session_id or not fact.run_id
                    or _work_card_presentation_id(session_id, fact.run_id, fact.card_id) != fact.presentation_id
                    or not _emit_work_card_reference(repl, bridge, session, fact.presentation_id,
                                                     fact.card_id, fact.run_id, active_run_id=None,
                                                     recovery_turn_run_id=active_run_id,
                                                     recovery_turn_agent=bound_agent)):
                failed += 1
            else:
                queued += 1
        counts = presentations.receipt_backlog_counts()
        with bridge._work_card_reference_lock:
            excluded = frozenset(bridge._work_card_reference_enqueued)
        more = bool(presentations.list_retryable_receipts(limit=1, exclude_ids=excluded))
        return {**response, "status": "partial" if failed or cancelled else "ok", "queued_count": queued,
                     "failed_count": failed, "has_more": more,
                     "pending_count": counts["pending"], "deferred_count": counts["deferred"],
                     "legacy_untracked_count": counts["legacy_untracked"], "batch_limit": 5,
                     "message": "仅排队，不代表已接收；旧历史不自动重发。"}
    except Exception:
        return {**response, "status": "unavailable", "message": "待接收卡片无法安全继续；未假定已送达。"}


def _bind_work_card_reference_intent(repl: ConversationRepl, bridge: WsBridge) -> None:
    """Bind the private recall result to this host run; intent is disabled by default."""
    def prepare(session: Session, resolution: Any) -> WorkCardReferenceIntent | str:
        snapshot = bridge.lifecycle.snapshot()
        run_id = getattr(snapshot, "run_id", None)
        if repl.session is not session or getattr(repl.agent, "session", None) is not session:
            return "rejected"
        if type(run_id) is not str or not run_id:
            return "rejected"
        return _prepare_confirmed_work_card_reference(
            session, resolution, run_id, enabled=_WORK_CARD_REFERENCE_PUBLICATION_ENABLED,
        )

    repl.agent.work_card_reference_intent_fn = prepare
    bound_agent = repl.agent

    def continue_receipts(session: Session) -> dict:
        snapshot = bridge.lifecycle.snapshot()
        if (repl.session is not session or repl.agent is not bound_agent
                or getattr(bound_agent, "session", None) is not session
                or snapshot is None or snapshot.state != "running" or snapshot.cancel_requested
                or bridge.cancel_event.is_set() or bridge._connection_closed.is_set()
                or bridge.lifecycle_conversation_id != session.conversation_id):
            return {"status": "unavailable"}
        return _continue_work_card_receipts(
            {"type": "work-card.receipt.continue", "session_id": session.conversation_id,
             "request_id": str(uuid.uuid4())}, repl, bridge, active_run_id=snapshot.run_id,
        )
    repl.agent.work_card_receipt_continue_fn = continue_receipts


def _work_card_result_reply_question_shape(text: str) -> bool:
    if type(text) is not str or not text.strip() or len(text.encode("utf-8")) > 4096:
        return False
    if any(mark in text for mark in ('"', "'", "`", "“", "”", "‘", "’", "「", "」", "『", "』", ">")):
        return False
    if re.search(r"取消|拒绝|不要|不用|(?<!特)别|不需要|无需|请勿|停止|撤销|否认|不标记|(?<![A-Za-z])(?:don't|do not|cancel|refuse|deny|stop)(?![A-Za-z])", text, re.IGNORECASE):
        return False
    lowered = text.casefold()
    return any(word in lowered for word in (
        "?", "？", "怎么", "如何", "为什么", "什么", "哪些", "哪", "解释", "说明", "意思", "含义",
        "能否", "可以", "how", "what", "why", "explain", "mean", "tell me",
    ))


def _work_card_candidate_terms(value: str) -> set[str]:
    lowered = value.casefold()
    terms: set[str] = set()
    for run in re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff]{2,}", lowered):
        terms.update(run[index:index + 2] for index in range(len(run) - 1))
    terms.update(re.findall(r"[a-z0-9]{3,}", lowered))
    return terms


_RESULT_REPLY_GENERIC_TERMS = frozenset((
    "结果", "刚才", "这个", "那个", "上述", "这样", "如此", "它", "工作卡", "进展", "输出",
    "result", "this", "that", "above", "it",
))
_RESULT_REPLY_STOP_TERMS = frozenset((
    "当时", "之前", "刚刚", "结果", "工作", "情况", "过程", "怎么", "如何", "什么", "哪些",
    "解释", "说明", "意思", "含义", "是否", "能够", "可以", "修改", "修复", "实现", "处理",
    "更新", "完成", "优化", "记录", "那个", "这个", "这样", "如此", "the", "and", "for", "with",
    "what", "how", "why", "about", "from", "when", "then", "was", "were", "does", "did", "are", "you",
))


def _eligible_work_card_result_reply(text: str, candidates=()) -> bool:
    """Gate outbound recognition to a non-negated question tied to result or candidate wording."""
    if not _work_card_result_reply_question_shape(text):
        return False
    lowered = text.casefold()
    generic_cjk = ("结果", "刚才", "这个", "那个", "上述", "这样", "如此", "它", "工作卡", "进展", "输出")
    if any(term in lowered for term in generic_cjk):
        return True
    generic_english = set(re.findall(r"[a-z0-9]{3,}", lowered))
    if generic_english.intersection(_RESULT_REPLY_GENERIC_TERMS):
        return True
    question_terms = _work_card_candidate_terms(text) - _RESULT_REPLY_STOP_TERMS
    if not question_terms:
        return False
    for candidate in candidates:
        for value in (getattr(candidate, "title", None), getattr(candidate, "requirement_text", None)):
            if type(value) is str and question_terms.intersection(
                _work_card_candidate_terms(value) - _RESULT_REPLY_STOP_TERMS
            ):
                return True
    return False

class _WorkCardResultReplyTurn:
    """Optional host bridge from a normal user append to one reliable result view."""

    def __init__(self, repl: ConversationRepl, bridge: WsBridge):
        self._repl = repl
        self._bridge = bridge

    def _live(self, session: Session, run_id: str) -> bool:
        bridge, repl = self._bridge, self._repl
        try:
            snapshot = bridge.lifecycle.snapshot()
            return bool(
                repl.session is session and getattr(repl.agent, "session", None) is session
                and snapshot is not None and snapshot.state == "running"
                and snapshot.run_id == run_id and not snapshot.cancel_requested
                and not bridge.cancel_event.is_set() and not bridge._connection_closed.is_set()
                and not bridge._session_binding_transitions
                and bridge.lifecycle_conversation_id == session.conversation_id
            )
        except Exception:
            return False

    def before_user_append(self, session: Session, user_text: str) -> object | None:
        """Recognize only a complete current-session result set under existing consent."""
        try:
            from llm_client import LLMClient
            from work_card_reply_binding import HostReplyBindingAuthority
            from work_card_result_reply_recognition import (
                prepare_result_reply_recognition, recognize_result_reply,
            )
            from work_card_visible_result_candidates import collect_displayed_result_candidates

            repl = self._repl
            agent = repl.agent
            if not _work_card_result_reply_question_shape(user_text):
                return None
            if repl.session is not session or getattr(agent, "session", None) is not session:
                return None
            snapshot = self._bridge.lifecycle.snapshot()
            run_id = getattr(snapshot, "run_id", None)
            if (snapshot is None or snapshot.state != "running" or type(run_id) is not str
                    or not run_id or not self._live(session, run_id)):
                return None
            collected = collect_displayed_result_candidates(session)
            if not collected.complete or not collected.candidates:
                return None
            if not _eligible_work_card_result_reply(user_text, collected.candidates):
                return None
            prepared = prepare_result_reply_recognition(
                session, user_text, collected.candidates, candidates_complete=collected.complete,
            )
            if prepared.status != "ready":
                return None
            confirm_fn = getattr(getattr(agent, "executor", None), "confirm_fn", None)
            if not callable(confirm_fn):
                return None
            client = LLMClient(paths=session.paths)
            client.set_cancel_event(agent.cancel_event)

            def fresh_candidates():
                if not self._live(session, run_id):
                    return ()
                current = collect_displayed_result_candidates(session)
                return current.candidates if current.complete else ()

            recognized = recognize_result_reply(
                prepared, client, confirm_fn=confirm_fn,
                current_session=lambda: session if self._live(session, run_id) else None,
                current_candidates=fresh_candidates,
                is_cancelled=lambda: not self._live(session, run_id),
            )
            if (recognized.status != "match" or recognized.candidate_id not in prepared.candidate_ids
                    or not self._live(session, run_id)):
                return None
            candidate = prepared.candidates[prepared.candidate_ids.index(recognized.candidate_id)]
            target = {
                "session_id": candidate.session_id,
                "presentation_id": candidate.presentation_id,
                "card_id": candidate.card_id,
                "result_id": candidate.result_id,
                "requirement_id": candidate.requirement_id,
            }
            authority = HostReplyBindingAuthority(session, lambda: repl.session)
            ingress = authority.prepare_user_message_binding(user_text, target)
            if ingress is None or not self._live(session, run_id):
                return None
            return (authority, ingress, candidate, run_id)
        except Exception:
            # Recognition/config/history uncertainty never authorizes a result view.
            return None

    def _live_with_lease(self, session: Session, run_id: str, lease: object) -> bool:
        bridge, repl = self._bridge, self._repl
        try:
            snapshot = bridge.lifecycle.snapshot()
            return bool(
                bridge.result_view_lease_is_bound(lease, session)
                and repl.session is session and getattr(repl.agent, "session", None) is session
                and snapshot is not None and snapshot.state == "running"
                and snapshot.run_id == run_id and not snapshot.cancel_requested
                and not bridge.cancel_event.is_set() and not bridge._connection_closed.is_set()
                and bridge.lifecycle_conversation_id == session.conversation_id
            )
        except Exception:
            return False

    def after_user_append(
        self, pending: object, session: Session, user_text: str, message_index: int,
    ) -> None:
        """ACK only the exact persisted message while holding session and cancel guards."""
        from work_card_reply_binding import HostReplyBindingAuthority
        if type(pending) is not tuple or len(pending) != 4:
            return
        authority, ingress, candidate, run_id = pending
        bridge = self._bridge
        if (type(authority) is not HostReplyBindingAuthority or not self._live(session, run_id)
                or type(user_text) is not str):
            return
        lease = bridge.try_acquire_result_view_lease(session)
        if lease is None:
            return
        try:
            if not self._live_with_lease(session, run_id, lease):
                return
            from work_card_visible_result_candidates import collect_displayed_result_candidates
            current = collect_displayed_result_candidates(session)
            if (not current.complete or candidate not in current.candidates
                    or not self._live_with_lease(session, run_id, lease)):
                return
            receipt = authority.bind_persisted_user_message(ingress, message_index)
            if receipt is None:
                return
            with bridge._result_view_write_lock:
                if not self._live_with_lease(session, run_id, lease):
                    return
                try:
                    from work_card_ledger import WorkCardLedger
                    from work_card_presentations import WorkCardPresentations
                    from work_records import WorkRecordStore

                    store = WorkRecordStore(session)
                    ledger = WorkCardLedger(session, store)
                    presentations = WorkCardPresentations(session, store, ledger)
                    _record_work_card_result_view(
                        session=session, store=store, ledger=ledger, presentations=presentations,
                        session_id=candidate.session_id, presentation_id=candidate.presentation_id,
                        card_id=candidate.card_id, result_id=candidate.result_id,
                        requirement_id=candidate.requirement_id, action="reliable_reply",
                        reply_binding_authority=authority, reply_binding=receipt, reply_text=user_text,
                        write_guard=lambda: self._live_with_lease(session, run_id, lease),
                    )
                except Exception:
                    return
        finally:
            bridge.release_result_view_lease(lease)

def _patch_repl(
    repl: ConversationRepl,
    bridge: WsBridge,
    *,
    evolution_client: Any | None = None,
) -> None:
    bridge._session_provider = lambda: repl.session
    bridge.init_post_turn_send_gate(repl.session)
    repl.agent.work_card_batch_confirm_fn = bridge.confirm_work_card_batch
    repl.agent.project_worker_verification_fn = bridge.verify_project_worker_requirement
    _bind_work_card_reference_intent(repl, bridge)
    repl.agent.work_card_result_reply_turn = _WorkCardResultReplyTurn(repl, bridge)
    repl.stream_handlers = bridge.stream_handlers()
    repl.agent.stream_handlers = repl.stream_handlers
    repl.agent.executor.confirm_fn = bridge.confirm_fn
    repl.agent.executor.session.evolution_confirm_with_receipt = bridge.confirm_with_receipt
    repl.agent.executor.on_event = bridge.on_executor_event
    repl.agent.on_turn_event = bridge.emit_turn_event
    repl.agent.bind_cancel_event(bridge.cancel_event)
    _bind_multi_project_overview_cancel_event(repl.agent, bridge.cancel_event)
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
        repl.agent.work_card_batch_confirm_fn = bridge.confirm_work_card_batch
        repl.agent.project_worker_verification_fn = bridge.verify_project_worker_requirement
        _bind_work_card_reference_intent(repl, bridge)
        repl.agent.work_card_result_reply_turn = _WorkCardResultReplyTurn(repl, bridge)
        repl.stream_handlers = bridge.stream_handlers()
        if evolution_client is not None:
            repl.agent.executor.evolution_client = evolution_client
        repl.agent.stream_handlers = repl.stream_handlers
        repl.agent.executor.confirm_fn = bridge.confirm_fn
        repl.agent.executor.session.evolution_confirm_with_receipt = bridge.confirm_with_receipt
        repl.agent.executor.on_event = bridge.on_executor_event
        repl.agent.on_turn_event = bridge.emit_turn_event
        repl.agent.bind_cancel_event(bridge.cancel_event)
        _bind_multi_project_overview_cancel_event(repl.agent, bridge.cancel_event)
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


def _desktop_work_record_controller_factory(
    paths: AgentPaths, bridge: WsBridge
) -> WorkRecordControllerFactory:
    """Bind current-session read-only recall; Agent requires fresh consent before send."""
    def factory(session: Session, agent: Any):
        from llm_client import LLMClient
        from work_records import WorkRecordStore
        from work_records_experiment import WorkRecordExperimentController
        from work_records_luna_recall import WorkRecordsLunaRecall
        from multi_project_luna_intent import MODEL_ID, OutboundRequest, MultiProjectLunaIntent
        from main import WorkRecordControllerBindings

        client = LLMClient(paths=paths)
        client.set_cancel_event(agent.cancel_event)
        store = WorkRecordStore(session)

        def approve_multi_project_intent_outbound(request: OutboundRequest) -> bool:
            """Confirm one exact text-only intent request; this is not data-read approval."""
            if (
                type(request) is not OutboundRequest
                or request.model != MODEL_ID
                or request.vendor != "0x567"
                or request.provider_model != "gpt-6-luna"
                or type(request.user_text) is not str
                or not request.user_text.strip()
            ):
                return False
            preview = (
                "多项目概况意图识别外发确认\n"
                "接收方：0x567（gpt-6-luna，模型标识 0x567-flash）\n"
                "用途：仅发送下列本条用户文本做意图识别和文本中项目名提取；"
                "不发送项目记录清单、工作记录、Session/messages 或其他对话上下文。\n"
                "分类结果只是非权威候选，不是读取、访问或写入授权；"
                "后续所选项目侧车读取仍需单独的精确范围确认。\n"
                "精确外发文本：\n" + request.user_text
            )
            try:
                return bridge.confirm_fn(preview, False) == "y"
            except Exception:
                return False

        from linked_project_refresh_intent import LinkedProjectRefreshIntent
        agent.project_card_refresh_recognizer = LinkedProjectRefreshIntent(client, bridge.confirm_fn)

        agent.multi_project_overview_recognizer = MultiProjectLunaIntent(
            client,
            approve_outbound=approve_multi_project_intent_outbound,
        )
        matcher = WorkRecordsLunaRecall(client)
        controller = WorkRecordExperimentController(
            agent=agent,
            store=store,
            recognizer=lambda *_args: {"action": "none"},
            semantic_matcher=matcher,
            semantic_excerpt_policy=lambda title, requirements: "\n".join((title, *requirements)),
        )
        return WorkRecordControllerBindings(recall=controller)

    return factory


def _build_repl(
    session: Session,
    paths: AgentPaths,
    bridge: WsBridge,
    *,
    evolution_client: Any | None = None,
    work_record_controller_factory: WorkRecordControllerFactory | None = None,
) -> ConversationRepl:
    if work_record_controller_factory is None:
        work_record_controller_factory = _desktop_work_record_controller_factory(paths, bridge)
    repl = ConversationRepl.from_session(
        session,
        paths=paths,
        input_fn=bridge.input_fn,
        output_fn=bridge.output_fn,
        config=ReplConfig(),
        stream_handlers=bridge.stream_handlers(),
        work_record_controller_factory=work_record_controller_factory,
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


def _line_may_rebind_session(line: Any) -> bool:
    """Recognize direct REPL commands that can replace ConversationRepl.session."""
    if not isinstance(line, str):
        return False
    stripped = line.strip()
    if stripped.casefold() in {"new", "新会话"}:
        return True
    try:
        from project_cli import parse_project_command
        parsed = parse_project_command(stripped)
    except Exception:
        return stripped.casefold().startswith(("项目", "project", "新项目", "new project"))
    return parsed is not None and parsed.kind in {"new", "open", "switch", "new_thread"}


def _refresh_repl_project_binding(repl: ConversationRepl) -> None:
    """Keep the long-lived executor in sync with project controls changed by the UI."""
    repl.agent.executor.session.refresh_bound_project_meta()


def _record_project_switch_boundary(
    *,
    session: Session,
    switch_event: dict[str, Any] | None,
    recorder: Callable[..., Any] | None,
    journal: Any | None,
    report: Callable[[str], None] | None = None,
) -> bool:
    """Record only a completed, non-noop switch; journal failures never undo it."""
    if (
        recorder is None
        or journal is None
        or not isinstance(switch_event, dict)
        or switch_event.get("type") != "project.switch.done"
        or switch_event.get("action") == "noop"
    ):
        return False
    event_id = switch_event.get("request_id")
    if not isinstance(event_id, str) or not event_id.strip():
        return False
    try:
        recorder(
            session,
            occurred_at=datetime.now(timezone.utc),
            event_id=event_id,
            journal=journal,
            paths=session.paths,
        )
    except Exception:
        if report is not None:
            try:
                report("Project switch succeeded, but day journal recording failed.")
            except Exception:
                pass
        return False
    return True



def _log_notification_failure(label: str, exc: BaseException) -> None:
    try:
        log_sidecar_exception(label, exc)
    except Exception:
        pass


def _emit_safely(emit: Callable[[dict[str, Any]], None], event: dict[str, Any], label: str) -> None:
    try:
        emit(event)
    except Exception as exc:
        _log_notification_failure(label, exc)


def _capture_requires_reconciliation(outcome: Any) -> bool:
    status = getattr(outcome, "status", None)
    return isinstance(status, str) and status in _CAPTURE_RECONCILIATION_MESSAGES


def _emit_capture_reconciliation_notice(bridge: WsBridge, outcome: Any) -> bool:
    """Warn without exposing capture text when prior persistence/execution is uncertain."""
    if not _capture_requires_reconciliation(outcome):
        return False
    message = _CAPTURE_RECONCILIATION_MESSAGES[outcome.status]
    _emit_safely(
        bridge.emit_turn_event,
        {"type": "turn.notice", "level": "warn", "text": message},
        "work-record reconciliation notice failed",
    )
    return True


def _finish_execution_safely(
    bridge: WsBridge, *, run_id: str, finish_reason: str, ok: bool,
) -> Any:
    """Make lifecycle finality independent of websocket notification delivery."""
    changed = False
    try:
        changed = bridge.finish_execution(finish_reason, ok=ok, run_id=run_id)
    except Exception as exc:
        _log_notification_failure("execution state notification failed", exc)

    snapshot = bridge.lifecycle.snapshot()
    active = {"queued", "running", "stopping"}
    final = {"settled", "paused", "failed"}
    if snapshot is not None and snapshot.run_id == run_id and snapshot.state in active:
        try:
            snapshot, changed = bridge.lifecycle.finish(
                finish_reason, ok=ok, run_id=run_id,
            )
        except Exception as exc:
            _log_notification_failure("execution lifecycle finalization failed", exc)
            snapshot = bridge.lifecycle.snapshot()
        if snapshot is not None and snapshot.run_id == run_id and snapshot.state in final:
            _emit_safely(bridge.emit, snapshot.to_event(), "execution state notification retry failed")
    elif (not changed and snapshot is not None and snapshot.run_id == run_id
          and snapshot.state in final):
        # finish_execution may have finalized state before its emit raised.
        _emit_safely(bridge.emit, snapshot.to_event(), "execution state notification retry failed")

    snapshot = bridge.lifecycle.snapshot()
    if snapshot is not None and snapshot.run_id == run_id and snapshot.state in final:
        _emit_safely(
            bridge.emit_turn_event,
            {"type": "turn.end", "ok": snapshot.ok is True, "finish_reason": snapshot.finish_reason or finish_reason},
            "turn-end notification failed",
        )
    return snapshot


def _guard_auto_record_atomic_store(store: Any, recognition_queue: Any, connection: object,
                                    session: Session, ticket: Any, *,
                                    expected_write_json: str | None = None,
                                    expected_session_id: str | None = None,
                                    expected_write_method: str = 'create_item_with_requirement') -> Any:
    """Guard only the local create CAS; caller retains the host binding lock.

    This proxy is deliberately not an authorization or a provider-send gate.
    Confirmations and network I/O must complete before calling the guarded method.
    """
    from work_record_recognition_queue import QueueStatus, RecognitionQueue
    from work_records import WorkRecordConflict
    if type(recognition_queue) is not RecognitionQueue or getattr(store, "_session", None) is not session:
        raise ValueError("untrusted recognition commit binding")

    class GuardedStore:
        def __getattr__(self, name: str) -> Any:
            return getattr(store, name)

        def create_item_with_requirement(self, *args: Any, **kwargs: Any) -> Any:
            return self._guarded_write('create_item_with_requirement', args, kwargs)

        def add_requirement(self, *args: Any, **kwargs: Any) -> Any:
            return self._guarded_write('add_requirement', args, kwargs)

        def _guarded_write(self, method: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
            if method != expected_write_method:
                raise WorkRecordConflict("candidate write method differs from confirmation")
            if expected_session_id is not None and session.conversation_id != expected_session_id:
                raise WorkRecordConflict("candidate session differs from confirmation")
            if expected_write_json is not None:
                try:
                    actual = json.dumps(kwargs, ensure_ascii=False, sort_keys=True,
                                        separators=(',', ':'), allow_nan=False)
                except (ValueError, TypeError, UnicodeError):
                    raise WorkRecordConflict("candidate write cannot be verified") from None
                if args or actual != expected_write_json:
                    raise WorkRecordConflict("candidate write differs from confirmed fields")
            committed = recognition_queue.commit_if_current(
                ticket, connection, session,
                lambda: getattr(store, method)(*args, **kwargs),
            )
            if committed.status is QueueStatus.COMMITTED:
                return committed.value
            if committed.status is QueueStatus.COMMIT_FAILED:
                raise RuntimeError("local work-record commit outcome uncertain")
            raise WorkRecordConflict("recognition candidate was revoked or consumed")

    return GuardedStore()


@dataclass(frozen=True)
class _AutoRecognitionResultIdentity:
    session: Session
    request_id: str


def _emit_auto_recognition_result(bridge: WsBridge, candidate: Any, status: str) -> None:
    with bridge._result_view_write_lock:
        if (bridge._connection_closed.is_set() or bridge._session_binding_transitions
                or not callable(bridge._session_provider) or bridge._session_provider() is not candidate.session):
            return
        try:
            bridge.emit({'type': 'work-record.auto-recognition.result',
                         'session_id': candidate.session.conversation_id,
                         'request_id': candidate.request_id, 'status': status})
            # Existing desktop understands this session-scoped result protocol.
            # Ordinary no-candidate chat is deliberately quiet in the UI.
            if status in {'not_recorded', 'skipped_explicit'}:
                return
            messages = {
                'recorded': '工作记录已保存并关联原回合；事项仍为未知、未经验证。',
                'already_recorded': '本条消息此前已记录，本次未重复写入。',
                'append_not_allowed': '候选需要追加已有事项；当前仅允许新建，未写入。',
                'queue_full': '识别队列已满，本条消息未识别；普通工作不会重做。',
                'capacity_exhausted': '本连接的识别请求保护已达上限，本条消息未识别；普通对话仍会继续。',
                'no_terminal': '本条消息缺少可核验的回合终态或请求编号，未识别、未写入。',
                'cancelled': '本条识别已取消或会话已变；已启动的外发无法撤回，不会重做原工作。',
                'storage_not_authorized': '本条候选未获保存授权，未写入。',
                'stale_context': '本条候选的会话或记录快照已变化，未写入。',
                'uncertain': '识别或保存结果无法确认，请核对记录，不要重做原工作。',
                'storage_uncertain': '保存结果无法确认，请核对记录，不要重做原工作。',
            }
            outcome = (status if status in {'recorded', 'already_recorded', 'append_not_allowed'} else
                       'uncertain' if status in {'uncertain', 'storage_uncertain'} else 'not_recorded')
            text = messages.get(status, '本条识别未完成或未保存；不会重做原工作。')
            if candidate.request_id:
                bridge.emit({'type': 'work-record.post-turn.result',
                             'session_id': candidate.session.conversation_id,
                             'request_id': candidate.request_id, 'outcome': outcome,
                             'message': text + ' 请求：' + candidate.request_id})
            elif (callable(bridge._session_provider) and bridge._session_provider() is candidate.session):
                bridge.emit({'type': 'notice', 'text': text})
        except Exception:
            pass


def _auto_recognition_binding(bridge: WsBridge, candidate: Any) -> bool:
    """Caller holds the same short lock as session change and revoke."""
    try:
        return (not bridge._connection_closed.is_set() and not bridge._session_binding_transitions
                and bridge._auto_recognition_session is candidate.session
                and callable(bridge._session_provider) and bridge._session_provider() is candidate.session
                and bridge._auto_recognition_preference_enabled_locked()
                and candidate.cancel_generation == bridge._post_turn_generation)
    except Exception:
        bridge._auto_recognition_preference_failed = True
        bridge._auto_recognition_opt_out = True
        try:
            bridge._revoke_auto_recognition_locked()
        except Exception:
            bridge._auto_recognition_session = None
        return False


def _auto_recognition_target(paths: AgentPaths) -> tuple[Any, str]:
    """Resolve only the designated HTTPS provider, not an arbitrary model alias."""
    from llm_models import ModelEntry, ModelRegistry
    from llm_client import _confirmed_endpoint
    entry = ModelRegistry.load(paths).resolve('0x567-flash')
    if (type(entry) is not ModelEntry or entry.id != '0x567-flash'
            or entry.vendor != '0x567' or entry.provider_model != 'gpt-6-luna'):
        raise ValueError('recognition model is not the authorized provider')
    endpoint, url = _confirmed_endpoint(entry)
    if (url.port not in (None, 443) or
            not (url.host == '0x567.com' or url.host.endswith('.0x567.com'))):
        raise ValueError('recognition endpoint is not the authorized provider')
    return entry, endpoint


class _AutoRecognitionClient:
    """One outbound request; current user text and sanitized same-session view only."""
    def __init__(self, client: Any, bridge: WsBridge, candidate: Any):
        self._client, self._bridge, self._candidate = client, bridge, candidate
        self.paths = getattr(client, 'paths', None)
        self._started = False
        self.failed = False

    def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> Any:
        from work_records_luna_intent import MODEL_ID, _SYSTEM
        if (kwargs != {'model': MODEL_ID, 'tools': [], 'temperature': 0.0,
                       'response_format': {'type': 'json_object'}}
                or type(messages) is not list or len(messages) != 2
                or messages[0] != {'role': 'system', 'content': _SYSTEM}
                or type(messages[1]) is not dict or set(messages[1]) != {'role', 'content'}
                or messages[1]['role'] != 'user'):
            raise ValueError('unexpected recognition provider payload')
        payload = json.loads(messages[1]['content'])
        if (type(payload) is not dict or set(payload) != {'text', 'recognition_view', 'current_project_available'}
                or payload['text'] != self._candidate.text or type(payload['current_project_available']) is not bool
                or type(payload['recognition_view']) is not list):
            raise ValueError('unexpected recognition content')
        for item in payload['recognition_view']:
            if (type(item) is not dict or set(item) != {'item_id', 'title', 'scope'}
                    or type(item['item_id']) is not str or type(item['title']) is not str
                    or item['scope'] not in ({'kind': 'independent'}, {'kind': 'project'})):
                raise ValueError('unexpected recognition view')
        # The service's project-availability flag is deliberately omitted: the
        # user's authorization covers only current text and the item view.
        safe_messages = [messages[0], {'role': 'user', 'content': json.dumps(
            {'text': payload['text'], 'recognition_view': payload['recognition_view']},
            ensure_ascii=False, separators=(',', ':'))}]
        entry, endpoint = _auto_recognition_target(self._bridge.paths)
        frozen_payload = {'model': entry.provider_model, 'messages': safe_messages, 'temperature': 0.0,
                          'stream': False, 'response_format': {'type': 'json_object'}}
        preview = json.dumps({'model_id': entry.id, 'vendor': entry.vendor, 'endpoint': endpoint,
                              'session_id': self._candidate.session.conversation_id,
                              'provider_payload': frozen_payload}, ensure_ascii=False, sort_keys=True,
                             separators=(',', ':'), allow_nan=False)
        def permit(shown: str, allow_approve_all: bool) -> str:
            with self._bridge._result_view_write_lock:
                if (shown != preview or allow_approve_all is not False or self._started
                        or not _auto_recognition_binding(self._bridge, self._candidate)):
                    return 'n'
                self._started = True  # authorization linearizes; network I/O holds no lock
                return 'y'
        def before_send() -> bool:
            # Re-read durable preference after request/client setup, at the last safe gate.
            with self._bridge._result_view_write_lock:
                return _auto_recognition_binding(self._bridge, self._candidate)
        try:
            response = self._client.chat_confirmed_json(safe_messages, model_id=MODEL_ID,
                session_id=self._candidate.session.conversation_id,
                expected_preview_json=preview, confirm_fn=permit, before_send_fn=before_send)
            content = response if isinstance(response, str) else getattr(response, 'content', None)
            if (not isinstance(content, str) or (not isinstance(response, str) and getattr(response, 'tool_calls', None))):
                self.failed = True
            else:
                from work_records_luna_intent import _unique_object
                parsed = json.loads(content, object_pairs_hook=_unique_object)
                if type(parsed) is not dict or set(parsed) not in (
                    {'action', 'kind', 'title', 'requirement', 'scope'},
                    {'action', 'kind', 'title', 'requirement', 'scope', 'item_id'},
                ):
                    self.failed = True
            return response
        except Exception:
            self.failed = True
            raise


def _auto_recognition_prepare(candidate: Any, bridge: WsBridge, state: dict[str, Any]) -> tuple[Any, Any]:
    from llm_client import LLMClient
    from work_records_service import WorkRecordCaptureService
    from work_record_recognition_queue import QueueStatus
    raw_client = LLMClient(paths=bridge.paths)
    # Each claim owns this event; a later ordinary turn never clears it.
    raw_client.set_cancel_event(state['cancel_event'])
    client = _AutoRecognitionClient(raw_client, bridge, candidate)
    def valid() -> bool:
        with bridge._result_view_write_lock:
            if not _auto_recognition_binding(bridge, candidate):
                return False
            ticket = state.get('ticket')
            return ticket is None or bridge._auto_recognition_queue.ticket_status(ticket, bridge, candidate.session) is QueueStatus.CANDIDATE_READY
    def confirm(preview: str) -> tuple[str, str]:
        if preview.startswith('工作记录单次外发确认\n'):
            return ('y', str(uuid.uuid4())) if valid() else ('n', '')
        if preview.startswith('工作记录本地落盘确认\n'):
            fields = state.get('candidate_fields')
            if type(fields) is not dict or type(state.get('expected_write_json')) is not str:
                return 'n', ''
            exact = json.dumps(fields, ensure_ascii=False, sort_keys=True, indent=2)
            return bridge.confirm_auto_record_candidate(
                preview + '\n本次实际拟存字段和关联的宿主事实：\n' + exact
                + '\n原始宿主 run_id：' + candidate.run_id
                + '\n结果仍为未知、未经验证（not_verified）；工具回执和助理摘录不保存（not_stored）。',
                session=candidate.session, binding_is_valid=valid, work_record_candidate=fields)
        return 'n', ''
    service = WorkRecordCaptureService(session=candidate.session, client=client, confirm=confirm,
                 host_binding_is_valid=valid, commit_critical_section=lambda: bridge._result_view_write_lock,
                 write_policy='create_or_append')
    result = service.recognize_candidate(candidate.text, request_id=candidate.request_id,
                                         track_run=True, defer_commit_until_terminal=True)
    return service, result, client.failed or (result.status == 'not_recorded' and not client._started)


def _auto_recognition_commit(candidate: Any, bridge: WsBridge, state: dict[str, Any], service: Any, proposal: Any) -> str:
    from work_records_service import WorkRecordTerminalRunReceipt
    ticket = state['ticket']
    try:
        from evolution.context import normalize_host_run_id
        # Use the service-owned immutable recognition snapshot, not mutable model or UI fields.
        with service._candidate_lock:
            values = service._recognition_candidates.get(proposal)
        if not isinstance(values, tuple) or len(values) != 10:
            return 'invalid_candidate'
        (session, request_id, key, snapshot_json, title, requirement, scope_json,
         target, track_run, defer_commit) = values
        scope = json.loads(scope_json)
        snapshot = json.loads(snapshot_json)
        run_id = normalize_host_run_id(candidate.run_id)
        if (session is not candidate.session or request_id != candidate.request_id
                or (target is not None and (type(target) is not str or not target))
                or track_run is not True or defer_commit is not True
                or type(title) is not str or not title or title != title.strip()
                or type(requirement) is not str or not requirement or requirement != requirement.strip()
                or type(snapshot) is not dict or not run_id):
            return 'invalid_candidate'
        if scope == {'kind': 'independent'}:
            stored_scope = {'kind': 'independent', 'object_ref': None}
        elif (type(scope) is dict and set(scope) == {'kind', 'project_id'}
              and scope['kind'] == 'project' and type(scope['project_id']) is str
              and scope['project_id'] and scope['project_id'] == scope['project_id'].strip()):
            stored_scope = dict(scope)
        else:
            return 'invalid_candidate'
        target_rows = [row for row in snapshot.get('items', [])
                       if type(row) is dict and row.get('item_id') == target] if target is not None else []
        if target is not None:
            if (len(target_rows) != 1 or target_rows[0].get('scope') != stored_scope
                    or type(target_rows[0].get('title')) is not str or not target_rows[0]['title']):
                return 'invalid_candidate'
            display_title = target_rows[0]['title']
        else:
            display_title = title
        state['candidate_fields'] = {
            'title': display_title, 'requirement': requirement, 'scope': stored_scope,
            'session_id': session.conversation_id, 'request_id': request_id, 'run_id': run_id,
            'sequence': candidate.sequence, 'terminal_state': 'settled', 'finish_reason': candidate.finish_reason,
        }
        terminal = {'run_id': run_id, 'sequence': candidate.sequence,
                    'terminal_state': 'settled', 'finish_reason': candidate.finish_reason}
        if target is None:
            expected_method = 'create_item_with_requirement'
            expected_write = {
                'title': title, 'scope': scope, 'requirement': requirement, 'event_id': request_id,
                'idempotency_key': key, 'expected_snapshot': snapshot, 'return_commit_status': True,
                'terminal_run': terminal, 'result_evidence': None,
            }
        else:
            state['candidate_fields'].update(operation='append_requirement', item_id=target)
            expected_method = 'add_requirement'
            expected_write = {
                'item_id': target, 'text': requirement, 'event_id': request_id,
                'idempotency_key': key, 'expected_snapshot': snapshot, 'return_commit_status': True,
                'terminal_run': terminal, 'expected_scope': stored_scope,
            }
        state['expected_write_json'] = json.dumps(expected_write, ensure_ascii=False, sort_keys=True,
                                                  separators=(',', ':'), allow_nan=False)
        prepared = service.confirm_candidate(proposal)
        if prepared.status != 'awaiting_host_run' or prepared.prepared_capture is None:
            return prepared.status
        service._store = _guard_auto_record_atomic_store(service._store, bridge._auto_recognition_queue,
                     bridge, candidate.session, ticket, expected_write_json=state['expected_write_json'],
                     expected_session_id=state['candidate_fields']['session_id'], expected_write_method=expected_method)
        receipt = WorkRecordTerminalRunReceipt(candidate.session.conversation_id, candidate.request_id,
                    candidate.run_id, 'settled', candidate.sequence, candidate.finish_reason)
        return service.commit_prepared_capture(prepared.prepared_capture, receipt=receipt).status
    except Exception:
        return 'uncertain'
    finally:
        bridge._auto_recognition_queue.discard_candidate(ticket, bridge, candidate.session)


async def _auto_recognition_drain(bridge: WsBridge) -> None:
    try:
        await _auto_recognition_drain_impl(bridge)
    except Exception:
        # Never leave a failed pump silently blocking this connection's queue.
        with bridge._result_view_write_lock:
            active = bridge._auto_recognition_active
            if active is not None and not active['reported']:
                active['reported'] = True
                _emit_auto_recognition_result(bridge, active['candidate'], 'uncertain')
            for candidate in bridge._auto_recognition_pending.values():
                _emit_auto_recognition_result(bridge, candidate, 'uncertain')
            bridge._auto_recognition_pending.clear()
            try:
                bridge._revoke_auto_recognition_locked()
            finally:
                bridge._auto_recognition_active = None
                bridge._auto_recognition_worker = None


async def _auto_recognition_drain_impl(bridge: WsBridge) -> None:
    from work_record_recognition_queue import QueueStatus
    while True:
        with bridge._result_view_write_lock:
            queue_ = bridge._auto_recognition_queue
            lease = queue_.claim_next() if queue_ is not None else None
            if lease is None:
                bridge._auto_recognition_worker = None
                return
            candidate = bridge._auto_recognition_pending.pop(lease.request_token, None)
            if candidate is None:
                queue_.finish_recognition(lease, bridge, bridge._auto_recognition_session, candidate=False)
                continue
            active = {'candidate': candidate, 'reported': False, 'cancel_event': threading.Event()}
            bridge._auto_recognition_active = active
        state: dict[str, Any] = {'ticket': None, 'cancel_event': active['cancel_event']}
        try:
            service, result, provider_failed = await asyncio.to_thread(_auto_recognition_prepare, candidate, bridge, state)
            has_candidate = result.status == 'candidate' and result.candidate is not None and not provider_failed
        except Exception:
            service, result, has_candidate, provider_failed = None, None, False, True
        with bridge._result_view_write_lock:
            still_bound = _auto_recognition_binding(bridge, candidate)
            completion = queue_.finish_recognition(lease, bridge, candidate.session,
                                                  candidate=has_candidate and still_bound)
            if bridge._auto_recognition_active is active:
                bridge._auto_recognition_active = None
            if completion.status is QueueStatus.CANDIDATE_READY:
                state['ticket'] = completion.ticket
        if completion.status is QueueStatus.CANDIDATE_READY:
            async def confirm_and_commit(candidate=candidate, service=service, result=result, state=state):
                try:
                    status = await asyncio.to_thread(_auto_recognition_commit, candidate, bridge, state, service, result.candidate)
                except Exception:
                    status = 'uncertain'
                _emit_auto_recognition_result(bridge, candidate, status)
            task = asyncio.create_task(confirm_and_commit())
            bridge._post_turn_tasks.add(task)
            task.add_done_callback(bridge._post_turn_tasks.discard)
        elif not active['reported']:
            status = ('cancelled' if not still_bound or completion.status is QueueStatus.REVOKED else
                      'uncertain' if provider_failed else getattr(result, 'status', 'uncertain'))
            _emit_auto_recognition_result(bridge, candidate, status)


def _enqueue_auto_recognition(bridge: WsBridge, candidate: Any) -> None:
    from work_record_recognition_queue import QueueStatus
    with bridge._result_view_write_lock:
        if not _auto_recognition_binding(bridge, candidate):
            _emit_auto_recognition_result(bridge, candidate, 'cancelled')
            return
        admission = bridge._auto_recognition_queue.enqueue(bridge, candidate.session)
        if admission.status is not QueueStatus.ENQUEUED:
            _emit_auto_recognition_result(bridge, candidate, 'queue_full' if admission.status is QueueStatus.FULL else 'cancelled')
            return
        bridge._auto_recognition_pending[admission.request_token] = candidate
        if bridge._auto_recognition_worker is None:
            bridge._auto_recognition_worker = asyncio.create_task(_auto_recognition_drain(bridge))
            bridge._post_turn_tasks.add(bridge._auto_recognition_worker)
            bridge._auto_recognition_worker.add_done_callback(bridge._post_turn_tasks.discard)


class _PostTurnApprovedClient:
    """Exact-payload one-shot adapter; the network call is always outside locks."""

    def __init__(self, client: Any, bridge: WsBridge, session: Any, ticket: object,
                 approved_messages: list[dict[str, Any]] | None = None) -> None:
        self._client, self._bridge, self._session, self._ticket = client, bridge, session, ticket
        self.paths = getattr(client, 'paths', None)
        self._approved = (json.dumps(approved_messages, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':'), allow_nan=False)
                          if approved_messages is not None else None)

    def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> Any:
        from work_records_luna_intent import MODEL_ID
        payload = json.dumps(messages, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
        if ((self._approved is not None and payload != self._approved)
                or kwargs != {'model': MODEL_ID, 'tools': [], 'temperature': 0.0,
                              'response_format': {'type': 'json_object'}}):
            raise ValueError('outbound payload does not match approved post-turn preview')
        digest = hashlib.sha256(payload.encode('utf-8')).hexdigest()
        if not self._bridge.try_start_post_turn_payload(self._ticket, self._session, digest):
            raise RuntimeError('post-turn outbound permission is unavailable')
        return self._client.chat(messages, **kwargs)


@dataclass(frozen=True)
class _FrozenPostTurnCandidate:
    """Private host-frozen evidence; not an authorization or a completion claim."""
    session: Session = field(repr=False)
    text: str = field(repr=False)
    request_id: str
    run_id: str
    sequence: int
    finish_reason: str
    receipts: tuple[tuple[str, str, str, bool], ...]
    tool_receipt_status: str = 'complete'
    cancel_generation: int | None = None
    result_excerpt: str | None = field(default=None, repr=False)


def _post_turn_result_excerpt(text: str | None) -> str:
    """Format bounded in-memory output without claiming persisted provenance."""
    from work_records import MAX_WORK_RECORD_RESULT_EXCERPT_BYTES
    header = '该次助理回复摘录（未核验；原始消息来源不可定位；不代表要求完成）。\n'
    if not isinstance(text, str) or not text.strip():
        return header + '摘录缺失。'
    try:
        # One character uses at least one UTF-8 byte; never encode an unbounded reply.
        raw = text[:MAX_WORK_RECORD_RESULT_EXCERPT_BYTES + 1].encode('utf-8')
        complete = '\n摘录状态：完整。\n'
        truncated = '\n摘录状态：已截断。\n'
        available = MAX_WORK_RECORD_RESULT_EXCERPT_BYTES - len((header + truncated).encode('utf-8'))
        if (len(text) <= MAX_WORK_RECORD_RESULT_EXCERPT_BYTES
                and len(raw) <= MAX_WORK_RECORD_RESULT_EXCERPT_BYTES - len((header + complete).encode('utf-8'))):
            return header + complete + text
        return header + truncated + raw[:available].decode('utf-8', errors='ignore')
    except (UnicodeError, ValueError):
        return header + '摘录缺失（编码不可用）。'


@dataclass(frozen=True)
class _FrozenExplicitRunExcerpt:
    """Host-bound excerpt for an already-settled explicit capture; not consent."""
    session: Session = field(repr=False)
    store: Any = field(repr=False)
    request_id: str
    item_id: str
    requirement_id: str
    run_id: str
    result_id: str
    sequence: int
    cancel_generation: int
    settlement_finish_reason: str | None
    excerpt: str = field(repr=False)


def _execute_explicit_run_excerpt_candidate(candidate: _FrozenExplicitRunExcerpt, bridge: WsBridge) -> Any:
    """Confirm outside locks, then linearize the exact approved CAS with invalidation."""
    from work_record_settlement import BoundExecution, ExecutionReceipt, WorkRecordSettlementAdapter, AssistantExcerptAppendResult
    from work_records import WorkRecordStaleRun
    if type(candidate) is not _FrozenExplicitRunExcerpt:
        return AssistantExcerptAppendResult('rejected', reason='invalid_host_candidate')
    status, ticket = bridge.create_post_turn_ticket(candidate.session, candidate.request_id, candidate.run_id,
                                                   expected_generation=candidate.cancel_generation, notify_failure=False)
    if status != 'created' or ticket is None:
        return AssistantExcerptAppendResult('rejected', reason=status)
    authorized = False
    consumed = False
    class GuardedStore:
        def __getattr__(self, name: str) -> Any:
            return getattr(candidate.store, name)
        def append_assistant_reply_excerpt_if_current_run(self, item_id: str, requirement_id: str, **kwargs: Any) -> Any:
            nonlocal consumed
            with bridge._result_view_write_lock:
                if (not authorized or consumed or not bridge.post_turn_binding_is_valid(ticket, candidate.session)
                        or item_id != candidate.item_id or requirement_id != candidate.requirement_id
                        or kwargs != {'run_id': candidate.run_id, 'sequence': candidate.sequence,
                                      'finish_reason': candidate.settlement_finish_reason,
                                      'text': candidate.excerpt, 'source_availability': 'unavailable', 'sources': []}):
                    raise WorkRecordStaleRun('excerpt authorization or connection binding is no longer valid')
                consumed = True
                return candidate.store.append_assistant_reply_excerpt_if_current_run(item_id, requirement_id, **kwargs)
    def confirm(preview: str) -> tuple[str, str]:
        nonlocal authorized
        try:
            if not preview.startswith('工作记录助理回复摘录本地保存确认\n'):
                return 'n', ''
            value = json.loads(preview.split('\n', 2)[2])
            expected = {'purpose': 'assistant_reply_excerpt', 'session_id': candidate.session.conversation_id,
                        'item_id': candidate.item_id, 'requirement_id': candidate.requirement_id,
                        'run_id': candidate.run_id, 'assistant_reply_excerpt': candidate.excerpt,
                        'source_availability': 'unavailable', 'sources': [],
                        'result_status': 'unknown', 'verification_status': 'not_verified'}
            if value != expected:
                return 'n', ''
            choice, confirmation_id = bridge.confirm_post_turn(
                preview, session=candidate.session, ticket=ticket, confirm_subtype='work_record_reply_excerpt')
            if choice == 'y':
                authorized = True
            return choice, confirmation_id
        except (ValueError, TypeError, UnicodeError, IndexError):
            return 'n', ''
    try:
        binding = BoundExecution(candidate.session.conversation_id, candidate.item_id, candidate.requirement_id,
                                 candidate.run_id, capture_request_id=candidate.request_id)
        receipt = ExecutionReceipt(candidate.session.conversation_id, candidate.item_id, candidate.requirement_id,
                                   candidate.run_id, candidate.sequence, 'failed',
                                   finish_reason=candidate.settlement_finish_reason)
        return WorkRecordSettlementAdapter(GuardedStore(), binding).append_assistant_reply_excerpt(
            receipt, assistant_excerpt=candidate.excerpt, source_availability='unavailable', sources=(), confirm_fn=confirm)
    except Exception:
        return AssistantExcerptAppendResult('rejected', reason='excerpt_outcome_uncertain')
    finally:
        bridge.finish_post_turn(ticket, candidate.session)


@dataclass(frozen=True)
class _PostTurnRunIdentity:
    run_id: str


def _execute_post_turn_candidate(candidate: _FrozenPostTurnCandidate, bridge: WsBridge, client: Any) -> Any:
    """Run the opt-in post-turn capture in a worker; never runs the original work.

    The caller must supply only a candidate returned by _run_line while TURN_LOCK
    was held. This function is not scheduled by the ordinary message path yet.
    """
    from work_records_service import (
        WorkRecordCaptureResult, WorkRecordCaptureService, WorkRecordTerminalRunReceipt,
        WorkRecordResultEvidence,
    )
    if type(candidate) is not _FrozenPostTurnCandidate or not callable(getattr(client, 'chat', None)):
        return WorkRecordCaptureResult('invalid_request', '无法核验回合证据；未外发、未写入。')
    if type(candidate.cancel_generation) is not int:
        return WorkRecordCaptureResult('stale_context', '事后候选缺少可信取消世代；未外发、未写入。')
    status, ticket = bridge.create_post_turn_ticket(candidate.session, candidate.request_id, candidate.run_id,
                                                   expected_generation=candidate.cancel_generation)
    if status != 'created' or ticket is None:
        return WorkRecordCaptureResult(status, '事后记录候选未启动；未重新执行原工作。')
    try:
        guarded_client = _PostTurnApprovedClient(client, bridge, candidate.session, ticket)
        def confirm(preview: str) -> tuple[str, str]:
            if preview.startswith('工作记录单次外发确认\n'):
                outbound = True
            elif (preview.startswith('工作记录本地落盘确认\n')
                  or preview.startswith('工作记录回合摘录与来源状态本地保存确认\n')):
                outbound = False
            else:
                return 'n', ''
            return bridge.confirm_post_turn(preview, session=candidate.session, ticket=ticket,
                                            approve_outbound=outbound)
        service = WorkRecordCaptureService(
            session=candidate.session, client=guarded_client, confirm=confirm,
            host_binding_is_valid=lambda: bridge.post_turn_binding_is_valid(ticket, candidate.session),
            commit_critical_section=lambda: bridge._result_view_write_lock, write_policy='create_only',
        )
        result = service.capture(candidate.text, request_id=candidate.request_id,
                                 track_run=True, defer_commit_until_terminal=True)
        if result.status != 'awaiting_host_run' or result.prepared_capture is None:
            return result
        receipt = WorkRecordTerminalRunReceipt(
            session_id=candidate.session.conversation_id, request_id=candidate.request_id,
            run_id=candidate.run_id, terminal_state='settled', sequence=candidate.sequence,
            finish_reason=candidate.finish_reason,
        )
        evidence = (WorkRecordResultEvidence(summary_excerpt=candidate.result_excerpt,
                                             source_availability='unavailable', sources=())
                    if candidate.result_excerpt is not None else None)
        return service.commit_prepared_capture(result.prepared_capture, receipt=receipt,
                                               result_evidence=evidence)
    except Exception:
        return WorkRecordCaptureResult('uncertain', '事后记录处理未能确认；请核对记录，不要重做原工作。')
    finally:
        bridge.finish_post_turn(ticket, candidate.session)


async def _run_line(
    repl: ConversationRepl,
    bridge: WsBridge,
    line: str,
    paths: AgentPaths,
    *,
    lock_held: bool = False,
    prestarted_run_id: str | None = None,
    work_record_binding: tuple[Any, Any] | None = None,
    post_turn_request_id: str | None = None,
    post_turn_source_text: str | None = None,
    explicit_result_capture: bool = False,
    external_user_message_request_id: str | None = None,
    external_user_message_attachment_session_id: str | None = None,
) -> _FrozenPostTurnCandidate | _FrozenExplicitRunExcerpt | None:
    repl.last_turn_finish_reason = None
    post_turn_candidate = None
    try:
        if type(post_turn_request_id) is not str or str(uuid.UUID(post_turn_request_id)) != post_turn_request_id:
            post_turn_request_id = None
    except (ValueError, AttributeError):
        post_turn_request_id = None
    try:
        if (type(external_user_message_request_id) is not str
                or str(uuid.UUID(external_user_message_request_id)) != external_user_message_request_id):
            external_user_message_request_id = None
    except (ValueError, AttributeError):
        external_user_message_request_id = None
    if (not lock_held or work_record_binding is not None
            or (post_turn_source_text is not None
                and (type(post_turn_source_text) is not str or post_turn_source_text.strip() != line))):
        post_turn_request_id = None
    worker_work_record_result = None
    from session import is_terminal_harness

    execution_mode = "terminal" if is_terminal_harness(repl.session.meta) else "ordinary"
    # Sample before execution. Cancellation/rebinding thereafter must invalidate
    # even the gap between terminal evidence freeze and ticket creation.
    with bridge._result_view_write_lock:
        post_turn_generation = bridge._post_turn_generation
    if prestarted_run_id is None:
        if not bridge.begin_turn(mode=execution_mode):
            return
        run_snapshot = bridge.lifecycle.snapshot()
    else:
        run_snapshot = bridge.lifecycle.snapshot()
        if (
            run_snapshot is None
            or run_snapshot.run_id != prestarted_run_id
            or run_snapshot.state not in {"queued", "running", "stopping"}
            or not bridge._turn_busy.is_set()
            or bridge.lifecycle_conversation_id != repl.session.conversation_id
        ):
            bridge.emit({"type": "error", "message": "预启动回合已失效；未执行本条消息。"})
            return
        if run_snapshot.state == "queued":
            try:
                run_snapshot = bridge.lifecycle.start_or_activate(execution_mode)
            except RuntimeError:
                bridge.emit({"type": "error", "message": "预启动回合无法激活；未执行本条消息。"})
                return
            if (run_snapshot.run_id != prestarted_run_id or run_snapshot.state != "running"):
                bridge.emit({"type": "error", "message": "预启动回合激活身份不匹配；未执行本条消息。"})
                return
            bridge.emit(run_snapshot.to_event())
        elif run_snapshot.state == "stopping" or run_snapshot.cancel_requested:
            # Preserve the matching prestarted run's common cancellation/finalization path.
            # Do not return here: the finally block must clear busy state and settle this run.
            bridge.cancel_event.set()
    if run_snapshot is not None:
        bridge.lifecycle_conversation_id = repl.session.conversation_id

    # Keep a local message-window baseline so executor receipts can be checked
    # against actual Agent tool-result messages for this exact Session/run.
    receipt_agent = None
    receipt_session = None
    tool_message_start_index = None
    if work_record_binding is not None or (post_turn_request_id is not None and execution_mode == "ordinary"):
        receipt_agent = getattr(repl, "agent", None)
        receipt_session = repl.session
        try:
            receipt_session.ensure_messages_loaded()
            tool_message_start_index = len(receipt_session.messages)
        except Exception:
            # Settlement will still record lifecycle facts but label tool receipts incomplete.
            tool_message_start_index = None

    bound_agent = repl.agent
    bound_session = repl.session
    stream_capture_enabled = lock_held and execution_mode == "ordinary"
    streamed_content: list[str] = []
    streamed_content_lock = threading.Lock()
    original_stream_handlers = getattr(bound_agent, "stream_handlers", None)
    original_repl_stream_handlers = getattr(repl, "stream_handlers", None)
    original_llm = getattr(bound_agent, "llm", None)
    original_chat = getattr(original_llm, "chat", None)
    llm_instance_dict = getattr(original_llm, "__dict__", {})
    had_chat_instance_value = "chat" in llm_instance_dict
    previous_chat_instance_value = llm_instance_dict.get("chat")
    chat_wrapper_installed = False
    stream_request_index = 0
    stream_request_start_index = 0
    stream_request_persisted = False
    previous_binding_transition = getattr(bound_agent, "_session_binding_transition", None)
    previous_work_instruction_checkpoint = getattr(bound_agent, "_work_instruction_checkpoint", None)
    if hasattr(bound_agent, "_session_binding_transition"):
        bound_agent._session_binding_transition = bridge.agent_session_binding_transition
    bind_host_run_context = getattr(bound_agent, "bind_host_run_context", None)
    if run_snapshot is not None and run_snapshot.state == "running" and callable(bind_host_run_context):
        # The lifecycle is authoritative; never manufacture or persist a host run id.
        executor_session = getattr(getattr(bound_agent, "executor", None), "session", None)
        authorization_id = getattr(executor_session, "evolution_authorization_id", "")
        bind_host_run_context(run_snapshot.run_id, authorization_id)
    if run_snapshot is not None and run_snapshot.state == "running":
        captured_instruction_run_id = run_snapshot.run_id
        captured_instruction_session_id = bound_session.conversation_id

        def work_instruction_checkpoint(
            phase: str, candidate_session: Session, model_context: list[dict[str, Any]] | None,
        ) -> None:
            if (candidate_session is not bound_session or repl.session is not bound_session
                    or repl.agent is not bound_agent
                    or bound_session.conversation_id != captured_instruction_session_id):
                return
            if phase == "prepare":
                bridge.prepare_work_instructions_at_checkpoint(
                    candidate_session, captured_instruction_run_id,
                )
            elif phase == "deliver" and model_context is not None:
                return bridge.commit_work_instruction_delivery(
                    candidate_session, captured_instruction_run_id, model_context,
                )

        bound_agent._work_instruction_checkpoint = work_instruction_checkpoint
    bind_project_worker_context = getattr(bound_agent, "bind_project_worker_live_context", None)
    if run_snapshot is not None and run_snapshot.state == "running" and callable(bind_project_worker_context):
        captured_run_id = run_snapshot.run_id
        captured_epoch = run_snapshot.sequence
        captured_conversation_id = bound_session.conversation_id

        def live_project_worker_binding(candidate_session: Session, run_id: str, turn_epoch: int) -> bool:
            current = bridge.lifecycle.snapshot()
            return bool(
                candidate_session is bound_session
                and repl.session is bound_session
                and repl.agent is bound_agent
                and bound_session.conversation_id == captured_conversation_id
                and run_id == captured_run_id
                and turn_epoch == captured_epoch
                and bridge.lifecycle_conversation_id == captured_conversation_id
                and bridge._turn_busy.is_set()
                and current is not None
                and current.run_id == captured_run_id
                and current.sequence == captured_epoch
                and current.state == "running"
                and not current.cancel_requested
                and not bridge.cancel_event.is_set()
                and not bridge._connection_closed.is_set()
            )

        bind_project_worker_context(captured_run_id, captured_epoch, live_project_worker_binding)
    bind_project_worker_record_context = getattr(
        bound_agent, "bind_project_worker_work_record_context", None,
    )
    if (work_record_binding is not None and run_snapshot is not None
            and run_snapshot.state == "running" and callable(bind_project_worker_record_context)):
        if type(work_record_binding) is tuple and len(work_record_binding) == 2:
            record_store, record_execution = work_record_binding
        else:
            record_store, record_execution = None, None
        bind_project_worker_record_context(
            record_store, record_execution, run_snapshot.run_id, run_snapshot.sequence,
        )
    bridge._turn_busy.set()
    ok = True
    finish_reason = "completed"
    captured_turn_results: list[str | None] = []
    original_run_turn = None
    previous_instance_run_turn = None
    had_instance_run_turn = False
    original_agent_user_append = None
    previous_instance_agent_user_append = None
    had_instance_agent_user_append = False
    # The ordinary REPL discards TurnResult after displaying it. Intercept only
    # its exact trusted Agent call for this opt-in host turn, never model IDs.
    if ((post_turn_request_id is not None
         or (explicit_result_capture is True and work_record_binding is not None)
         or external_user_message_request_id is not None)
            and lock_held and execution_mode == "ordinary"):

        from agent import Agent, TurnResult
        if type(repl) is ConversationRepl and type(bound_agent) is Agent:
            had_instance_run_turn = 'run_turn' in bound_agent.__dict__
            previous_instance_run_turn = bound_agent.__dict__.get('run_turn')
            original_run_turn = bound_agent.run_turn
            if not had_instance_run_turn and getattr(original_run_turn, '__func__', None) is Agent.run_turn:
                receipt_sent = False
                receipt_lock = threading.Lock()

                def emit_user_message_persisted(persisted_session: Session) -> None:
                    nonlocal receipt_sent
                    if (persisted_session is not bound_session or repl.session is not bound_session
                            or repl.agent is not bound_agent
                            or getattr(bound_agent, "session", None) is not bound_session
                            or bound_session.conversation_id != repl.session.conversation_id
                            or (external_user_message_attachment_session_id is not None
                                and bound_session.conversation_id != external_user_message_attachment_session_id)
                            or bridge._connection_closed.is_set()):
                        return
                    with receipt_lock:
                        if receipt_sent:
                            return
                        receipt_sent = True
                    _emit_safely(bridge.emit, {
                        "type": "user.message.persisted",
                        "session_id": bound_session.conversation_id,
                        "request_id": external_user_message_request_id,
                    }, "user message persistence receipt failed")

                if (external_user_message_request_id is not None
                        and external_user_message_attachment_session_id is not None):
                    had_instance_agent_user_append = "_append_user_message_with_result_reply" in bound_agent.__dict__
                    previous_instance_agent_user_append = bound_agent.__dict__.get(
                        "_append_user_message_with_result_reply"
                    )
                    original_agent_user_append = getattr(
                        bound_agent, "_append_user_message_with_result_reply", None,
                    )
                    if callable(original_agent_user_append):
                        def _observe_agent_user_append(
                            session: Session, user_text: str, *append_args: Any, **append_kwargs: Any,
                        ) -> Any:
                            result = original_agent_user_append(
                                session, user_text, *append_args, **append_kwargs,
                            )
                            emit_user_message_persisted(session)
                            return result

                        bound_agent._append_user_message_with_result_reply = _observe_agent_user_append

                def _observe_run_turn(*args: Any, **kwargs: Any) -> Any:
                    if (external_user_message_request_id is not None
                            and external_user_message_attachment_session_id is None):
                        kwargs["on_external_user_message_persisted"] = emit_user_message_persisted
                    result = original_run_turn(*args, **kwargs)
                    snapshot = bridge.lifecycle.snapshot()
                    if (type(result) is TurnResult and snapshot is not None and run_snapshot is not None
                            and snapshot.run_id == run_snapshot.run_id and snapshot.state == 'running'
                            and repl.agent is bound_agent and repl.session is bound_session
                            and bound_agent.session is bound_session):
                        if not captured_turn_results:
                            captured_turn_results.append(_post_turn_result_excerpt(
                                result.assistant_text if type(result.assistant_text) is str else None))
                        elif len(captured_turn_results) == 1:
                            captured_turn_results.append(None)
                    elif len(captured_turn_results) < 2:
                        captured_turn_results.append(None)
                    return result
                bound_agent.run_turn = _observe_run_turn
            else:
                original_run_turn = None
    stream_journal = None
    pending_stream_commit = None
    stream_request_id = None
    stream_journal_lock = threading.RLock()
    original_append_message = bound_session.append_message
    original_session_save = bound_session.save
    had_instance_append_message = "append_message" in bound_session.__dict__
    previous_instance_append_message = bound_session.__dict__.get("append_message")
    had_instance_save = "save" in bound_session.__dict__
    previous_instance_save = bound_session.__dict__.get("save")
    stream_request_start_index = len(bound_session.messages)
    accepted_user_message_persisted = False
    stream_journal_started = False

    if stream_capture_enabled:
        from stream_journal import StreamJournal

        def persist_streamed_content(finish_reason: str) -> bool:
            nonlocal stream_journal, pending_stream_commit
            with stream_journal_lock:
                journal = stream_journal
            if journal is None:
                return True
            try:
                text = journal.content()
                if not text:
                    journal.settle()
                    with stream_journal_lock:
                        if stream_journal is journal:
                            stream_journal = None
                    return True
                existing = next((message for message in bound_session.messages
                                 if isinstance(message, dict)
                                 and isinstance(message.get(HOST_INTERNAL_METADATA_KEY), dict)
                                 and message[HOST_INTERNAL_METADATA_KEY].get("stream_request_id") == journal.request_id), None)
                if existing is not None:
                    if existing.get("content") != text:
                        raise RuntimeError("stream request identity conflicts with existing assistant row")
                else:
                    bound_session.append_message_durable({
                        "role": "assistant",
                        "content": text,
                        HOST_INTERNAL_METADATA_KEY: {
                            "stream_incomplete": True,
                            "stream_finish_reason": finish_reason,
                            "stream_request_id": journal.request_id,
                            "stream_run_id": run_snapshot.run_id if run_snapshot is not None else None,
                        },
                    })
                journal.settle()
                with stream_journal_lock:
                    if stream_journal is journal:
                        stream_journal = None
                    if pending_stream_commit is journal:
                        pending_stream_commit = None
                return True
            except Exception as exc:
                # The journal remains the recovery authority if folding is uncertain.
                log_sidecar_exception("interrupted assistant stream could not be persisted", exc)
                _emit_safely(bridge.emit, {
                    "type": "turn.notice", "level": "warn",
                    "text": "中断前已显示的助手片段未能确认写入原会话；journal 保留供恢复，当前回合停止继续写入。",
                }, "interrupted stream persistence notice failed")
                return False

        def append_message_with_stream_identity(message: dict[str, Any], *, persist: bool = True) -> None:
            nonlocal pending_stream_commit, stream_journal, accepted_user_message_persisted
            candidate = pending_stream_commit
            if (candidate is not None and persist is True and type(message) is dict and message.get("role") == "assistant"
                    and isinstance(message.get("content"), str)
                    and message.get("content") == candidate.content()):
                message = dict(message)
                metadata = message.get(HOST_INTERNAL_METADATA_KEY)
                metadata = dict(metadata) if isinstance(metadata, dict) else {}
                metadata["stream_request_id"] = candidate.request_id
                if candidate.run_id is not None:
                    metadata["stream_run_id"] = candidate.run_id
                message[HOST_INTERNAL_METADATA_KEY] = metadata
                bound_session.append_message_durable(message)
                # If settlement fails, the durable assistant row carries the request id;
                # recovery recognizes it and will not duplicate it.
                candidate.settle()
                with stream_journal_lock:
                    if pending_stream_commit is candidate:
                        pending_stream_commit = None
                    if stream_journal is candidate:
                        stream_journal = None
                return
            original_append_message(message, persist=persist)
            if (persist is True and type(message) is dict and message.get("role") == "user"
                    and message.get("content") == line):
                accepted_user_message_persisted = True

        bound_session.append_message = append_message_with_stream_identity

        def save_with_stream_identity(*args: Any, **kwargs: Any) -> Any:
            nonlocal pending_stream_commit, stream_journal
            candidate = pending_stream_commit
            message_index = None
            if candidate is not None:
                for index in range(len(bound_session.messages) - 1, stream_request_start_index - 1, -1):
                    message = bound_session.messages[index]
                    if (type(message) is dict and message.get("role") == "assistant"
                            and message.get("content") == candidate.content()):
                        message_index = index
                        break
            if message_index is None:
                return original_session_save(*args, **kwargs)
            message = dict(bound_session.messages[message_index])
            metadata = message.get(HOST_INTERNAL_METADATA_KEY)
            metadata = dict(metadata) if isinstance(metadata, dict) else {}
            metadata["stream_request_id"] = candidate.request_id
            if candidate.run_id is not None:
                metadata["stream_run_id"] = candidate.run_id
            message[HOST_INTERNAL_METADATA_KEY] = metadata
            bound_session.messages[message_index] = message
            result = original_session_save(*args, **kwargs)
            candidate.settle()
            with stream_journal_lock:
                if pending_stream_commit is candidate:
                    pending_stream_commit = None
                if stream_journal is candidate:
                    stream_journal = None
            return result

        bound_session.save = save_with_stream_identity

        def capture_content_delta(text: str) -> None:
            nonlocal stream_journal, stream_journal_started
            if isinstance(text, str) and text:
                with stream_journal_lock:
                    journal = stream_journal
                    request_id = stream_request_id
                    if journal is None:
                        if not request_id:
                            raise OSError("stream request identity is unavailable")
                        journal = StreamJournal(
                            bound_session.session_dir, bound_session.conversation_id, request_id,
                            run_id=run_snapshot.run_id if run_snapshot is not None else None,
                        )
                        stream_journal = journal
                        stream_journal_started = True
                # Must be durable before forwarding/displaying this delta.
                journal.append(text)
            callback = getattr(original_stream_handlers, "on_content_delta", None)
            if callable(callback):
                callback(text)
            else:
                bridge.emit_content_delta(text)

        def forward_reasoning_delta(text: str) -> None:
            callback = getattr(original_stream_handlers, "on_reasoning_delta", None)
            if callable(callback):
                callback(text)
            else:
                bridge.emit_reasoning_delta(text)

        bound_agent.stream_handlers = StreamHandlers(
            on_content_delta=capture_content_delta,
            on_reasoning_delta=forward_reasoning_delta,
        )
        repl.stream_handlers = bound_agent.stream_handlers
        if callable(original_chat):
            def tracked_chat(*args: Any, **kwargs: Any) -> Any:
                nonlocal stream_request_id, pending_stream_commit, stream_request_start_index
                # A successful provider request that did not reach its matching
                # assistant append is closed as an incomplete retry before reuse.
                if stream_journal is not None and not persist_streamed_content("request_retry"):
                    raise OSError("prior streamed request could not be durably settled")
                bound_session.ensure_messages_loaded()
                stream_request_start_index = len(bound_session.messages)
                stream_request_id = uuid.uuid4().hex
                try:
                    result = original_chat(*args, **kwargs)
                except Exception as exc:
                    if isinstance(exc, LLMCancelledError):
                        reason = "cancelled"
                    elif isinstance(exc, LLMTimeoutError):
                        reason = "timeout"
                    elif isinstance(exc, (LLMApiError, LLMNetworkError)):
                        reason = "provider_error"
                    else:
                        reason = "error"
                    persist_streamed_content(reason)
                    raise
                else:
                    with stream_journal_lock:
                        pending_stream_commit = stream_journal
                    return result
                finally:
                    stream_request_id = None

            try:
                original_llm.chat = tracked_chat
                chat_wrapper_installed = True
            except Exception:
                stream_capture_enabled = False
                bound_session.append_message = original_append_message
                bound_session.save = original_session_save
                bound_agent.stream_handlers = original_stream_handlers
                repl.stream_handlers = original_repl_stream_handlers
        else:
            stream_capture_enabled = False
            bound_session.append_message = original_append_message
            bound_session.save = original_session_save
            bound_agent.stream_handlers = original_stream_handlers
            repl.stream_handlers = original_repl_stream_handlers
    try:
        repl.agent.work_card_reference_intent = None
        if bridge.cancel_event.is_set():
            # A pre-run confirmation can be cancelled after its run has begun.
            # Do not enter Agent execution; the common finally block settles the
            # run and releases Desktop busy state exactly once.
            finish_reason = bridge.resolve_cancel_finish_reason() or "cancelled"
            ok = False
        else:
            agent_worker = asyncio.create_task(asyncio.to_thread(repl.handle_line, line))
            try:
                outcome = await asyncio.shield(agent_worker)
            except asyncio.CancelledError:
                bridge.request_cancel()
                bridge.cancel_event.set()
                while not agent_worker.done():
                    try:
                        await asyncio.shield(agent_worker)
                    except asyncio.CancelledError:
                        bridge.request_cancel()
                        bridge.cancel_event.set()
                    except Exception:
                        break
                try:
                    agent_worker.result()
                except BaseException:
                    pass
                finish_reason = bridge.resolve_cancel_finish_reason() or "cancelled"
                ok = False
                raise
            agent_reason = repl.last_turn_finish_reason
            finish_reason = bridge.resolve_turn_finish_reason(agent_reason)
            candidates = getattr(bound_agent, "_current_dialogue_project_card_candidates", None)
            if type(candidates) is list:
                bound_agent._current_dialogue_project_card_candidates = []
                if finish_reason not in {"cancelled", "timeout", "error"}:
                    project_ids: set[str] = set()
                    for candidate in candidates:
                        if (type(candidate) is dict
                                and candidate.get("session_id") == bound_session.conversation_id
                                and candidate.get("parent_run_id") == run_snapshot.run_id
                                and candidate.get("turn_epoch") == run_snapshot.sequence
                                and type(candidate.get("item_id")) is str
                                and type(candidate.get("project_ids")) is tuple):
                            project_ids.update(project_id for project_id in candidate["project_ids"]
                                               if type(project_id) is str)
                    if project_ids:
                        _schedule_current_dialogue_project_work_cards(
                            repl=repl, bridge=bridge, session=bound_session,
                            project_ids=tuple(sorted(project_ids)),
                        )
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
                # Only this still-live turn's confirmed copies may be offered here.
                if run_snapshot is not None and finish_reason not in {"cancelled", "timeout", "error"}:
                    try:
                        from work_records import WorkRecordStore
                        _resume_linked_item_copy_cards(
                            repl, bridge, bound_session, WorkRecordStore(bound_session),
                            emit_event=True, active_run_id=run_snapshot.run_id, turn_epoch=run_snapshot.sequence,
                        )
                        _resume_linked_project_session_copies(
                            repl, bridge, bound_session, WorkRecordStore(bound_session),
                            emit_event=True, active_run_id=run_snapshot.run_id, turn_epoch=run_snapshot.sequence,
                        )
                        _resume_linked_project_session_refreshes(
                            repl, bridge, bound_session, WorkRecordStore(bound_session),
                            emit_event=True, active_run_id=run_snapshot.run_id, turn_epoch=run_snapshot.sequence,
                        )
                    except Exception:
                        _work_card_publish_notice(bridge, "已确认副本仍待安全恢复；未假定已送达。")
                intent = getattr(repl.agent, "work_card_reference_intent", None)
                if intent is not None:
                    snapshot = bridge.lifecycle.snapshot()
                    if (type(intent) is WorkCardReferenceIntent and snapshot is not None
                            and snapshot.run_id == run_snapshot.run_id
                            and intent.session_id == repl.session.conversation_id
                            and uuid.UUID(intent.run_id).hex == uuid.UUID(run_snapshot.run_id).hex):
                        _emit_work_card_reference(
                            repl, bridge, repl.session, intent.presentation_id, intent.card_id,
                            intent.run_id, active_run_id=run_snapshot.run_id,
                        )
                    else:
                        _work_card_publish_notice(
                            bridge, "工作卡卡片投递因 Session/Run 绑定无法确认而停止；完整文字回查结果已保留，未标记已读。",
                        )
                    repl.agent.work_card_reference_intent = None
    except asyncio.CancelledError:
        ok = False
        finish_reason = bridge.resolve_cancel_finish_reason() or "cancelled"
        raise
    except Exception as exc:  # pragma: no cover
        ok = False
        finish_reason = "error"
        log_sidecar_exception(f"_run_line failed line={line!r}", exc)
        try:
            emit_error(bridge, str(exc))
        except Exception as notify_exc:
            _log_notification_failure("turn error notification failed", notify_exc)
    finally:
        if stream_capture_enabled and finish_reason in {"cancelled", "timeout", "error", "provider_error"}:
            # bound_session is the immutable owner captured for this turn. Its
            # append path locks and version-checks the old Session even after a rebind.
            persist_streamed_content(finish_reason)
            # If the accepted user message produced no content delta, there is no
            # stream journal to recover after restart. Persist only a machine-readable
            # terminal marker; never turn provider exceptions into assistant text.
            run_id = run_snapshot.run_id if run_snapshot is not None else None
            if (accepted_user_message_persisted and not stream_journal_started
                    and type(run_id) is str and run_id):
                try:
                    bound_session.ensure_messages_loaded()
                    existing = next((message for message in bound_session.messages
                                     if isinstance(message, dict)
                                     and isinstance(message.get(HOST_INTERNAL_METADATA_KEY), dict)
                                     and message[HOST_INTERNAL_METADATA_KEY].get("turn_run_id") == run_id), None)
                    if existing is None:
                        bound_session.append_message_durable({
                            "role": "assistant",
                            "content": "",
                            HOST_INTERNAL_METADATA_KEY: {
                                "stream_incomplete": True,
                                "turn_outcome": finish_reason,
                                "turn_run_id": run_id,
                            },
                        })
                    else:
                        metadata = existing.get(HOST_INTERNAL_METADATA_KEY)
                        if (existing.get("role") != "assistant" or existing.get("content") != ""
                                or not isinstance(metadata, dict)
                                or metadata.get("stream_incomplete") is not True
                                or metadata.get("turn_outcome") != finish_reason):
                            raise RuntimeError("turn outcome identity conflicts with existing history")
                except Exception as exc:
                    log_sidecar_exception("empty streamed turn outcome could not be persisted", exc)
        if stream_capture_enabled:
            for journal in (stream_journal, pending_stream_commit):
                if journal is not None:
                    try:
                        journal.release_lease()
                    except Exception as exc:
                        log_sidecar_exception("stream journal lease release failed", exc)
            if had_instance_append_message:
                bound_session.append_message = previous_instance_append_message
            else:
                bound_session.__dict__.pop("append_message", None)
            if had_instance_save:
                bound_session.save = previous_instance_save
            else:
                bound_session.__dict__.pop("save", None)
            bound_agent.stream_handlers = original_stream_handlers
            repl.stream_handlers = original_repl_stream_handlers
            if chat_wrapper_installed:
                if had_chat_instance_value:
                    original_llm.chat = previous_chat_instance_value
                else:
                    original_llm.__dict__.pop("chat", None)
        if original_run_turn is not None:
            if had_instance_run_turn:
                bound_agent.run_turn = previous_instance_run_turn
            else:
                bound_agent.__dict__.pop('run_turn', None)
        if original_agent_user_append is not None:
            if had_instance_agent_user_append:
                bound_agent._append_user_message_with_result_reply = previous_instance_agent_user_append
            else:
                bound_agent.__dict__.pop("_append_user_message_with_result_reply", None)
        if hasattr(bound_agent, "_session_binding_transition"):
            bound_agent._session_binding_transition = previous_binding_transition
        if hasattr(bound_agent, "_work_instruction_checkpoint"):
            bound_agent._work_instruction_checkpoint = previous_work_instruction_checkpoint
        bridge._turn_busy.clear()
        try:
            bridge.end_turn()
        except Exception as exc:
            _log_notification_failure("turn watchdog cleanup failed", exc)

        final_snapshot = None
        if run_snapshot is not None:
            final_snapshot = _finish_execution_safely(
                bridge, run_id=run_snapshot.run_id,
                finish_reason=finish_reason, ok=ok,
            )
            bridge.finish_work_instructions(
                run_snapshot.run_id, bound_session.conversation_id, finish_reason,
            )
            worker_work_record_result = getattr(
                bound_agent, "_project_worker_work_record_result", None,
            )

        # The caller retains TURN_LOCK here, so the executor's run receipts
        # cannot be replaced by a later ordinary turn while facts are frozen.
        if (post_turn_request_id is not None and execution_mode == "ordinary"
                and final_snapshot is not None and run_snapshot is not None
                and final_snapshot.run_id == run_snapshot.run_id
                and final_snapshot.state == "settled" and final_snapshot.ok is True
                and final_snapshot.finish_reason in {"completed", "stop"} and not final_snapshot.cancel_requested
                and final_snapshot.sequence > run_snapshot.sequence
                and receipt_session is bound_session and repl.session is bound_session
                and repl.agent is bound_agent and getattr(bound_agent, "session", None) is bound_session
                and bridge.lifecycle_conversation_id == bound_session.conversation_id
                and not bridge._connection_closed.is_set() and not bridge.cancel_event.is_set()):
            frozen_receipts: tuple[tuple[str, str, str, bool], ...] = ()
            receipt_status = 'unknown'
            try:
                facts, omitted, complete = _snapshot_bound_host_tool_receipts(
                    agent=receipt_agent, current_session=bound_session,
                    binding=_PostTurnRunIdentity(run_snapshot.run_id),
                    tool_result_count=_tool_result_message_count(receipt_session, tool_message_start_index),
                )
                ids = [str(uuid.UUID(fact["call_id"])) for fact in facts]
                if (complete and omitted == 0 and len(ids) == len(set(ids))
                        and all(fact["call_id"] == call_id and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", fact["tool"])
                                for fact, call_id in zip(facts, ids))):
                    receipt_status = 'complete'
                    frozen_receipts = tuple((fact["host_run_id"], fact["call_id"], fact["tool"], fact["ok"]) for fact in facts)
            except Exception:
                # Missing/invalid receipt evidence is unknown, never fabricated.
                pass
            with bridge._result_view_write_lock:
                if (bridge._post_turn_generation == post_turn_generation
                        and not bridge._connection_closed.is_set() and not bridge.cancel_event.is_set()
                        and callable(bridge._session_provider) and bridge._session_provider() is bound_session):
                    post_turn_candidate = _FrozenPostTurnCandidate(
                        session=bound_session, text=post_turn_source_text if post_turn_source_text is not None else line,
                        request_id=post_turn_request_id,
                        run_id=run_snapshot.run_id, sequence=final_snapshot.sequence,
                        finish_reason=final_snapshot.finish_reason,
                        receipts=frozen_receipts, tool_receipt_status=receipt_status,
                        cancel_generation=post_turn_generation,
                        result_excerpt=(captured_turn_results[0]
                            if len(captured_turn_results) == 1 and captured_turn_results[0] is not None
                            else _post_turn_result_excerpt(None))
                            if original_run_turn is not None else None,
                    )

        # These notifications are best-effort and cannot prevent lifecycle
        # finality or durable work-record settlement.
        for event_factory, label in (
            (lambda: session_memory_event(repl.session), "session memory notification failed"),
            (lambda: _proposals_payload(paths), "proposal notification failed"),
            (lambda: _memories_payload(paths), "memory notification failed"),
        ):
            try:
                _emit_safely(bridge.emit, event_factory(), label)
            except Exception as exc:
                _log_notification_failure(label, exc)

        if hasattr(repl.agent, "work_card_reference_intent"):
            repl.agent.work_card_reference_intent = None
        if work_record_binding is not None:
            store, binding = work_record_binding
            try:
                # Settlement re-reads the exact lifecycle snapshot after finalization;
                # an emit failure cannot turn an active run into a recorded result.
                tool_result_count = _tool_result_message_count(
                    receipt_session, tool_message_start_index,
                )
                settled = _settle_work_record_run(
                    current_session=repl.session, bridge=bridge,
                    store=store, binding=binding, repl=repl,
                    agent=receipt_agent,
                    tool_result_count=tool_result_count,
                    worker_work_record_result=worker_work_record_result,
                    worker_verification_receipt=getattr(bound_agent, "_project_worker_verification_result", None),
                    emit=lambda event: _emit_safely(
                        bridge.emit_turn_event, event, "work-record settlement notice failed",
                    ),
                )
                # Freeze only after the existing settlement has succeeded. Do not
                # await a confirmation or replace the unknown result under TURN_LOCK.
                if (settled is True and explicit_result_capture is True and lock_held
                        and execution_mode == "ordinary" and original_run_turn is not None
                        and len(captured_turn_results) == 1 and captured_turn_results[0]
                        and captured_turn_results[0] not in {
                            _post_turn_result_excerpt(None), _post_turn_result_excerpt('\ud800')}
                        and final_snapshot is not None and run_snapshot is not None
                        and final_snapshot.run_id == run_snapshot.run_id == binding.run_id
                        and final_snapshot.state == 'settled' and final_snapshot.ok is True
                        and final_snapshot.finish_reason in {'completed', 'stop'}
                        and not final_snapshot.cancel_requested
                        and repl.session is bound_session and getattr(store, '_session', None) is bound_session
                        and getattr(binding, 'session_id', None) == bound_session.conversation_id
                        and bridge.lifecycle_conversation_id == bound_session.conversation_id
                        and worker_work_record_result is None):
                    request_id = getattr(binding, 'capture_request_id', None)
                    canonical_request = str(uuid.UUID(request_id)) if type(request_id) is str else ''
                    item = store.get_item(binding.item_id)
                    requirements = [r for r in item.get('requirements', []) if type(r) is dict
                                    and r.get('requirement_id') == binding.requirement_id]
                    runs = [r for r in item.get('runs', []) if type(r) is dict
                            and r.get('run_id') == binding.run_id and r.get('requirement_id') == binding.requirement_id]
                    edges = [e for e in item.get('history', []) if type(e) is dict
                             and e.get('event_id') == request_id and e.get('kind') == 'requirement_added'
                             and type(e.get('payload')) is dict and e['payload'].get('requirement_id') == binding.requirement_id
                             and type(e.get('idempotency_key')) is str
                             and e['idempotency_key'].startswith(f'desktop:user.message:{request_id}:')]
                    if (canonical_request == request_id and len(requirements) == len(runs) == len(edges) == 1
                            and requirements[0].get('current_run_id') == binding.run_id
                            and requirements[0].get('status') == 'unknown'
                            and runs[0].get('status') == 'unknown'
                            and type(runs[0].get('verification')) is dict
                            and runs[0]['verification'].get('status') == 'not_verified'
                            and type(runs[0]['verification'].get('host_execution')) is dict
                            and runs[0]['verification']['host_execution'].get('sequence') == final_snapshot.sequence
                            and type(runs[0].get('result_id')) is str and runs[0]['result_id']):
                        with bridge._result_view_write_lock:
                            if (bridge._post_turn_generation == post_turn_generation
                                    and not bridge._connection_closed.is_set() and not bridge.cancel_event.is_set()
                                    and callable(bridge._session_provider) and bridge._session_provider() is bound_session):
                                post_turn_candidate = _FrozenExplicitRunExcerpt(
                                    session=bound_session, store=store, request_id=request_id,
                                    item_id=binding.item_id, requirement_id=binding.requirement_id,
                                    run_id=binding.run_id, result_id=runs[0]['result_id'],
                                    sequence=final_snapshot.sequence, cancel_generation=post_turn_generation,
                                    settlement_finish_reason=runs[0]['verification']['host_execution'].get('finish_reason'),
                                    excerpt=captured_turn_results[0],
                                )
            except Exception as exc:
                _log_notification_failure("work-record settlement or excerpt freeze failed", exc)
        clear_project_worker_context = getattr(bound_agent, "clear_project_worker_live_context", None)
        if (callable(clear_project_worker_context) and final_snapshot is not None and run_snapshot is not None
                and final_snapshot.run_id == run_snapshot.run_id
                and final_snapshot.state in {"settled", "paused", "failed"}):
            clear_project_worker_context(run_snapshot.run_id, run_snapshot.sequence)

    return post_turn_candidate


async def _capture_work_record_until_thread_stops(
    service: Any,
    text: str,
    *,
    request_id: str | None,
    bridge: WsBridge,
    on_cancelled: Callable[[Any | None, BaseException | None], None],
    track_run: bool = False,
) -> Any:
    """Shield the worker thread; on caller cancellation signal it and wait before unwinding."""
    capture_kwargs = {"request_id": request_id}
    if track_run:
        capture_kwargs["track_run"] = True
    worker = asyncio.create_task(asyncio.to_thread(
        service.capture, text, **capture_kwargs,
    ))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        # A caller cancellation does not stop asyncio.to_thread. Signal the
        # service's shared cancellation event, then keep the owning task (and
        # its TURN_LOCK) alive until the worker has actually stopped.
        bridge.request_cancel()
        bridge.cancel_event.set()
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                bridge.request_cancel()
                bridge.cancel_event.set()
            except Exception:
                break
        try:
            result = worker.result()
        except BaseException as exc:
            on_cancelled(None, exc)
        else:
            on_cancelled(result, None)
        raise


def _begin_work_record_run_binding(
    *, session: Session, capture: Any, run_id: str,
    current_session: Session, bridge: WsBridge,
    emit: Callable[[dict[str, Any]], None], request_id: str | None = None,
) -> tuple[Any, Any] | None | object:
    """Persist a run claim only for the exact approved capture and lifecycle run."""
    snapshot = bridge.lifecycle.snapshot()
    item_id = getattr(capture, "item_id", None)
    requirement_id = getattr(capture, "requirement_id", None)
    if getattr(capture, "status", None) != "recorded":
        return None
    if (
        current_session is not session
        or not isinstance(item_id, str) or not item_id.strip()
        or not isinstance(requirement_id, str) or not requirement_id.strip()
        or not isinstance(run_id, str) or not run_id.strip()
        or snapshot is None or snapshot.run_id != run_id
        or snapshot.state not in {"running", "stopping"}
        or bridge.lifecycle_conversation_id != session.conversation_id
    ):
        emit({
            "type": "turn.notice", "level": "warn",
            "text": "工作记录已保存但精确运行绑定未确认；为防重复副作用，本条不自动执行，请核对。",
        })
        return _UNCONFIRMED_CAPTURE_RUN
    try:
        from work_records import WorkRecordStore
        from work_record_settlement import BoundExecution, WorkRecordSettlementAdapter

        store = WorkRecordStore(session)
        try:
            started = store.begin_requirement_run(
                item_id, requirement_id, run_id=run_id, expected_current_run_id=None,
            )
        except Exception as exc:
            from work_records import WorkRecordStaleRun
            if isinstance(exc, WorkRecordStaleRun) and _capture_is_bound_to_prior_run(
                store, item_id=item_id, requirement_id=requirement_id,
                attempted_run_id=run_id, request_id=request_id,
            ):
                emit({
                    "type": "turn.notice", "level": "warn",
                    "text": "检测到相同请求已绑定既有 run；为避免重复副作用，本条不自动重执行，请先核对既有运行结果。",
                })
                return _DUPLICATE_CAPTURE_RUN
            raise
        if not isinstance(started, dict) or started.get("current_run_id") != run_id:
            raise RuntimeError("run CAS did not return the requested current run")
        try:
            capture_request_id = str(uuid.UUID(request_id)) if isinstance(request_id, str) else None
        except (ValueError, AttributeError, TypeError):
            capture_request_id = None
        binding = BoundExecution(
            session_id=session.conversation_id,
            item_id=item_id,
            requirement_id=requirement_id,
            run_id=run_id,
            capture_request_id=capture_request_id,
        )
        return store, binding
    except Exception:
        emit({
            "type": "turn.notice", "level": "warn",
            "text": "工作记录已保存但运行绑定/重放状态未确认；为防重复副作用，本条不自动执行，请核对。",
        })
        return _UNCONFIRMED_CAPTURE_RUN


def _capture_is_bound_to_prior_run(
    store: Any, *, item_id: str, requirement_id: str,
    attempted_run_id: str, request_id: str | None,
) -> bool:
    """Require both an existing run and the exact request-to-requirement history edge."""
    try:
        if not isinstance(request_id, str) or not request_id.strip():
            return False
        canonical_request_id = str(uuid.UUID(request_id.strip()))
        item = store.get_item(item_id)
        if not isinstance(item, dict) or item.get("item_id") != item_id:
            return False
        requirement = next((row for row in item.get("requirements", [])
                            if isinstance(row, dict) and row.get("requirement_id") == requirement_id), None)
        current_run_id = requirement.get("current_run_id") if isinstance(requirement, dict) else None
        if not isinstance(current_run_id, str) or not current_run_id.strip() or current_run_id == attempted_run_id:
            return False
        return any(
            isinstance(event, dict)
            and event.get("event_id") == canonical_request_id
            and event.get("kind") == "requirement_added"
            and isinstance(event.get("payload"), dict)
            and event["payload"].get("requirement_id") == requirement_id
            for event in item.get("history", [])
        )
    except Exception:
        return False


def _tool_result_message_count(session: Session | None, start_index: int | None) -> int | None:
    """Count Agent tool-result messages appended after this turn's loaded snapshot."""
    if not isinstance(session, Session) or type(start_index) is not int or start_index < 0:
        return None
    messages = getattr(session, "messages", None)
    if not isinstance(messages, list) or start_index > len(messages):
        return None
    current_turn_messages = messages[start_index:]
    if any(not isinstance(message, dict) for message in current_turn_messages):
        return None
    return sum(message.get("role") == "tool" for message in current_turn_messages)


def _snapshot_bound_host_tool_receipts(
    *, agent: Any, current_session: Session, binding: Any,
    tool_result_count: int | None,
) -> tuple[list[dict[str, Any]], int, bool]:
    """Read only one atomic executor snapshot proven to match the bound run."""
    if getattr(agent, "session", None) is not current_session:
        return [], 0, False
    executor = getattr(agent, "executor", None)
    executor_session = getattr(executor, "session", None)
    if executor_session is None:
        return [], 0, False
    session_dir = getattr(executor_session, "session_dir", None)
    if session_dir is None:
        return [], 0, False
    try:
        if Path(session_dir).resolve(strict=False) != Path(current_session.session_dir).resolve(strict=False):
            return [], 0, False
    except (OSError, TypeError, ValueError):
        return [], 0, False

    snapshot_fn = getattr(executor_session, "snapshot_host_tool_receipts", None)
    if not callable(snapshot_fn):
        return [], 0, False
    from evolution.context import normalize_host_run_id

    bound_run_id = normalize_host_run_id(getattr(binding, "run_id", None))
    run_before = normalize_host_run_id(getattr(executor_session, "host_run_id", None))
    if not bound_run_id or run_before != bound_run_id:
        return [], 0, False
    try:
        snapshot = snapshot_fn()
    except Exception:
        return [], 0, False
    run_after = normalize_host_run_id(getattr(executor_session, "host_run_id", None))
    if type(snapshot) is not tuple or len(snapshot) != 4:
        return [], 0, False
    generation, recorded_run_id, entries, omitted_count = snapshot
    normalized_snapshot_run_id = normalize_host_run_id(recorded_run_id)
    if (
        type(generation) is not int or generation < 1
        or normalized_snapshot_run_id != bound_run_id
        or run_after != bound_run_id
        or type(omitted_count) is not int or omitted_count < 0
        or type(entries) is not list or len(entries) > 20
        or tool_result_count is None
        or tool_result_count != len(entries) + omitted_count
    ):
        return [], 0, False
    expected_keys = {"host_run_id", "call_id", "tool", "ok"}
    for entry in entries:
        if (
            type(entry) is not dict or set(entry) != expected_keys
            or normalize_host_run_id(entry.get("host_run_id")) != bound_run_id
            or type(entry.get("call_id")) is not str
            or type(entry.get("tool")) is not str
            or type(entry.get("ok")) is not bool
        ):
            return [], 0, False
    return [dict(entry) for entry in entries], omitted_count, True


def _work_record_settlement_publication_scope(
    store: Any, binding: Any, *, worker_work_record_result: Any | None = None,
) -> str | None:
    """Classify only the exact current item; worker-batch outer records never become standalone cards."""
    try:
        store._validate_scope()
        item = store.get_item(binding.item_id)
        if type(item) is not dict or item.get("item_id") != binding.item_id:
            return None
        requirements = item.get("requirements")
        matches = [row for row in requirements if type(row) is dict
                   and row.get("requirement_id") == binding.requirement_id]
        if len(matches) != 1 or matches[0].get("current_run_id") != binding.run_id:
            return None
        scope = item.get("scope")
        if (type(scope) is dict and scope.get("kind") == "project"
                and scope.get("project_id") == getattr(store, "_bound_project_id", None)
                and scope.get("project_id")):
            return "project"
        if scope == {"kind": "independent", "object_ref": None}:
            history = item.get("history")
            batch_keys = [event.get("idempotency_key") for event in history
                          if type(event) is dict and type(event.get("idempotency_key")) is str] if type(history) is list else []
            if (item.get("title") == "多项目任务批次"
                    and any(key.startswith("worker-batch-original:") for key in batch_keys)
                    and any(key.startswith("worker-batch-task:") for key in batch_keys)):
                return "none"
            # Standalone auto-publication requires the original explicit user.message
            # capture edge. Project-worker batches have distinct keys/event IDs even
            # though their outer container also uses independent scope.
            capture_id = getattr(binding, "capture_request_id", None)
            if worker_work_record_result is not None or type(capture_id) is not str:
                return "none"
            try:
                canonical = str(uuid.UUID(capture_id))
            except (ValueError, TypeError, AttributeError):
                return "none"
            if canonical != capture_id:
                return "none"
            edges = [event for event in item.get("history", [])
                     if type(event) is dict and event.get("event_id") == canonical
                     and event.get("kind") == "requirement_added"
                     and type(event.get("payload")) is dict
                     and event["payload"].get("requirement_id") == binding.requirement_id
                     and type(event.get("idempotency_key")) is str
                     and event["idempotency_key"].startswith(f"desktop:user.message:{canonical}:")]
            return "independent" if len(edges) == 1 else "none"
        return None
    except Exception:
        return None


def _settle_work_record_run(
    *, current_session: Session, bridge: WsBridge,
    store: Any, binding: Any, emit: Callable[[dict[str, Any]], None],
    agent: Any | None = None, tool_result_count: int | None = None,
    repl: ConversationRepl | None = None,
    worker_work_record_result: Any | None = None,
    worker_verification_receipt: Any | None = None,
) -> bool:
    """Persist unknown only for the original store Session and its exact run."""
    from execution_lifecycle import ExecutionSnapshot
    from work_record_settlement import ExecutionReceipt, WorkRecordSettlementAdapter

    original_session = getattr(store, "_session", None)
    if (
        not isinstance(original_session, Session)
        or current_session is not original_session
        or original_session.conversation_id != binding.session_id
    ):
        emit({
            "type": "turn.notice", "level": "warn",
            "text": "当前会话对象已与原工作记录会话不同；拒绝写入结果，记录状态需核对。",
        })
        return False
    try:
        if getattr(store, "_bound_project_id", None) != getattr(original_session.meta, "project_id", ""):
            raise RuntimeError("bound project scope changed")
        validate_scope = getattr(store, "_validate_scope", None)
        if not callable(validate_scope):
            raise RuntimeError("store does not expose a scope validator")
        validate_scope()
    except Exception:
        emit({
            "type": "turn.notice", "level": "warn",
            "text": "原会话或项目绑定范围已变化/无法验证；拒绝写入工作记录结果。",
        })
        return False

    publication_scope = _work_record_settlement_publication_scope(
        store, binding, worker_work_record_result=worker_work_record_result,
    )
    if publication_scope is None:
        emit({
            "type": "turn.notice", "level": "warn",
            "text": "工作记录 item/requirement/run 无法精确复核；拒绝结算以避免错绑。",
        })
        return False
    publish_project_update = publication_scope == "project"
    publish_independent_update = publication_scope == "independent"

    snapshot = bridge.lifecycle.snapshot()
    terminal_states = {"settled", "paused", "failed"}
    if (
        not isinstance(snapshot, ExecutionSnapshot)
        or snapshot.run_id != binding.run_id
        or snapshot.state not in terminal_states
        or type(snapshot.sequence) is not int
        or snapshot.sequence < 1
        or bridge.lifecycle_conversation_id != binding.session_id
    ):
        emit({
            "type": "turn.notice", "level": "warn",
            "text": "本回合尚无可核验的匹配终态；工作记录结果保持未知，未标记完成。",
        })
        return False

    # Preserve only the terminal snapshot's bounded lifecycle label. State alone
    # must never be used to guess a failure cause; paused is the one state-level
    # normalization because its internal finish reasons are intentionally private.
    allowed_finish_reasons = {"completed", "cancelled", "timeout", "error", "paused", "unknown"}
    if snapshot.state == "paused":
        finish_reason = "paused"
    elif snapshot.finish_reason == "stop":
        # Preserve only this matched run's actual successful, non-cancelled
        # terminal provider label; it is not evidence of task completion.
        finish_reason = ("stop" if snapshot.state == "settled" and snapshot.ok is True
                         and not snapshot.cancel_requested else "unknown")
    elif isinstance(snapshot.finish_reason, str) and snapshot.finish_reason in allowed_finish_reasons:
        finish_reason = snapshot.finish_reason
    else:
        finish_reason = "unknown"

    # Retain the legacy required receipt field for API compatibility only. The
    # settlement adapter does not persist this mapped outcome; finish_reason is
    # the explicitly labelled host lifecycle fact.
    outcome = {
        "completed": "succeeded",
        "cancelled": "cancelled",
        "timeout": "timeout",
        "error": "exception",
    }.get(finish_reason, "failed")

    if agent is None:
        host_tool_receipts, omitted_count, tool_receipts_complete = [], 0, False
    else:
        host_tool_receipts, omitted_count, tool_receipts_complete = _snapshot_bound_host_tool_receipts(
            agent=agent,
            current_session=original_session,
            binding=binding,
            tool_result_count=tool_result_count,
        )
    receipt = ExecutionReceipt(
        session_id=binding.session_id,
        item_id=binding.item_id,
        requirement_id=binding.requirement_id,
        run_id=binding.run_id,
        sequence=snapshot.sequence,
        outcome=outcome,
        finish_reason=finish_reason,
        host_tool_receipts=host_tool_receipts,
        omitted_count=omitted_count,
    )
    try:
        from project_worker_work_record_settlement import (
            ProjectWorkerWorkRecordBinding,
            settle_project_worker_work_record,
        )
        from project_worker_host import ProjectWorkerHostOutcome
        if (type(worker_work_record_result) is tuple and len(worker_work_record_result) == 2
                and type(worker_work_record_result[0]) is ProjectWorkerWorkRecordBinding
                and type(worker_work_record_result[1]) is ProjectWorkerHostOutcome
                and worker_work_record_result[0].execution == binding):
            worker_binding, worker_outcome = worker_work_record_result
            result = settle_project_worker_work_record(
                store, worker_binding, worker_outcome,
                sequence=receipt.sequence,
                execution_receipt=receipt,
                verification_receipt=worker_verification_receipt,
                verification_host=getattr(agent, "project_worker_host", None),
                verification_bridge=bridge,
                publish_project_update=publish_project_update,
                publication_window_seconds=PROJECT_WORK_CARD_MERGE_SECONDS,
            )
            if result.status == "rejected":
                if result.reason in {
                    "worker_self_report_receipt_replayed", "worker_self_report_stale",
                }:
                    emit({
                        "type": "turn.notice", "level": "warn",
                        "text": "项目自报告票据已消费或不属于当前连接/回合；拒绝覆盖已有工作记录结果。",
                    })
                else:
                    emit({
                        "type": "turn.notice", "level": "warn",
                        "text": "worker task 与记录绑定无法复核；仅按父回合生命周期结算，要求仍未知、未经验证。",
                    })
                    result = WorkRecordSettlementAdapter(store, binding).settle(
                        receipt,
                        publish_project_update=publish_project_update,
                        publish_independent_update=publish_independent_update,
                        publication_window_seconds=PROJECT_WORK_CARD_MERGE_SECONDS,
                    )
        else:
            result = WorkRecordSettlementAdapter(store, binding).settle(
                receipt,
                publish_project_update=publish_project_update,
                publish_independent_update=publish_independent_update,
                publication_window_seconds=PROJECT_WORK_CARD_MERGE_SECONDS,
            )
    except Exception:
        emit({
            "type": "turn.notice", "level": "warn",
            "text": "本回合已结束，但工作记录结果未能确认保存；不会重做执行，要求状态需核对。",
        })
        return False
    if result.status == "recorded":
        publication_status = getattr(result, "publication_status", None)
        if publication_status in {"replay_no_new_intent", "result_saved_without_intent"}:
            if publication_status == "replay_no_new_intent":
                message = (
                    "工作结果已记录；同一 result 已存在，本次未创建或改写发布 intent，也未自动追溯补发。"
                    "结果仍未知、未经验证；不能据此确认它已进入工作卡，也不表示工作未完成。"
                )
            else:
                message = (
                    "工作结果已记录，但本次原子结算未生成持久发布 intent，未自动追溯补发。"
                    "结果仍未知、未经验证；这不表示工作未完成。"
                )
            emit({"type": "turn.notice", "level": "warn", "text": message})
            return True
        if result.reason == "project_self_reported_pass":
            if repl is not None and publish_project_update:
                _schedule_settled_project_work_card(
                    repl=repl, bridge=bridge, session=original_session,
                    store=store, binding=binding,
                )
            emit({"type": "turn.notice", "level": "info", "text":
                  "已记录用户授权的项目自报告：所选单个 pytest 目标通过。项目代码可伪造结果；这不是认证，也不证明整体要求完成。"})
            return True
        if repl is not None and publish_project_update:
            _schedule_settled_project_work_card(
                repl=repl, bridge=bridge, session=original_session,
                store=store, binding=binding,
            )
        elif repl is not None and publish_independent_update:
            _schedule_settled_independent_work_card(
                repl=repl, bridge=bridge, session=original_session,
                store=store, binding=binding,
            )
        if not tool_receipts_complete:
            emit({
                "type": "turn.notice", "level": "warn",
                "text": "宿主工具回执不完整或身份无法验证；空白不代表本回合没有工具调用，要求状态仍未知、未经验证。",
            })
        elif omitted_count > 0:
            emit({
                "type": "turn.notice", "level": "warn",
                "text": f"宿主工具回执有 {omitted_count} 条未收录；仅记录可验证条目，要求状态仍未知、未经验证。",
            })
        elif not host_tool_receipts:
            emit({
                "type": "turn.notice", "level": "info",
                "text": "本回合没有可持久化的宿主工具回执条目；不据此断言未调用工具，要求状态仍未知、未经验证。",
            })
        emit({
            "type": "turn.notice", "level": "info",
            "text": "本回合已关联到工作记录；完成及验证情况未知，未标记完成。",
        })
        return True
    emit({
        "type": "turn.notice", "level": "warn",
        "text": "本回合已结束，但工作记录结果未能确认保存；不会重做执行，要求状态需核对。",
    })
    return False


def _current_dialogue_card_parent_run_id(snapshot: Any) -> str | None:
    """Read the focused receipt from a trusted projection; never infer its parent.

    Callers must supply a store-built or ledger-validated snapshot. Structural
    checks here are defense in depth, not authentication of arbitrary snapshots.
    Legacy snapshots without focus retain their original first-receipt behavior.
    """
    def field(value, name, default=None):
        return value.get(name, default) if type(value) is dict else getattr(value, name, default)

    focus = field(snapshot, "change_focus")
    cards = field(snapshot, "cards", ())
    if focus is not None:
        from work_card_project_scope import _strict_object, _valid_receipt
        from work_record_cards import WorkCardChangeFocus
        if type(focus) is dict:
            if set(focus) != {"item_id", "requirement_id", "result_id"}:
                return None
        elif type(focus) is not WorkCardChangeFocus:
            return None
        item_id, requirement_id, result_id = (field(focus, key) for key in
                                               ("item_id", "requirement_id", "result_id"))
        session_id, project_id = field(snapshot, "session_id"), field(snapshot, "project_id")
        if (field(snapshot, "kind") != "project_scoped" or field(snapshot, "completeness") != "complete"
                or any(type(value) is not str or not value for value in
                       (item_id, requirement_id, result_id, session_id, project_id))
                or type(cards) not in (list, tuple)):
            return None
        matches = []
        for card in cards:
            if field(card, "item_id") != item_id:
                continue
            requirements = field(card, "requirements", ())
            if type(requirements) not in (list, tuple):
                return None
            for requirement in requirements:
                if field(requirement, "requirement_id") == requirement_id:
                    matches.append((card, requirement))
        if len(matches) != 1:
            return None
        card, requirement = matches[0]
        result = field(requirement, "latest_result")
        run_id = field(requirement, "current_run_id")
        if (field(card, "project_id") != project_id or field(card, "session_id") != session_id
                or field(requirement, "project_id") != project_id
                or field(requirement, "session_id") != session_id
                or field(requirement, "item_id") != item_id
                or field(field(requirement, "source_ref"), "kind") != "project"
                or field(field(requirement, "source_ref"), "id") != project_id
                or field(requirement, "latest_result_is_current") is not True
                or field(result, "project_id") != project_id or field(result, "session_id") != session_id
                or field(result, "item_id") != item_id or field(result, "requirement_id") != requirement_id
                or field(result, "result_id") != result_id or field(result, "run_id") != run_id
                or field(result, "status") != "unknown" or field(result, "verification_status") != "not_verified"):
            return None
        try:
            summary = field(result, "summary")
            receipt = json.loads(summary, object_pairs_hook=_strict_object)
            if type(receipt) is not dict:
                return None
            # The projection omits host_execution metadata; the store already
            # validated it. These values only permit reuse of receipt shape and
            # identity checks and are not evidence of independent verification.
            projected_result = {
                "status": "unknown", "run_id": run_id, "result_id": result_id,
                "result_summary": summary,
                "verification": {"status": "not_verified", "host_execution": {
                    "sequence": receipt.get("parent_epoch"), "finish_reason": "unknown"}},
            }
            projected_requirement = {"requirement_id": requirement_id, "current_run_id": run_id}
            if not _valid_receipt(projected_requirement, projected_result, session_id=session_id,
                                  item_id=item_id, project_id=project_id):
                return None
            return receipt["parent_run_id"]
        except (TypeError, ValueError, KeyError, UnicodeError, RecursionError):
            return None

    for card in cards if type(cards) in (list, tuple) else ():
        requirements = field(card, "requirements", ())
        for requirement in requirements if type(requirements) in (list, tuple) else ():
            result = field(requirement, "latest_result")
            summary = field(result, "summary")
            if type(summary) is not str:
                continue
            try:
                receipt = json.loads(summary)
                parent_run_id = receipt.get("parent_run_id") if type(receipt) is dict else None
                parsed = uuid.UUID(parent_run_id)
            except (TypeError, ValueError, AttributeError, json.JSONDecodeError):
                continue
            if parent_run_id in {str(parsed), parsed.hex}:
                return parent_run_id
    return None


def _retry_saved_project_scoped_card_reference(
    repl: ConversationRepl, bridge: WsBridge, session: Session, store: Any,
    intent: dict[str, Any], *, emit_event: bool = True,
) -> bool:
    """Retry only the reference for a verified immutable card_saved intent."""
    if type(intent) is not dict or intent.get("state") != "card_saved":
        return False
    try:
        from work_card_ledger import WorkCardLedger
        from work_card_publish_outbox import ProjectScopedCardOutbox

        ledger = WorkCardLedger(session, store)
        record = ledger.get_by_request_id(intent["request_id"])
        snapshot = record.get("snapshot") if type(record) is dict else None
        focus_keys = ("latest_item_id", "latest_requirement_id", "latest_run_id", "latest_result_id")
        has_focus_intent = all(key in intent for key in focus_keys)
        expected_focus = ({
            "item_id": intent["latest_item_id"],
            "requirement_id": intent["latest_requirement_id"],
            "result_id": intent["latest_result_id"],
        } if has_focus_intent else None)
        if (type(record) is not dict or record.get("card_id") != intent.get("card_id")
                or type(snapshot) is not dict or snapshot.get("kind") != "project_scoped"
                or snapshot.get("project_id") != intent.get("project_id")
                or snapshot.get("session_id") != session.conversation_id
                or snapshot.get("change_focus") != expected_focus):
            _work_card_publish_notice(bridge, "已保存卡片的原始快照无法核实；持久待投递状态保留。")
            return False
        parent_run_id = _current_dialogue_card_parent_run_id(record["snapshot"])
        if parent_run_id is None:
            _work_card_publish_notice(bridge, "已保存卡片缺少可核验 parent-run；仍保留为未排入历史引用。")
            return False
        outbox = ProjectScopedCardOutbox(session, store)
        callback = lambda: outbox.mark_reference_queued(
            project_id=intent["project_id"], generation=intent["generation"],
            request_id=intent["request_id"], card_id=intent["card_id"],
        )
        return _emit_work_card_reference(
            repl, bridge, session,
            _work_card_presentation_id(session.conversation_id, parent_run_id, intent["card_id"]),
            intent["card_id"], parent_run_id, active_run_id=None,
            after_durable_queue=callback, emit_event=emit_event,
        )
    except Exception:
        _work_card_publish_notice(bridge, "已保存卡片引用未能确认排队；原卡和待投递事实保留。")
        return False


def _publish_current_dialogue_project_work_card(
    repl: ConversationRepl, bridge: WsBridge, session: Session,
    store: Any, project_id: str,
) -> bool:
    """Save once, then durably queue/retry the reference; never equate save with delivery."""
    if (repl.session is not session or getattr(repl.agent, "session", None) is not session
            or getattr(store, "_session", None) is not session
            or bridge._connection_closed.is_set()):
        return False
    try:
        from work_card_ledger import WorkCardLedger
        from work_card_project_scope import (
            build_project_scoped_work_record_cards,
            project_scoped_snapshot_request_id,
        )
        from work_card_publish_outbox import ProjectScopedCardOutbox

        store._validate_scope()
        snapshot = build_project_scoped_work_record_cards(store, project_id)
        if snapshot.completeness != "complete" or not snapshot.cards:
            _work_card_publish_notice(
                bridge, f"项目 {project_id} 的当前对话要求/host 回执未能完整复核；未投影卡片。",
            )
            return False
        request_id = project_scoped_snapshot_request_id(snapshot)
        ledger = WorkCardLedger(session, store)
        durable_outbox = ProjectScopedCardOutbox(session, store)
        intent = durable_outbox.record_update(
            project_id, request_id, window_seconds=PROJECT_WORK_CARD_MERGE_SECONDS,
        )
        if intent["request_id"] != request_id:
            _work_card_publish_notice(
                bridge, "较旧 project-scoped 快照被更新的持久 intent 取代；保留新 request，未保存或投递旧卡。",
            )
            return False
        if intent["state"] == "reference_queued":
            return False  # history owns retry; this is queued, not delivered
        if intent["state"] == "card_saved":
            return _retry_saved_project_scoped_card_reference(
                repl, bridge, session, store, intent,
            )
        parent_run_id = _current_dialogue_card_parent_run_id(snapshot)
        if parent_run_id is None:
            _work_card_publish_notice(
                bridge, "卡片尚无可核验的 host parent-run；pending 保留，未保存或标记投递。",
            )
            return False
        remaining = durable_outbox.remaining_seconds(intent)
        if remaining > 0:
            from work_card_update_coalescer import ProjectWorkCardUpdateCoalescer
            coalescer = bridge._work_card_update_coalescer
            if coalescer is None:
                coalescer = ProjectWorkCardUpdateCoalescer(
                    delay_seconds=max(remaining, 0.001),
                )
                bridge._work_card_update_coalescer = coalescer
            coalescer.schedule(
                f"current-dialogue:{session.conversation_id}:{project_id}",
                lambda: _publish_current_dialogue_project_work_card(
                    repl, bridge, session, store, project_id,
                ),
                delay_seconds=max(remaining, 0.001),
            )
            return False
        saved = ledger.save_project_scoped_snapshot(
            request_id, snapshot, generation=intent["generation"],
        )
        if saved.status not in {"saved", "already_saved"} or not saved.card_id:
            _work_card_publish_notice(
                bridge, f"项目 {project_id} 的不可变工作卡快照未能确认保存；pending 保留。",
            )
            return False
        record = saved.record or ledger.get_by_request_id(request_id)
        focus_keys = ("latest_item_id", "latest_requirement_id", "latest_run_id", "latest_result_id")
        has_focus_intent = all(key in intent for key in focus_keys)
        expected_focus = ({
            "item_id": intent["latest_item_id"],
            "requirement_id": intent["latest_requirement_id"],
            "result_id": intent["latest_result_id"],
        } if has_focus_intent else None)
        if (type(record) is not dict or record.get("card_id") != saved.card_id
                or record.get("snapshot", {}).get("kind") != "project_scoped"
                or record.get("snapshot", {}).get("project_id") != project_id
                or record.get("snapshot", {}).get("session_id") != session.conversation_id
                or record.get("snapshot", {}).get("change_focus") != expected_focus):
            _work_card_publish_notice(bridge, f"项目 {project_id} 工作卡身份不匹配；pending 保留。")
            return False
        marked = durable_outbox.mark_card_saved(
            project_id=project_id, generation=intent["generation"],
            request_id=request_id, card_id=saved.card_id,
        )
        if marked not in {"saved", "already_saved"}:
            _work_card_publish_notice(
                bridge, "不可变卡片已保存，但待投递阶段无法确认；原 pending 保留，未宣称送达。",
            )
            return False
        refreshed = next((row for row in durable_outbox.list_intents()
                          if row["project_id"] == project_id and row["generation"] == intent["generation"]), None)
        if refreshed is None:
            return False
        return _retry_saved_project_scoped_card_reference(
            repl, bridge, session, store, refreshed,
        )
    except Exception:
        _work_card_publish_notice(
            bridge, f"项目 {project_id} 工作卡 source/投递链无法安全确认；pending 保留，未重做项目操作。",
        )
        return False


def _schedule_current_dialogue_project_work_cards(
    *, repl: ConversationRepl, bridge: WsBridge, session: Session,
    project_ids: tuple[str, ...], defer_immediate: bool = False,
) -> int:
    """Coalesce current-dialogue project updates on a first-update 30-second deadline."""
    if (repl.session is not session or getattr(repl.agent, "session", None) is not session
            or bridge._connection_closed.is_set() or not project_ids):
        return 0
    try:
        from work_card_update_coalescer import ProjectWorkCardUpdateCoalescer
        from work_card_publish_outbox import ProjectScopedCardOutbox
        from work_card_project_scope import (
            build_project_scoped_work_record_cards, project_scoped_snapshot_request_id,
        )
        from work_records import WorkRecordStore

        store = WorkRecordStore(session)
        durable_outbox = ProjectScopedCardOutbox(session, store)
        coalescer = bridge._work_card_update_coalescer
        if coalescer is None:
            coalescer = ProjectWorkCardUpdateCoalescer(delay_seconds=PROJECT_WORK_CARD_MERGE_SECONDS)
            bridge._work_card_update_coalescer = coalescer
        scheduled = 0
        for project_id in tuple(dict.fromkeys(project_ids)):
            if type(project_id) is not str or not project_id:
                continue
            snapshot = build_project_scoped_work_record_cards(store, project_id)
            if snapshot.completeness != "complete" or not snapshot.cards or snapshot.result_count <= 0:
                continue
            request_id = project_scoped_snapshot_request_id(snapshot)
            intent = durable_outbox.record_update(
                project_id, request_id, window_seconds=PROJECT_WORK_CARD_MERGE_SECONDS,
            )
            if intent["request_id"] != request_id:
                continue
            if intent["state"] == "reference_queued":
                continue
            remaining = (0.0 if intent["state"] == "card_saved"
                         else durable_outbox.remaining_seconds(intent))
            key = f"current-dialogue:{session.conversation_id}:{project_id}"
            coalescer.schedule(
                key,
                lambda project_id=project_id: _publish_current_dialogue_project_work_card(
                    repl, bridge, session, store, project_id,
                ),
                immediate=remaining <= 0 and not defer_immediate,
                delay_seconds=max(remaining, 0.001),
            )
            scheduled += 1
        return scheduled
    except Exception:
        _work_card_publish_notice(
            bridge, "当前对话项目卡 30 秒合并计时未能安全接入；source/result 保留，未重做项目操作。",
        )
        return 0


def _resume_current_dialogue_project_work_cards(
    repl: ConversationRepl, bridge: WsBridge, session: Session, store: Any,
) -> int:
    """Resume persisted timers and unqueued saved cards; never restart project workers."""
    if (repl.session is not session or getattr(repl.agent, "session", None) is not session
            or getattr(store, "_session", None) is not session
            or bridge._connection_closed.is_set()):
        return 0
    try:
        from work_card_ledger import WorkCardLedger
        from work_card_publish_outbox import ProjectScopedCardOutbox
        from work_card_project_scope import (
            build_project_scoped_work_record_cards,
            list_project_scoped_project_ids,
            project_scoped_snapshot_request_id,
        )

        ledger = WorkCardLedger(session, store)
        durable_outbox = ProjectScopedCardOutbox(session, store)
        intents = durable_outbox.list_intents()
        pending: list[str] = []
        recovered = 0
        handled_saved: set[tuple[str, int]] = set()

        # Retry saved-but-not-yet-queued cards from their own immutable snapshots,
        # even if a newer source update now exists for the same project.
        for intent in intents:
            if intent["state"] != "card_saved":
                continue
            identity = (intent["project_id"], intent["generation"])
            handled_saved.add(identity)
            if _retry_saved_project_scoped_card_reference(
                repl, bridge, session, store, intent, emit_event=False,
            ):
                recovered += 1

        for project_id in list_project_scoped_project_ids(store):
            snapshot = build_project_scoped_work_record_cards(store, project_id)
            if snapshot.completeness != "complete" or not snapshot.cards or snapshot.result_count <= 0:
                continue
            request_id = project_scoped_snapshot_request_id(snapshot)
            intent = next((row for row in intents
                           if row["project_id"] == project_id and row["request_id"] == request_id), None)
            existing = ledger.get_by_request_id(request_id)
            if existing is not None:
                if intent is not None and intent["state"] == "reference_queued":
                    continue  # durable history is the retry queue; this is not a delivery receipt
                if intent is not None and intent["state"] == "card_saved":
                    identity = (project_id, intent["generation"])
                    if identity not in handled_saved and _retry_saved_project_scoped_card_reference(
                        repl, bridge, session, store, intent, emit_event=False,
                    ):
                        recovered += 1
                    continue
                if intent is not None and intent["state"] == "pending":
                    parent_run_id = _current_dialogue_card_parent_run_id(snapshot)
                    if parent_run_id is None:
                        pending.append(project_id)
                        continue
                    marked = durable_outbox.mark_card_saved(
                        project_id=project_id, generation=intent["generation"],
                        request_id=request_id, card_id=existing["card_id"],
                    )
                    if marked not in {"saved", "already_saved"}:
                        continue
                    refreshed = next((row for row in durable_outbox.list_intents()
                                      if row["project_id"] == project_id
                                      and row["generation"] == intent["generation"]), None)
                    if refreshed is not None and _retry_saved_project_scoped_card_reference(
                        repl, bridge, session, store, refreshed, emit_event=False,
                    ):
                        recovered += 1
                    continue
                # Pre-outbox immutable cards remain replayable through the established path.
                parent_run_id = _current_dialogue_card_parent_run_id(snapshot)
                if parent_run_id is not None and _emit_work_card_reference(
                    repl, bridge, session,
                    _work_card_presentation_id(session.conversation_id, parent_run_id, existing["card_id"]),
                    existing["card_id"], parent_run_id, active_run_id=None, emit_event=False,
                ):
                    recovered += 1
                continue
            pending.append(project_id)
        return recovered + _schedule_current_dialogue_project_work_cards(
            repl=repl, bridge=bridge, session=session, project_ids=tuple(pending),
            defer_immediate=True,
        )
    except Exception:
        _work_card_publish_notice(
            bridge, "当前对话项目卡恢复状态无法安全确认；待处理事实保留，未重做项目操作。",
        )
        return 0


def _retry_saved_independent_work_card_reference(
    *, repl: ConversationRepl, bridge: WsBridge, session: Session, store: Any,
    intent: dict[str, Any], emit_event: bool = True,
) -> bool:
    try:
        from independent_work_card_publication import IndependentWorkCardPublicationOutbox
        from work_card_ledger import WorkCardLedger

        outbox = IndependentWorkCardPublicationOutbox(session, store)
        if not outbox.validate(intent) or intent.get("state") != "card_saved":
            return False
        ledger = WorkCardLedger(session, store)
        record = ledger.get_card(intent["card_id"])
        public = _public_work_card(record)
        expected_focus = {
            "item_id": intent["latest_item_id"],
            "requirement_id": intent["latest_requirement_id"],
            "result_id": intent["result_id"],
        }
        stored_focus = public.get("snapshot", {}).get("change_focus") if public is not None else None
        if (public is None or public.get("snapshot", {}).get("kind") != "independent"
                or public.get("snapshot", {}).get("item_id") != intent["latest_item_id"]
                or public.get("snapshot", {}).get("session_id") != session.conversation_id
                or (stored_focus is not None and stored_focus != expected_focus)):
            _work_card_publish_notice(bridge, "独立事项不可变卡片身份无法复核；持久 intent 保留。")
            return False
        run_id = intent["latest_run_id"]
        presentation_id = _work_card_presentation_id(session.conversation_id, run_id, intent["card_id"])
        queued = _emit_work_card_reference(
            repl, bridge, session, presentation_id, intent["card_id"], run_id,
            active_run_id=None,
            after_durable_queue=lambda: outbox.mark_queued(
                intent_id=intent["intent_id"], generation=intent["generation"],
                card_id=intent["card_id"],
            ),
            emit_event=emit_event,
        )
        return queued
    except Exception:
        _work_card_publish_notice(
            bridge, "独立事项卡片引用未能确认排队；不可变卡片及 intent 保留，不重做任务。",
        )
        return False


def _publish_latest_independent_work_card(
    *, repl: ConversationRepl, bridge: WsBridge, session: Session, store: Any, item_id: str,
    emit_event: bool = True,
) -> bool:
    try:
        from independent_work_card_publication import IndependentWorkCardPublicationOutbox
        from work_card_ledger import WorkCardLedger
        from work_record_cards import bind_work_card_change_focus, build_independent_work_record_card

        if (repl.session is not session or getattr(repl.agent, "session", None) is not session
                or getattr(store, "_session", None) is not session
                or bridge._connection_closed.is_set()):
            return False
        store._validate_scope()
        outbox = IndependentWorkCardPublicationOutbox(session, store)
        intent = next((row for row in outbox.list_pending()
                       if row["latest_item_id"] == item_id), None)
        if intent is None or not outbox.validate(intent):
            return False
        if intent["state"] == "card_saved":
            return _retry_saved_independent_work_card_reference(
                repl=repl, bridge=bridge, session=session, store=store, intent=intent,
                emit_event=emit_event,
            )
        item = store.get_item(item_id)
        if (type(item) is not dict or item.get("item_id") != item_id
                or item.get("scope") != {"kind": "independent", "object_ref": None}):
            _work_card_publish_notice(bridge, "独立事项 scope 已变化；卡片 intent 保留，未改写其他对象。")
            return False
        requirements = item.get("requirements")
        matching = [row for row in requirements if type(row) is dict
                    and row.get("requirement_id") == intent["latest_requirement_id"]]
        if len(matching) != 1 or matching[0].get("current_run_id") != intent["latest_run_id"]:
            _work_card_publish_notice(bridge, "独立事项当前 run 与待发布身份不一致；intent 保留。")
            return False
        run = next((row for row in item.get("runs", []) if type(row) is dict
                    and row.get("result_id") == intent["result_id"]
                    and row.get("run_id") == intent["latest_run_id"]
                    and row.get("requirement_id") == intent["latest_requirement_id"]), None)
        host = run.get("verification", {}).get("host_execution") if type(run) is dict else None
        if (type(run) is not dict or run.get("status") != "unknown"
                or type(host) is not dict or type(host.get("sequence")) is not int or host["sequence"] < 1):
            _work_card_publish_notice(bridge, "待发布结果缺少精确、可信的 host settlement；intent 保留。")
            return False

        snapshot = build_independent_work_record_card(store, item_id)
        if (snapshot.completeness != "complete" or snapshot.kind != "independent"
                or snapshot.session_id != session.conversation_id or snapshot.item_id != item_id
                or snapshot.card is None):
            _work_card_publish_notice(bridge, "独立事项快照不完整或身份不匹配；intent 保留，未发布。")
            return False
        snapshot_requirement = next((row for row in snapshot.card.requirements
                                     if row.requirement_id == intent["latest_requirement_id"]), None)
        latest = snapshot_requirement.latest_result if snapshot_requirement is not None else None
        if (latest is None or latest.run_id != intent["latest_run_id"]
                or latest.result_id != intent["result_id"] or latest.status != "unknown"
                or latest.verification_status != "not_verified"):
            _work_card_publish_notice(bridge, "独立事项快照未包含匹配的未知/未经验证结果；intent 保留。")
            return False
        ledger = WorkCardLedger(session, store)
        snapshot = bind_work_card_change_focus(snapshot, intent)
        saved = ledger.save_snapshot(intent["request_id"], snapshot)
        if saved.status not in {"saved", "already_saved"} or not saved.card_id:
            _work_card_publish_notice(bridge, "独立事项不可变卡片保存未确认；持久 intent 保留。")
            return False
        public = _public_work_card(saved.record or ledger.get_card(saved.card_id))
        saved_focus = public.get("snapshot", {}).get("change_focus") if public is not None else None
        if saved_focus is not None and saved_focus != {
            "item_id": intent["latest_item_id"],
            "requirement_id": intent["latest_requirement_id"],
            "result_id": intent["result_id"],
        }:
            _work_card_publish_notice(bridge, "独立事项快照变化焦点与持久 intent 不一致；保留 intent。")
            return False
        marked = outbox.bind_card(
            intent_id=intent["intent_id"], generation=intent["generation"], card_id=saved.card_id,
        )
        if marked not in {"saved", "already_saved"}:
            _work_card_publish_notice(bridge, "独立事项卡片已保存但 intent 阶段未确认；将按原 request id 恢复。")
            return False
        refreshed = next((row for row in outbox.list_pending()
                          if row["intent_id"] == intent["intent_id"]
                          and row["generation"] == intent["generation"]), None)
        if refreshed is None:
            return False
        return _retry_saved_independent_work_card_reference(
            repl=repl, bridge=bridge, session=session, store=store, intent=refreshed,
            emit_event=emit_event,
        )
    except Exception as exc:
        _work_card_publish_notice(
            bridge, f"独立事项卡片 source/投递链无法安全确认（{type(exc).__name__}）；pending 保留，不重做任务。",
        )
        return False


def _schedule_independent_work_card(
    *, repl: ConversationRepl, bridge: WsBridge, session: Session, store: Any, item_id: str,
    emit_event: bool = True,
) -> bool:
    if (repl.session is not session or getattr(repl.agent, "session", None) is not session
            or getattr(store, "_session", None) is not session
            or bridge._connection_closed.is_set()):
        return False
    try:
        from independent_work_card_publication import IndependentWorkCardPublicationOutbox
        outbox = IndependentWorkCardPublicationOutbox(session, store)
        all_rows = outbox.list_all()
        pending = next((row for row in reversed(all_rows)
                        if row["latest_item_id"] == item_id
                        and row["state"] in {"pending", "card_saved"}), None)
        if pending is None:
            # Duplicate settlement/replay after a durable reference is already queued.
            return any(row["latest_item_id"] == item_id and row["state"] == "reference_queued"
                       for row in all_rows)
        remaining = 0.0 if pending["state"] == "card_saved" else outbox.remaining_seconds(pending)
        from work_card_update_coalescer import ProjectWorkCardUpdateCoalescer
        coalescer = bridge._work_card_update_coalescer
        if coalescer is None:
            coalescer = ProjectWorkCardUpdateCoalescer(delay_seconds=max(remaining, 0.001))
            bridge._work_card_update_coalescer = coalescer
        coalescer.schedule(
            f"independent:{session.conversation_id}:{item_id}",
            lambda: _publish_latest_independent_work_card(
                repl=repl, bridge=bridge, session=session, store=store, item_id=item_id,
                emit_event=emit_event,
            ),
            immediate=remaining <= 0,
            delay_seconds=max(remaining, 0.001),
        )
        return True
    except Exception:
        _work_card_publish_notice(
            bridge, "独立事项已结算，但 30 秒卡片 pending/计时无法确认；待恢复，不重做任务。",
        )
        return False


def _resume_independent_work_cards(
    repl: ConversationRepl, bridge: WsBridge, session: Session, store: Any,
) -> int:
    if (repl.session is not session or getattr(repl.agent, "session", None) is not session
            or getattr(store, "_session", None) is not session
            or bridge._connection_closed.is_set()):
        return 0
    try:
        from independent_work_card_publication import IndependentWorkCardPublicationOutbox
        outbox = IndependentWorkCardPublicationOutbox(session, store)
        recovered = 0
        for intent in outbox.list_pending():
            if intent["state"] == "card_saved":
                if _retry_saved_independent_work_card_reference(
                    repl=repl, bridge=bridge, session=session, store=store, intent=intent,
                    emit_event=False,
                ):
                    recovered += 1
            else:
                _schedule_independent_work_card(
                    repl=repl, bridge=bridge, session=session, store=store,
                    item_id=intent["latest_item_id"], emit_event=False,
                )
        return recovered
    except Exception:
        _work_card_publish_notice(bridge, "独立事项卡片 pending 恢复无法确认；记录保留且未重做任务。")
        return 0


def _schedule_settled_independent_work_card(
    *, repl: ConversationRepl, bridge: WsBridge, session: Session, store: Any, binding: Any,
) -> bool:
    """Schedule only the exact explicit-capture independent item after durable settlement."""
    if (type(getattr(binding, "capture_request_id", None)) is not str
            or getattr(binding, "session_id", None) != session.conversation_id
            or getattr(store, "_session", None) is not session):
        return False
    try:
        item = store.get_item(binding.item_id)
        if (type(item) is not dict or item.get("scope") != {"kind": "independent", "object_ref": None}):
            return False
        return _schedule_independent_work_card(
            repl=repl, bridge=bridge, session=session, store=store, item_id=binding.item_id,
        )
    except Exception:
        _work_card_publish_notice(bridge, "独立事项已结算，但 item 绑定无法复核；没有发布卡片。")
        return False


def _schedule_settled_project_work_card(
    *, repl: ConversationRepl, bridge: WsBridge, session: Session,
    store: Any, binding: Any,
) -> bool:
    """Publish only after a server-bound settlement was durably recorded.

    The timer coalesces per project from the first update. At its deadline it
    reads the latest locked WorkRecordStore snapshot, persists an immutable card,
    then uses the existing durable-reference/WebSocket publication path. It does
    not infer requirement success from lifecycle settlement or replay project work.
    """
    project_id = getattr(getattr(session, "meta", None), "project_id", "")
    if (type(project_id) is not str or not project_id
            or getattr(repl, "session", None) is not session
            or getattr(getattr(repl, "agent", None), "session", None) is not session
            or getattr(store, "_session", None) is not session):
        return False
    try:
        item = store.get_item(binding.item_id)
        scope = item.get("scope") if type(item) is dict else None
        if (type(scope) is not dict or scope.get("kind") != "project"
                or scope.get("project_id") != project_id):
            return False
        from work_card_publish_outbox import WorkCardPublicationOutbox
        outbox = WorkCardPublicationOutbox(session, store)
        run_id = uuid.UUID(binding.run_id).hex
        pending = next((intent for intent in outbox.list_pending()
                        if intent["latest_item_id"] == binding.item_id
                        and intent["latest_requirement_id"] == binding.requirement_id
                        and intent["latest_run_id"] == run_id), None)
        if pending is None:
            _work_card_publish_notice(
                bridge, "本回合结果已保存，但原子待发布 intent 不存在/无法匹配；未重做项目操作。",
            )
            return False
        remaining = outbox.remaining_seconds(pending)
        from work_card_update_coalescer import ProjectWorkCardUpdateCoalescer
        coalescer = bridge._work_card_update_coalescer
        if coalescer is None:
            coalescer = ProjectWorkCardUpdateCoalescer(
                delay_seconds=max(remaining, 0.001),
            )
            bridge._work_card_update_coalescer = coalescer

        def publish_latest_snapshot() -> None:
            _publish_pending_project_work_cards(repl, bridge, session, store)

        coalescer.schedule(
            project_id, publish_latest_snapshot, immediate=remaining <= 0,
        )
        return True
    except Exception:
        _work_card_publish_notice(
            bridge, "工作记录已保存，但持久待发布标记/计时未能确认；卡片可能未提醒，项目操作不会重做。",
        )
        return False


def _finish_work_record_capture_turn(
    bridge: WsBridge, run_id: str, *, finish_reason: str,
    work_record_binding: tuple[Any, Any] | None = None,
    current_session: Session | None = None,
    repl: ConversationRepl | None = None,
) -> bool:
    """Close an abandoned pre-run capture turn, but never touch a replacement run."""
    snapshot = bridge.lifecycle.snapshot()
    if (
        snapshot is None
        or snapshot.run_id != run_id
        or snapshot.state not in {"queued", "running", "stopping"}
    ):
        return False
    reason = bridge.resolve_cancel_finish_reason() or finish_reason
    ok = reason not in {"cancelled", "timeout"} and finish_reason != "error"
    bridge._turn_busy.clear()
    bridge.end_turn()
    final_snapshot = _finish_execution_safely(
        bridge, run_id=run_id, finish_reason=reason, ok=ok,
    )
    if (work_record_binding is not None and current_session is not None
            and final_snapshot is not None and final_snapshot.run_id == run_id
            and final_snapshot.state in {"settled", "paused", "failed"}):
        store, binding = work_record_binding
        try:
            _settle_work_record_run(
                current_session=current_session, bridge=bridge,
                store=store, binding=binding, repl=repl,
                emit=lambda event: _emit_safely(
                    bridge.emit_turn_event, event, "work-record settlement notice failed",
                ),
            )
        except Exception as exc:
            _log_notification_failure("work-record settlement or excerpt freeze failed", exc)
    return final_snapshot is not None and final_snapshot.run_id == run_id and final_snapshot.state in {"settled", "paused", "failed"}


def _begin_work_record_capture_turn(repl: ConversationRepl, bridge: WsBridge) -> str | None:
    """Start the ordinary run before record consent so turn.cancel is authoritative."""
    from session import is_terminal_harness

    execution_mode = "terminal" if is_terminal_harness(repl.session.meta) else "ordinary"
    if not bridge.begin_turn(mode=execution_mode):
        return None
    snapshot = bridge.lifecycle.snapshot()
    if snapshot is None:
        return None
    bridge.lifecycle_conversation_id = repl.session.conversation_id
    bridge._turn_busy.set()
    return snapshot.run_id


class WsSessionHandler:
    # Fail closed until the store accepts the lifecycle's exact host run ID and
    # the full synthetic WebSocket acceptance gate has passed.
    _post_turn_commit_ready = False
    # Only explicitly recorded, host-bound runs may offer a local excerpt confirmation.
    _explicit_run_excerpt_ready = True
    PROJECT_JOURNAL_ENABLE_PHRASE = "\u5f00\u542f\u9879\u76ee\u65e5\u8bb0\u8bb0\u5f55"
    PROJECT_JOURNAL_DISABLE_PHRASE = "\u5173\u95ed\u9879\u76ee\u65e5\u8bb0\u8bb0\u5f55"

    def __init__(
        self,
        paths: AgentPaths,
        *,
        evolution_client: Any | None = None,
        evolution_scope_id: str = "",
        evolution_owned_paths: tuple[str, ...] = (),
        managed_readonly_context: Any | None = None,
        work_record_controller_factory: WorkRecordControllerFactory | None = None,
        project_boundary_recorder: Callable[..., Any] | None = None,
        project_boundary_journal: Any | None = None,
        day_journal_intent_service_factory: Callable[..., Any] | None = None,
        day_journal_query: Callable[..., dict[str, Any]] | None = None,
    ) -> None:
        self.paths = paths
        self.evolution_client = evolution_client
        self.evolution_scope_id = evolution_scope_id
        self.evolution_owned_paths = tuple(evolution_owned_paths)
        self.managed_readonly_context = managed_readonly_context
        self.work_record_controller_factory = work_record_controller_factory
        self.project_boundary_recorder = project_boundary_recorder
        self.project_boundary_journal = project_boundary_journal
        self.day_journal_intent_service_factory = day_journal_intent_service_factory
        self.day_journal_query = day_journal_query
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
            session,
            self.paths,
            bridge,
            evolution_client=self.evolution_client,
            work_record_controller_factory=self.work_record_controller_factory,
        )

        sender_task = asyncio.create_task(self._sender(websocket, outbox, bridge))
        bridge.emit_session_state(repl.session)
        emit_corruption_notices(bridge.emit, repl.session)
        _emit_pending_work_card_references(repl, bridge)

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
                # Do not await natural-language routing here: sibling tasks must deliver
                # prompt.response, confirm.response, and run-bound stop immediately.
                # Active natural messages serialize themselves through the bridge FIFO lock.
                asyncio.create_task(self._handle_incoming(message, repl, bridge))
        finally:
            bridge.mark_connection_closed()
            bridge.cancel_overview_query()
            if bridge._work_card_update_coalescer is not None:
                bridge._work_card_update_coalescer.close()
            await outbox.put(None)
            await sender_task

    async def _handle_project_journal_command(
        self, text: str, bridge: WsBridge, conversation_id: str = ""
    ) -> bool:
        """Run a control command as a real, serialized ordinary turn."""
        if not bridge.begin_turn(mode="ordinary"):
            return True
        snapshot = bridge.lifecycle.snapshot()
        if snapshot is not None:
            bridge.lifecycle_conversation_id = conversation_id
        bridge._turn_busy.set()
        ok = True
        try:
            await self._apply_project_journal_command(text, bridge)
        except Exception:
            ok = False
            bridge.project_journal_enabled = False
            bridge.emit({"type": "turn.notice", "level": "warn", "text": "\u9879\u76ee\u65e5\u8bb0\u64cd\u4f5c\u5f02\u5e38\uff1b\u666e\u901a\u5bf9\u8bdd\u4ecd\u53ef\u7ee7\u7eed\u3002"})
        finally:
            bridge._turn_busy.clear()
            bridge.end_turn()
            if bridge.finish_execution("completed" if ok else "error", ok=ok):
                bridge.emit_turn_event({"type": "turn.end", "ok": ok, "finish_reason": "completed" if ok else "error"})
        return True

    async def _apply_project_journal_command(self, text: str, bridge: WsBridge) -> bool:
        """Handle only exact phrases; confirmation and state are connection scoped."""
        if text == self.PROJECT_JOURNAL_DISABLE_PHRASE:
            bridge.project_journal_enabled = False
            bridge.emit({"type": "turn.notice", "level": "info", "text": "\u9879\u76ee\u65e5\u8bb0\u8bb0\u5f55\u5df2\u5728\u672c\u8fde\u63a5\u5173\u95ed\u3002"})
            return True
        if text != self.PROJECT_JOURNAL_ENABLE_PHRASE:
            return False

        preview = (
            "\u5f00\u542f\u672c\u8fde\u63a5\u7684\u9879\u76ee\u65e5\u8bb0\u8bb0\u5f55\uff1f\u4ec5\u5728\u4e4b\u540e\u6210\u529f\u5207\u6362\u9879\u76ee\u65f6\uff0c"
            "\u5c06\u9879\u76eeID\u3001\u4f1a\u8bddID\u548c\u5207\u6362\u65f6\u95f4\u5199\u5165\u672c\u5730\u65e5\u8bb0\uff1b"
            "\u4e0d\u4f1a\u53d1\u9001\u7ed9\u6a21\u578b\uff0c\u4e5f\u4e0d\u4f1a\u626b\u63cf\u6216\u56de\u586b\u65e7\u5386\u53f2\u3002"
            "\u53ea\u5bf9\u672c\u8fde\u63a5\u751f\u6548\uff0c\u65ad\u5f00\u540e\u81ea\u52a8\u5173\u95ed\u3002"
        )
        try:
            choice, _receipt = await asyncio.to_thread(bridge.confirm_with_receipt, preview, False)
            if not isinstance(choice, str) or choice.strip().casefold() not in {"y", "yes", "confirm", "allow"}:
                bridge.project_journal_enabled = False
                bridge.emit({"type": "turn.notice", "level": "info", "text": "\u9879\u76ee\u65e5\u8bb0\u8bb0\u5f55\u672a\u542f\u7528\uff1b\u666e\u901a\u5bf9\u8bdd\u4e0d\u53d7\u5f71\u54cd\u3002"})
                return True
            if self.project_boundary_recorder is None and self.project_boundary_journal is None:
                from day_journal import DayJournal
                from day_journal_events import record_project_boundary

                self.project_boundary_journal = DayJournal(self.paths.data / "day-journal")
                self.project_boundary_recorder = record_project_boundary
            if self.project_boundary_recorder is None or self.project_boundary_journal is None:
                raise RuntimeError("journal dependencies are incomplete")
            bridge.project_journal_enabled = True
            bridge.emit({"type": "turn.notice", "level": "info", "text": "\u9879\u76ee\u65e5\u8bb0\u8bb0\u5f55\u5df2\u4e3a\u672c\u8fde\u63a5\u542f\u7528\uff1b\u4ec5\u8bb0\u5f55\u4e4b\u540e\u6210\u529f\u5207\u6362\u7684\u9879\u76ee\u3002"})
        except Exception:
            bridge.project_journal_enabled = False
            bridge.emit({"type": "turn.notice", "level": "warn", "text": "\u9879\u76ee\u65e5\u8bb0\u672a\u80fd\u5b89\u5168\u542f\u7528\uff1b\u666e\u901a\u5bf9\u8bdd\u4e0d\u53d7\u5f71\u54cd\u3002"})
        return True

    def _record_project_switch_for_connection(
        self, bridge: WsBridge | None, session: Session, switch_event: dict[str, Any] | None,
        report: Callable[[str], None] | None = None,
    ) -> bool:
        if bridge is None:
            return False
        if self.project_boundary_recorder is None or self.project_boundary_journal is None:
            from day_journal import DayJournal
            from day_journal_events import record_project_boundary

            self.project_boundary_journal = DayJournal(self.paths.data / "day-journal")
            self.project_boundary_recorder = record_project_boundary

        def fail_closed_notice(_text: str) -> None:
            if report is not None:
                report("\u9879\u76ee\u5207\u6362\u5df2\u5b8c\u6210\uff0c\u4f46\u672c\u5730\u65e5\u8bb0\u5199\u5165\u5931\u8d25\u3002")

        return _record_project_switch_boundary(
            session=session,
            switch_event=switch_event,
            recorder=self.project_boundary_recorder,
            journal=self.project_boundary_journal,
            report=fail_closed_notice,
        )

    async def _classify_day_journal_intent(
        self, text: str, repl: ConversationRepl, bridge: WsBridge
    ) -> dict[str, Any]:
        """Classify only locally gated unsupported diary queries, after confirmation."""
        from day_journal_intent import DayJournalIntentService
        from llm_client import LLMClient

        conversation_id = repl.session.conversation_id
        def current_context_token() -> str:
            current = repl.session
            serialized = json.dumps(current.messages, ensure_ascii=False, sort_keys=True, default=str)
            revision = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
            return f"{id(bridge)}:{current.conversation_id}:{len(current.messages)}:{revision}"

        context_token = current_context_token()

        def confirm_outbound(preview: dict[str, Any]) -> bool:
            rendered = json.dumps(preview, ensure_ascii=False, indent=2, sort_keys=True)
            prompt = (
                "将向 0x567-flash / gpt-6-luna 外发以下完整内容，仅用于识别日记日期；"
                "不会提供日记文件或执行工具：\n\n" + rendered
            )
            choice, _receipt = bridge.confirm_with_receipt(prompt, False)
            return isinstance(choice, str) and choice.strip().casefold() in {"y", "yes", "confirm", "allow"}

        try:
            factory = getattr(self, "day_journal_intent_service_factory", None)
            if factory is not None:
                service = factory(client=None, confirm=confirm_outbound, current_context_token=current_context_token)
            else:
                client = LLMClient(paths=self.paths)
                client.set_cancel_event(bridge.cancel_event)
                service = DayJournalIntentService(
                    client=client, confirm=confirm_outbound, current_context_token=current_context_token
                )
            today = datetime.now(_shanghai_timezone()).date()
            result = await asyncio.to_thread(
                service.classify, text=text, today=today, timezone="Asia/Shanghai", context_token=context_token
            )
            if isinstance(result, dict) and result.get("action") in {"none", "uncertain", "recall"}:
                return result
        except Exception:
            pass
        return {"action": "uncertain", "start_date": None, "end_date": None}

    async def _handle_day_journal_summary(
        self,
        text: str,
        repl: ConversationRepl,
        bridge: WsBridge,
        *,
        request_id: Any = None,
        persist_user_question: bool = True,
        ws_done: dict[str, Any] | None = None,
    ) -> None:
        """Summarize one unambiguous, current-session requirement into an existing day entry."""
        if not bridge.begin_turn(mode="ordinary"):
            return
        snapshot_lifecycle = bridge.lifecycle.snapshot()
        if snapshot_lifecycle is not None:
            bridge.lifecycle_conversation_id = repl.session.conversation_id
        bridge._turn_busy.set()
        session = repl.session
        session_id = session.conversation_id
        ok = True
        finish_reason = "completed"
        response = ""
        question_persisted = False
        journal_saved = False
        journal_publish_started = False
        try:
            try:
                if persist_user_question:
                    if not _append_day_journal_question(
                        session, repl, text,
                        on_persisted=lambda persisted: _emit_day_journal_user_message_persisted(
                            bridge=bridge, repl=repl, session=persisted, request_id=request_id,
                        ),
                    ):
                        raise _DayJournalQuestionPersistenceFailure
                    question_persisted = True
                _raise_if_day_journal_cancelled(bridge)
                from day_journal import DayJournal
                from day_journal_project_summary import discover_same_day_requirement_keys
                from day_journal_summary import DayJournalSummaryService
                from day_journal_summary_accept import SummaryAcceptanceError, accept_summary_candidate
                from day_journal_summary_sources import DayJournalSummarySourceSelector
                from llm_client import LLMClient
                from work_records import WorkRecordStore

                project_id = getattr(session.meta, "project_id", "")
                if not isinstance(project_id, str) or not project_id or not isinstance(session_id, str) or not session_id:
                    response = "当前对话没有唯一绑定的项目和会话；未读取来源、未外发、未保存。"
                else:
                    zone = _shanghai_timezone()
                    at = datetime.now(zone)
                    day_key = at.date().isoformat()
                    journal = self.project_boundary_journal
                    if journal is None:
                        journal = DayJournal(self.paths.data / "day-journal")
                    if type(journal) is not DayJournal:
                        raise RuntimeError("untrusted journal")
                    day_record = await asyncio.to_thread(journal.read_day, day_key)
                    _raise_if_day_journal_cancelled(bridge)
                    if (not isinstance(day_record, dict) or day_record.get("date") != day_key
                            or project_id not in day_record.get("projects", [])):
                        response = "今天没有当前项目的既有日记条目；不会新建条目，也未调用模型。"
                    else:
                        segments = day_record.get("segments")
                        has_current_boundary = isinstance(segments, list) and any(
                            isinstance(segment, dict)
                            and segment.get("date") == day_key
                            and segment.get("project_id") == project_id
                            and segment.get("summary") is None
                            and "summary_metadata" not in segment
                            and segment.get("source") == {"kind": "session", "session_id": session_id}
                            and isinstance(segment.get("segment_id"), str)
                            and bool(segment["segment_id"])
                            for segment in segments
                        )
                        if not has_current_boundary:
                            response = "今天的日记没有当前项目与当前会话的明确边界记录；未读取候选来源，也未调用模型。"
                        else:
                            store = WorkRecordStore(session)
                            selector = DayJournalSummarySourceSelector(store, session, timezone_name="Asia/Shanghai")

                            def discover_candidates(items: list[dict[str, Any]] | None = None) -> list[tuple[str, str]] | None:
                                return discover_same_day_requirement_keys(
                                    store,
                                    project_id=project_id,
                                    day_key=day_key,
                                    zone=zone,
                                    items=items,
                                )

                            candidate_keys = discover_candidates()
                            if candidate_keys is None or not candidate_keys:
                                response = "今天的当前项目需求来源无效、缺失或超过20条上限；未调用模型。"
                            else:
                                snapshots = []
                                invalid = False
                                for item_id, event_id in candidate_keys:
                                    snapshot = selector.select(day=day_key, project_id=project_id,
                                                               item_id=item_id, event_id=event_id)
                                    if snapshot.status != "ready" or not selector.validate(snapshot):
                                        invalid = True
                                        break
                                    snapshots.append(snapshot)
                                if invalid or len(snapshots) != len(candidate_keys):
                                    response = "至少一条当前项目需求来源无法通过独立验证；未调用模型。"
                                else:
                                    sources = [source for snapshot in snapshots for source in snapshot.sources]
                                    source_ids = [source["source_id"] for source in sources]
                                    if (not sources or len(sources) > 20 or len(set(source_ids)) != len(source_ids)):
                                        response = "可核验来源无效或超出20条处理上限；未调用模型。"
                                    elif sum(len(source["text"]) for source in sources) > 6000:
                                        response = "来源原文总长超过6000字符上限；未截断、未外发、未保存。"
                                    else:
                                        session_identity = session_id
                                        def current_context_token() -> str:
                                            current = repl.session
                                            if (current is not session or current.conversation_id != session_identity
                                                    or getattr(current.meta, "project_id", "") != project_id):
                                                return "stale-context"
                                            body = json.dumps(current.messages, ensure_ascii=False, sort_keys=True, default=str)
                                            current_keys = discover_candidates()
                                            if current_keys != candidate_keys:
                                                return "stale-context"
                                            for saved_snapshot in snapshots:
                                                if not selector.validate(saved_snapshot):
                                                    return "stale-context"
                                            current_day = journal.read_day(day_key)
                                            if (not isinstance(current_day, dict)
                                                    or current_day.get("revision") != day_record.get("revision")
                                                    or project_id not in current_day.get("projects", [])):
                                                return "stale-context"
                                            payload = {
                                                "conversation_id": session_identity,
                                                "project_id": project_id,
                                                "messages_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
                                                "source_fingerprints": [snapshot.fingerprint for snapshot in snapshots],
                                                "source_order": candidate_keys,
                                                "journal_revision": day_record.get("revision"),
                                            }
                                            return hashlib.sha256(json.dumps(
                                                payload, sort_keys=True, separators=(",", ":")
                                            ).encode("utf-8")).hexdigest()

                                        context_token = current_context_token()
                                        def confirm_outbound(preview: dict[str, Any]) -> bool:
                                            rendered = json.dumps(preview, ensure_ascii=False, indent=2, sort_keys=True)
                                            prompt = (
                                                "即将向 0x567-flash / gpt-6-luna 外发以下完整请求（含全部来源原文）。"
                                                "仅确认本次外发；不授权保存。拒绝或取消则不会调用模型：\n\n" + rendered
                                            )
                                            choice, _receipt = bridge.confirm_with_receipt(prompt, False)
                                            return isinstance(choice, str) and choice.strip().casefold() in {
                                                "y", "yes", "confirm", "allow"
                                            }

                                        client = LLMClient(paths=self.paths)
                                        client.set_cancel_event(bridge.cancel_event)
                                        factory = getattr(self, "day_journal_summary_service_factory", None)
                                        service = (factory(client=client, confirm=confirm_outbound,
                                                           current_context_token=current_context_token)
                                                   if factory is not None else DayJournalSummaryService(
                                                       client=client, confirm=confirm_outbound,
                                                       current_context_token=current_context_token))
                                        candidate = await asyncio.to_thread(
                                            service.generate, sources=sources, context_token=context_token
                                        )
                                        _raise_if_day_journal_cancelled(bridge)
                                        if candidate.get("status") != "candidate":
                                            response = str(candidate.get("notice") or "资料不足，未形成摘要。")
                                        elif not isinstance(candidate.get("summary"), str) or len(candidate["summary"]) > 6000:
                                            response = "模型调用已发生；摘要超过6000字符上限，摘要无效，未保存。"
                                        else:
                                            snapshot_descriptor = {
                                                "date": day_key, "project_id": project_id, "session_id": session_id,
                                                "sources": sources, "context_token": context_token,
                                            }
                                            def validate_snapshot(supplied: dict[str, Any]) -> bool:
                                                return (supplied == snapshot_descriptor
                                                        and discover_candidates() == candidate_keys
                                                        and all(selector.validate(saved) for saved in snapshots)
                                                        and current_context_token() == context_token)
                                            save_confirmation = {"asked": False, "approved": False}
                                            def confirm_save(preview: dict[str, Any]) -> bool:
                                                rendered = json.dumps(preview, ensure_ascii=False, indent=2, sort_keys=True)
                                                prompt = (
                                                    "请单独确认是否将该候选追加到今天已有的项目日记。"
                                                    "内容标记为未经核实；需求记录时间不代表工作完成或验证。"
                                                    "这是保存确认，不是外发确认：\n\n" + rendered
                                                )
                                                save_confirmation["asked"] = True
                                                if bridge.cancel_event.is_set():
                                                    return False
                                                choice, _receipt = bridge.confirm_with_receipt(prompt, False)
                                                approved = (not bridge.cancel_event.is_set()
                                                            and isinstance(choice, str)
                                                            and choice.strip().casefold() in {"y", "yes", "confirm", "allow"})
                                                save_confirmation["approved"] = approved
                                                return approved
                                            def finalize_with_locked_sources(publish):
                                                nonlocal journal_publish_started
                                                def commit_if_current(locked_items):
                                                    if discover_candidates(locked_items) != candidate_keys:
                                                        raise SummaryAcceptanceError("work-record source set changed before commit")
                                                    if not all(selector.validate_items(saved, locked_items) for saved in snapshots):
                                                        raise SummaryAcceptanceError("work-record snapshot changed before commit")
                                                    # This is the only nested lock: work-record then day-journal.
                                                    # No model call, confirmation, or context callback runs here.
                                                    journal_publish_started = True
                                                    return publish()
                                                return store.with_locked_items(commit_if_current)
                                            try:
                                                _raise_if_day_journal_cancelled(bridge)
                                                await asyncio.to_thread(
                                                    accept_summary_candidate, journal,
                                                    candidate=candidate, approval=confirm_save, at=at,
                                                    project_id=project_id, session_id=session_id,
                                                    snapshot=snapshot_descriptor, expected_revision=day_record["revision"],
                                                    segment_id="summary:" + uuid.uuid4().hex,
                                                    candidate_context_token=context_token,
                                                    current_context_token=current_context_token,
                                                    validate_snapshot=validate_snapshot,
                                                    finalize=finalize_with_locked_sources,
                                                )
                                            except SummaryAcceptanceError:
                                                if save_confirmation["asked"] and not save_confirmation["approved"]:
                                                    response = "保存确认未批准，候选摘要未写入日记。"
                                                else:
                                                    raise
                                            else:
                                                journal_saved = True
                                                source_labels = "、".join(source_ids)
                                                source_text = "\n".join(item["text"] for item in sources)
                                                response = (
                                                    "摘要已追加到今天的项目日记。注意：这是模型生成、未经核实的摘要，不代表需求已完成或验证。"
                                                    f"\n来源ID：{source_labels}"
                                                    f"\n来源内容（仅表示记录了需求；记录时间不代表实际发生时间）：\n{source_text}"
                                                    f"\n摘要：{candidate['summary']}"
                                                )
            except _DayJournalQuestionPersistenceFailure:
                ok = False
                finish_reason = "error"
                response = "本地对话记录写入失败；日记处理未执行，问题未能确认保存。"
            except asyncio.CancelledError:
                ok = False
                finish_reason = "cancelled"
                if question_persisted:
                    response = ("日记发布结果无法确认；原问题已记录，请核对当天日记后再决定下一步。"
                                if journal_publish_started else "日记处理已取消；原问题已记录，查询结果未确认。")
                else:
                    response = ("日记发布结果无法确认；请核对当天日记后再决定下一步。"
                                if journal_publish_started else "日记摘要已取消。")
            except Exception:
                ok = False
                finish_reason = "error"
                if question_persisted:
                    response = ("日记发布结果无法确认；原问题已记录，请检查当天日记。"
                                if journal_publish_started else "日记摘要未能安全处理；原问题已记录，普通对话仍可继续。")
                else:
                    response = ("日记发布结果无法确认；请检查当天日记。"
                                if journal_publish_started else "日记摘要未能安全处理。")

            if question_persisted and not journal_saved and _day_journal_cancel_requested(bridge):
                ok = False
                finish_reason = "cancelled"
                response = ("日记发布结果无法确认；原问题已记录，请检查当天日记。"
                            if journal_publish_started else "日记处理已取消；原问题已记录，查询结果未确认。")
            if ws_done is not None:
                bridge.emit({
                    **ws_done,
                    "ok": ok and finish_reason == "completed",
                    "saved": journal_saved,
                    "message": response,
                })
                if journal_saved:
                    from day_journal_directory import build_directory_payload

                    payload = await asyncio.to_thread(
                        build_directory_payload,
                        self.paths.data / "day-journal",
                    )
                    bridge.emit(payload)
            elif not question_persisted:
                bridge.emit_assistant(response)
            else:
                persist_status = _persist_day_journal_assistant(session, repl, response)
                if persist_status == "saved":
                    bridge.emit_assistant(response)
                elif persist_status == "session_save_failed":
                    ok = False
                    finish_reason = "error"
                    bridge.emit_turn_event({
                        "type": "turn.notice", "level": "warn",
                        "text": "助手消息已追加，但完整会话保存未能确认；不能承诺所有内容均已保存。",
                    })
                    bridge.emit_assistant(response)
                elif persist_status == "assistant_append_failed":
                    ok = False
                    finish_reason = "error"
                    bridge.emit_assistant(
                        "本地对话记录写入失败；原问题已追加，助手答复未能确认记录，处理结果未显示。"
                    )
                else:
                    ok = False
                    finish_reason = "error"
                    bridge.emit_turn_event({
                        "type": "turn.notice", "level": "warn",
                        "text": "会话已切换；原问题保留在开始时的会话，本次日记结果未显示。",
                    })
        finally:
            bridge._turn_busy.clear()
            bridge.end_turn()
            if bridge.finish_execution(finish_reason, ok=ok):
                bridge.emit_turn_event({
                    "type": "turn.end", "ok": ok,
                    "finish_reason": finish_reason,
                })

    async def _handle_day_journal_recall(
        self, text: str, day: str | None, repl: ConversationRepl, bridge: WsBridge,
        *, start_date: str | None = None, end_date: str | None = None,
        request_id: Any = None,
    ) -> None:
        """Answer an explicit bounded journal query in the current chat turn, locally."""
        if not bridge.begin_turn(mode="ordinary"):
            return
        snapshot = bridge.lifecycle.snapshot()
        if snapshot is not None:
            bridge.lifecycle_conversation_id = repl.session.conversation_id
        bridge._turn_busy.set()
        session = repl.session
        ok = True
        finish_reason = "completed"
        response = ""
        question_persisted = False
        try:
            try:
                if not _append_day_journal_question(
                    session, repl, text,
                    on_persisted=lambda persisted: _emit_day_journal_user_message_persisted(
                        bridge=bridge, repl=repl, session=persisted, request_id=request_id,
                    ),
                ):
                    raise _DayJournalQuestionPersistenceFailure
                question_persisted = True
                _raise_if_day_journal_cancelled(bridge)
                if day is None and (start_date is None or end_date is None):
                    response = "目前只支持明确查询；未能安全确定唯一日期范围。请明确日期或范围（最多31天），我不会猜测或查询日记。"
                else:
                    try:
                        from day_journal import DayJournal
                        from day_journal_recall import query_day_journal as default_query_day_journal

                        journal = getattr(self, "project_boundary_journal", None)
                        if journal is None:
                            journal_root = self.paths.data / "day-journal"
                        elif type(journal) is DayJournal:
                            # This is the server-injected, explicitly enabled local store;
                            # no path supplied by chat text is accepted here.
                            journal_root = journal.root
                        else:
                            raise RuntimeError("injected day journal is not a trusted DayJournal")
                        query = getattr(self, "day_journal_query", None) or default_query_day_journal
                        query_kwargs = {"authorized": True, "timezone": "Asia/Shanghai"}
                        if day is not None:
                            query_kwargs["day"] = day
                        else:
                            query_kwargs.update(start_date=start_date, end_date=end_date)
                        result = await asyncio.to_thread(query, journal_root, **query_kwargs)
                        meta = getattr(repl.session, "meta", None)
                        current_project_id = getattr(meta, "project_id", None) if meta is not None else None
                        project_id = resolve_recall_project_id(
                            text, result, current_project_id=current_project_id,
                        )
                        if project_id:
                            query_kwargs["project_id"] = project_id
                            result = await asyncio.to_thread(query, journal_root, **query_kwargs)
                        result = attach_project_store_pointers(self.paths, result)
                        result = attach_session_excerpts(self.paths, result)
                        _raise_if_day_journal_cancelled(bridge)
                        response = _format_day_journal_result(result)
                    except (OSError, PermissionError):
                        response = "本地日记当前不可读取（可能缺失或权限受限）；没有改查其他日期。"
                    except Exception:
                        response = "本地日记查询未能安全完成；没有改查其他日期。"
                response = (
                    "本次查询仅本地处理；结果保存在对话中，后续模型对话可能发送该内容。\n\n"
                    + response
                )
            except _DayJournalQuestionPersistenceFailure:
                ok = False
                finish_reason = "error"
                response = "本地对话记录写入失败；日记查询未执行，问题未能确认保存。"
            except asyncio.CancelledError:
                ok = False
                finish_reason = "cancelled"
                response = "日记查询已取消；原问题已记录，查询结果未确认。"
            except Exception:
                ok = False
                finish_reason = "error"
                response = "日记查询未能安全完成；原问题已记录，普通对话仍可继续。"

            if question_persisted and _day_journal_cancel_requested(bridge):
                ok = False
                finish_reason = "cancelled"
                response = "日记查询已取消；原问题已记录，查询结果未确认。"
            if not question_persisted:
                bridge.emit_assistant(response)
            else:
                persist_status = _persist_day_journal_assistant(session, repl, response)
                if persist_status == "saved":
                    bridge.emit_assistant(response)
                elif persist_status == "session_save_failed":
                    ok = False
                    finish_reason = "error"
                    bridge.emit_turn_event({
                        "type": "turn.notice", "level": "warn",
                        "text": "助手消息已追加，但完整会话保存未能确认；不能承诺所有内容均已保存。",
                    })
                    bridge.emit_assistant(response)
                elif persist_status == "assistant_append_failed":
                    ok = False
                    finish_reason = "error"
                    bridge.emit_assistant(
                        "本地对话记录写入失败；原问题已追加，助手答复未能确认记录，查询结果未显示。"
                    )
                else:
                    ok = False
                    finish_reason = "error"
                    bridge.emit_turn_event({
                        "type": "turn.notice", "level": "warn",
                        "text": "会话已切换；原问题保留在开始时的会话，本次日记结果未显示。",
                    })
        finally:
            bridge._turn_busy.clear()
            bridge.end_turn()
            if bridge.finish_execution(finish_reason, ok=ok):
                bridge.emit_turn_event({
                    "type": "turn.end", "ok": ok,
                    "finish_reason": finish_reason,
                })

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
            elif request_type == _WORK_RECORDS_OVERVIEW_QUERY:
                response = {
                    "type": _WORK_RECORDS_OVERVIEW_RESPONSE,
                    "state": "error", "reason": "managed_readonly_rejected",
                    "groups": [], "unread_important_results": [], "unread_status": "unknown",
                    "visibility_limitations": [],
                    "visibility_counts": {"visible_groups": 0, "visible_items": 0,
                                           "collapsed_groups": 0, "collapsed_items": 0},
                    "snapshot_id": None,
                    "page": {"page_size": 6, "offset": 0, "total_groups": 0,
                             "total_visible_groups": 0, "total_visible_items": 0,
                             "total_collapsed_groups": 0, "total_collapsed_items": 0,
                             "has_more": False, "next_cursor": None},
                }
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
        if msg_type == "work-record.auto-recognition.set":
            bridge.emit(bridge.set_auto_recognition(message, getattr(repl, "session", None)))
            return
        if msg_type == _WORK_RECORDS_OVERVIEW_QUERY:
            try:
                await self._dispatch(message, repl, bridge)
            except Exception:
                emit_error(bridge, "work-records overview query failed")
            return

        if msg_type == "work.instruction":
            request_id = message.get("request_id")
            invalid = _WorkInstruction(
                request_id if type(request_id) is str else "",
                message.get("run_id") if type(message.get("run_id")) is str else "",
                getattr(getattr(repl, "session", None), "conversation_id", ""),
                message.get("text") if type(message.get("text")) is str else "",
            )
            invalid.state = "undelivered"
            bridge._emit_work_instruction_state(
                invalid, "undelivered", reason="client_selected_route_disabled_use_user_message",
            )
            return

        if msg_type == "chat.message":
            bridge.emit({"type": "chat.error",
                         "request_id": message.get("request_id") if type(message.get("request_id")) is str else "",
                         "work_run_id": getattr(bridge.lifecycle.snapshot(), "run_id", ""),
                         "session_id": getattr(getattr(repl, "session", None), "conversation_id", ""),
                         "code": "client_selected_route_disabled",
                         "message": "\u8bf7\u6539\u7528 user.message\uff1b\u7531\u670d\u52a1\u7aef\u7ed3\u5408\u4f1a\u8bdd\u4e0a\u4e0b\u6587\u8def\u7531\u3002"})
            return

        if msg_type == "user.message":
            # Natural language must never be consumed as interactive tool input.
            # Tool prompts use the run-bound prompt.response message handled inline.
            text = message.get("text")
            active_work = self._active_work_run_for_session(bridge, getattr(repl, "session", None))
            if active_work is not None:
                if self._looks_like_natural_stop_request(text if type(text) is str else ""):
                    request_id = message.get("request_id")
                    session = getattr(repl, "session", None)
                    try:
                        canonical = type(request_id) is str and str(uuid.UUID(request_id)) == request_id
                    except (ValueError, TypeError, AttributeError):
                        canonical = False
                    if (not canonical or message.get("session_id") != getattr(session, "conversation_id", None)
                            or not isinstance(session, Session)):
                        accepted = False
                    else:
                        accepted = bridge.request_execution_stop(active_work.run_id)
                    if accepted:
                        bridge.invalidate_post_turn()
                    result = {"type": "execution.stop.result", "accepted": accepted,
                              "run_id": active_work.run_id, "source": "user.message"}
                    if type(request_id) is str:
                        result["request_id"] = request_id
                    bridge.emit(result)
                    return
                if bridge._awaiting_input.is_set():
                    with bridge._input_request_lock:
                        pending_prompt = bridge._pending_input_request
                    if pending_prompt is not None:
                        bridge.emit({
                            "type": "error", "code": "prompt_response_required",
                            "request_id": message.get("request_id") if type(message.get("request_id")) is str else "",
                            "prompt_request_id": pending_prompt[0], "run_id": pending_prompt[1],
                            "session_id": pending_prompt[2],
                            "message": "\u5f53\u524d\u6709\u4e00\u4e2a\u7b49\u5f85\u4e2d\u7684\u5de5\u5177\u8f93\u5165\u63d0\u793a\uff1b\u672c\u6761 user.message \u672a\u88ab\u5f53\u4f5c\u5de5\u5177\u8f93\u5165\u3002\u8bf7\u4f7f\u7528 prompt.response \u5e76\u5e26\u56de prompt.request \u4e2d\u7684 request_id\u3001run_id \u548c session_id\u3002",
                        })
                        return
                await self._route_active_work_message(message, repl, bridge, active_work)
                return
            # A replay of an admitted turn must not become input to a running tool.
            # This check and admission below run on the connection's event loop.
            request_id = message.get("request_id")
            session_id = getattr(getattr(repl, "session", None), "conversation_id", None)
            if ((getattr(self, '_post_turn_commit_ready', False)
                     or bridge._auto_recognition_session is getattr(repl, 'session', None))
                    and type(session_id) is str and type(request_id) is str
                    and (session_id, request_id) in bridge._post_turn_seen_requests):
                bridge.emit({"type": "notice", "text": "重复请求已拦截；未再次执行，请核对原回合。"})
                return
            text = message.get("text")
            if text in (self.PROJECT_JOURNAL_ENABLE_PHRASE, self.PROJECT_JOURNAL_DISABLE_PHRASE):
                async with TURN_LOCK:
                    await self._handle_project_journal_command(text, bridge, repl.session.conversation_id)
                return

        line = message.get("text") if msg_type == "user.message" else message.get("name")
        changes_session_binding = msg_type in {"session.open", "session.create_sandbox"} or (
            isinstance(msg_type, str) and msg_type.startswith("project.") and msg_type != "project.state"
        ) or (msg_type in {"user.message", "command"} and _line_may_rebind_session(line))

        if msg_type == "evolution.scripted":
            async with TURN_LOCK:
                try:
                    await self._dispatch(message, repl, bridge)
                except Exception as exc:  # pragma: no cover
                    emit_error(bridge, str(exc))
            return

        if msg_type in {"work-card.result.view", "work-card.snapshot.view"}:
            # This one explicit ACK may run during a turn, but session rebinding must
            # wait for its exact-Session lease before mutating repl.session.
            session = getattr(repl, "session", None)
            lease = bridge.try_acquire_result_view_lease(session) if isinstance(session, Session) else None
            try:
                await self._dispatch(message, repl, bridge, result_view_lease=lease)
            except Exception:  # pragma: no cover
                emit_error(bridge, "work-card result view failed")
            finally:
                if lease is not None:
                    bridge.release_result_view_lease(lease)
            return

        if (msg_type in {"user.message", "command", "work-record.capture"} | _WORK_CARD_MESSAGE_TYPES
                and not changes_session_binding):
            with bridge._result_view_write_lock:
                auto_session = getattr(repl, 'session', None)
                auto_enabled = (msg_type == 'user.message' and bridge._auto_recognition_session is auto_session
                                and auto_session is not None and not bridge._connection_closed.is_set())
                auto_generation = bridge._post_turn_generation if auto_enabled else None
            candidate = None
            async with TURN_LOCK:
                # Limited staged-path replay barrier, before any dispatch side effect.
                # Connection-local only: this is not a cross-reconnect execution ledger.
                if msg_type == "user.message" and (getattr(self, '_post_turn_commit_ready', False) or auto_enabled):
                    request_id = message.get("request_id")
                    try:
                        canonical = type(request_id) is str and str(uuid.UUID(request_id)) == request_id
                    except (ValueError, TypeError, AttributeError):
                        canonical = False
                    session_id = getattr(getattr(repl, "session", None), "conversation_id", None)
                    if canonical and type(session_id) is str and session_id:
                        key = (session_id, request_id)
                        if key in bridge._post_turn_seen_requests:
                            bridge.emit({"type": "notice", "text": "重复请求已拦截；未再次执行，请核对原回合。"})
                            return
                        if len(bridge._post_turn_seen_requests) >= _POST_TURN_REQUEST_CAPACITY:
                            if getattr(self, '_post_turn_commit_ready', False):
                                bridge.emit({"type": "notice", "text": "本连接的请求保护已达上限；本次未执行，请核对记录并重新连接。"})
                                return
                            _emit_auto_recognition_result(bridge, _AutoRecognitionResultIdentity(auto_session, request_id), 'capacity_exhausted')
                            auto_enabled = False
                        else:
                            bridge._post_turn_seen_requests.add(key)
                with bridge._result_view_write_lock:
                    bridge._auto_recognition_turn_generation = (
                        auto_generation if auto_enabled and bridge._auto_recognition_session is auto_session
                        and bridge._post_turn_generation == auto_generation else None)
                try:
                    candidate = await self._dispatch(message, repl, bridge)
                except Exception as exc:  # pragma: no cover
                    emit_error(bridge, str(exc))
                finally:
                    bridge._auto_recognition_turn_generation = None
            if auto_enabled and type(candidate) is _FrozenPostTurnCandidate:
                _enqueue_auto_recognition(bridge, candidate)
            elif auto_enabled:
                raw_request = message.get('request_id')
                try:
                    safe_request = raw_request if type(raw_request) is str and str(uuid.UUID(raw_request)) == raw_request else ''
                except (ValueError, TypeError, AttributeError):
                    safe_request = ''
                from work_records_service import is_explicit_record_request
                reason = ('skipped_explicit' if is_explicit_record_request(message.get('text'))
                          else 'no_terminal')
                _emit_auto_recognition_result(bridge, _AutoRecognitionResultIdentity(auto_session, safe_request), reason)
            if type(candidate) is _FrozenExplicitRunExcerpt and getattr(self, '_explicit_run_excerpt_ready', False):
                async def run_explicit_excerpt() -> None:
                    try:
                        result = await asyncio.to_thread(_execute_explicit_run_excerpt_candidate, candidate, bridge)
                        status, reason = getattr(result, 'status', ''), getattr(result, 'reason', '')
                        outcome = ('recorded' if status == 'recorded' else
                                   'uncertain' if reason in {'assistant_excerpt_storage_failed', 'excerpt_outcome_uncertain'} else
                                   'not_recorded')
                    except Exception:
                        outcome = 'uncertain'
                    messages = {
                        'recorded': '回复摘录已追加；原工作记录仍为未知、未经验证。',
                        'not_recorded': '回复摘录未追加，原工作记录保留；不会重新执行原工作。',
                        'uncertain': '回复摘录是否追加无法确认；原工作记录此前已保存，请核对，勿重新执行原工作。',
                    }
                    if not bridge._connection_closed.is_set():
                        bridge.emit({'type': 'work-record.post-turn.result',
                                     'session_id': candidate.session.conversation_id,
                                     'request_id': candidate.request_id, 'outcome': outcome,
                                     'message': messages[outcome]})
                task = asyncio.create_task(run_explicit_excerpt())
                bridge._post_turn_tasks.add(task)
                task.add_done_callback(bridge._post_turn_tasks.discard)
            if type(candidate) is _FrozenPostTurnCandidate and getattr(self, '_post_turn_commit_ready', False) and not auto_enabled:
                async def run_post_turn() -> None:
                    try:
                        from llm_client import LLMClient
                        client = LLMClient(paths=self.paths)
                        result = await asyncio.to_thread(_execute_post_turn_candidate, candidate, bridge, client)
                        status = getattr(result, "status", "uncertain")
                    except Exception:
                        # Never restart the ordinary work or claim a write succeeded.
                        status = "uncertain"
                    if status in {"recorded", "already_recorded", "append_not_allowed"}:
                        outcome = status
                    elif status in {"not_recorded", "not_authorized", "storage_not_authorized", "stale_context", "invalid_request", "request_conflict", "unavailable"}:
                        outcome = "not_recorded"
                    else:
                        outcome = "uncertain"
                    if not bridge._connection_closed.is_set():
                        bridge.emit({"type": "work-record.post-turn.result",
                                     "session_id": candidate.session.conversation_id,
                                     "request_id": candidate.request_id, "outcome": outcome})
                task = asyncio.create_task(run_post_turn())
                bridge._post_turn_tasks.add(task)
                task.add_done_callback(bridge._post_turn_tasks.discard)
            return

        if msg_type == "project.state":
            # This query does not rebind the Session. Preserve serialized access to
            # project state without revoking unrelated background recognition.
            try:
                await asyncio.wait_for(TURN_LOCK.acquire(), timeout=SESSION_SWITCH_WAIT_SECONDS)
            except asyncio.TimeoutError:
                emit_error(bridge, "session binding is busy; retry the project state query after the active operation finishes")
                return
            try:
                await self._dispatch(message, repl, bridge)
            except Exception as exc:  # pragma: no cover
                emit_error(bridge, str(exc))
            finally:
                TURN_LOCK.release()
            return

        # Session replacement and project operations that can rebind repl.session
        # share the capture/turn lock. Bound the wait so a pending user confirmation
        # cannot make a session switch appear hung; confirm.response remains inline.
        if changes_session_binding:
            bridge.begin_session_binding_change()
            try:
                deadline = asyncio.get_running_loop().time() + SESSION_SWITCH_WAIT_SECONDS
                try:
                    leases_released = await asyncio.to_thread(
                        bridge.wait_for_result_view_leases, SESSION_SWITCH_WAIT_SECONDS,
                    )
                    if not leases_released:
                        raise asyncio.TimeoutError
                    remaining = max(0.0, deadline - asyncio.get_running_loop().time())
                    await asyncio.wait_for(TURN_LOCK.acquire(), timeout=remaining)
                except asyncio.TimeoutError:
                    emit_error(bridge, "session binding is busy; retry the session switch after the active operation finishes")
                    return
                try:
                    await self._dispatch(message, repl, bridge)
                except Exception as exc:  # pragma: no cover
                    emit_error(bridge, str(exc))
                finally:
                    TURN_LOCK.release()
            finally:
                bridge.end_session_binding_change()
            return

        try:
            await self._dispatch(message, repl, bridge)
        except Exception as exc:  # pragma: no cover
            emit_error(bridge, str(exc))

    def _mark_work_card_transport_offer(self, event: dict[str, Any]) -> None:
        """Count a successful websocket.send without claiming renderer receipt."""
        if (type(event) is not dict or event.get("type") != "work-card.present"
                or type(event.get("session_id")) is not str
                or type(event.get("presentation_id")) is not str
                or type(event.get("card_id")) is not str):
            return
        try:
            session = Session.load(self.paths, event["session_id"], message_cap=0)
            from work_records import WorkRecordStore
            from work_card_ledger import WorkCardLedger
            from work_card_presentations import WorkCardPresentations
            store = WorkRecordStore(session)
            ledger = WorkCardLedger(session, store)
            presentations = WorkCardPresentations(session, store, ledger)
            fact = presentations.get(event["presentation_id"])
            if fact.status != "found" or fact.fact is None or fact.fact.card_id != event["card_id"]:
                return
            presentations.mark_transport_offer(event["presentation_id"], event["card_id"])
        except Exception:
            logging.getLogger(SIDECAR_LOGGER_NAME).warning(
                "work-card transport offer accounting failed; renderer receipt remains pending",
            )

    async def _sender(
        self,
        websocket: ServerConnection,
        outbox: asyncio.Queue[dict[str, Any] | None],
        bridge: WsBridge | None = None,
    ) -> None:
        while True:
            event = await outbox.get()
            if event is None:
                break
            if type(event) is dict and event.get("type") == "work-card.present" and bridge is not None:
                # Keep an ACK racing the in-flight send behind the successful send
                # marker. A failed send leaves no offer evidence to ACK.
                async with bridge._work_card_receipt_lock:
                    try:
                        await websocket.send(json.dumps(event, ensure_ascii=False))
                    except ConnectionClosed:
                        if bridge is not None:
                            bridge.mark_connection_closed()
                        break
                    self._mark_work_card_transport_offer(event)
                continue
            try:
                await websocket.send(json.dumps(event, ensure_ascii=False))
            except ConnectionClosed:
                if bridge is not None:
                    bridge.mark_connection_closed()
                break

    @staticmethod
    def _active_work_run_for_session(bridge: WsBridge, session: Any) -> Any | None:
        if not isinstance(session, Session) or not bridge._turn_busy.is_set():
            return None
        snapshot = bridge.lifecycle.snapshot()
        if (snapshot is None or snapshot.state not in {"running", "stopping"}
                or bridge.lifecycle_conversation_id != session.conversation_id):
            return None
        return snapshot

    @staticmethod
    def _looks_like_natural_stop_request(text: str) -> bool:
        normalized = " ".join(text.strip().casefold().split()).rstrip(".!?。！？")
        return normalized in {
            "stop current work", "stop the current work", "stop work", "stop the work",
            "\u505c\u6b62\u5f53\u524d\u5de5\u4f5c", "\u505c\u6b62\u5de5\u4f5c",
        }

    @staticmethod
    def _looks_like_ambiguous_stop_request(text: str) -> bool:
        normalized = " ".join(text.strip().casefold().split()).rstrip(".!?。！？")
        return normalized in {
            "hold on", "pause", "wait", "\u505c\u4e00\u4e0b", "\u7b49\u4e00\u4e0b",
            "\u5148\u505c\u4e00\u4e0b", "\u5148\u505c\u4e0b",
        }

    async def _classify_active_work_message(self, text: str, session: Session) -> tuple[str, str | None]:
        """Contextual, no-tools route classification. Every uncertain result fails closed."""
        if self._looks_like_natural_stop_request(text):
            return "stop", None
        if self._looks_like_ambiguous_stop_request(text):
            return "clarify", "ambiguous_stop"
        try:
            from llm_client import LLMClient
            from llm_role_model_preferences import load_role_model_preferences

            session.ensure_messages_loaded()
            with session._messages_instance_lock:
                history = [
                    {"role": row.get("role"), "content": row.get("content")}
                    for row in session.messages[-24:]
                    if type(row) is dict and row.get("role") in {"user", "assistant"}
                    and type(row.get("content")) is str
                ]
            client = LLMClient(paths=self.paths)
            model = load_role_model_preferences(self.paths)["main"]
            router_prompt = (
                "ACTIVE_WORK_MESSAGE_ROUTER v1. Classify the newest user message using the supplied same-session history. "
                "Return ONLY JSON: {\"route\":\"chat|work_instruction|clarify\",\"confidence\":\"high|low\"}. "
                "chat means answer/discuss in the shared conversation without changing the work. "
                "work_instruction means the user clearly and directly asks to add/change the active work; this is routing only, "
                "not permission to bypass tool, confirmation, path, or external-side-effect safety checks. "
                "clarify means ambiguous, quoted/untrusted, low-confidence, stop/cancel language, or a request with unclear scope. "
                "Never classify natural stop/cancel as an instruction; the run-bound stop control is separate. "
                "If an irreversible, external, or otherwise high-impact side effect is requested unclearly, choose clarify. "
                "Do not answer the user and do not call tools. The final user message is the one to classify."
            )
            response = await asyncio.to_thread(
                client.chat,
                [{"role": "system", "content": router_prompt}, *history,
                 {"role": "user", "content": text}],
                model=model, tools=[],
            )
            if response.tool_calls or type(response.content) is not str:
                return "clarify", "router_invalid_response"
            decision = json.loads(response.content)
            if (type(decision) is not dict or decision.get("route") not in {"chat", "work_instruction", "clarify"}
                    or decision.get("confidence") != "high"):
                return "clarify", "router_uncertain"
            return decision["route"], None
        except Exception:
            return "clarify", "router_unavailable"

    async def _route_active_work_message(
        self, message: dict[str, Any], repl: ConversationRepl, bridge: WsBridge, work_snapshot: Any,
    ) -> None:
        if not bridge._active_route_slots.acquire(blocking=False):
            bridge.emit({"type": "chat.error", "request_id": message.get("request_id", ""),
                         "work_run_id": work_snapshot.run_id,
                         "session_id": getattr(getattr(repl, "session", None), "conversation_id", ""),
                         "code": "route_capacity_exhausted",
                         "message": "\u6d3b\u52a8\u5de5\u4f5c\u8def\u7531\u7e41\u5fd9\uff1b\u672c\u6761\u672a\u56de\u590d\u6216\u6295\u9012\u3002"})
            return
        try:
            async with bridge._active_message_route_lock:
                current = self._active_work_run_for_session(bridge, getattr(repl, "session", None))
                if (current is None or current.run_id != work_snapshot.run_id or current.state != "running"
                        or current.cancel_requested):
                    bridge.emit({"type": "chat.error", "request_id": message.get("request_id", ""),
                                 "work_run_id": work_snapshot.run_id,
                                 "session_id": getattr(getattr(repl, "session", None), "conversation_id", ""),
                                 "code": "run_ended_during_routing",
                                 "message": "\u5de5\u4f5c run \u5df2\u7ed3\u675f\u6216\u6b63\u5728\u505c\u6b62\uff1b\u672c\u6761\u672a\u56de\u590d\u6216\u6295\u9012\u3002"})
                    return
                await self._route_active_work_message_locked(message, repl, bridge, current)
        finally:
            bridge._active_route_slots.release()

    async def _route_active_work_message_locked(
        self, message: dict[str, Any], repl: ConversationRepl, bridge: WsBridge, work_snapshot: Any,
    ) -> None:
        session = getattr(repl, "session", None)
        request_id = message.get("request_id")
        text = message.get("text")
        try:
            canonical = type(request_id) is str and str(uuid.UUID(request_id)) == request_id
        except (ValueError, TypeError, AttributeError):
            canonical = False
        if (not canonical or not isinstance(session, Session)
                or message.get("session_id") != session.conversation_id
                or type(text) is not str or not text.strip()):
            bridge.emit({"type": "chat.error", "request_id": request_id if type(request_id) is str else "",
                         "work_run_id": work_snapshot.run_id,
                         "session_id": getattr(session, "conversation_id", ""),
                         "code": "invalid_request", "message": "\u6d3b\u52a8\u5de5\u4f5c\u671f\u95f4\u9700\u8981\u6709\u6548 request_id\u3001session_id \u548c\u975e\u7a7a\u6587\u672c\uff1b\u672c\u6761\u672a\u6295\u9012\u3002"})
            return
        if message.get("attachments") not in (None, []):
            bridge.emit({"type": "chat.error", "request_id": request_id, "work_run_id": work_snapshot.run_id,
                         "session_id": session.conversation_id, "code": "attachments_unsupported",
                         "message": "\u6d3b\u52a8\u5de5\u4f5c\u671f\u95f4\u6682\u4e0d\u652f\u6301\u5e26\u9644\u4ef6\u6d88\u606f\uff1b\u672c\u6761\u672a\u6295\u9012\u3002"})
            return
        if request_id in bridge._parallel_chat_seen_requests or request_id in bridge._work_instructions:
            bridge.emit({"type": "chat.error", "request_id": request_id, "work_run_id": work_snapshot.run_id,
                         "session_id": session.conversation_id, "code": "duplicate_request",
                         "message": "\u91cd\u590d\u8bf7\u6c42\u5df2\u62e6\u622a\uff1b\u6ca1\u6709\u518d\u6b21\u56de\u590d\u6216\u6295\u9012\u3002"})
            return
        if request_id in bridge._active_message_seen_requests:
            bridge.emit({"type": "chat.error", "request_id": request_id, "work_run_id": work_snapshot.run_id,
                         "session_id": session.conversation_id, "code": "duplicate_request",
                         "message": "\u91cd\u590d\u8bf7\u6c42\u5df2\u62e6\u622a\uff1b\u672a\u91cd\u590d\u5904\u7406\u3002"})
            return
        if len(bridge._active_message_seen_requests) >= 4096:
            bridge.emit({"type": "chat.error", "request_id": request_id, "work_run_id": work_snapshot.run_id,
                         "session_id": session.conversation_id, "code": "request_capacity_exhausted",
                         "message": "\u672c\u8fde\u63a5\u7684\u8bf7\u6c42\u53bb\u91cd\u5bb9\u91cf\u5df2\u8fbe\u4e0a\u9650\uff1b\u672c\u6761\u672a\u5904\u7406\u3002"})
            return
        bridge._active_message_seen_requests.add(request_id)
        route, reason = await self._classify_active_work_message(text, session)
        current = self._active_work_run_for_session(bridge, session)
        if (current is None or current.run_id != work_snapshot.run_id or current.state != "running"
                or current.cancel_requested):
            bridge.emit({"type": "chat.error", "request_id": request_id, "work_run_id": work_snapshot.run_id,
                         "session_id": session.conversation_id, "code": "run_ended_during_routing",
                         "message": "\u5de5\u4f5c run \u5df2\u7ed3\u675f\u6216\u6b63\u5728\u505c\u6b62\uff1b\u672c\u6761\u672a\u56de\u590d\u6216\u6295\u9012\u3002"})
            return
        if route == "stop":
            accepted = bridge.request_execution_stop(work_snapshot.run_id)
            if accepted:
                bridge.invalidate_post_turn()
            bridge.emit({"type": "execution.stop.result", "accepted": accepted,
                         "run_id": work_snapshot.run_id, "request_id": request_id,
                         "source": "user.message"})
            return
        if route == "work_instruction":
            bridge.enqueue_work_instruction(
                request_id=request_id, run_id=work_snapshot.run_id, session=session, text=text,
            )
            return
        if route == "chat":
            await self._dispatch_parallel_chat(message, repl, bridge, work_snapshot)
            return
        if reason in {"stop_request", "ambiguous_stop"}:
            clarification = (
                "\u4f60\u662f\u5e0c\u671b\u505c\u6b62\u5f53\u524d\u5de5\u4f5c\uff0c\u8fd8\u662f\u53ea\u60f3\u8ba8\u8bba\u505c\u6b62\u65b9\u5f0f\uff1f"
                "\u5f53\u524d\u5de5\u4f5c\u5c1a\u672a\u505c\u6b62\uff1b\u5982\u786e\u9700\u505c\u6b62\uff0c\u8bf7\u4f7f\u7528\u5f53\u524d\u5de5\u4f5c\u5bf9\u5e94\u7684\u505c\u6b62\u63a7\u4ef6\u3002"
            )
        else:
            clarification = (
                "\u6211\u8fd8\u4e0d\u80fd\u53ef\u9760\u5224\u65ad\u8fd9\u6761\u6d88\u606f\u662f\u8ba8\u8bba\uff0c\u8fd8\u662f\u8981\u6539\u53d8\u5f53\u524d\u5de5\u4f5c\uff1b\u5f53\u524d\u5de5\u4f5c\u5c1a\u672a\u6536\u5230\u65b0\u6307\u4ee4\u3002"
                "\u8bf7\u8865\u5145\u4f60\u5e0c\u671b\u6211\u56de\u7b54\u7684\u5177\u4f53\u95ee\u9898\uff0c\u6216\u660e\u786e\u8bf4\u660e\u8981\u5bf9\u5f53\u524d\u5de5\u4f5c\u505a\u7684\u5177\u4f53\u6539\u52a8\u3002"
            )
        await self._dispatch_parallel_chat(
            message, repl, bridge, work_snapshot, forced_response=clarification,
        )

    async def _dispatch_parallel_chat(
        self, message: dict[str, Any], repl: ConversationRepl, bridge: WsBridge, work_snapshot: Any,
        *, forced_response: str | None = None,
    ) -> None:
        request_id = message.get("request_id")
        text = message.get("text")
        try:
            canonical_request = type(request_id) is str and str(uuid.UUID(request_id)) == request_id
        except (ValueError, TypeError, AttributeError):
            canonical_request = False
        if not canonical_request or type(text) is not str or not text.strip():
            bridge.emit({"type": "chat.error", "request_id": request_id if type(request_id) is str else "",
                         "work_run_id": work_snapshot.run_id, "session_id": repl.session.conversation_id,
                         "code": "invalid_request", "message": "并行聊天需要有效 request_id 和非空文本。"})
            return
        if message.get("attachments") not in (None, []):
            bridge.emit({"type": "chat.error", "request_id": request_id, "work_run_id": work_snapshot.run_id,
                         "session_id": repl.session.conversation_id, "code": "attachments_unsupported",
                         "message": "工作运行期间的并行聊天暂不支持附件。"})
            return
        session = repl.session
        request = _ParallelChatRequest(request_id, work_snapshot.run_id, session)
        if not bridge.register_parallel_chat(request):
            bridge.emit({"type": "chat.error", "request_id": request_id, "work_run_id": work_snapshot.run_id,
                         "session_id": session.conversation_id, "code": "request_unavailable",
                         "message": "聊天请求重复、连接已关闭或并行请求已达上限。"})
            return
        fields = {"request_id": request_id, "work_run_id": work_snapshot.run_id,
                  "session_id": session.conversation_id}
        try:
            if bridge._connection_closed.is_set():
                request.cancel_event.set()
                raise LLMCancelledError("connection closed")
            # Session.append_message uses both the per-instance and OS-backed lock.
            # Never save a stale full-history snapshot from this side channel.
            session.ensure_messages_loaded()
            parallel_chat_meta = {
                HOST_INTERNAL_METADATA_KEY: {"parallel_chat": True, "request_id": request_id},
            }
            session.append_message({"role": "user", "content": text, **parallel_chat_meta})
            bridge.emit({"type": "chat.start", **fields})
            with session._messages_instance_lock:
                history = [
                    {"role": row.get("role"), "content": row.get("content")}
                    for row in session.messages
                    if type(row) is dict and row.get("role") in {"user", "assistant"}
                    and type(row.get("content")) is str
                ][-40:]
            if forced_response is not None:
                # Deterministic clarifications must remain available when routing/model
                # providers are unhealthy; they never claim the message was delivered to work.
                answer = forced_response
                bridge.emit({"type": "chat.delta", **fields, "text": answer})
            else:
                from llm_client import LLMClient, StreamHandlers
                from llm_role_model_preferences import load_role_model_preferences
                from loader import build_system_prompt

                client = LLMClient(paths=self.paths)
                request.client = client
                client.set_cancel_event(request.cancel_event)
                if request.cancel_event.is_set():
                    raise LLMCancelledError("parallel chat cancelled")
                model = load_role_model_preferences(self.paths)["main"]
                base_prompt = build_system_prompt(
                    session, paths=self.paths, include_overlay=False,
                ).prompt
                shared_context_guard = (
                    "This is the same conversation as the active work. Answer naturally in the established assistant voice. "
                    "Do not claim live execution facts that are absent from the conversation history. "
                    "This response call has no tools; any requested work change must be separately admitted at the active run's safety checkpoint."
                )
                messages = [{"role": "system", "content": base_prompt + "\n\n" + shared_context_guard}, *history]
                stream = StreamHandlers(on_content_delta=lambda delta: bridge.emit({
                    "type": "chat.delta", **fields, "text": delta,
                }))
                response = await asyncio.to_thread(
                    client.chat, messages, model=model, tools=[], stream=stream,
                )
                if request.cancel_event.is_set():
                    raise LLMCancelledError("parallel chat cancelled")
                if response.tool_calls:
                    raise RuntimeError("parallel chat tool calls are forbidden")
                answer = response.content if type(response.content) is str else ""
            session.append_message({"role": "assistant", "content": answer, **parallel_chat_meta})
            bridge.emit({"type": "chat.done", **fields, "text": answer,
                         "finish_reason": (response.finish_reason or "unknown") if forced_response is None else "clarification"})
        except LLMCancelledError:
            bridge.emit({"type": "chat.error", **fields, "code": "cancelled", "message": "聊天请求已取消；工作 run 未受影响。"})
        except Exception:
            bridge.emit({"type": "chat.error", **fields, "code": "chat_failed",
                         "message": "并行聊天未能完成或保存；工作 run 未受影响。"})
        finally:
            bridge.finish_parallel_chat(request)

    def _dispatch_inline(self, message: dict[str, Any], bridge: WsBridge) -> bool:
        """Handle messages that must not wait behind an in-flight turn.

        While a user turn runs in a worker thread (blocked on confirm/input queues),
        the websocket loop must still process confirm.response and routed user input.
        """
        msg_type = message.get("type")
        if not isinstance(msg_type, str):
            return False

        if msg_type == "prompt.response":
            request_id = message.get("request_id")
            run_id = message.get("run_id")
            text = message.get("text")
            accepted = bridge.route_prompt_response(request_id, run_id, message.get("session_id"), text)
            response = {"type": "prompt.response.result", "accepted": accepted}
            if type(request_id) is str:
                response["request_id"] = request_id
            if type(run_id) is str:
                response["run_id"] = run_id
            if type(message.get("session_id")) is str:
                response["session_id"] = message["session_id"]
            bridge.emit(response)
            return True

        if msg_type == "confirm.response":
            request_id = message.get("request_id")
            choice = message.get("choice")
            if not isinstance(request_id, str) or not isinstance(choice, str):
                emit_error(bridge, "confirm.response requires request_id and choice")
                return True
            with bridge._result_view_write_lock:
                post_turn_id = request_id in bridge._post_turn_confirmation_ids
            if post_turn_id:
                with bridge._result_view_write_lock:
                    is_auto_record = request_id in bridge._auto_record_confirmations
                if is_auto_record:
                    bridge.deliver_auto_record_confirm(request_id, choice.strip().casefold())
                else:
                    bridge.deliver_post_turn_confirm(request_id, choice.strip().casefold())
            else:
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

        if msg_type == "execution.stop":
            run_id = message.get("run_id")
            accepted = bridge.request_execution_stop(run_id)
            if accepted:
                bridge.invalidate_post_turn()
            response: dict[str, Any] = {"type": "execution.stop.result", "accepted": accepted}
            if type(run_id) is str:
                response["run_id"] = run_id
            bridge.emit(response)
            return True

        if msg_type == "chat.cancel":
            request_id = message.get("request_id")
            cancelled = bridge.cancel_parallel_chat(request_id)
            bridge.emit({"type": "chat.cancel.result", "request_id": request_id if type(request_id) is str else "",
                         "accepted": cancelled})
            return True

        if msg_type == "turn.cancel":
            # Legacy compatibility: Terminal and older clients intentionally send
            # an unbound cancel for their current turn. New UI must use execution.stop.
            bridge.invalidate_post_turn()
            overview_cancelled = bridge.cancel_overview_query()
            run = bridge.lifecycle.snapshot()
            turn_active = bridge._turn_busy.is_set() or (
                run is not None and run.state in {"queued", "running", "stopping"}
            )
            if turn_active or not overview_cancelled:
                bridge.request_cancel()
            return True

        return False

    async def _dispatch_work_record_capture(
        self,
        message: dict[str, Any],
        repl: ConversationRepl,
        bridge: WsBridge,
    ) -> None:
        """Explicit one-shot entry; no session-persistent consent is created."""
        allowed = {"type", "text", "session_id", "request_id"}
        if set(message) - allowed:
            emit_error(bridge, "work-record.capture has unsupported fields")
            return
        session_id = message.get("session_id")
        text = message.get("text")
        request_id = message.get("request_id")
        if session_id != repl.session.conversation_id:
            emit_error(bridge, "work-record.capture must target the current session")
            return
        if not isinstance(text, str) or not text.strip():
            emit_error(bridge, "work-record.capture requires non-empty text")
            return
        if not isinstance(request_id, str) or not request_id.strip():
            emit_error(bridge, "work-record.capture requires a stable request_id")
            return

        def confirm(preview: str) -> tuple[str, str]:
            return bridge.confirm_with_receipt(preview, False)

        try:
            from llm_client import LLMClient
            from work_records_service import WorkRecordCaptureService

            bridge.cancel_event.clear()
            client = LLMClient(paths=self.paths)
            client.set_cancel_event(bridge.cancel_event)
            service = WorkRecordCaptureService(session=repl.session, client=client, confirm=confirm)

            def report_cancelled_explicit_capture(outcome: Any | None, error: BaseException | None) -> None:
                if outcome is not None:
                    bridge.emit({
                        "type": "work-record.capture.done",
                        "request_id": request_id,
                        "session_id": repl.session.conversation_id,
                        "status": outcome.status,
                        "message": outcome.message,
                        "item_id": outcome.item_id,
                        "requirement_id": outcome.requirement_id,
                    })
                else:
                    bridge.emit({
                        "type": "work-record.capture.done",
                        "request_id": request_id,
                        "session_id": repl.session.conversation_id,
                        "status": "outcome_uncertain",
                        "message": "收到取消并已等待本地处理结束；外发或写入可能已发生，请核对当前记录。",
                        "item_id": None,
                    })

            outcome = await _capture_work_record_until_thread_stops(
                service, text, request_id=request_id, bridge=bridge,
                on_cancelled=report_cancelled_explicit_capture,
            )
            bridge.emit({
                "type": "work-record.capture.done",
                "request_id": request_id,
                "session_id": repl.session.conversation_id,
                "status": outcome.status,
                "message": outcome.message,
                "item_id": outcome.item_id,
                "requirement_id": outcome.requirement_id,
            })
        except Exception:
            # Never echo source text, provider diagnostics, paths, or credentials.
            bridge.emit({
                "type": "work-record.capture.done",
                "request_id": request_id,
                "session_id": repl.session.conversation_id,
                "status": "failed_closed",
                "message": "工作记录服务未能安全完成；请检查本地状态。",
                "item_id": None,
            })

    async def _dispatch_work_records_overview(
        self,
        message: dict[str, Any],
        repl: ConversationRepl,
        bridge: WsBridge,
    ) -> None:
        """Read only the exact current Session and pass the API projection through unchanged."""
        request_id = message.get("request_id")
        valid_request_id = False
        if type(request_id) is str:
            try:
                valid_request_id = str(uuid.UUID(request_id)) == request_id
            except (ValueError, TypeError, AttributeError):
                pass
        session = getattr(repl, "session", None)
        session_id = getattr(session, "conversation_id", None)

        def response(state: str, reason: str, result: dict[str, Any] | None = None) -> dict[str, Any]:
            payload = dict(result) if type(result) is dict else {
                "state": state,
                "groups": [],
                "unread_important_results": [],
                "unread_status": "unknown",
                "visibility_limitations": [],
                "snapshot_id": None,
                "page": {"page_size": 6, "offset": 0, "total_groups": 0,
                         "has_more": False, "next_cursor": None},
            }
            payload["type"] = _WORK_RECORDS_OVERVIEW_RESPONSE
            payload["request_id"] = request_id if valid_request_id else None
            payload["session_id"] = session_id if type(session_id) is str else None
            if "state" not in payload:
                payload["state"] = state
            if reason and "reason" not in payload:
                payload["reason"] = reason
            payload.setdefault("groups", [])
            payload.setdefault("unread_important_results", [])
            payload.setdefault("unread_status", "unknown")
            payload.setdefault("visibility_limitations", [])
            payload.setdefault("visibility_counts", {
                "visible_groups": 0, "visible_items": 0,
                "collapsed_groups": 0, "collapsed_items": 0,
            })
            payload.setdefault("snapshot_id", None)
            delay = payload.get("visibility_recheck_after_ms")
            if type(delay) is not int or not 60_000 <= delay <= 30 * 60 * 1000:
                payload["visibility_recheck_after_ms"] = 10 * 60 * 1000
            payload.setdefault("page", {
                "page_size": 6, "offset": 0, "total_groups": 0,
                "total_visible_groups": 0, "total_visible_items": 0,
                "total_collapsed_groups": 0, "total_collapsed_items": 0,
                "has_more": False, "next_cursor": None,
            })
            return payload

        allowed = {"type", "session_id", "request_id", "cursor", "page_size", "detail_paging"}
        if (set(message) - allowed or not valid_request_id
                or ("detail_paging" in message and type(message["detail_paging"]) is not bool)
                or (message.get("detail_paging") is True and type(message.get("cursor")) is str
                    and len(message["cursor"]) > 4096)
                or type(message.get("session_id")) is not str
                or ("cursor" in message and message["cursor"] is not None
                    and type(message["cursor"]) is not str)
                or ("page_size" in message and
                    (type(message["page_size"]) is not int or isinstance(message["page_size"], bool)))):
            _emit_safely(bridge.emit, response("error", "invalid_request"),
                         "work-records overview invalid request response failed")
            return
        if message["session_id"] != session_id:
            _emit_safely(bridge.emit, response("error", "session_mismatch"),
                         "work-records overview session mismatch response failed")
            return
        if (type(session) is not Session or getattr(getattr(repl, "agent", None), "session", None) is not session):
            _emit_safely(bridge.emit, response("error", "current_session_unavailable"),
                         "work-records overview missing session response failed")
            return
        if bridge._connection_closed.is_set():
            return

        query_cancel = bridge.begin_overview_query(request_id)
        if query_cancel is None:
            _emit_safely(bridge.emit, response("error", "query_busy"),
                         "work-records overview busy response failed")
            return
        cancel_was_set = bridge.cancel_event.is_set()
        try:
            try:
                from work_records_overview_api import query_work_records_overview
                kwargs: dict[str, Any] = {"cursor": message.get("cursor")}
                if "page_size" in message:
                    kwargs["page_size"] = message["page_size"]
                if "detail_paging" in message:
                    kwargs["detail_paging"] = message["detail_paging"]
                result = await asyncio.to_thread(query_work_records_overview, session, **kwargs)
            except Exception:
                result = {"state": "error", "reason": "service_unavailable"}
            if query_cancel.is_set() or (not cancel_was_set and bridge.cancel_event.is_set()):
                result = {"state": "cancelled", "reason": "cancelled"}
            elif bridge._connection_closed.is_set():
                return
            elif (bridge._session_binding_transitions
                    or repl.session is not session
                    or getattr(getattr(repl, "agent", None), "session", None) is not session):
                result = {"state": "error", "reason": "session_changed"}
            if type(result) is not dict:
                result = {"state": "error", "reason": "invalid_api_response"}
            payload = response(
                result.get("state") if type(result.get("state")) is str else "error",
                result.get("reason") if type(result.get("reason")) is str else "",
                result,
            )
            _emit_safely(bridge.emit, payload, "work-records overview response delivery failed")
        finally:
            bridge.finish_overview_query(request_id, query_cancel)

    async def _dispatch_work_card_receipt_continue(self, message, repl, bridge):
        bridge.emit(_continue_work_card_receipts(message, repl, bridge))

    async def _dispatch_work_card_protocol(
        self,
        message: dict[str, Any],
        repl: ConversationRepl,
        bridge: WsBridge,
        *,
        result_view_lease: object | None = None,
    ) -> None:
        """Serve current-Session project or explicit independent immutable cards."""
        msg_type = message.get("type")
        operation = msg_type.removeprefix("work-card.")
        request_id = message.get("request_id")
        card_id = message.get("card_id")
        item_id = message.get("item_id")
        if msg_type == "work-card.create":
            valid_request_id = False
            if type(request_id) is str:
                try:
                    valid_request_id = str(uuid.UUID(request_id)) == request_id
                except (ValueError, TypeError, AttributeError):
                    pass
            allowed_fields = {"type", "request_id"} | ({"item_id"} if "item_id" in message else set())
            valid_item_id = "item_id" not in message or (type(item_id) is str and bool(item_id))
            if set(message) != allowed_fields or not valid_request_id or not valid_item_id:
                _emit_safely(bridge.emit, {
                    "type": "work-card.create.done", "status": "invalid_request",
                    "message": "请求字段无效；create 只接受规范 request_id 和可选精确 item_id。",
                }, "work-card invalid request response failed")
                return
        elif msg_type in {"work-card.get", "work-card.seen"}:
            if (set(message) != {"type", "card_id"} or type(card_id) is not str
                    or re.fullmatch(r"wc-[0-9a-f]{64}", card_id) is None):
                _emit_safely(bridge.emit, {
                    "type": f"{msg_type}.done", "status": "invalid_request",
                    "message": "请求字段无效；只接受当前会话中的规范 card_id。",
                }, "work-card invalid request response failed")
                return
        elif msg_type == "work-card.snapshot.view":
            presentation_id = message.get("presentation_id")
            requested_session_id = message.get("session_id")
            try:
                valid_presentation = type(presentation_id) is str and str(uuid.UUID(presentation_id)) == presentation_id
            except (ValueError, TypeError, AttributeError):
                valid_presentation = False
            if (set(message) != {"type", "session_id", "presentation_id", "card_id"}
                    or not valid_presentation or type(requested_session_id) is not str
                    or not requested_session_id or type(card_id) is not str
                    or re.fullmatch(r"wc-[0-9a-f]{64}", card_id) is None):
                _emit_safely(bridge.emit, {
                    "type": "work-card.snapshot.view.done", "status": "invalid_request",
                    "message": "查看身份无效；未记录查看。",
                    **{key: message.get(key) for key in ("session_id", "presentation_id", "card_id")
                       if type(message.get(key)) is str},
                }, "work-card snapshot view invalid request response failed")
                return
        elif msg_type == "work-card.received":
            presentation_id = message.get("presentation_id")
            requested_session_id = message.get("session_id")
            valid_presentation = False
            try:
                valid_presentation = type(presentation_id) is str and str(uuid.UUID(presentation_id)) == presentation_id
            except (ValueError, TypeError, AttributeError):
                pass
            if (set(message) != {"type", "session_id", "presentation_id", "card_id"}
                    or not valid_presentation or type(requested_session_id) is not str
                    or type(card_id) is not str or re.fullmatch(r"wc-[0-9a-f]{64}", card_id) is None):
                _emit_safely(bridge.emit, {
                    "type": "work-card.received.done", "status": "invalid_request",
                    "message": "接收回执身份无效；未更新 presentation 状态。",
                    **{key: message.get(key) for key in ("session_id", "presentation_id", "card_id")
                       if type(message.get(key)) is str},
                }, "work-card receipt invalid request response failed")
                return
        elif msg_type == "work-card.result.view":
            presentation_id = message.get("presentation_id")
            result_id = message.get("result_id")
            requirement_id = message.get("requirement_id")
            requested_session_id = message.get("session_id")
            valid_presentation = False
            try:
                valid_presentation = type(presentation_id) is str and str(uuid.UUID(presentation_id)) == presentation_id
            except (ValueError, TypeError, AttributeError):
                pass
            result_request_id = message.get("request_id")
            has_result_request_id = "request_id" in message
            valid_result_request_id = False
            if type(result_request_id) is str and len(result_request_id) == 36:
                try:
                    valid_result_request_id = str(uuid.UUID(result_request_id)) == result_request_id
                except (ValueError, TypeError, AttributeError):
                    pass
            result_fields = {"type", "session_id", "presentation_id", "card_id", "result_id", "requirement_id"}
            if has_result_request_id:
                result_fields.add("request_id")
            if (set(message) != result_fields
                    or (has_result_request_id and not valid_result_request_id)
                    or not valid_presentation or type(requested_session_id) is not str
                    or type(card_id) is not str or re.fullmatch(r"wc-[0-9a-f]{64}", card_id) is None
                    or type(result_id) is not str or not result_id or len(result_id.encode("utf-8")) > 256
                    or type(requirement_id) is not str or not requirement_id or len(requirement_id.encode("utf-8")) > 256):
                _emit_safely(bridge.emit, {
                    "type": "work-card.result.view.done", "status": "invalid_request",
                    "message": "结果查看事件身份无效；未确认任何结果。",
                    **({"request_id": result_request_id} if valid_result_request_id else {}),
                    **{key: message.get(key) for key in ("session_id", "presentation_id", "card_id", "result_id", "requirement_id")
                       if type(message.get(key)) is str},
                }, "work-card result-view invalid request response failed")
                return
        elif msg_type == "work-card.list":
            list_request_id = message.get("request_id")
            valid_list_request_id = False
            if type(list_request_id) is str:
                try:
                    valid_list_request_id = str(uuid.UUID(list_request_id)) == list_request_id
                except (ValueError, TypeError, AttributeError):
                    pass
            if not valid_list_request_id:
                _emit_safely(bridge.emit, {
                    "type": "work-card.list.done", "status": "invalid_request",
                    "message": "列表请求缺少规范 request_id；未读取工作卡。",
                }, "work-card invalid list identity response failed")
                return

            def reject_valid_list_request(text: str) -> None:
                _emit_safely(bridge.emit, {
                    "type": "work-card.list.done", "status": "invalid_request",
                    "request_id": list_request_id,
                    "message": text,
                }, "work-card invalid list response failed")

            allowed_fields = {"type", "request_id"}
            if "limit" in message:
                allowed_fields.add("limit")
                limit_value = message.get("limit")
                if type(limit_value) is not int or not 1 <= limit_value <= 20:
                    reject_valid_list_request("列表参数无效；limit 必须是 1–20 的整数。")
                    return
            if "before_card_id" in message:
                allowed_fields.add("before_card_id")
                cursor_value = message.get("before_card_id")
                if (type(cursor_value) is not str
                        or re.fullmatch(r"wc-[0-9a-f]{64}", cursor_value) is None):
                    reject_valid_list_request("列表游标无效；未读取工作卡。")
                    return
            if set(message) != allowed_fields:
                reject_valid_list_request("列表请求包含不支持字段；未读取工作卡。")
                return
        else:
            return

        def response(status: str, text: str, **fields: Any) -> dict[str, Any]:
            payload: dict[str, Any] = {
                "type": f"work-card.{operation}.done",
                "status": status,
                "message": text,
            }
            if operation in {"create", "list"}:
                payload["request_id"] = request_id
            elif isinstance(card_id, str):
                payload["card_id"] = card_id
            if operation == "result.view":
                if has_result_request_id:
                    payload["request_id"] = result_request_id
                payload.update({key: message.get(key) for key in ("session_id", "presentation_id", "result_id", "requirement_id")})
                if isinstance(card_id, str):
                    payload["card_id"] = card_id
            elif operation in {"received", "snapshot.view"}:
                payload.update({key: message.get(key) for key in ("session_id", "presentation_id")})
                if isinstance(card_id, str):
                    payload["card_id"] = card_id
            payload.update(fields)
            return payload

        session = getattr(repl, "session", None)
        agent = getattr(repl, "agent", None)
        if (not isinstance(session, Session) or getattr(agent, "session", None) is not session
                or (bridge._turn_busy.is_set() and msg_type not in {"work-card.result.view", "work-card.snapshot.view"})
                or (msg_type in {"work-card.result.view", "work-card.snapshot.view"}
                    and not bridge.result_view_lease_is_bound(result_view_lease, session))):
            _emit_safely(bridge.emit, response(
                "session_unavailable", "当前 Session 绑定未确认或仍有活动回合；未读取或修改工作卡。",
            ), "work-card session binding response failed")
            return
        project_id = getattr(getattr(session, "meta", None), "project_id", "")
        if type(project_id) is not str:
            _emit_safely(bridge.emit, response(
                "project_unbound", "当前 Session 项目绑定无法核对；未读取或修改工作卡。",
            ), "work-card binding response failed")
            return

        def operation_body() -> dict[str, Any]:
            try:
                from work_records import WorkRecordStore
                from work_record_cards import (
                    IndependentWorkRecordCardsSnapshot,
                    ProjectWorkRecordCardsSnapshot,
                    build_independent_work_record_card,
                    build_project_work_record_cards,
                )
                from work_card_ledger import WorkCardLedger
                from work_card_presentations import WorkCardPresentations

                if (repl.session is not session or getattr(repl.agent, "session", None) is not session
                        or getattr(session.meta, "project_id", "") != project_id):
                    return response("session_changed", "Session 已切换或项目绑定变化；未读取其他 Session。")
                store = WorkRecordStore(session)
                if store._session is not session or store._bound_project_id != project_id:
                    return response("session_changed", "当前 Session 绑定无法核对；未读取或修改工作卡。")
                ledger = WorkCardLedger(session, store)
                presentations = WorkCardPresentations(session, store, ledger)

                def source_scope(snapshot: Any) -> dict[str, Any]:
                    if type(snapshot) is dict and snapshot.get("kind") == "independent":
                        return {"session_id": session.conversation_id, "kind": "independent"}
                    if type(snapshot) is dict and snapshot.get("kind") == "linked_project_session_copy":
                        return {"session_id": session.conversation_id, "kind": "linked_project_session_copy",
                                "project_id": snapshot["source"]["project_id"], "mapping_claim": "not_asserted"}
                    if type(snapshot) is dict and snapshot.get("kind") == "linked_item_copy":
                        return {"session_id": session.conversation_id, "kind": "linked_item_copy",
                                "project_id": snapshot["project_id"], "item_id": snapshot["item_id"]}
                    return {"session_id": session.conversation_id, "project_id": project_id}

                def public_card(record: Any) -> dict[str, Any] | None:
                    if type(record) is not dict or not all(
                        key in record for key in ("card_id", "snapshot", "saved_at", "viewed_at")
                    ):
                        return None
                    return {key: record[key] for key in ("card_id", "snapshot", "saved_at", "viewed_at")}

                if msg_type == "work-card.received":
                    received_session_id = message["session_id"]
                    if received_session_id != session.conversation_id:
                        return response("rejected", "回执 Session 不匹配；未更新其他 Session。")
                    session.ensure_messages_loaded()
                    if session._messages_corrupt or session._messages_unpersisted:
                        return response("history_unavailable", "当前 Session 引用历史无法复核；未确认接收。")
                    from session import _valid_work_card_ref
                    references = [row for row in session.messages if _valid_work_card_ref(row)
                                  and row.get("presentation_id") == message["presentation_id"]
                                  and row.get("card_id") == card_id]
                    if len(references) != 1 or type(references[0].get("run_id")) is not str:
                        return response("rejected", "当前 Session 没有唯一匹配的持久卡片引用；未确认接收。")
                    run_id = references[0]["run_id"]
                    if _work_card_presentation_id(session.conversation_id, run_id, card_id) != message["presentation_id"]:
                        return response("rejected", "卡片引用与 run 身份不匹配；未确认接收。")
                    existing_receipt = presentations.get(message["presentation_id"])
                    if existing_receipt.status == "not_found":
                        created = presentations.create(message["presentation_id"], card_id, run_id=run_id)
                        if created.status not in {"created", "already_created"}:
                            return response("rejected", "presentation 记录无法在当前 Session 中建立；未确认接收。")
                    elif (existing_receipt.status != "found" or existing_receipt.fact is None
                            or existing_receipt.fact.card_id != card_id
                            or existing_receipt.fact.run_id not in (None, run_id)):
                        return response("rejected", "presentation/card/run 不匹配；未确认接收。")
                    receipt = presentations.mark_received(
                        message["presentation_id"], card_id, session.conversation_id, run_id,
                    )
                    if receipt.status in {"received", "already_received"} and receipt.fact is not None:
                        return response(
                            receipt.status, "已持久化 renderer 接收回执；不代表用户已查看、认可或验收。",
                            received_at=receipt.fact.received_at,
                        )
                    return response("rejected", "presentation/card/run 未通过当前 Session 校验；未更新接收状态。")

                if msg_type == "work-card.create":
                    independent_request = "item_id" in message
                    # Resolve immutable replay before reading/rebuilding source data. The
                    # request scope is part of idempotency, not merely the request UUID.
                    prior = ledger.get_by_request_id(request_id)
                    if prior is not None:
                        prior_snapshot = prior.get("snapshot") if type(prior) is dict else None
                        if independent_request:
                            same_scope = (
                                type(prior_snapshot) is dict
                                and prior_snapshot.get("kind") == "independent"
                                and prior_snapshot.get("item_id") == item_id
                            )
                        else:
                            same_scope = (
                                type(prior_snapshot) is dict
                                and prior_snapshot.get("kind") is None
                                and bool(project_id)
                                and prior_snapshot.get("project_id") == project_id
                            )
                        if not same_scope:
                            return response(
                                "request_conflict", "request_id 已关联不同卡片范围；未覆盖或重新生成原卡。",
                            )
                        card = public_card(prior)
                        if card is None:
                            return response("ledger_unavailable", "已保存卡片无法安全读取；未重新生成。")
                        return response(
                            "already_saved", "复用原已保存快照；未重算、未重复写入，也不表示已送达或已查看。",
                            card_id=card["card_id"], card=card,
                            source_scope=source_scope(card["snapshot"]),
                        )

                    if independent_request:
                        snapshot = build_independent_work_record_card(store, item_id)
                        expected_type = IndependentWorkRecordCardsSnapshot
                    else:
                        if not project_id:
                            return response("project_unbound", "当前 Session 未绑定项目；请提供当前会话独立事项的精确 item_id。")
                        snapshot = build_project_work_record_cards(store, project_id)
                        expected_type = ProjectWorkRecordCardsSnapshot
                    if type(snapshot) is not expected_type or snapshot.completeness != "complete":
                        return response("incomplete", "当前事项快照不完整；未保存工作卡。")
                    if (repl.session is not session or getattr(repl.agent, "session", None) is not session
                            or getattr(session.meta, "project_id", "") != project_id):
                        return response("session_changed", "构建期间 Session 绑定发生变化；未保存工作卡。")
                    store._validate_scope()
                    result = ledger.save_snapshot(request_id, snapshot)
                    if result.status not in {"saved", "already_saved"}:
                        errors = {
                            "conflict": ("request_conflict", "request_id 已关联不同快照或事项范围；未覆盖旧卡。"),
                            "rejected": ("rejected", "当前 Session 或快照身份未通过校验；未保存工作卡。"),
                            "corrupt": ("ledger_unavailable", "当前 Session 工作卡账本损坏；未覆盖原数据。"),
                            "failed": ("save_failed", "工作卡未能确认保存；未重做项目工作。"),
                            "uncertain": ("save_uncertain", "保存结果无法确认；请用同一 request_id 查询，勿重复项目工作。"),
                        }
                        code, text = errors.get(result.status, ("save_failed", "工作卡保存未能确认；未重做项目工作。"))
                        return response(code, text)
                    card = public_card(result.record)
                    if card is None:
                        return response("save_uncertain", "保存结果缺少可核对卡片；请用同一 request_id 查询，勿重复项目工作。")
                    return response(
                        result.status, "当前 Session ledger 已保存只读快照；不表示已送达、已查看、执行完成或验证通过。",
                        card_id=card["card_id"], card=card,
                        source_scope=source_scope(card["snapshot"]),
                    )

                if msg_type == "work-card.snapshot.view":
                    from session import _messages_unusable_for_ref, _session_messages_lock, _valid_work_card_ref
                    from linked_snapshot_view_evidence import LinkedSnapshotViewEvidence
                    if requested_session_id != session.conversation_id:
                        return response("session_changed", "查看事件不属于当前 Session；未记录查看。")
                    # As with result.view, a trusted desktop explicit action is the
                    # boundary; this does not defend against a compromised renderer.
                    session.ensure_messages_loaded()
                    with _session_messages_lock(session.session_dir):
                        if (session._messages_corrupt or session._messages_unpersisted
                                or _messages_unusable_for_ref(session)):
                            return response("history_unavailable", "会话历史无法核对；未记录查看。")
                        refs = [r for r in session.messages if _valid_work_card_ref(r)
                                and r.get("presentation_id") == presentation_id]
                        if (len(refs) != 1 or refs[0].get("card_id") != card_id
                                or type(refs[0].get("run_id")) is not str):
                            return response("rejected", "没有唯一的持久卡片引用；未记录查看。")
                        run_id = refs[0]["run_id"]
                        if _work_card_presentation_id(session.conversation_id, run_id, card_id) != presentation_id:
                            return response("rejected", "卡片引用身份不匹配；未记录查看。")
                    fact = presentations.get(presentation_id)
                    if (fact.status != "found" or fact.fact is None or fact.fact.card_id != card_id
                            or fact.fact.run_id not in (None, run_id)):
                        return response("rejected", "展示身份不匹配；未记录查看。")
                    record = ledger.get_card(card_id)
                    if (type(record) is not dict or type(record.get("snapshot")) is not dict
                            or record["snapshot"].get("kind") not in {"linked_item_copy", "linked_project_session_copy"}):
                        return response("rejected", "卡片不是当前 Session 来源快照；未记录查看。")
                    ack = presentations.acknowledge(presentation_id, card_id, explicit_user_action=True)
                    if ack.status not in {"acknowledged", "already_acknowledged"}:
                        return response("unavailable", "查看记录未能安全保存；可再次明确查看重试。")
                    evidence = LinkedSnapshotViewEvidence(session, store, ledger).record_view(
                        presentation_id, card_id, explicit_user_action=True)
                    if evidence.status not in {"acknowledged", "already_acknowledged"}:
                        return response("unavailable", "计时证据未能安全保存；可再次明确查看重试。")
                    return response(evidence.status, "已记录历史快照的明确查看；不代表当前工作完成或已验证。")

                if msg_type == "work-card.result.view":
                    if requested_session_id != session.conversation_id:
                        return response("session_changed", "结果查看事件属于其他或已切换 Session；未确认结果。")
                    # Client protocol has no action field; it can request only this
                    # explicit renderer detail action. reliable_reply is reserved
                    # for a later host call carrying an internal reply-binding receipt.
                    status = _record_work_card_result_view(
                        session=session, store=store, ledger=ledger, presentations=presentations,
                        session_id=requested_session_id, presentation_id=presentation_id,
                        card_id=card_id, result_id=result_id, requirement_id=requirement_id,
                        action="result_detail",
                    )
                    if status in {"acknowledged", "already_acknowledged"}:
                        fields: dict[str, Any] = {"requirement_id": requirement_id}
                        try:
                            record = ledger.get_card(card_id)
                            snapshot = record.get("snapshot") if type(record) is dict else None
                            result_item_id = _work_card_snapshot_item_for_result(
                                snapshot, session_id=session.conversation_id,
                                requirement_id=requirement_id, result_id=result_id,
                            )
                            if result_item_id is not None and bridge.result_view_lease_is_bound(result_view_lease, session):
                                from work_record_cards import project_local_assistant_reply_excerpt_detail
                                detail = project_local_assistant_reply_excerpt_detail(
                                    store, session_id=session.conversation_id, item_id=result_item_id,
                                    requirement_id=requirement_id, result_id=result_id,
                                )
                                if detail is not None and bridge.result_view_lease_is_bound(result_view_lease, session):
                                    fields["local_assistant_reply_excerpt_detail"] = {
                                        name: getattr(detail, name) for name in (
                                            "session_id", "item_id", "requirement_id", "result_id", "label",
                                            "escaped_excerpt_text", "source_availability", "result_status", "verification_status",
                                        )
                                    }
                        except Exception:
                            pass  # Existing view ACK remains valid; no detail is returned.
                        return response(status, "已记录该结果的显式详情展开；不代表认可、完成验收或交付。", **fields)
                    if status == "history_unavailable":
                        return response(status, "当前会话历史无法核对；未确认结果。")
                    if status == "unavailable":
                        return response(status, "结果查看确认未能安全完成；未确认结果。")
                    return response("rejected", "结果查看身份或其不可变快照不匹配；未确认结果。")
                if msg_type == "work-card.get":
                    card = public_card(ledger.get_card(card_id))
                    if card is None:
                        return response("not_found", "当前 Session ledger 中没有该工作卡；未读取其他 Session。")
                    return response(
                        "found", "返回当前 Session 中已保存的只读卡片；未自动标记已查看。",
                        card=card, source_scope=source_scope(card["snapshot"]),
                    )

                if msg_type == "work-card.list":
                    page_limit = message.get("limit", 20)
                    cursor = message.get("before_card_id")
                    records = ledger.list_cards()
                    if repl.session is not session or getattr(repl.agent, "session", None) is not session:
                        return response("session_changed", "Session 已切换；未返回其他 Session 的卡片索引。")
                    store._validate_scope()
                    cursor_index = len(records)
                    if cursor is not None:
                        cursor_index = next(
                            (index for index, row in enumerate(records) if row.get("card_id") == cursor),
                            -1,
                        )
                        if cursor_index < 0:
                            return response("invalid_cursor", "游标不属于当前 Session ledger；未返回卡片索引。")
                    eligible = records[:cursor_index]
                    selected = eligible[-page_limit:][::-1]

                    def bounded_title(value: Any, fallback: str) -> str:
                        if type(value) is not str:
                            return fallback
                        cleaned = re.sub(r"\s+", " ", "".join(
                            char if char.isprintable() else " " for char in value
                        )).strip()
                        if not cleaned:
                            return fallback
                        return cleaned[:120]

                    def listing_entry(record: Any) -> dict[str, Any] | None:
                        if type(record) is not dict:
                            return None
                        snapshot = record.get("snapshot")
                        if type(snapshot) is not dict:
                            return None
                        if snapshot.get("kind") == "linked_project_session_copy":
                            source = snapshot["source"]
                            scope = {"kind": "linked_project_session_copy", "project_id": source["project_id"],
                                     "source_session_id": source["source_session_id"], "mapping_claim": "not_asserted"}
                            title = bounded_title(source["project_id"], "来源 Session 项目副本")
                        elif snapshot.get("kind") == "linked_item_copy":
                            scope = {"kind": "linked_item_copy", "project_id": snapshot["project_id"],
                                     "item_id": snapshot["item_id"]}
                            title = bounded_title(snapshot.get("item_title"), "事项副本")
                        elif snapshot.get("kind") == "independent":
                            item_id_value = snapshot.get("item_id")
                            independent_card = snapshot.get("card")
                            if type(item_id_value) is not str or type(independent_card) is not dict:
                                return None
                            scope = {"kind": "independent", "item_id": item_id_value}
                            title = bounded_title(independent_card.get("title"), "独立事项卡片")
                        else:
                            project_value = snapshot.get("project_id")
                            source_cards = snapshot.get("cards")
                            if type(project_value) is not str or type(source_cards) is not list:
                                return None
                            scope = {"kind": "project", "project_id": project_value}
                            first_title = next((
                                row.get("title") for row in source_cards
                                if type(row) is dict and type(row.get("title")) is str
                            ), None)
                            title = bounded_title(first_title, f"项目工作卡（{len(source_cards)}项）")
                            if len(source_cards) > 1:
                                suffix = f" 等{len(source_cards)}项"
                                title = title[:max(0, 120 - len(suffix))] + suffix
                        card_identity = record.get("card_id")
                        saved_at = record.get("saved_at")
                        viewed_at = record.get("viewed_at")
                        if (type(card_identity) is not str or re.fullmatch(r"wc-[0-9a-f]{64}", card_identity) is None
                                or type(saved_at) is not str
                                or (viewed_at is not None and type(viewed_at) is not str)):
                            return None
                        return {
                            "card_id": card_identity,
                            "scope": scope,
                            "saved_at": saved_at,
                            "viewed_at": viewed_at,
                            "title": title,
                        }

                    entries = [listing_entry(row) for row in selected]
                    if any(entry is None for entry in entries):
                        return response("ledger_unavailable", "当前 Session ledger 无法安全生成卡片索引。")
                    has_more = len(eligible) > len(selected)
                    next_cursor = selected[-1]["card_id"] if has_more and selected else None
                    return response(
                        "ok", "仅返回当前 Session 的紧凑工作卡索引；没有读取完整卡片或标记已查看。",
                        items=entries, has_more=has_more, next_cursor=next_cursor,
                        source_scope={"session_id": session.conversation_id},
                    )

                # Read first to establish the current-Session card's kind/scope before
                # acknowledging. The ledger itself rejects unknown and foreign IDs.
                existing = public_card(ledger.get_card(card_id))
                if existing is None:
                    return response("rejected", "当前 Session ledger 未确认此工作卡；未访问其他 Session。")
                ack = ledger.acknowledge_viewed(card_id)
                if ack.status in {"acknowledged", "already_acknowledged"}:
                    return response(
                        ack.status, "已记录显式查看确认；不代表执行完成或验证通过。",
                        viewed_at=ack.viewed_at, source_scope=source_scope(existing["snapshot"]),
                    )
                return response("rejected", "当前 Session ledger 未确认此工作卡；未访问其他 Session。")
            except Exception:
                # No exception text, paths, client payload, or sidecar contents are echoed.
                return response("unavailable", "当前工作卡操作未能安全完成；没有重做或自动重试项目工作。")

        async def run_operation():
            if msg_type == "work-card.received":
                async with bridge._work_card_receipt_lock:
                    return await asyncio.to_thread(operation_body)
            return await asyncio.to_thread(operation_body)

        operation_task = asyncio.create_task(run_operation())
        try:
            result = await asyncio.shield(operation_task)
        except asyncio.CancelledError:
            # Cancelling to_thread's waiter does not stop its worker. Keep the caller's
            # Session lease until the actual disk operation has exited.
            while not operation_task.done():
                try:
                    await asyncio.shield(operation_task)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            try:
                operation_task.result()
            except BaseException:
                pass
            raise
        except Exception:
            result = response("unavailable", "当前工作卡操作未能安全完成；没有重做或自动重试项目工作。")
        if repl.session is not session or getattr(repl.agent, "session", None) is not session:
            result = response("session_changed", "Session 已切换；结果未用于新 Session，也未读取其他 Session。")
        _emit_safely(bridge.emit, result, "work-card protocol response delivery failed")

    async def _dispatch(
        self,
        message: dict[str, Any],
        repl: ConversationRepl,
        bridge: WsBridge,
        *,
        result_view_lease: object | None = None,
    ) -> None:
        msg_type = message.get("type")
        if not isinstance(msg_type, str):
            emit_error(bridge, "missing type")
            return
        if (self.managed_readonly_context is not None
                and (msg_type in _WORK_CARD_MESSAGE_TYPES or msg_type == _WORK_RECORDS_OVERVIEW_QUERY)):
            emit_error(bridge, "managed read-only mode rejects work-card protocol")
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
        if msg_type == "work-record.capture":
            await self._dispatch_work_record_capture(message, repl, bridge)
            return

        if msg_type == _WORK_RECORDS_OVERVIEW_QUERY:
            await self._dispatch_work_records_overview(message, repl, bridge)
            return

        if msg_type == "work-card.receipt.continue":
            await self._dispatch_work_card_receipt_continue(message, repl, bridge)
            return

        if msg_type in _WORK_CARD_MESSAGE_TYPES:
            await self._dispatch_work_card_protocol(
                message, repl, bridge, result_view_lease=result_view_lease,
            )
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
            if not attachment_ids and _is_day_journal_summary_request(text):
                await self._handle_day_journal_summary(
                    text, repl, bridge, request_id=message.get("request_id"),
                )
                return
            from file_stage import compose_user_message

            attachment_session = repl.session
            attachments = self._file_stage.take(attachment_session.conversation_id, attachment_ids)
            if attachment_ids and len(attachments) != len(attachment_ids):
                emit_error(bridge, "unknown or expired attachment id")
                return

            line = compose_user_message(text=text, attachments=attachments)
            prestarted_run_id: str | None = None
            work_record_binding: tuple[Any, Any] | None = None
            post_turn_candidate: _FrozenPostTurnCandidate | None = None
            if not attachments and _looks_like_unsupported_day_journal_query(text):
                day = _parse_day_journal_request(text)
                if day is not None:
                    await self._handle_day_journal_recall(
                        text, day, repl, bridge, request_id=message.get("request_id"),
                    )
                    return
                intent = await self._classify_day_journal_intent(text, repl, bridge)
                if intent.get("action") == "recall":
                    await self._handle_day_journal_recall(
                        text, None, repl, bridge,
                        start_date=intent.get("start_date"), end_date=intent.get("end_date"),
                        request_id=message.get("request_id"),
                    )
                    return
                if intent.get("action") == "uncertain":
                    await self._handle_day_journal_recall(
                        text, None, repl, bridge, request_id=message.get("request_id"),
                    )
                    return
                # A confidently non-journal utterance continues through ordinary chat.
            # Only a clear local one-shot recording phrase starts the separate
            # record-classification consent flow. Normal chat and recall stay quiet.
            if not attachments:
                try:
                    from work_records_service import is_explicit_record_request, WorkRecordCaptureService
                    if is_explicit_record_request(text):
                        # Begin this message's run before consent: this clears a
                        # stale cancel_event from an older run and makes a new
                        # turn.cancel stop both capture and the follow-up _run_line.
                        capture_session = repl.session
                        prestarted_run_id = _begin_work_record_capture_turn(repl, bridge)
                        if prestarted_run_id is None:
                            return
                        from llm_client import LLMClient
                        client = LLMClient(paths=self.paths)
                        client.set_cancel_event(bridge.cancel_event)
                        service = WorkRecordCaptureService(
                            session=repl.session,
                            client=client,
                            confirm=lambda preview: bridge.confirm_with_receipt(preview, False),
                        )
                        cancelled_capture: list[Any] = []

                        def report_cancelled_capture(outcome: Any | None, error: BaseException | None) -> None:
                            if outcome is not None:
                                cancelled_capture.append(outcome)
                                if not _emit_capture_reconciliation_notice(bridge, outcome):
                                    emit_work_record_outcome_notice(bridge.emit, outcome)
                            else:
                                bridge.emit({
                                    "type": "turn.notice", "level": "warn",
                                    "text": "记录确认期间收到取消；已等待本地处理结束，但实际写入结果可能不确定，请核对记录。",
                                })

                        outcome = await _capture_work_record_until_thread_stops(
                            service, text, request_id=message.get("request_id"),
                            bridge=bridge, on_cancelled=report_cancelled_capture,
                            track_run=True,
                        )
                        if _emit_capture_reconciliation_notice(bridge, outcome):
                            _finish_work_record_capture_turn(
                                bridge, prestarted_run_id, finish_reason="error", repl=repl,
                            )
                            prestarted_run_id = None
                            return
                        if getattr(outcome, "status", None) == "not_authorized":
                            notice = (
                                "本次未获分类外发授权，本回合已安全停止："
                                "未进入普通 Agent、未执行工具、未写入工作记录。"
                            )
                            # Preserve the original user's attribution only in the
                            # still-bound session; a real cancellation wins the race.
                            with bridge._result_view_write_lock:
                                if (not bridge.cancel_event.is_set()
                                        and not bridge._connection_closed.is_set()
                                        and repl.session is capture_session):
                                    history_notice = (
                                        notice + "原文按用户消息保存在当前对话中，后续对话可能发送。"
                                    )
                                    try:
                                        capture_session.append_message({"role": "user", "content": text})
                                        capture_session.append_message({"role": "assistant", "content": history_notice})
                                    except Exception:
                                        notice += "对话历史保存未能确认，请核对。"
                                    else:
                                        notice = history_notice
                            _emit_safely(
                                bridge.emit_turn_event,
                                {"type": "turn.notice", "level": "warn", "text": notice},
                                "work-record refusal notice failed",
                            )
                            _finish_work_record_capture_turn(
                                bridge, prestarted_run_id, finish_reason="stopped", repl=repl,
                            )
                            prestarted_run_id = None
                            return
                        emit_work_record_outcome_notice(bridge.emit, outcome)
                        if getattr(outcome, "status", None) == "recorded":
                            work_record_binding = _begin_work_record_run_binding(
                                session=capture_session, capture=outcome,
                                run_id=prestarted_run_id, current_session=repl.session,
                                bridge=bridge, emit=bridge.emit_turn_event,
                                request_id=message.get("request_id"),
                            )
                            if work_record_binding is _DUPLICATE_CAPTURE_RUN or work_record_binding is _UNCONFIRMED_CAPTURE_RUN:
                                _finish_work_record_capture_turn(
                                    bridge, prestarted_run_id, finish_reason="error", repl=repl,
                                )
                                prestarted_run_id = None
                                return
                except asyncio.CancelledError:
                    if prestarted_run_id is not None:
                        if cancelled_capture and not _capture_requires_reconciliation(cancelled_capture[-1]):
                            work_record_binding = _begin_work_record_run_binding(
                                session=capture_session, capture=cancelled_capture[-1],
                                run_id=prestarted_run_id, current_session=repl.session,
                                bridge=bridge, emit=bridge.emit_turn_event,
                                request_id=message.get("request_id"),
                            )
                            if work_record_binding is _DUPLICATE_CAPTURE_RUN or work_record_binding is _UNCONFIRMED_CAPTURE_RUN:
                                work_record_binding = None
                        _finish_work_record_capture_turn(
                            bridge, prestarted_run_id, finish_reason="cancelled",
                            work_record_binding=work_record_binding,
                            current_session=repl.session, repl=repl,
                        )
                        prestarted_run_id = None
                    raise
                except Exception:
                    # Before a run starts, import/local-gate failures retain ordinary-chat fallback.
                    # After prestart, an exception may follow a committed side effect, so block.
                    if prestarted_run_id is not None:
                        bridge.emit_turn_event({
                            "type": "turn.notice", "level": "warn",
                            "text": "工作记录捕获在本轮启动后异常，无法确认是否已有提交；为防重复副作用，本轮不自动继续，请核对当前记录与运行状态。",
                        })
                        _finish_work_record_capture_turn(
                            bridge, prestarted_run_id, finish_reason="error", repl=repl,
                        )
                        prestarted_run_id = None
                        return
                    # No lifecycle run began; keep the existing fallback and avoid
                    # exposing source text, provider diagnostics, paths, or credentials.
                    bridge.emit({
                        "type": "turn.notice", "level": "warn",
                        "text": "工作记录本地预检查不可用；普通对话仍会继续。",
                    })
            try:
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
                    post_turn_candidate = await _run_line(
                        repl, bridge, line, self.paths, lock_held=True,
                        prestarted_run_id=prestarted_run_id,
                        work_record_binding=work_record_binding,
                        post_turn_request_id=(message.get("request_id")
                            if (getattr(self, '_post_turn_commit_ready', False)
                                or (bridge._auto_recognition_session is repl.session
                                    and bridge._auto_recognition_turn_generation is not None
                                    and bridge._auto_recognition_turn_generation == bridge._post_turn_generation))
                            and not attachments and prestarted_run_id is None else None),
                        post_turn_source_text=(text if not attachments else None),
                        explicit_result_capture=(getattr(self, '_explicit_run_excerpt_ready', False)
                            and work_record_binding is not None),
                        external_user_message_request_id=(
                            message.get("request_id")
                            if prestarted_run_id is None and work_record_binding is None
                            and (not attachment_ids or (
                                len(attachments) == len(attachment_ids)
                                and repl.session is attachment_session
                            )) else None
                        ),
                        external_user_message_attachment_session_id=(
                            attachment_session.conversation_id
                            if attachment_ids and len(attachments) == len(attachment_ids)
                            else None
                        ),
                    )

                finally:
                    repl.session._turn_companion_world_context = None
                repl.session._turn_active_doc = ""
                repl.session._turn_active_doc_selection = ""
                if _repl_refreshes_session_state(line):
                    bridge.emit_session_state(repl.session)
                    _emit_session_list(bridge, self.paths)
            finally:
                if prestarted_run_id is not None:
                    _finish_work_record_capture_turn(
                        bridge, prestarted_run_id, finish_reason="error",
                        work_record_binding=work_record_binding,
                        current_session=repl.session, repl=repl,
                    )
            return post_turn_candidate

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

        if msg_type == "session.restart_today":
            await self._dispatch_session_control(message, repl, bridge)
            return

        if msg_type == "day_journal.summary.status":
            await self._emit_day_journal_summary_status(repl, bridge, message)
            return

        if msg_type == "day_journal.refresh_mechanical":
            await self._handle_day_journal_refresh_mechanical(repl, bridge, message)
            return

        if msg_type == "day_journal.suggest_summary":
            if bridge._turn_busy.is_set():
                emit_error(bridge, "回合进行中，结束后再生成项目摘要")
                return
            request_id = message.get("request_id")
            ws_done = {"type": "day_journal.suggest_summary.done"}
            if request_id is not None:
                ws_done["request_id"] = request_id
            await self._handle_day_journal_summary(
                "",
                repl,
                bridge,
                request_id=request_id,
                persist_user_question=False,
                ws_done=ws_done,
            )
            return

        if isinstance(msg_type, str) and msg_type.startswith("day_journal."):
            await self._dispatch_day_journal(message, bridge)
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
                _emit_pending_work_card_references(repl, bridge)
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
                _emit_pending_work_card_references(repl, bridge)
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
            confirm = message.get("confirm") is True
            current_mode = repl.session.meta.turn_mode
            if current_mode != mode_raw and not confirm:
                if mode_raw == "agent":
                    prompt = "切换到动手模式将允许写文件与执行命令。"
                else:
                    prompt = "切换到只聊模式将禁止写文件与执行命令。"
                request_id = message.get("request_id")
                rid = request_id.strip() if isinstance(request_id, str) and request_id.strip() else str(uuid.uuid4())
                bridge.emit(
                    {
                        "type": "session.turn_mode.request",
                        "request_id": rid,
                        "from_mode": current_mode,
                        "to_mode": mode_raw,
                        "message": prompt,
                    }
                )
                return
            repl.session.set_turn_mode(mode_raw)
            repl.agent.executor.session.turn_mode = repl.session.meta.turn_mode
            repl.session.save()
            bridge.emit_session_state(repl.session)
            label = "动手 (Agent)" if repl.session.meta.turn_mode == "agent" else "只聊 (Ask)"
            bridge.emit({"type": "notice", "text": f"已切换模式：{label}"})
            if confirm:
                bridge.emit(
                    {
                        "type": "session.turn_mode.done",
                        "request_id": message.get("request_id"),
                        "applied": True,
                        "to_mode": repl.session.meta.turn_mode,
                    }
                )
            return

        if msg_type == "memory.list":
            bridge.emit(_memories_payload(self.paths))
            bridge.emit(_proposals_payload(self.paths))
            return

        if msg_type == "proposal.accept":
            proposal_id = message.get("proposal_id")
            if not isinstance(proposal_id, str) or not proposal_id.strip():
                emit_error(bridge, "proposal.accept requires proposal_id")
                return
            pid = proposal_id.strip()
            confirm = message.get("confirm") is True
            request_id = message.get("request_id")
            rid = request_id.strip() if isinstance(request_id, str) and request_id.strip() else None
            try:
                from memory_proposal_confirm import (
                    build_memory_proposal_accept_request,
                    memory_proposal_accept_requires_confirm,
                )
                from evolve import find_proposal_path, load_proposal_file

                proposal_path = find_proposal_path(self.paths.evolve, pid)
                needs_memory_confirm = False
                if proposal_path is not None:
                    record = load_proposal_file(proposal_path)
                    needs_memory_confirm = memory_proposal_accept_requires_confirm(record)
                    if needs_memory_confirm and not confirm:
                        bridge.emit(
                            build_memory_proposal_accept_request(
                                self.paths,
                                pid,
                                request_id=rid,
                            )
                        )
                        return
                result = accept_proposal(
                    pid,
                    paths=self.paths,
                    conversation_id=repl.session.conversation_id,
                )
                bridge.emit({"type": "notice", "text": result.message})
                if confirm and needs_memory_confirm:
                    bridge.emit(
                        {
                            "type": "memory.proposal.accept.done",
                            "request_id": rid,
                            "proposal_id": pid,
                            "applied": True,
                        }
                    )
                bridge.emit(_proposals_payload(self.paths))
                bridge.emit(_memories_payload(self.paths))
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
                bridge.emit(_memories_payload(self.paths))
            except EvolveError as exc:
                emit_error(bridge, str(exc))
            return

        if isinstance(msg_type, str) and msg_type.startswith("host_scope."):
            await self._dispatch_host_scope(message, bridge)
            return

        if isinstance(msg_type, str) and msg_type.startswith("llm_keys."):
            await self._dispatch_llm_keys(message, bridge)
            return

        if isinstance(msg_type, str) and msg_type.startswith("llm_role_models."):
            await self._dispatch_llm_role_models(message, bridge)
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
            if isinstance(payload, dict) and "_events" in payload:
                if "_session" in payload:
                    repl.session = payload["_session"]
                    repl._rebind_agent()
                _refresh_repl_project_binding(repl)
                if message.get("type") == "project.switch":
                    done = next(
                        (event for event in payload["_events"]
                         if isinstance(event, dict) and event.get("type") == "project.switch.done"),
                        None,
                    )
                    await asyncio.to_thread(
                        self._record_project_switch_for_connection,
                        bridge,
                        repl.session,
                        done,
                        lambda text: bridge.emit({"type": "notice", "text": text}),
                    )
                for event in payload["_events"]:
                    bridge.emit(event)
                return
            _refresh_repl_project_binding(repl)
            bridge.emit(payload)
        except ProjectApiError as exc:
            emit_error(bridge, str(exc))
        except Exception as exc:
            log_sidecar_exception("project dispatch failed", exc)
            emit_error(bridge, f"project request failed: {exc}")

    async def _dispatch_session_control(
        self,
        message: dict[str, Any],
        repl: ConversationRepl,
        bridge: WsBridge,
    ) -> None:
        from session_api import SessionApiError, dispatch_session_message

        try:
            payload = await asyncio.to_thread(
                dispatch_session_message,
                repl.session,
                self.paths,
                message,
            )
            if isinstance(payload, dict) and "_events" in payload:
                if "_session" in payload:
                    repl.session = payload["_session"]
                    repl._rebind_agent()
                _refresh_repl_project_binding(repl)
                for event in payload["_events"]:
                    bridge.emit(event)
                bridge.emit_session_state(repl.session)
                return
            bridge.emit(payload)
        except SessionApiError as exc:
            emit_error(bridge, str(exc))
        except Exception as exc:
            log_sidecar_exception("session control dispatch failed", exc)
            emit_error(bridge, f"session request failed: {exc}")

    async def _emit_day_journal_summary_status(
        self,
        repl: ConversationRepl,
        bridge: WsBridge,
        message: dict[str, Any],
    ) -> None:
        from day_journal import DayJournal
        from day_journal_project_summary import evaluate_summary_readiness

        journal = self.project_boundary_journal
        if journal is None:
            journal = DayJournal(self.paths.data / "day-journal")
        result = await asyncio.to_thread(evaluate_summary_readiness, repl.session, journal)
        payload = {"type": "day_journal.summary.status", **result}
        request_id = message.get("request_id")
        if request_id is not None:
            payload["request_id"] = request_id
        bridge.emit(payload)

    async def _handle_day_journal_refresh_mechanical(
        self,
        repl: ConversationRepl,
        bridge: WsBridge,
        message: dict[str, Any],
    ) -> None:
        from day_journal_compact import refresh_mechanical_day_summary
        from day_journal_directory import build_directory_payload

        result = await asyncio.to_thread(refresh_mechanical_day_summary, repl.session, self.paths)
        payload = {
            "type": "day_journal.refresh_mechanical.done",
            "ok": bool(result.get("updated")),
            **result,
        }
        request_id = message.get("request_id")
        if request_id is not None:
            payload["request_id"] = request_id
        bridge.emit(payload)
        directory = await asyncio.to_thread(
            build_directory_payload,
            self.paths.data / "day-journal",
        )
        bridge.emit(directory)

    async def _dispatch_day_journal(
        self,
        message: dict[str, Any],
        bridge: WsBridge,
    ) -> None:
        from day_journal_api import DayJournalApiError, dispatch_day_journal_message

        try:
            payload = await asyncio.to_thread(dispatch_day_journal_message, self.paths, message)
            bridge.emit(payload)
        except DayJournalApiError as exc:
            emit_error(bridge, str(exc))
        except Exception as exc:
            log_sidecar_exception("day journal dispatch failed", exc)
            emit_error(bridge, f"day journal request failed: {exc}")

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

    async def _dispatch_llm_role_models(
        self,
        message: dict[str, Any],
        bridge: WsBridge,
    ) -> None:
        from llm_role_model_preferences import LlmRoleModelPreferenceError, dispatch_llm_role_models_message

        try:
            payloads = await asyncio.to_thread(
                dispatch_llm_role_models_message,
                self.paths,
                message,
            )
            for payload in payloads:
                # A failed set must finish the UI's pending request, not look
                # like an unsolicited state refresh.
                if (message.get("type") == "llm_role_models.set"
                        and payload.get("type") == "llm_role_models.state"
                        and payload.get("status") == "rejected"):
                    payload = {**payload, "type": "llm_role_models.done"}
                bridge.emit(payload)
        except LlmRoleModelPreferenceError as exc:
            bridge.emit({"type": "llm_role_models.done", "status": "rejected", "message": str(exc)})

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
        "work-record.capture",
        "work-card.create",
        "work-card.get",
        "work-card.seen",
        "work-card.list",
        "work-card.result.view",
        "work-card.snapshot.view",
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


def emit_work_record_outcome_notice(emit: Callable[[dict[str, Any]], None], outcome: Any) -> None:
    """Surface only the service's safe outcome message on the existing notice channel."""
    message = getattr(outcome, "message", None)
    if not isinstance(message, str) or not message.strip():
        return
    status = getattr(outcome, "status", None)
    emit({
        "type": "turn.notice",
        "level": "info" if status == "recorded" else "warn",
        "text": message,
    })


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
    work_record_controller_factory: WorkRecordControllerFactory | None = None,
    project_boundary_recorder: Callable[..., Any] | None = None,
    project_boundary_journal: Any | None = None,
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
        work_record_controller_factory=work_record_controller_factory,
        project_boundary_recorder=project_boundary_recorder,
        project_boundary_journal=project_boundary_journal,
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
    assert events[1]["type"] == "session.history"
    assert events[2]["type"] == "session.memory"
    types = [e["type"] for e in events]
    assert "evolve.proposals" in types
    assert "evolve.memories" in types
    print("[PASS] T-904a: session.banner + history + memory + proposals + memories")

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
