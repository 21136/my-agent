"""Session resume, persistence, and conversation state (RUNTIME.md §2, TASKS T-203)."""

from __future__ import annotations

import json
import hashlib
import contextlib
import os
import re
import secrets
import stat
import sys
import threading
import tempfile
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

_AGENT_CORE = Path(__file__).resolve().parent
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from llm_client import HOST_INTERNAL_METADATA_KEY, resolve_session_model
from paths import AgentPaths

SESSIONS_DIRNAME = "sessions"
GOAL_FILENAME = "goal.md"
GOAL_PROMPT = "这次主要做什么？"
META_FILENAME = "meta.json"
MESSAGES_FILENAME = "messages.jsonl"
# HOST_INTERNAL_METADATA_KEY is reserved, not authority: future writers must
# authenticate provenance and reject client injection before acting on its data.
DIGEST_FILENAME = "digest.md"
TOOL_OUTPUTS_DIRNAME = "tool_outputs"
STATE_FILENAME = "state.json"
STATE_LAST_SESSION_KEY = "last_conversation_id"
STATE_TERMINAL_LAST_SESSION_KEY = "terminal_last_session"

VALID_PHASES = frozenset({"S1", "S2", "S3", "S4"})
SessionPhase = Literal["S1", "S2", "S3", "S4"]

TurnMode = Literal["ask", "agent"]
DEFAULT_TURN_MODE: TurnMode = "agent"
VALID_TURN_MODES = frozenset({"ask", "agent"})

def normalize_evolution_id(value: Any) -> str:
    if not isinstance(value, str) or not value:
        return ""
    try:
        return value if str(uuid.UUID(value)) == value else ""
    except (ValueError, AttributeError, TypeError):
        return ""

# Deprecated since shell consolidation (Phase 3). Kept for old session compatibility.
ShellId = Literal["grow", "daily", "govern", "project", "unified"]
DEFAULT_ACTIVE_SHELL: ShellId = "grow"
VALID_SHELLS = frozenset({"grow", "daily", "govern", "project", "unified"})

PlanStatus = Literal["", "draft", "confirmed", "plan_dirty"]
VALID_PLAN_STATUSES = frozenset({"", "draft", "confirmed", "plan_dirty"})

ProjectWorkflowStage = Literal[
    "requirements",
    "documentation",
    "design",
    "implementation",
    "verification",
    "release",
]
VALID_PROJECT_WORKFLOW_STAGES = frozenset(
    {"requirements", "documentation", "design", "implementation", "verification", "release"}
)

ProjectDeliveryProfile = Literal["solo", "ritual"]
DEFAULT_PROJECT_DELIVERY_PROFILE: ProjectDeliveryProfile = "solo"
VALID_PROJECT_DELIVERY_PROFILES = frozenset({"solo", "ritual"})

ProjectEntry = Literal["", "plan", "direct"]
DEFAULT_PROJECT_ENTRY: ProjectEntry = ""
VALID_PROJECT_ENTRIES = frozenset({"", "plan", "direct"})

HarnessKind = Literal["desktop", "terminal"]
DEFAULT_HARNESS: HarnessKind = "desktop"
VALID_HARNESSES = frozenset({"desktop", "terminal"})

TerminalScopeKind = Literal["agent", "host", "foreign"]
VALID_TERMINAL_SCOPE_KINDS = frozenset({"agent", "host", "foreign"})

ANCHOR_HEADER = "[本次会议上下文]"


class SessionError(Exception):
    """Invalid session operation."""


class HarnessMismatchError(SessionError):
    """TM-4: session harness does not match entry harness."""

    def __init__(
        self,
        conversation_id: str,
        *,
        session_harness: HarnessKind,
        entry_harness: HarnessKind,
    ) -> None:
        self.conversation_id = conversation_id
        self.session_harness = session_harness
        self.entry_harness = entry_harness
        super().__init__(
            format_harness_mismatch_message(
                conversation_id,
                session_harness=session_harness,
                entry_harness=entry_harness,
            )
        )


def format_harness_mismatch_message(
    conversation_id: str,
    *,
    session_harness: HarnessKind,
    entry_harness: HarnessKind,
) -> str:
    """Readable TM-4 rejection copy."""
    if entry_harness == "desktop" and session_harness == "terminal":
        return (
            f"会话 {conversation_id} 为 Terminal 会话，桌面入口不可续接。"
            "请先 exit Terminal，再在桌面续接 desktop 会话。"
        )
    if entry_harness == "terminal" and session_harness == "desktop":
        return (
            f"会话 {conversation_id} 为 Desktop 会话，Terminal 入口不可续接。"
            "请使用 my-agent terminal 续接 Terminal 会话或新建。"
        )
    return (
        f"会话 {conversation_id} harness 不匹配："
        f"meta={session_harness!r}，入口={entry_harness!r}"
    )


@dataclass
class SessionMeta:
    """Persisted session metadata (RUNTIME.md §2.2 meta.json)."""

    topics: list[str] = field(default_factory=list)
    llm_model: str = ""
    llm_model_override: bool = False
    execution_model: str = ""
    planning_model: str = ""
    updated_at: str = ""
    phase: SessionPhase = "S1"
    workspace_evolved_approved: bool = False
    evolution_id: str = ""
    pending_feedback: list[dict[str, Any]] = field(default_factory=list)
    compact_before_index: int = 0
    evolve_offer_pending: bool = False
    evolve_offer_used: bool = False
    turn_mode: TurnMode = DEFAULT_TURN_MODE
    reasoning_effort: str = "medium"  # low | medium | high | max
    active_shell: ShellId = DEFAULT_ACTIVE_SHELL
    project_root: str = ""
    project_id: str = ""
    project_plan_status: PlanStatus = ""
    project_workflow_stage: ProjectWorkflowStage = "requirements"
    project_design_confirmed_at: str = ""
    project_active_task_id: str = ""
    project_plan_confirmed_at: str = ""
    project_scope_confirmed_at: str = ""
    project_phase_fingerprint: str = ""
    project_doc_fingerprint: str = ""
    project_delivery_profile: ProjectDeliveryProfile = DEFAULT_PROJECT_DELIVERY_PROFILE
    project_entry: ProjectEntry = DEFAULT_PROJECT_ENTRY
    harness: HarnessKind = DEFAULT_HARNESS
    terminal_scope_kind: TerminalScopeKind | Literal[""] = ""
    terminal_cwd: str = ""
    terminal_foreign_root: str = ""
    terminal_host_id: str = ""
    write_scope_root: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "topics": list(self.topics),
            "llm_model": self.llm_model,
            "llm_model_override": self.llm_model_override,
            "execution_model": self.execution_model,
            "planning_model": self.planning_model,
            "updated_at": self.updated_at,
            "phase": self.phase,
            "workspace_evolved_approved": self.workspace_evolved_approved,
            "pending_feedback": list(self.pending_feedback),
            "compact_before_index": self.compact_before_index,
            "evolve_offer_pending": self.evolve_offer_pending,
            "evolve_offer_used": self.evolve_offer_used,
            "turn_mode": self.turn_mode,
            "reasoning_effort": self.reasoning_effort,
            "active_shell": self.active_shell,
            "project_root": self.project_root,
            "project_id": self.project_id,
            "project_plan_status": self.project_plan_status,
            "project_workflow_stage": self.project_workflow_stage,
            "project_design_confirmed_at": self.project_design_confirmed_at,
            "project_active_task_id": self.project_active_task_id,
            "project_plan_confirmed_at": self.project_plan_confirmed_at,
            "project_scope_confirmed_at": self.project_scope_confirmed_at,
            "project_phase_fingerprint": self.project_phase_fingerprint,
            "project_doc_fingerprint": self.project_doc_fingerprint,
            "project_delivery_profile": self.project_delivery_profile,
            "project_entry": self.project_entry,
            "harness": self.harness,
            "terminal_scope_kind": self.terminal_scope_kind,
            "terminal_cwd": self.terminal_cwd,
            "terminal_foreign_root": self.terminal_foreign_root,
            "terminal_host_id": self.terminal_host_id,
            "write_scope_root": self.write_scope_root,
            "evolution_id": self.evolution_id,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> SessionMeta:
        topics_raw = payload.get("topics", [])
        topics = [str(item) for item in topics_raw] if isinstance(topics_raw, list) else []

        phase_raw = payload.get("phase", "S1")
        phase: SessionPhase = phase_raw if phase_raw in VALID_PHASES else "S1"

        feedback_raw = payload.get("pending_feedback", [])
        pending_feedback: list[dict[str, Any]] = []
        if isinstance(feedback_raw, list):
            pending_feedback = [item for item in feedback_raw if isinstance(item, dict)]

        llm_model = payload.get("llm_model", "")
        if not isinstance(llm_model, str):
            llm_model = ""

        llm_model_override = bool(payload.get("llm_model_override", False))

        execution_model = payload.get("execution_model", "")
        if not isinstance(execution_model, str):
            execution_model = ""
        planning_model = payload.get("planning_model", "")
        if not isinstance(planning_model, str):
            planning_model = ""

        updated_at = payload.get("updated_at", "")
        if not isinstance(updated_at, str):
            updated_at = ""

        active_shell_raw = payload.get("active_shell", DEFAULT_ACTIVE_SHELL)
        active_shell: ShellId = (
            active_shell_raw if active_shell_raw in VALID_SHELLS else DEFAULT_ACTIVE_SHELL
        )

        project_root = payload.get("project_root", "")
        if not isinstance(project_root, str):
            project_root = ""

        project_id = payload.get("project_id", "")
        if not isinstance(project_id, str):
            project_id = ""

        plan_raw = payload.get("project_plan_status", "")
        project_plan_status: PlanStatus = (
            plan_raw if plan_raw in VALID_PLAN_STATUSES else ""
        )

        workflow_stage_raw = payload.get("project_workflow_stage", "requirements")
        project_workflow_stage: ProjectWorkflowStage = (
            workflow_stage_raw
            if workflow_stage_raw in VALID_PROJECT_WORKFLOW_STAGES
            else "requirements"
        )

        design_confirmed_at = payload.get("project_design_confirmed_at", "")
        if not isinstance(design_confirmed_at, str):
            design_confirmed_at = ""
        active_task_id = payload.get("project_active_task_id", "")
        if not isinstance(active_task_id, str):
            active_task_id = ""

        confirmed_at = payload.get("project_plan_confirmed_at", "")
        if not isinstance(confirmed_at, str):
            confirmed_at = ""

        scope_confirmed_at = payload.get("project_scope_confirmed_at", "")
        if not isinstance(scope_confirmed_at, str):
            scope_confirmed_at = ""

        phase_fp = payload.get("project_phase_fingerprint", "")
        if not isinstance(phase_fp, str):
            phase_fp = ""
        doc_fp = payload.get("project_doc_fingerprint", "")
        if not isinstance(doc_fp, str):
            doc_fp = ""

        profile_raw = payload.get(
            "project_delivery_profile", DEFAULT_PROJECT_DELIVERY_PROFILE
        )
        project_delivery_profile: ProjectDeliveryProfile = (
            profile_raw
            if profile_raw in VALID_PROJECT_DELIVERY_PROFILES
            else DEFAULT_PROJECT_DELIVERY_PROFILE
        )

        entry_raw = payload.get("project_entry", DEFAULT_PROJECT_ENTRY)
        project_entry: ProjectEntry = (
            entry_raw if entry_raw in VALID_PROJECT_ENTRIES else DEFAULT_PROJECT_ENTRY
        )

        harness = normalize_harness(payload.get("harness", DEFAULT_HARNESS))
        terminal_scope_kind = normalize_terminal_scope_kind(
            payload.get("terminal_scope_kind", "")
        )
        terminal_cwd = normalize_terminal_path_field(payload.get("terminal_cwd", ""))
        terminal_foreign_root = normalize_terminal_path_field(
            payload.get("terminal_foreign_root", ""), relative=False
        )
        terminal_host_id = payload.get("terminal_host_id", "")
        if not isinstance(terminal_host_id, str):
            terminal_host_id = ""
        terminal_host_id = terminal_host_id.strip()

        write_scope_raw = payload.get("write_scope_root", "")
        write_scope_root = ""
        if isinstance(write_scope_raw, str) and write_scope_raw.strip():
            from sandbox_evolution import normalize_write_scope_root

            try:
                write_scope_root = normalize_write_scope_root(write_scope_raw)
            except Exception:
                write_scope_root = ""

        evolution_id = normalize_evolution_id(payload.get("evolution_id", ""))

        turn_mode = normalize_turn_mode(payload.get("turn_mode", DEFAULT_TURN_MODE))
        if harness == "terminal":
            turn_mode = "agent"

        return cls(
            topics=topics,
            llm_model=llm_model,
            llm_model_override=llm_model_override,
            execution_model=execution_model.strip(),
            planning_model=planning_model.strip(),
            updated_at=updated_at,
            phase=phase,
            workspace_evolved_approved=bool(payload.get("workspace_evolved_approved", False)),
            evolution_id=evolution_id,
            pending_feedback=pending_feedback,
            compact_before_index=int(payload.get("compact_before_index", 0) or 0),
            evolve_offer_pending=bool(payload.get("evolve_offer_pending", False)),
            evolve_offer_used=bool(payload.get("evolve_offer_used", False)),
            turn_mode=turn_mode,
            reasoning_effort=normalize_reasoning_effort(
                payload.get("reasoning_effort", "medium")
            ),
            active_shell=active_shell,
            project_root=project_root.strip(),
            project_id=project_id.strip(),
            project_plan_status=project_plan_status,
            project_workflow_stage=project_workflow_stage,
            project_design_confirmed_at=design_confirmed_at,
            project_active_task_id=active_task_id,
            project_plan_confirmed_at=confirmed_at,
            project_scope_confirmed_at=scope_confirmed_at,
            project_phase_fingerprint=phase_fp,
            project_doc_fingerprint=doc_fp,
            project_delivery_profile=project_delivery_profile,
            project_entry=project_entry,
            harness=harness,
            terminal_scope_kind=terminal_scope_kind,
            terminal_cwd=terminal_cwd,
            terminal_foreign_root=terminal_foreign_root,
            terminal_host_id=terminal_host_id,
            write_scope_root=write_scope_root,
        )


@dataclass
class Session:
    """One conversation thread under ``data/sessions/<conversation_id>/``."""

    conversation_id: str
    session_dir: Path
    goal: str
    meta: SessionMeta
    messages: list[dict[str, Any]]
    paths: AgentPaths
    subagent_overlay: str | None = field(default=None, compare=False, repr=False)
    last_review_verdict: str | None = field(default=None, compare=False, repr=False)
    last_review_blockers_count: int = field(default=0, compare=False, repr=False)
    turn_intent: str | None = field(default=None, compare=False, repr=False)
    # Ordinary work intent/task scope are request-local and never serialized.
    next_work_intent: str | None = field(default=None, compare=False, repr=False)
    next_task_scope: str | None = field(default=None, compare=False, repr=False)
    plan_files_fingerprint_at_turn_start: str | None = field(
        default=None, compare=False, repr=False
    )
    scaffold_tool_turn: bool = field(default=False, compare=False, repr=False)
    direct_implement_turn: bool = field(default=False, compare=False, repr=False)
    skip_ordinary_plan_spawn: bool = field(default=False, compare=False, repr=False)
    scaffold_check_status: str | None = field(default=None, compare=False, repr=False)
    scaffold_check_tool: str | None = field(default=None, compare=False, repr=False)
    # Request-scoped Skill package; never serialized or reused across turns.
    _turn_skill_selection: Any | None = field(default=None, compare=False, repr=False)
    # Ephemeral companion life context for one ordinary desktop chat turn only.
    _turn_companion_world_context: str | None = field(default=None, compare=False, repr=False)
    # Current self-change transaction identity; persisted transaction lives under Agent data.
    _self_change_transaction_id: str | None = field(default=None, compare=False, repr=False)
    # Current bounded CI repair decision; durable state lives under Agent data.
    _ci_repair_state: dict[str, Any] | None = field(default=None, compare=False, repr=False)
    _ci_repair_auto_turns: int = field(default=0, compare=False, repr=False)
    # Ephemeral load warnings (bad jsonl/meta); not persisted. STABILIZATION §3.9.1
    corruption_notices: list[str] = field(default_factory=list, compare=False, repr=False)
    # UI-5972: desktop switch may defer full messages.jsonl load until first turn.
    messages_total_count: int | None = field(default=None, compare=False, repr=False)
    _messages_fully_loaded: bool = field(default=True, compare=False, repr=False)
    _messages_disk_version: str | None = field(default=None, compare=False, repr=False)
    _messages_version_initialized: bool = field(default=False, compare=False, repr=False)
    _messages_unpersisted: bool = field(default=False, compare=False, repr=False)
    _messages_corrupt: bool = field(default=False, compare=False, repr=False)
    # Serialize same-object disk/message-list updates; the OS lock alone cannot
    # protect the gap between a completed append and its in-memory list update.
    _messages_instance_lock: Any = field(default_factory=threading.RLock, compare=False, repr=False)

    @property
    def goal_path(self) -> Path:
        return self.session_dir / GOAL_FILENAME

    @property
    def meta_path(self) -> Path:
        return self.session_dir / META_FILENAME

    @property
    def messages_path(self) -> Path:
        return self.session_dir / MESSAGES_FILENAME

    @property
    def digest_path(self) -> Path:
        return self.session_dir / DIGEST_FILENAME

    @property
    def tool_outputs_dir(self) -> Path:
        return self.session_dir / TOOL_OUTPUTS_DIRNAME

    def set_goal(self, goal: str, *, phase: SessionPhase | None = None) -> None:
        self.goal = goal
        if phase is not None:
            self.meta.phase = phase
        elif self.meta.phase == "S1" and goal.strip():
            self.meta.phase = "S2"

    def persist_goal(self) -> None:
        """Write ``goal.md`` only (MEMORY §6.1)."""
        from file_guard import atomic_write_text

        self.session_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_text(
            self.goal_path,
            self.goal,
            agent_root=self.paths.agent_root,
            allow_truncate_to_empty=True,
        )

    def set_topics(self, topics: list[str], *, phase: SessionPhase | None = None) -> None:
        cleaned = [topic.strip() for topic in topics if topic.strip()]
        self.meta.topics = cleaned
        if not self.meta.llm_model_override:
            self.meta.llm_model = resolve_session_model(cleaned)
        if phase is not None:
            self.meta.phase = phase

    def set_llm_model(self, model: str, *, override: bool = True) -> str:
        """Set session LLM model (deepseek-v4-flash / deepseek-v4-pro). Returns canonical id."""
        from llm_client import normalize_session_model

        canonical = normalize_session_model(model)
        if canonical is None:
            raise SessionError(
                f"unsupported llm model: {model!r} (pick a model from session.models)"
            )
        self.meta.llm_model = canonical
        self.meta.llm_model_override = override
        return canonical

    def set_turn_mode(self, mode: TurnMode) -> None:
        if is_terminal_harness(self.meta):
            raise SessionError(
                "terminal harness does not support turn_mode changes (TM-23)"
            )
        self.meta.turn_mode = normalize_turn_mode(mode)

    def append_message(self, message: dict[str, Any], *, persist: bool = True) -> None:
        with self._messages_instance_lock:
            self.ensure_messages_loaded()
            if not persist:
                self.messages.append(message)
                self._messages_unpersisted = True
                return
            self._append_message_line(message)
            self.messages.append(message)

    def append_message_durable(self, message: dict[str, Any]) -> None:
        """Append and fsync one message before a request journal is settled."""
        with self._messages_instance_lock:
            self.ensure_messages_loaded()
            self._append_message_line(message, durable=True)
            self.messages.append(message)

    def append_sent_work_card_reference(self, presentation_id: str, card_id: str, *, run_id: str | None = None) -> tuple[int, bool]:
        """Queue a host-authored card reference, not proof of delivery or viewing.

        Trusted host only: before calling, verify the card in this exact Session's
        ledger. Ledger, history and WS enqueue are not atomic; after uncertainty,
        retry this same presentation_id/card_id/run_id, never regenerate identity.
        Returns (zero-based JSONL record index, created); conflicts and stale/corrupt
        data fail closed.
        """
        with self._messages_instance_lock:
            if (not _valid_work_card_ref_ids(presentation_id, card_id)
                    or (run_id is not None and not _valid_run_identity(run_id))):
                raise SessionError("invalid work-card presentation identity")
            self.ensure_messages_loaded()
            with _session_messages_lock(self.session_dir):
                self._initialize_messages_version_locked()
                if self._messages_corrupt or self._messages_unpersisted or _messages_unusable_for_ref(self):
                    raise SessionError("session messages changed or are incomplete")
                found = None
                pending: set[str] = set()
                seen: set[str] = set()
                for index, message in enumerate(self.messages):
                    if _is_internal_work_card_message(message):
                        if not _valid_work_card_ref(message):
                            raise SessionError("invalid work-card reference record")
                        prior_id = message['presentation_id']
                        if prior_id in seen:
                            raise SessionError("duplicate work-card presentation identity")
                        seen.add(prior_id)
                        if prior_id == presentation_id:
                            if message['card_id'] != card_id or message.get('run_id') != run_id:
                                raise SessionError("work-card presentation identity conflict")
                            found = index
                    if message.get('role') == 'assistant' and message.get('tool_calls'):
                        calls=message['tool_calls']
                        if type(calls) is not list or pending:
                            raise SessionError("incomplete tool exchange")
                        for call in calls:
                            if type(call) is not dict or type(call.get('id')) is not str or not call['id']:
                                raise SessionError("incomplete tool exchange")
                            pending.add(call['id'])
                    elif message.get('role') == 'tool':
                        call_id=message.get('tool_call_id')
                        if call_id in pending:
                            pending.remove(call_id)
                    elif pending:
                        raise SessionError("incomplete tool exchange")
                if found is not None:
                    return found, False
                if pending:
                    raise SessionError("incomplete tool exchange")
                message = {"role": "work_card_presentation", "_work_card_reference": 1,
                           "presentation_id": presentation_id, "card_id": card_id}
                if run_id is not None:
                    message["run_id"] = run_id
                line = json.dumps(message, ensure_ascii=False, separators=(',', ':')) + "\n"
                with self.messages_path.open("a", encoding="utf-8", newline="\n") as handle:
                    handle.write(line)
                self._messages_disk_version = _messages_version(self.messages_path)
                self.messages.append(message)
                return len(self.messages) - 1, True

    def save(self) -> None:
        """Persist messages with a disk-version check; metadata is not transactional."""
        with self._messages_instance_lock:
            self.session_dir.mkdir(parents=True, exist_ok=True)
            self.tool_outputs_dir.mkdir(parents=True, exist_ok=True)
            with _session_messages_lock(self.session_dir):
                if self._messages_fully_loaded:
                    self._initialize_messages_version_locked()
                    if self._messages_corrupt or _messages_version(self.messages_path) != self._messages_disk_version:
                        raise SessionError("session messages changed or contain damaged records; reload before saving")
                    _write_messages_snapshot(self.messages_path, self.messages, agent_root=self.paths.agent_root)
                    self._messages_disk_version = _messages_version(self.messages_path)
                    self._messages_unpersisted = False
                self.meta.updated_at = utc_now_iso()
                self.persist_goal()
                _write_meta(self.meta_path, self.meta, agent_root=self.paths.agent_root)
                from file_guard import backup_session_files
                backup_session_files(self.session_dir, self.paths.agent_root)
            if not is_internal_session_id(self.conversation_id):
                if is_terminal_harness(self.meta):
                    write_terminal_last_session_id(self.paths, self.conversation_id)
                else:
                    write_last_conversation_id(self.paths, self.conversation_id)

    def refresh_pending_feedback_from_disk(self) -> None:
        """Reload ``pending_feedback`` from disk (executor may update meta.json directly)."""
        if not self.meta_path.is_file():
            return
        loaded = _read_meta(self.meta_path)
        self.meta.pending_feedback = list(loaded.pending_feedback)

    def ensure_messages_loaded(self) -> None:
        """Load and, where safe, repair under the shared message lock."""
        with self._messages_instance_lock:
            if self._messages_fully_loaded:
                return
            with _session_messages_lock(self.session_dir):
                skipped_lines: list[int] = []
                messages = _read_messages(self.messages_path, skipped_lines=skipped_lines)
                from context import repair_tool_messages
                repaired = repair_tool_messages(messages)
                if repaired != messages and not skipped_lines:
                    _write_messages_snapshot(self.messages_path, repaired, agent_root=self.paths.agent_root)
                    messages = repaired
                if skipped_lines:
                    self.corruption_notices.append(format_messages_corruption_notice(skipped_lines))
                self.messages = messages
                self._messages_disk_version = _messages_version(self.messages_path)
                self._messages_version_initialized = True
                self._messages_corrupt = bool(skipped_lines)
                self._messages_fully_loaded = True
                self.messages_total_count = None

    @classmethod
    def load(
        cls,
        paths: AgentPaths,
        conversation_id: str,
        *,
        message_cap: int | None = None,
    ) -> Session:
        if (type(conversation_id) is not str
                or not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9._-]{0,127}", conversation_id)
                or conversation_id in {".", ".."} or conversation_id.endswith(".")):
            raise SessionError("invalid session identity")
        reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
        if conversation_id.split(".", 1)[0].upper() in reserved:
            raise SessionError("invalid session identity")
        session_root = sessions_root(paths)
        try:
            session_root_real = session_root.resolve(strict=True)
            session_dir = session_root_real / conversation_id
            session_dir_real = session_dir.resolve(strict=True)
        except OSError as exc:
            raise SessionError(f"session does not exist: {conversation_id}") from exc
        if (session_dir_real.parent != session_root_real
                or session_dir_real != session_dir
                or not session_dir_real.is_dir()
                or session_dir.is_symlink()
                or (getattr(os.path, "isjunction", None) is not None and os.path.isjunction(session_dir))):
            raise SessionError("session path escapes or is not a direct child of sessions root")
        session_dir = session_dir_real

        goal = ""
        if (session_dir / GOAL_FILENAME).is_file():
            goal = (session_dir / GOAL_FILENAME).read_text(encoding="utf-8")

        meta_issues: list[str] = []
        meta = _read_meta(session_dir / META_FILENAME, corruption_kinds=meta_issues)
        skipped_lines: list[int] = []
        messages_total_count: int | None = None
        messages_fully_loaded = True
        messages_path = session_dir / MESSAGES_FILENAME
        with _session_messages_lock(session_dir):
            _recover_stream_journals_locked(session_dir, messages_path, conversation_id)
            if message_cap is not None and message_cap <= 0:
                messages: list[dict[str, Any]] = []
                messages_total_count = _count_file_newlines(messages_path)
                messages_fully_loaded = False
            elif message_cap is not None and message_cap > 0:
                messages, messages_total_count = _read_messages_tail(messages_path, message_cap, skipped_lines=skipped_lines)
                messages_fully_loaded = messages_total_count <= len(messages)
            else:
                messages = _read_messages(messages_path, skipped_lines=skipped_lines)
            if messages_fully_loaded:
                from context import repair_tool_messages
                repaired = repair_tool_messages(messages)
                if repaired != messages and not skipped_lines:
                    _write_messages_snapshot(messages_path, repaired, agent_root=paths.agent_root)
                    messages = repaired
            version = _messages_version(messages_path)
        notices: list[str] = []
        for kind in meta_issues:
            notices.append(format_meta_corruption_notice(kind))
        if skipped_lines:
            notices.append(format_messages_corruption_notice(skipped_lines))
        return cls(
            conversation_id=conversation_id,
            session_dir=session_dir,
            goal=goal,
            meta=meta,
            messages=messages,
            paths=paths,
            corruption_notices=notices,
            messages_total_count=messages_total_count,
            _messages_fully_loaded=messages_fully_loaded,
            _messages_disk_version=version,
            _messages_version_initialized=True,
            _messages_corrupt=bool(skipped_lines),
        )

    def _initialize_messages_version_locked(self) -> None:
        """Admit an untracked instance only if disk is complete and identical.

        Caller holds the session message lock. Loaded instances are already bound
        to their original version and must never be re-authorized this way.
        """
        if self._messages_version_initialized:
            return
        try:
            version = _messages_version(self.messages_path)
            if version is not None:
                if self._messages_unpersisted:
                    raise ValueError("unpersisted changes are not attributable")
                index = 0
                with self.messages_path.open("r", encoding="utf-8", newline="") as handle:
                    for line in handle:
                        if not line.endswith("\n") or not line.strip():
                            raise ValueError("incomplete or empty record")
                        record = json.loads(line, object_pairs_hook=_unique_message_object,
                                            parse_constant=_reject_message_constant)
                        if (type(record) is not dict or index >= len(self.messages)
                                or not _exact_json_record(record, self.messages[index])):
                            raise ValueError("records differ")
                        index += 1
                if index != len(self.messages) or _messages_version(self.messages_path) != version:
                    raise ValueError("records changed")
        except Exception:
            raise SessionError("cannot establish exact existing session messages") from None
        self._messages_disk_version = version
        self._messages_version_initialized = True

    def _append_message_line(self, message: dict[str, Any], *, durable: bool = False) -> None:
        self.session_dir.mkdir(parents=True, exist_ok=True)
        line = json.dumps(message, ensure_ascii=False) + "\n"
        with _session_messages_lock(self.session_dir):
            self._initialize_messages_version_locked()
            if self._messages_corrupt or _messages_version(self.messages_path) != self._messages_disk_version:
                raise SessionError("session messages changed or contain damaged records; reload before appending")
            if self.messages_path.exists():
                with self.messages_path.open("rb") as existing:
                    existing.seek(0, os.SEEK_END)
                    if existing.tell():
                        existing.seek(-1, os.SEEK_END)
                        if existing.read(1) != b"\n":
                            raise SessionError("session messages have an incomplete final line")
            with self.messages_path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(line)
                if durable:
                    handle.flush()
                    os.fsync(handle.fileno())
            self._messages_disk_version = _messages_version(self.messages_path)


def _valid_run_identity(run_id: Any) -> bool:
    if type(run_id) is not str:
        return False
    try:
        return str(uuid.UUID(run_id)) == run_id
    except (ValueError, AttributeError):
        return False


def _valid_work_card_ref_ids(presentation_id: Any, card_id: Any) -> bool:
    if type(presentation_id) is not str or type(card_id) is not str:
        return False
    try:
        return str(uuid.UUID(presentation_id)) == presentation_id and re.fullmatch(r"wc-[0-9a-f]{64}", card_id) is not None
    except (ValueError, AttributeError):
        return False


def _is_internal_work_card_message(message: Any) -> bool:
    return (isinstance(message, dict) and (message.get("role") == "work_card_presentation"
            or "_work_card_reference" in message))


def _valid_work_card_ref(message: Any) -> bool:
    keys = {"role", "_work_card_reference", "presentation_id", "card_id"}
    run_bound = type(message) is dict and set(message) == keys | {"run_id"}
    legacy = type(message) is dict and set(message) == keys
    return ((run_bound or legacy) and message.get("role") == "work_card_presentation"
            and type(message.get("_work_card_reference")) is int and message["_work_card_reference"] == 1
            and _valid_work_card_ref_ids(message.get("presentation_id"), message.get("card_id"))
            and (not run_bound or _valid_run_identity(message.get("run_id"))))


def _messages_unusable_for_ref(session: Session) -> bool:
    """Check exact complete disk/memory equivalence while holding the message lock."""
    try:
        version = _messages_version(session.messages_path)
        if version != session._messages_disk_version:
            return True
        if version is None:
            return bool(session.messages)
        index = 0
        with session.messages_path.open("r", encoding="utf-8", newline="") as handle:
            for line in handle:
                if not line.endswith("\n") or not line.strip():
                    return True
                row = json.loads(line, object_pairs_hook=_unique_message_object, parse_constant=_reject_message_constant)
                if (type(row) is not dict or index >= len(session.messages)
                        or not _exact_json_record(row, session.messages[index])):
                    return True
                index += 1
        return index != len(session.messages) or _messages_version(session.messages_path) != version
    except Exception:
        return True


def _unique_message_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_message_constant(_value: str) -> Any:
    raise ValueError("non-finite JSON number")


def _exact_json_record(left: Any, right: Any) -> bool:
    # Python's equality treats True == 1 and 1 == 1.0; those are not the same
    # parsed JSON facts and must not bootstrap an untracked Session.
    if type(left) is not type(right):
        return False
    if type(left) is dict:
        return (all(type(key) is str for key in right) and left.keys() == right.keys()
                and all(_exact_json_record(value, right[key]) for key, value in left.items()))
    if type(left) is list:
        return len(left) == len(right) and all(_exact_json_record(a, b) for a, b in zip(left, right))
    if type(left) not in (str, int, float, bool, type(None)):
        return False
    if type(left) is float:
        # Preserve the distinction between positive and negative zero as well.
        return json.dumps(left, allow_nan=False) == json.dumps(right, allow_nan=False)
    return left == right


@contextlib.contextmanager
def _session_messages_lock(session_dir: Path):
    # Share the already established OS-backed lock implementation, but not the
    # work-record lock namespace. Imports are delayed to avoid a module cycle.
    from work_records import _file_lock, WorkRecordLockUnavailable
    try:
        with _file_lock(session_dir / ".messages.lock", 5.0):
            yield
    except WorkRecordLockUnavailable as exc:
        raise SessionError("session message lock unavailable") from exc


@contextlib.contextmanager
def _open_messages_for_journal_recovery(session_dir: Path, messages_path: Path):
    """Pin the session directory and safely open its private JSONL for read/append."""
    session_real = session_dir.resolve(strict=True)
    expected_path = session_real / MESSAGES_FILENAME
    if os.path.normcase(os.path.abspath(messages_path)) != os.path.normcase(os.path.abspath(expected_path)):
        raise SessionError("journal recovery messages path is outside its session")

    def is_reparse(info: os.stat_result) -> bool:
        attrs = getattr(info, "st_file_attributes", 0)
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        return stat.S_ISLNK(info.st_mode) or bool(attrs & reparse_flag)

    def regular_private(info: os.stat_result) -> bool:
        return stat.S_ISREG(info.st_mode) and not is_reparse(info) and info.st_nlink == 1

    def same_file(left: os.stat_result, right: os.stat_result) -> bool:
        return left.st_dev == right.st_dev and left.st_ino == right.st_ino

    dir_fd = None
    fd = None
    directory_handle = None
    file_handle = None
    win_close_handle = None
    try:
        if os.name == "nt":
            import ctypes
            import msvcrt
            from ctypes import wintypes

            class _FileTime(ctypes.Structure):
                _fields_ = [("low", wintypes.DWORD), ("high", wintypes.DWORD)]

            class _ByHandleInfo(ctypes.Structure):
                _fields_ = [
                    ("attributes", wintypes.DWORD), ("creation", _FileTime),
                    ("access", _FileTime), ("write", _FileTime),
                    ("volume", wintypes.DWORD), ("size_high", wintypes.DWORD),
                    ("size_low", wintypes.DWORD), ("links", wintypes.DWORD),
                    ("index_high", wintypes.DWORD), ("index_low", wintypes.DWORD),
                ]

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            create_file = kernel32.CreateFileW
            create_file.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                    wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
            create_file.restype = wintypes.HANDLE
            get_info = kernel32.GetFileInformationByHandle
            get_info.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ByHandleInfo)]
            get_info.restype = wintypes.BOOL
            close_handle = kernel32.CloseHandle
            close_handle.argtypes = [wintypes.HANDLE]
            close_handle.restype = wintypes.BOOL
            win_close_handle = close_handle
            invalid = ctypes.c_void_p(-1).value
            share_read_write = 0x1 | 0x2  # Deliberately omit FILE_SHARE_DELETE while pinned.
            open_existing = 3
            create_new = 1
            open_reparse = 0x00200000
            backup_semantics = 0x02000000
            file_read_attributes = 0x80
            generic_read = 0x80000000
            file_append_data = 0x4
            attr_directory = 0x10
            attr_reparse = 0x400

            def win_open(path: Path, access: int, creation: int, flags: int):
                handle = create_file(str(path), access, share_read_write, None, creation, flags, None)
                if handle == invalid:
                    error = ctypes.get_last_error()
                    raise OSError(error, "CreateFileW failed for session recovery path", str(path))
                return handle

            def win_info(handle):
                info = _ByHandleInfo()
                if not get_info(handle, ctypes.byref(info)):
                    error = ctypes.get_last_error()
                    raise OSError(error, "GetFileInformationByHandle failed")
                return info

            directory_handle = win_open(
                session_real, file_read_attributes, open_existing,
                open_reparse | backup_semantics,
            )
            dir_info = win_info(directory_handle)
            dir_stat = session_real.lstat()
            if (dir_info.attributes & (attr_directory | attr_reparse) != attr_directory
                    or not stat.S_ISDIR(dir_stat.st_mode) or is_reparse(dir_stat)
                    or ((dir_info.index_high << 32) | dir_info.index_low) != dir_stat.st_ino):
                raise OSError("session directory identity or reparse validation failed")

            try:
                before = messages_path.lstat()
                existed = True
            except FileNotFoundError:
                before = None
                existed = False
            if before is not None and not regular_private(before):
                raise OSError("messages.jsonl must be a private regular file")
            try:
                file_handle = win_open(
                    messages_path, generic_read | file_append_data | file_read_attributes,
                    open_existing if existed else create_new, open_reparse,
                )
            except OSError:
                raise
            info = win_info(file_handle)
            file_attrs_ok = not (info.attributes & (attr_directory | attr_reparse))
            if not file_attrs_ok or info.links != 1:
                raise OSError("messages.jsonl is a reparse point, directory, or hardlink")
            handle_identity = (info.volume, (info.index_high << 32) | info.index_low)
            if info.volume != dir_info.volume:
                raise OSError("messages.jsonl is not on the pinned session volume")
            try:
                opened_stat = messages_path.lstat()
            except OSError:
                raise
            if (not regular_private(opened_stat)
                    or opened_stat.st_ino != handle_identity[1]
                    or (before is not None and not same_file(before, opened_stat))):
                raise OSError("messages.jsonl changed identity while opening")
            fd = msvcrt.open_osfhandle(
                int(file_handle), os.O_RDWR | os.O_APPEND | os.O_BINARY,
            )
            file_handle = None  # The descriptor now owns the native handle.

            def verify_open_identity() -> None:
                current_dir = session_real.lstat()
                current_file = messages_path.lstat()
                current_info = win_info(msvcrt.get_osfhandle(fd))
                opened = os.fstat(fd)
                identity = (current_info.volume, (current_info.index_high << 32) | current_info.index_low)
                if (is_reparse(current_dir) or not stat.S_ISDIR(current_dir.st_mode)
                        or current_dir.st_ino != ((dir_info.index_high << 32) | dir_info.index_low)
                        or not regular_private(current_file) or not regular_private(opened)
                        or current_info.links != 1
                        or current_info.volume != dir_info.volume
                        or current_file.st_ino != identity[1]
                        or opened.st_ino != identity[1]):
                    raise OSError("messages.jsonl/session directory identity changed")

        else:
            directory_stat = session_real.lstat()
            if not stat.S_ISDIR(directory_stat.st_mode) or is_reparse(directory_stat):
                raise OSError("session directory is not a direct regular directory")
            dir_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
            dir_fd = os.open(session_real, dir_flags)
            opened_dir = os.fstat(dir_fd)
            if not stat.S_ISDIR(opened_dir.st_mode) or is_reparse(opened_dir) or not same_file(directory_stat, opened_dir):
                raise OSError("session directory changed while opening")
            try:
                before = os.stat(MESSAGES_FILENAME, dir_fd=dir_fd, follow_symlinks=False)
                existed = True
            except FileNotFoundError:
                before = None
                existed = False
            if before is not None and not regular_private(before):
                raise OSError("messages.jsonl must be a private regular file")
            flags = os.O_RDWR | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
            if not existed:
                flags |= os.O_CREAT | os.O_EXCL
            fd = os.open(MESSAGES_FILENAME, flags, 0o600, dir_fd=dir_fd)
            opened = os.fstat(fd)
            current = os.stat(MESSAGES_FILENAME, dir_fd=dir_fd, follow_symlinks=False)
            if (not regular_private(opened) or not regular_private(current)
                    or not same_file(opened, current)
                    or (before is not None and not same_file(before, opened))):
                raise OSError("messages.jsonl changed identity while opening")

            def verify_open_identity() -> None:
                current_dir = os.fstat(dir_fd)
                named_dir = session_real.lstat()
                opened_file = os.fstat(fd)
                named_file = os.stat(MESSAGES_FILENAME, dir_fd=dir_fd, follow_symlinks=False)
                if (not stat.S_ISDIR(current_dir.st_mode) or is_reparse(current_dir)
                        or not same_file(current_dir, named_dir)
                        or not regular_private(opened_file) or not regular_private(named_file)
                        or not same_file(opened_file, named_file)):
                    raise OSError("messages.jsonl/session directory identity changed")

        verify_open_identity()
        yield fd, verify_open_identity
    except (OSError, ValueError) as exc:
        raise SessionError("stream journal recovery failed closed: unsafe messages file") from exc
    finally:
        if fd is not None:
            os.close(fd)
        elif file_handle is not None and win_close_handle is not None:
            win_close_handle(file_handle)
        if dir_fd is not None:
            os.close(dir_fd)
        if directory_handle is not None and win_close_handle is not None:
            # The parent directory handle pins its identity against rename/replacement.
            win_close_handle(directory_handle)


def _read_messages_from_open_fd(fd: int) -> tuple[list[dict[str, Any]], list[int]]:
    os.lseek(fd, 0, os.SEEK_SET)
    chunks: list[bytes] = []
    while True:
        chunk = os.read(fd, 128 * 1024)
        if not chunk:
            break
        chunks.append(chunk)
    try:
        lines = b"".join(chunks).decode("utf-8").splitlines()
    except UnicodeError as exc:
        raise SessionError("session messages are not valid UTF-8") from exc
    messages: list[dict[str, Any]] = []
    skipped: list[int] = []
    for line_no, line in enumerate(lines, 1):
        text = line.strip()
        if not text:
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            skipped.append(line_no)
            continue
        if isinstance(payload, dict):
            messages.append(payload)
        else:
            skipped.append(line_no)
    return messages, skipped


def _append_jsonl_record_to_open_fd(fd: int, record: dict[str, Any], verify_identity: Callable[[], None]) -> None:
    verify_identity()
    end = os.lseek(fd, 0, os.SEEK_END)
    if end:
        os.lseek(fd, -1, os.SEEK_END)
        if os.read(fd, 1) != b"\n":
            raise SessionError("session messages have an incomplete final line")
    verify_identity()
    payload = (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")
    view = memoryview(payload)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OSError("short write while recovering session journal")
        view = view[written:]
    os.fsync(fd)


def _recover_stream_journals_locked(
    session_dir: Path, messages_path: Path, conversation_id: str,
) -> None:
    """Fold request journals into this session under its cross-process message lock."""
    from stream_journal import journal_lease, lease_path_for, pending_journals, read_journal

    journals = pending_journals(session_dir, conversation_id)
    if not journals:
        return
    with _open_messages_for_journal_recovery(session_dir, messages_path) as (messages_fd, verify_identity):
        messages, skipped = _read_messages_from_open_fd(messages_fd)
        if skipped:
            raise SessionError("cannot recover streamed reply into damaged session history")
        by_request: dict[str, dict[str, Any]] = {}
        for message in messages:
            metadata = message.get(HOST_INTERNAL_METADATA_KEY)
            if isinstance(metadata, dict) and isinstance(metadata.get("stream_request_id"), str):
                request_id = metadata["stream_request_id"]
                if request_id in by_request:
                    raise SessionError("duplicate streamed reply request identity in session history")
                by_request[request_id] = message
        for path in journals:
            try:
                with journal_lease(lease_path_for(path), nonblocking=True) as acquired:
                    if not acquired or not os.path.lexists(path):
                        continue
                    rows = read_journal(
                        path, expected_session_dir=session_dir,
                        expected_session_id=conversation_id, expected_request_id=path.stem,
                    )
                    header = rows[0]
                    request_id = header["request_id"]
                    content = "".join(row["text"] for row in rows if row["kind"] == "delta")
                    settled = any(row["kind"] == "settled" for row in rows)
                    prior = by_request.get(request_id)
                    if prior is not None:
                        prior_meta = prior.get(HOST_INTERNAL_METADATA_KEY)
                        if (prior.get("role") != "assistant" or prior.get("content") != content
                                or not isinstance(prior_meta, dict)
                                or prior_meta.get("stream_run_id") != header.get("run_id")):
                            raise ValueError("stream request identity conflicts with a non-matching assistant row")
                    elif settled and content:
                        raise ValueError("settled stream journal has no matching durable assistant row")
                    elif not settled and content:
                        message = {
                            "role": "assistant", "content": content,
                            HOST_INTERNAL_METADATA_KEY: {
                                "stream_incomplete": True,
                                "stream_finish_reason": "process_interrupted",
                                "stream_request_id": request_id,
                                "stream_run_id": header.get("run_id"),
                            },
                        }
                        _append_jsonl_record_to_open_fd(messages_fd, message, verify_identity)
                        messages.append(message)
                        by_request[request_id] = message
                    path.unlink()
            except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
                raise SessionError("stream journal recovery failed closed") from exc


def _messages_version(path: Path) -> str | None:
    try:
        with path.open("rb") as handle:
            digest = hashlib.sha256()
            for chunk in iter(lambda: handle.read(128 * 1024), b""):
                digest.update(chunk)
            return digest.hexdigest()
    except FileNotFoundError:
        return None


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize_harness(value: Any) -> HarnessKind:
    if isinstance(value, str):
        lowered = value.strip().casefold()
        if lowered in VALID_HARNESSES:
            return lowered  # type: ignore[return-value]
    return DEFAULT_HARNESS


def normalize_terminal_scope_kind(value: Any) -> TerminalScopeKind | Literal[""]:
    if isinstance(value, str):
        lowered = value.strip().casefold()
        if lowered in VALID_TERMINAL_SCOPE_KINDS:
            return lowered  # type: ignore[return-value]
    return ""


def normalize_terminal_path_field(value: Any, *, relative: bool = True) -> str:
    if not isinstance(value, str):
        return ""
    text = value.strip().replace("\\", "/")
    while "//" in text:
        text = text.replace("//", "/")
    if not relative:
        return text.rstrip("/")
    return text.strip("/") if text not in {"", "/"} else ""


def is_terminal_harness(meta: SessionMeta) -> bool:
    return meta.harness == "terminal"


def enforce_terminal_meta_invariants(meta: SessionMeta) -> None:
    """TM-23: terminal sessions always persist ``turn_mode=agent``."""
    if is_terminal_harness(meta):
        meta.turn_mode = "agent"


def assert_harness_immutable(
    existing_payload: dict[str, Any] | None,
    meta: SessionMeta,
) -> None:
    """TM-2: ``meta.harness`` is set at creation and cannot change."""
    if not existing_payload:
        return
    raw = existing_payload.get("harness")
    if not isinstance(raw, str) or not raw.strip():
        return
    existing = normalize_harness(raw)
    if meta.harness != existing:
        raise SessionError(
            f"harness is immutable: {existing!r} → {meta.harness!r} not allowed"
        )


def normalize_turn_mode(value: Any) -> TurnMode:
    if isinstance(value, str):
        lowered = value.strip().casefold()
        if lowered in VALID_TURN_MODES:
            return lowered  # type: ignore[return-value]
    return DEFAULT_TURN_MODE


def parse_turn_mode_command(line: str) -> TurnMode | None:
    """Parse REPL mode switch: 只聊/ask → ask; 动手/agent → agent."""
    stripped = line.strip()
    if not stripped:
        return None
    lower = stripped.casefold()
    if lower in {"只聊", "ask"}:
        return "ask"
    if lower in {"动手", "agent"}:
        return "agent"
    return None


def turn_mode_label(mode: TurnMode) -> str:
    if mode == "ask":
        return "只聊 (read-only; run_evolved disabled)"
    return "动手 (full tools including run_evolved)"


REASONING_EFFORT_LEVELS = ("low", "medium", "high", "max")


def normalize_reasoning_effort(raw: Any) -> str:
    """Validate and normalize reasoning_effort value; fallback to ``"medium"``."""
    if isinstance(raw, str) and raw.casefold() in REASONING_EFFORT_LEVELS:
        return raw.lower()
    return "medium"


def parse_reasoning_effort_command(line: str) -> str | None:
    """Parse REPL effort switch: 推理强度 <level> / effort <level>."""
    stripped = line.strip()
    if not stripped:
        return None
    lower = stripped.casefold()
    for level in REASONING_EFFORT_LEVELS:
        if lower in {f"effort {level}", f"推理强度 {level}", f"强度 {level}"}:
            return level
    return None


def reasoning_effort_label(level: str) -> str:
    return {
        "low": "低推理，省 token",
        "medium": "中等推理（默认）",
        "high": "高推理",
        "max": "最高推理强度",
    }.get(level, "")


def session_banner_event(session: Session) -> dict[str, Any]:
    from llm_client import llm_model_label
    from llm_routing import resolve_model_id_for_role
    from sandbox_evolution import write_scope_label

    model = resolve_model_id_for_role("main_turn", session.meta)
    payload: dict[str, Any] = {
        "type": "session.banner",
        "session_id": session.conversation_id,
        "goal": session.goal.strip() or None,
        "topics": list(session.meta.topics),
        "turn_mode": session.meta.turn_mode,
        "turn_mode_label": turn_mode_label(session.meta.turn_mode),
        "reasoning_effort": session.meta.reasoning_effort,
        "reasoning_effort_label": reasoning_effort_label(session.meta.reasoning_effort),
        "llm_model": model,
        "llm_model_label": llm_model_label(model),
        "llm_model_override": bool(session.meta.llm_model_override),
        "phase": session.meta.phase,
        "active_shell": session.meta.active_shell,
    }
    if session.meta.project_id:
        payload["project_id"] = session.meta.project_id
        payload["project_root"] = session.meta.project_root or None
        payload["project_plan_status"] = session.meta.project_plan_status or "draft"
    scope = (session.meta.write_scope_root or "").strip()
    if scope:
        payload["write_scope_root"] = scope
        label = write_scope_label(scope)
        if label:
            payload["write_scope_label"] = label
    return payload


def generate_conversation_id(*, now: datetime | None = None) -> str:
    moment = now or datetime.now(UTC)
    return moment.strftime("%Y%m%d") + "-" + secrets.token_hex(4)


def is_internal_session_id(conversation_id: str) -> bool:
    """Underscore-prefixed ids are demo/CLI internals, not default resume targets."""
    return conversation_id.startswith("_")


def sessions_root(paths: AgentPaths) -> Path:
    return paths.data / SESSIONS_DIRNAME


def list_session_ids(
    paths: AgentPaths,
    *,
    include_internal: bool = False,
) -> list[str]:
    root = sessions_root(paths)
    if not root.is_dir():
        return []

    ids: list[str] = []
    for entry in root.iterdir():
        if not entry.is_dir():
            continue
        conversation_id = entry.name
        if not include_internal and is_internal_session_id(conversation_id):
            continue
        if (entry / META_FILENAME).is_file() or (entry / MESSAGES_FILENAME).is_file():
            ids.append(conversation_id)
    return sorted(ids)


def _count_file_newlines(path: Path) -> int:
    try:
        with path.open("rb") as handle:
            count = 0
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                count += chunk.count(b"\n")
            return count
    except OSError:
        return 0


def _user_text_from_jsonl_line(line: str) -> str:
    try:
        msg = json.loads(line)
    except json.JSONDecodeError:
        return ""
    if not isinstance(msg, dict) or msg.get("role") != "user":
        return ""
    content = str(msg.get("content", "")).strip()
    if not content:
        return ""
    if any(content.startswith(prefix) for prefix in _UI_SKIP_USER_PREFIXES):
        return ""
    if _ui_skip_chat_text(content):
        return ""
    return content


def _extract_user_messages_from_lines(lines: list[str]) -> tuple[str, str]:
    first_user = ""
    last_user = ""
    for line in lines:
        if not line.strip():
            continue
        text = _user_text_from_jsonl_line(line)
        if text and not first_user:
            first_user = text
            break
    for line in reversed(lines):
        if not line.strip():
            continue
        text = _user_text_from_jsonl_line(line)
        if text:
            last_user = text
            break
    return first_user, last_user


_JSONL_TAIL_BYTES = 65536


def _extract_user_messages(messages_path: Path) -> tuple[str, str, int]:
    """Return (first_user_content, last_user_content, message_count) from a jsonl file.

    Skips anchor / kernel / seed prefixes so title and preview are real user messages.
    UI-5972: tail-read large files instead of loading entire messages.jsonl.
    """
    if not messages_path.is_file():
        return "", "", 0
    msg_count = _count_file_newlines(messages_path)
    if msg_count == 0:
        return "", "", 0
    try:
        size = messages_path.stat().st_size
    except OSError:
        return "", "", 0
    if size <= _JSONL_TAIL_BYTES * 2:
        try:
            text = messages_path.read_text(encoding="utf-8")
        except OSError:
            return "", "", 0
        first_user, last_user = _extract_user_messages_from_lines(text.splitlines())
        return first_user, last_user, msg_count

    try:
        head_text = messages_path.read_bytes()[:_JSONL_TAIL_BYTES].decode(
            "utf-8", errors="replace"
        )
        with messages_path.open("rb") as handle:
            handle.seek(max(0, size - _JSONL_TAIL_BYTES))
            tail_text = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return "", "", msg_count

    head_lines = head_text.splitlines()
    if head_lines and not head_text.endswith("\n"):
        head_lines = head_lines[:-1]
    tail_lines = tail_text.splitlines()
    if tail_lines and size > _JSONL_TAIL_BYTES and not tail_text.startswith("\n"):
        tail_lines = tail_lines[1:]
    first_user, _ = _extract_user_messages_from_lines(head_lines)
    _, last_user = _extract_user_messages_from_lines(tail_lines)
    return first_user, last_user, msg_count


def list_session_summaries(
    paths: AgentPaths,
    *,
    limit: int = 50,
    include_internal: bool = False,
) -> list[dict[str, Any]]:
    """Return recent session summaries for UX-020 / §7.6 dropdown.

    Each entry: session_id, title, preview, updated_at, message_count, project_id.
    - Unbound sessions: title = goal[:80] > first user message[:80] > conversation_id
    - Project-bound: one row per project_id (newest / project_sessions mapping);
      title = project_id
    """
    root = sessions_root(paths)
    if not root.is_dir():
        return []

    preferred: dict[str, str] = {}
    try:
        from project_switch import read_project_sessions

        preferred = read_project_sessions(paths)
    except Exception:
        preferred = {}

    entries: list[dict[str, Any]] = []
    for entry in root.iterdir():
        if not entry.is_dir():
            continue
        cid = entry.name
        if not include_internal and is_internal_session_id(cid):
            continue
        if not ((entry / META_FILENAME).is_file() or (entry / MESSAGES_FILENAME).is_file()):
            continue
        goal = ""
        updated_at = ""
        project_id = ""
        meta_path = entry / META_FILENAME
        harness = DEFAULT_HARNESS
        write_scope_root = ""
        if meta_path.is_file():
            try:
                payload = json.loads(meta_path.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    updated_at = str(payload.get("updated_at", "") or "")
                    raw_pid = payload.get("project_id", "")
                    if isinstance(raw_pid, str):
                        project_id = raw_pid.strip()
                    harness = normalize_harness(payload.get("harness", DEFAULT_HARNESS))
                    raw_scope = payload.get("write_scope_root", "")
                    if isinstance(raw_scope, str):
                        write_scope_root = raw_scope.strip()
            except (OSError, json.JSONDecodeError):
                pass
        if harness == "terminal":
            continue
        goal_path = entry / GOAL_FILENAME
        if goal_path.is_file():
            try:
                goal = goal_path.read_text(encoding="utf-8").strip()
            except OSError:
                pass

        first_user, last_user, msg_count = _extract_user_messages(entry / MESSAGES_FILENAME)
        if project_id:
            title = project_id
        else:
            title = goal[:80] if goal else (first_user[:80] if first_user else cid)
        if write_scope_root and not project_id:
            title = f"[实验] {title}"[:120]
        preview = last_user[:120] if last_user else ""
        entries.append({
            "session_id": cid,
            "title": title,
            "preview": preview,
            "updated_at": updated_at,
            "message_count": msg_count,
            "project_id": project_id,
        })

    # D5/D6: one row per project — prefer project_sessions mapping, else newest updated_at.
    # Project-bound rows must survive the global limit: a burst of ordinary sessions
    # (including empty sessions created during reconnects) must not make projects
    # disappear from the desktop's project-session tab.
    by_project: dict[str, dict[str, Any]] = {}
    unbound: list[dict[str, Any]] = []
    for item in entries:
        pid = str(item.get("project_id") or "").strip()
        if not pid:
            unbound.append(item)
            continue
        mapped = preferred.get(pid)
        if mapped and item["session_id"] == mapped:
            by_project[pid] = item
            continue
        if pid in by_project and by_project[pid]["session_id"] == mapped:
            continue
        existing = by_project.get(pid)
        if existing is None:
            by_project[pid] = item
        elif mapped and existing["session_id"] != mapped and item["session_id"] == mapped:
            by_project[pid] = item
        elif not mapped or existing["session_id"] != mapped:
            if str(item.get("updated_at") or "") > str(existing.get("updated_at") or ""):
                by_project[pid] = item

    if limit <= 0:
        return []

    project_bound = list(by_project.values())
    project_bound.sort(key=lambda e: e["updated_at"], reverse=True)
    unbound.sort(key=lambda e: e["updated_at"], reverse=True)

    # Reserve the first slots for project rows, then fill the remaining capacity
    # with the newest ordinary conversations. Keep the final result chronological.
    selected = project_bound[:limit]
    selected.extend(unbound[: max(0, limit - len(selected))])
    selected.sort(key=lambda e: e["updated_at"], reverse=True)
    return selected


def _harness_for_session_dir(session_dir: Path) -> HarnessKind:
    meta_path = session_dir / META_FILENAME
    if not meta_path.is_file():
        return DEFAULT_HARNESS
    try:
        payload = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return DEFAULT_HARNESS
    if not isinstance(payload, dict):
        return DEFAULT_HARNESS
    return normalize_harness(payload.get("harness", DEFAULT_HARNESS))


def find_latest_session_id(
    paths: AgentPaths,
    *,
    include_internal: bool = False,
    harness: HarnessKind | None = None,
) -> str | None:
    candidates = list_session_ids(paths, include_internal=include_internal)
    if harness is not None:
        candidates = [
            conversation_id
            for conversation_id in candidates
            if _harness_for_session_dir(sessions_root(paths) / conversation_id) == harness
        ]
    if not candidates:
        return None

    def sort_key(conversation_id: str) -> tuple[str, float]:
        session_dir = sessions_root(paths) / conversation_id
        meta_path = session_dir / META_FILENAME
        updated_at = ""
        if meta_path.is_file():
            try:
                payload = json.loads(meta_path.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    raw = payload.get("updated_at")
                    if isinstance(raw, str):
                        updated_at = raw
            except (OSError, json.JSONDecodeError):
                pass
        mtime = session_dir.stat().st_mtime
        return (updated_at, mtime)

    return max(candidates, key=sort_key)


def read_last_conversation_id(paths: AgentPaths) -> str | None:
    from paths import read_agent_state_payload

    payload = read_agent_state_payload(paths)
    raw = payload.get(STATE_LAST_SESSION_KEY)
    if not isinstance(raw, str) or not raw.strip():
        return None
    conversation_id = raw.strip()
    if is_internal_session_id(conversation_id):
        return None
    session_dir = sessions_root(paths) / conversation_id
    if not session_dir.is_dir():
        return None
    return conversation_id


def assert_session_harness(session: Session, expected: HarnessKind) -> None:
    """TM-4: entry harness must match persisted session harness."""
    if session.meta.harness != expected:
        raise HarnessMismatchError(
            session.conversation_id,
            session_harness=session.meta.harness,
            entry_harness=expected,
        )


def load_session_for_harness(
    paths: AgentPaths,
    conversation_id: str,
    *,
    expected: HarnessKind,
    full_messages: bool = False,
) -> Session:
    """Load session and enforce harness match (TM-4).

    Desktop UI paths default to deferred messages.jsonl load (UI-5972).
    Pass ``full_messages=True`` when the caller needs the entire log in RAM.
    """
    message_cap: int | None = None
    if expected == "desktop" and not full_messages:
        message_cap = desktop_switch_message_cap()
    session = Session.load(paths, conversation_id, message_cap=message_cap)
    assert_session_harness(session, expected)
    return session


def write_last_conversation_id(paths: AgentPaths, conversation_id: str) -> None:
    from paths import read_agent_state_payload, write_agent_state_payload

    payload = read_agent_state_payload(paths)
    payload[STATE_LAST_SESSION_KEY] = conversation_id
    write_agent_state_payload(paths, payload)


def read_terminal_last_session_id(paths: AgentPaths) -> str | None:
    from paths import read_agent_state_payload

    payload = read_agent_state_payload(paths)
    raw = payload.get(STATE_TERMINAL_LAST_SESSION_KEY)
    if not isinstance(raw, str) or not raw.strip():
        return None
    conversation_id = raw.strip()
    if is_internal_session_id(conversation_id):
        return None
    session_dir = sessions_root(paths) / conversation_id
    if not session_dir.is_dir():
        return None
    return conversation_id


def write_terminal_last_session_id(paths: AgentPaths, conversation_id: str) -> None:
    from paths import read_agent_state_payload, write_agent_state_payload

    payload = read_agent_state_payload(paths)
    payload[STATE_TERMINAL_LAST_SESSION_KEY] = conversation_id
    write_agent_state_payload(paths, payload)


def create_new(
    paths: AgentPaths,
    *,
    conversation_id: str | None = None,
    harness: HarnessKind = DEFAULT_HARNESS,
    terminal_scope_kind: TerminalScopeKind | Literal[""] = "",
    terminal_cwd: str = "",
    terminal_foreign_root: str = "",
    terminal_host_id: str = "",
    write_scope_root: str = "",
) -> Session:
    """Start a fresh session (``新会话``); empty goal/topics; phase S4 (direct chat)."""
    cid = conversation_id or generate_conversation_id()
    session_dir = sessions_root(paths) / cid
    if session_dir.exists():
        raise SessionError(f"session already exists: {cid}")

    # UX-016: carry over workspace_evolved_approved from latest session
    workspace_approved = False
    latest_id = find_latest_session_id(paths, include_internal=True)
    if latest_id is not None:
        latest_meta_path = sessions_root(paths) / latest_id / META_FILENAME
        if latest_meta_path.is_file():
            try:
                latest_payload = json.loads(latest_meta_path.read_text(encoding="utf-8"))
                if isinstance(latest_payload, dict):
                    workspace_approved = bool(latest_payload.get("workspace_evolved_approved", False))
            except (OSError, json.JSONDecodeError):
                pass

    harness_kind = normalize_harness(harness)
    scope_kind = normalize_terminal_scope_kind(terminal_scope_kind)
    if harness_kind == "terminal" and not scope_kind:
        raise SessionError("terminal session requires terminal_scope_kind")

    normalized_write_scope = ""
    if write_scope_root.strip():
        from sandbox_evolution import normalize_write_scope_root

        normalized_write_scope = normalize_write_scope_root(write_scope_root)
    if normalized_write_scope and harness_kind != "desktop":
        raise SessionError("write_scope_root is only supported for desktop harness sessions")

    meta = SessionMeta(
        topics=[],
        llm_model=resolve_session_model([]),
        updated_at=utc_now_iso(),
        phase="S4",
        workspace_evolved_approved=workspace_approved,
        harness=harness_kind,
        terminal_scope_kind=scope_kind,
        terminal_cwd=normalize_terminal_path_field(terminal_cwd),
        terminal_foreign_root=normalize_terminal_path_field(
            terminal_foreign_root, relative=False
        ),
        terminal_host_id=terminal_host_id.strip(),
        turn_mode="agent" if harness_kind == "terminal" else DEFAULT_TURN_MODE,
        write_scope_root=normalized_write_scope,
    )
    enforce_terminal_meta_invariants(meta)
    session = Session(
        conversation_id=cid,
        session_dir=session_dir,
        goal="",
        meta=meta,
        messages=[],
        paths=paths,
    )
    session.save()
    return session


def create_sandbox_evolution_session(paths: AgentPaths) -> Session:
    """Desktop session that may only write under the sandbox git worktree."""
    from sandbox_evolution import (
        sandbox_session_seed_content,
        sandbox_worktree_ready,
        sandbox_write_scope_relative,
    )

    if not sandbox_worktree_ready(paths):
        raise SessionError("sandbox 未初始化；请先运行 init-sandbox.bat")
    scope = sandbox_write_scope_relative(paths)
    session = create_new(paths, write_scope_root=scope)
    session.goal = "实验进化（sandbox）"
    session.append_message(
        {"role": "user", "content": sandbox_session_seed_content(scope)},
        persist=False,
    )
    session.save()
    write_last_conversation_id(paths, session.conversation_id)
    return session


def create_terminal_session(
    paths: AgentPaths,
    *,
    conversation_id: str | None = None,
    terminal_scope_kind: TerminalScopeKind,
    terminal_cwd: str = "",
    terminal_foreign_root: str = "",
    terminal_host_id: str = "",
) -> Session:
    """Create a Terminal harness session (TERMINAL-MODE §4.1)."""
    return create_new(
        paths,
        conversation_id=conversation_id,
        harness="terminal",
        terminal_scope_kind=terminal_scope_kind,
        terminal_cwd=terminal_cwd,
        terminal_foreign_root=terminal_foreign_root,
        terminal_host_id=terminal_host_id,
    )


def resume_terminal_session(
    paths: AgentPaths,
    scope: "TerminalScopeFields",
) -> Session:
    """Resume ``terminal_last_session`` when harness=terminal, else create (TM-15)."""
    from terminal_scope import TerminalScopeFields, scope_fields_to_session_kwargs

    if not isinstance(scope, TerminalScopeFields):
        raise SessionError("resume_terminal_session requires TerminalScopeFields")

    conversation_id = read_terminal_last_session_id(paths)
    if conversation_id is not None:
        try:
            return load_session_for_harness(paths, conversation_id, expected="terminal")
        except HarnessMismatchError:
            pass
        except SessionError:
            pass
    return create_terminal_session(paths, **scope_fields_to_session_kwargs(scope))


def resume_desktop_latest(paths: AgentPaths) -> Session:
    """Resume the most recent desktop harness session, or create one."""
    conversation_id = find_latest_session_id(paths, harness="desktop")
    if conversation_id is None:
        return create_new(paths)
    return load_session_for_harness(paths, conversation_id, expected="desktop")


def resume_latest(paths: AgentPaths) -> Session:
    """Resume the most recent session, or create one if none exist."""
    return resume_desktop_latest(paths)


def resume_desktop_or_create(paths: AgentPaths | None = None) -> Session:
    """Desktop/CLI entry: prefer ``last_conversation_id`` when harness=desktop."""
    agent_paths = paths or AgentPaths.discover()
    conversation_id = read_last_conversation_id(agent_paths)
    if conversation_id is not None and not is_internal_session_id(conversation_id):
        try:
            return load_session_for_harness(agent_paths, conversation_id, expected="desktop")
        except HarnessMismatchError:
            pass
        except SessionError:
            pass
    return resume_desktop_latest(agent_paths)


def resume_or_create(paths: AgentPaths | None = None) -> Session:
    """Default desktop/CLI startup (harness=desktop only)."""
    return resume_desktop_or_create(paths)


def build_anchor_message(session: Session) -> dict[str, str]:
    """Fixed anchor block inserted before S4 history (RUNTIME.md §5)."""
    topics = ", ".join(session.meta.topics) if session.meta.topics else "(none)"
    content = (
        f"{ANCHOR_HEADER}\n"
        f"conversation_id: {session.conversation_id}\n"
        f"目标: {session.goal.strip() or '(unset)'}\n"
        f"主题: {topics}\n"
        f"说明: 工作区 workspace/；进化目录 evolve/；动手只用 Builtin 与 run_evolved。"
    )
    return {"role": "user", "content": content}


SEED_PREFIX = "[上下文衔接]"
_UI_SKIP_USER_PREFIXES = (ANCHOR_HEADER, "[内核]", SEED_PREFIX, "[Harness]")


def build_seed_message(
    *,
    previous_session_id: str,
    previous_goal: str,
    reason: str,
    hint: str = "",
) -> dict[str, str]:
    """Injected into a new session so the agent remembers why it was created."""
    parts = [
        f"{SEED_PREFIX}",
        f"延续自会话: {previous_session_id}",
    ]
    if reason:
        parts.append(f"切换原因: {reason}")
    parts.append(f"上一会话目标: {previous_goal.strip() or '(unset)'}")
    if hint:
        parts.append(f"提示: {hint}")
    return {"role": "user", "content": "\n".join(parts)}


def _ui_skip_chat_text(text: str) -> bool:
    """Hide kernel/harness routing lines from Desktop chat history."""
    if any(text.startswith(prefix) for prefix in _UI_SKIP_USER_PREFIXES):
        return True
    if text.startswith("狂奔模式：") or text.startswith("狂奔模式:"):
        return True
    try:
        from exec_reliability import is_internal_harness_chat_line

        if is_internal_harness_chat_line(text):
            return True
    except Exception:
        pass
    return False
_DEFAULT_SESSION_HISTORY_MAX_ITEMS = 200


def session_history_max_items() -> int:
    """UI-5972 · Desktop session.history window (0 = unlimited)."""
    raw = os.environ.get(
        "MY_AGENT_SESSION_HISTORY_MAX_ITEMS",
        str(_DEFAULT_SESSION_HISTORY_MAX_ITEMS),
    )
    try:
        return max(0, int(raw))
    except ValueError:
        return _DEFAULT_SESSION_HISTORY_MAX_ITEMS


_CHAT_IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")
_CHAT_IMAGE_TOOLS = frozenset({"generate_image", "show_images"})


def _is_chat_image_rel_path(value: str) -> bool:
    lowered = value.strip().lower().replace("\\", "/")
    return any(lowered.endswith(ext) for ext in _CHAT_IMAGE_SUFFIXES)


def _image_paths_from_tool_message(message: dict[str, Any]) -> list[str]:
    if message.get("role") != "tool":
        return []
    content = message.get("content")
    if not isinstance(content, str):
        return []
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, dict) or not payload.get("ok"):
        return []
    tool = str(payload.get("tool") or "")
    if tool not in _CHAT_IMAGE_TOOLS:
        return []
    data = payload.get("data")
    if not isinstance(data, dict):
        return []
    paths: list[str] = []
    raw = data.get("display_paths")
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, str) and item.strip():
                normalized = item.strip().replace("\\", "/")
                if _is_chat_image_rel_path(normalized):
                    paths.append(normalized)
    if not paths and tool == "generate_image":
        path = data.get("path")
        if isinstance(path, str) and _is_chat_image_rel_path(path):
            paths.append(path.strip().replace("\\", "/"))
    return paths


def _append_chat_history_item(
    items: list[dict[str, Any]],
    item: dict[str, Any],
    *,
    max_items: int | None,
) -> None:
    items.append(item)
    if max_items and max_items > 0 and len(items) > max_items:
        items.pop(0)


def _build_chat_history_from_messages(
    messages: list[dict[str, Any]],
    *,
    max_items: int | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """User/assistant lines (+ inline_images from tool results) for Desktop hydration."""
    items: list[dict[str, Any]] = []
    total = 0
    pending_images: list[str] = []

    for message in messages:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        if _is_internal_work_card_message(message):
            if _valid_work_card_ref(message):
                total += 1
                _append_chat_history_item(items, {"role": "work_card", "type": "work_card_reference",
                    "presentation_id": message["presentation_id"], "card_id": message["card_id"],
                    "text": "工作卡回查引用；仅记录待恢复投递，不表示已送达、已查看或验收。"}, max_items=max_items)
                pending_images = []
            continue
        if role == "tool":
            for path in _image_paths_from_tool_message(message):
                if path not in pending_images:
                    pending_images.append(path)
            continue

        content = message.get("content")
        if role == "user":
            pending_images = []
            if not isinstance(content, str):
                continue
            text = content.strip()
            if not text or _ui_skip_chat_text(text):
                continue
            total += 1
            _append_chat_history_item(items, {"role": "user", "text": text}, max_items=max_items)
            continue

        if role == "assistant":
            host_meta = message.get(HOST_INTERNAL_METADATA_KEY)
            has_stream_identity = isinstance(host_meta, dict) and isinstance(host_meta.get("stream_request_id"), str)
            if message.get("tool_calls") and not (has_stream_identity and isinstance(content, str)):
                continue
            if not isinstance(content, str):
                continue
            text = content.strip()
            if not text or _ui_skip_chat_text(text):
                continue
            item: dict[str, Any] = {"role": "assistant", "text": text}
            host_meta = message.get(HOST_INTERNAL_METADATA_KEY)
            if isinstance(host_meta, dict) and host_meta.get("stream_incomplete") is True:
                item["text"] = (
                    text + "\n\n（流式回复中断；以上仅为此前已显示的片段，不代表完整答复。）"
                )
            if pending_images:
                item["inline_images"] = list(pending_images)
                pending_images = []
            total += 1
            _append_chat_history_item(items, item, max_items=max_items)

    return items, total



def build_session_chat_history(session: Session) -> list[dict[str, Any]]:
    """User/assistant lines for desktop chat hydration (DESKTOP §5.2 session.history)."""
    if not session._messages_fully_loaded and session.messages_path.is_file():
        max_items = session_history_max_items()
        items, _total = build_session_chat_history_from_path(
            session.messages_path,
            max_items=max_items if max_items > 0 else None,
        )
        return items
    messages = session.messages
    if any(_is_internal_work_card_message(message) for message in messages):
        try:
            with _session_messages_lock(session.session_dir):
                safe = not session._messages_corrupt and not _messages_unusable_for_ref(session)
        except Exception:
            safe = False
        if not safe:
            messages = [message for message in messages if not _is_internal_work_card_message(message)]
    items, _ = _build_chat_history_from_messages(messages)
    return items


def build_session_chat_history_from_path(
    messages_path: Path,
    *,
    max_items: int | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """Stream jsonl once; return (items, total_ui_item_count). UI-5972 switch fast path."""
    if not messages_path.is_file():
        return [], 0
    parsed: list[dict[str, Any]] = []
    corrupt = False
    try:
        with _session_messages_lock(messages_path.parent):
            with messages_path.open("r", encoding="utf-8", newline="") as handle:
                for line in handle:
                    text_line = line.strip()
                    if not text_line or not line.endswith("\n"):
                        corrupt = True
                        if not text_line:
                            continue
                    try:
                        message = json.loads(text_line)
                    except json.JSONDecodeError:
                        corrupt = True
                        continue
                    try:
                        json.loads(text_line, object_pairs_hook=_unique_message_object,
                                   parse_constant=_reject_message_constant)
                    except ValueError:
                        corrupt = True
                    if isinstance(message, dict):
                        parsed.append(message)
                    else:
                        corrupt = True
    except (OSError, UnicodeError, SessionError):
        return [], 0
    if corrupt:
        parsed = [message for message in parsed if not _is_internal_work_card_message(message)]
    return _build_chat_history_from_messages(parsed, max_items=max_items)


def session_history_event(session: Session) -> dict[str, Any]:
    max_items = session_history_max_items()
    if not session._messages_fully_loaded and session.messages_path.is_file():
        items, total_items = build_session_chat_history_from_path(
            session.messages_path,
            max_items=max_items if max_items > 0 else None,
        )
    else:
        items = build_session_chat_history(session)
        total_items = len(items)
    truncated = False
    omitted_count = 0
    if max_items > 0 and total_items > max_items:
        omitted_count = total_items - max_items
        items = items[-max_items:]
        truncated = True
    payload: dict[str, Any] = {
        "type": "session.history",
        "session_id": session.conversation_id,
        "project_id": session.meta.project_id or None,
        "items": items,
        "total_items": total_items,
    }
    if truncated:
        payload["truncated"] = True
        payload["omitted_count"] = omitted_count
    return payload


def prompt_and_set_goal(
    session: Session,
    input_fn: Callable[[str], str],
    *,
    prompt: str = GOAL_PROMPT,
) -> str:
    """S1: ask goal, persist ``goal.md``, advance phase to S2 when non-empty (MEMORY §6.1)."""
    try:
        goal = input_fn(f"{prompt} ").strip()
    except (KeyboardInterrupt, EOFError):
        goal = ""
    session.set_goal(goal)
    session.persist_goal()
    session.meta.updated_at = utc_now_iso()
    _write_meta(session.meta_path, session.meta, agent_root=session.paths.agent_root)
    return goal


def _read_meta(
    meta_path: Path,
    *,
    corruption_kinds: list[str] | None = None,
) -> SessionMeta:
    """Load SessionMeta; optionally record structural failure kinds for notices.

    Kinds: ``missing`` | ``unreadable`` | ``non_object``. Field-level defaults
    via ``from_dict`` are not corruption (DOC-05). Callers that omit
    ``corruption_kinds`` stay silent (e.g. refresh_pending_feedback).
    """
    default = SessionMeta(llm_model=resolve_session_model([]), updated_at=utc_now_iso())
    if not meta_path.is_file():
        if corruption_kinds is not None:
            corruption_kinds.append("missing")
        return default
    try:
        payload = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        if corruption_kinds is not None:
            corruption_kinds.append("unreadable")
        return default
    if not isinstance(payload, dict):
        if corruption_kinds is not None:
            corruption_kinds.append("non_object")
        return default
    return SessionMeta.from_dict(payload)


def _write_meta(meta_path: Path, meta: SessionMeta, *, agent_root: Path | None = None) -> None:
    payload: dict[str, Any] = {}
    existing: dict[str, Any] | None = None
    if meta_path.is_file():
        try:
            loaded = json.loads(meta_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                payload = loaded
                existing = loaded
        except (OSError, json.JSONDecodeError):
            payload = {}
    assert_harness_immutable(existing, meta)
    enforce_terminal_meta_invariants(meta)
    payload.update(meta.to_dict())
    from file_guard import atomic_write_text

    atomic_write_text(
        meta_path,
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        agent_root=agent_root,
    )


def format_meta_corruption_notice(kind: str) -> str:
    """User-facing text when meta.json structurally failed (T-1823-03)."""
    if kind == "missing":
        lead = "会话元数据缺失，已回退默认（meta.json）。"
    else:
        lead = "会话元数据损坏，已回退默认（meta.json）。"
    return f"{lead}主题/壳/项目绑定可能已丢失，请核对顶栏。"


def format_messages_corruption_notice(skipped_lines: list[int]) -> str:
    """User-facing text when bad ``messages.jsonl`` lines were skipped (T-1823-01)."""
    count = len(skipped_lines)
    text = f"会话历史有 {count} 行损坏已跳过（messages.jsonl）。聊天区仅显示可读消息。"
    if skipped_lines and count <= 12:
        text += f" 行号: {', '.join(str(n) for n in skipped_lines)}。"
    return text


def corruption_notice_events(session: Session) -> list[dict[str, Any]]:
    """WS events for session + paths corruption notices (emit after ``session.history``)."""
    texts: list[str] = []
    for text in session.corruption_notices:
        if isinstance(text, str) and text.strip():
            texts.append(text)
    for text in session.paths.corruption_notices:
        if isinstance(text, str) and text.strip() and text not in texts:
            texts.append(text)
    return [{"type": "turn.notice", "level": "warn", "text": text} for text in texts]


def emit_corruption_notices(
    emit: Callable[[dict[str, Any]], None],
    session: Session,
) -> None:
    for event in corruption_notice_events(session):
        emit(event)


def _read_messages(
    messages_path: Path,
    *,
    skipped_lines: list[int] | None = None,
) -> list[dict[str, Any]]:
    """Load message dicts from jsonl; optionally record 1-based physical line skips."""
    if not messages_path.is_file():
        return []
    messages: list[dict[str, Any]] = []
    for line_no, line in enumerate(
        messages_path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        text = line.strip()
        if not text:
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            if skipped_lines is not None:
                skipped_lines.append(line_no)
            continue
        if isinstance(payload, dict):
            messages.append(payload)
        elif skipped_lines is not None:
            skipped_lines.append(line_no)
    return messages


def _read_messages_tail(
    messages_path: Path,
    max_records: int,
    *,
    skipped_lines: list[int] | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """Load trailing jsonl records without reading the whole file into memory."""
    total = _count_file_newlines(messages_path)
    if total == 0 or max_records <= 0:
        return [], total
    if total <= max_records:
        return _read_messages(messages_path, skipped_lines=skipped_lines), total
    try:
        size = messages_path.stat().st_size
    except OSError:
        return [], total
    chunk_size = 256 * 1024
    collected: list[str] = []
    with messages_path.open("rb") as handle:
        pos = size
        buffer = ""
        while pos > 0 and len(collected) < max_records:
            read_size = min(chunk_size, pos)
            pos -= read_size
            handle.seek(pos)
            chunk = handle.read(read_size).decode("utf-8", errors="replace")
            buffer = chunk + buffer
            lines = buffer.split("\n")
            if pos > 0:
                buffer = lines[0]
                lines = lines[1:]
            else:
                buffer = ""
            for line in reversed(lines):
                stripped = line.strip()
                if stripped:
                    collected.append(stripped)
                    if len(collected) >= max_records:
                        break
    collected.reverse()
    messages: list[dict[str, Any]] = []
    for line in collected:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            if skipped_lines is not None:
                skipped_lines.append(-1)
            continue
        if isinstance(payload, dict):
            messages.append(payload)
    return messages, total


def desktop_switch_message_cap() -> int:
    """0 = defer messages.jsonl load until first turn (UI-5972)."""
    raw = os.environ.get("MY_AGENT_SWITCH_MESSAGE_CAP", "0").strip()
    try:
        return max(0, int(raw))
    except ValueError:
        return 0


def session_summary_for_id(
    paths: AgentPaths,
    conversation_id: str,
) -> dict[str, Any] | None:
    """Single-session summary for project threads (avoid full list_session_summaries)."""
    entry = sessions_root(paths) / conversation_id
    if not entry.is_dir():
        return None
    if not ((entry / META_FILENAME).is_file() or (entry / MESSAGES_FILENAME).is_file()):
        return None
    goal = ""
    updated_at = ""
    project_id = ""
    meta_path = entry / META_FILENAME
    harness = DEFAULT_HARNESS
    if meta_path.is_file():
        try:
            payload = json.loads(meta_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                updated_at = str(payload.get("updated_at", "") or "")
                raw_pid = payload.get("project_id", "")
                if isinstance(raw_pid, str):
                    project_id = raw_pid.strip()
                harness = normalize_harness(payload.get("harness", DEFAULT_HARNESS))
        except (OSError, json.JSONDecodeError):
            pass
    if harness == "terminal":
        return None
    goal_path = entry / GOAL_FILENAME
    if goal_path.is_file():
        try:
            goal = goal_path.read_text(encoding="utf-8").strip()
        except OSError:
            pass
    first_user, last_user, msg_count = _extract_user_messages(entry / MESSAGES_FILENAME)
    if project_id:
        title = project_id
    else:
        title = goal[:80] if goal else (first_user[:80] if first_user else conversation_id)
    return {
        "session_id": conversation_id,
        "title": title,
        "preview": last_user[:120] if last_user else "",
        "first_user_message": first_user,
        "last_user_message": last_user,
        "updated_at": updated_at,
        "message_count": msg_count,
        "project_id": project_id,
    }


def _write_messages_snapshot(
    messages_path: Path,
    messages: list[dict[str, Any]],
    *,
    agent_root: Path | None = None,
) -> None:
    lines = [json.dumps(message, ensure_ascii=False) for message in messages]
    content = "\n".join(lines)
    if content:
        content += "\n"
    from file_guard import atomic_write_text

    atomic_write_text(messages_path, content, agent_root=agent_root)


def _demo() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "evolve").mkdir()
        (root / "evolve" / "_index.core.toml").write_text("", encoding="utf-8")
        (root / "workspace").mkdir()
        paths = AgentPaths.from_root(root)

        first = create_new(paths, conversation_id="demo-alpha")
        first.set_goal("Ship session persistence")
        first.set_topics(["workflow"])
        first.append_message({"role": "user", "content": "hello"})
        first.save()

        assert first.goal_path.is_file()
        assert first.meta_path.is_file()
        assert first.messages_path.is_file()
        assert first.tool_outputs_dir.is_dir()
        print("[PASS] create_new writes goal/meta/messages/tool_outputs")

        loaded = Session.load(paths, "demo-alpha")
        assert loaded.goal == "Ship session persistence"
        assert loaded.meta.topics == ["workflow"]
        assert loaded.messages == [{"role": "user", "content": "hello"}]
        print("[PASS] Session.load round-trip")

        resumed = resume_latest(paths)
        assert resumed.conversation_id == "demo-alpha"
        print("[PASS] resume_latest picks recent session")

        second = create_new(paths, conversation_id="demo-beta")
        second.set_goal("Second thread")
        second.save()

        assert resume_latest(paths).conversation_id == "demo-beta"
        print("[PASS] newer session wins resume_latest")

        state = json.loads((paths.data / STATE_FILENAME).read_text(encoding="utf-8"))
        assert state[STATE_LAST_SESSION_KEY] == "demo-beta"
        print("[PASS] state.json last_conversation_id updated")

        internal = create_new(paths, conversation_id="_internal_demo")
        internal.set_goal("internal")
        internal.save()
        assert find_latest_session_id(paths) == "demo-beta"
        assert "_internal_demo" not in list_session_ids(paths)
        print("[PASS] underscore sessions excluded from default resume")

        pointer = read_last_conversation_id(paths)
        assert pointer == "demo-beta"
        resumed_via_state = resume_or_create(paths)
        assert resumed_via_state.conversation_id == "demo-beta"
        print("[PASS] resume_or_create uses state.json pointer")

        empty_root = root / "empty-agent"
        (empty_root / "evolve").mkdir(parents=True)
        (empty_root / "evolve" / "_index.core.toml").write_text("", encoding="utf-8")
        (empty_root / "workspace").mkdir()
        empty_paths = AgentPaths.from_root(empty_root)
        fresh = resume_latest(empty_paths)
        assert fresh.messages == []
        assert fresh.meta.phase == "S4"
        print("[PASS] resume_latest creates session when none exist")

        anchor = build_anchor_message(loaded)
        assert anchor["role"] == "user"
        assert "demo-alpha" in anchor["content"]
        assert "Ship session persistence" in anchor["content"]
        assert "workflow" in anchor["content"]
        assert anchor["content"].startswith(ANCHOR_HEADER)
        print("[PASS] build_anchor_message template")

        goal_session = create_new(paths, conversation_id="demo-goal")
        goal_session.meta.phase = "S1"
        captured: list[str] = []

        def fake_input(prompt: str) -> str:
            captured.append(prompt)
            return "Write MEMORY.md"

        goal_text = prompt_and_set_goal(goal_session, fake_input)
        assert goal_text == "Write MEMORY.md"
        assert GOAL_PROMPT in captured[0]
        assert goal_session.goal_path.read_text(encoding="utf-8") == "Write MEMORY.md"
        assert goal_session.meta.phase == "S2"
        reloaded = Session.load(paths, "demo-goal")
        assert reloaded.goal == "Write MEMORY.md"
        anchor_goal = build_anchor_message(reloaded)
        assert "目标: Write MEMORY.md" in anchor_goal["content"]
        print("[PASS] T-303: goal prompt → goal.md → anchor context")

        quick = create_new(paths, conversation_id="demo-quick")
        assert quick.meta.phase == "S4"
        assert quick.goal == ""
        assert quick.meta.topics == []
        print("[PASS] create_new: direct chat (S4, empty goal/topics)")

        refresh_session = create_new(paths, conversation_id="demo-pending")
        refresh_session.meta.pending_feedback = [
            {"entity_id": "mem-a", "type": "memory", "level": "L2", "used_at": utc_now_iso()}
        ]
        refresh_session.save()
        refresh_session.meta.pending_feedback = []
        disk_meta = json.loads(refresh_session.meta_path.read_text(encoding="utf-8"))
        disk_meta["pending_feedback"] = [
            {"entity_id": "mem-b", "type": "memory", "level": "L2", "used_at": utc_now_iso()}
        ]
        refresh_session.meta_path.write_text(
            json.dumps(disk_meta, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        refresh_session.refresh_pending_feedback_from_disk()
        assert refresh_session.meta.pending_feedback[0]["entity_id"] == "mem-b"
        print("[PASS] T-602b: refresh_pending_feedback_from_disk")

        none = resume_latest(empty_paths)
        none_id = none.conversation_id
        assert resume_or_create(empty_paths).conversation_id == none_id
        print("[PASS] resume_or_create falls back to latest when no state pointer")

        mode_session = create_new(paths, conversation_id="demo-mode")
        assert mode_session.meta.turn_mode == "agent"
        mode_session.set_turn_mode("ask")
        mode_session.save()
        reloaded_mode = Session.load(paths, "demo-mode")
        assert reloaded_mode.meta.turn_mode == "ask"
        assert parse_turn_mode_command("只聊") == "ask"
        assert parse_turn_mode_command("动手") == "agent"
        assert parse_turn_mode_command("ask") == "ask"
        assert parse_turn_mode_command("agent") == "agent"
        assert parse_turn_mode_command("hello") is None
        print("[PASS] T-702: turn_mode persist + parse_turn_mode_command")

        hist_session = create_new(paths, conversation_id="_demo_chat_hist")
        hist_session.messages = [
            build_anchor_message(hist_session),
            {"role": "user", "content": "你好"},
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好，需要什么？"},
            {"role": "assistant", "content": None, "tool_calls": [{"id": "x", "type": "function", "function": {"name": "list_dir", "arguments": "{}"}}]},
            {"role": "user", "content": "[内核] 请直接回答"},
        ]
        history = build_session_chat_history(hist_session)
        assert history == [
            {"role": "user", "text": "你好"},
            {"role": "user", "text": "你好"},
            {"role": "assistant", "text": "你好，需要什么？"},
        ]
        event = session_history_event(hist_session)
        assert event["type"] == "session.history" and len(event["items"]) == 3
        print("[PASS] T-905d: chat history preserves repeated turns and skips anchor/tool-only/kernel")


if __name__ == "__main__":
    _demo()
