"""Session resume, persistence, and conversation state (RUNTIME.md §2, TASKS T-203)."""

from __future__ import annotations

import json
import secrets
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

_AGENT_CORE = Path(__file__).resolve().parent
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from llm_client import resolve_session_model
from paths import AgentPaths

SESSIONS_DIRNAME = "sessions"
GOAL_FILENAME = "goal.md"
GOAL_PROMPT = "这次主要做什么？"
META_FILENAME = "meta.json"
MESSAGES_FILENAME = "messages.jsonl"
DIGEST_FILENAME = "digest.md"
TOOL_OUTPUTS_DIRNAME = "tool_outputs"
STATE_FILENAME = "state.json"
STATE_LAST_SESSION_KEY = "last_conversation_id"

VALID_PHASES = frozenset({"S1", "S2", "S3", "S4"})
SessionPhase = Literal["S1", "S2", "S3", "S4"]

TurnMode = Literal["ask", "agent"]
DEFAULT_TURN_MODE: TurnMode = "agent"
VALID_TURN_MODES = frozenset({"ask", "agent"})

# Deprecated since shell consolidation (Phase 3). Kept for old session compatibility.
ShellId = Literal["grow", "daily", "govern", "project", "unified"]
DEFAULT_ACTIVE_SHELL: ShellId = "grow"
VALID_SHELLS = frozenset({"grow", "daily", "govern", "project", "unified"})

PlanStatus = Literal["", "draft", "confirmed", "plan_dirty"]
VALID_PLAN_STATUSES = frozenset({"", "draft", "confirmed", "plan_dirty"})

ProjectDeliveryProfile = Literal["solo", "ritual"]
DEFAULT_PROJECT_DELIVERY_PROFILE: ProjectDeliveryProfile = "solo"
VALID_PROJECT_DELIVERY_PROFILES = frozenset({"solo", "ritual"})

ANCHOR_HEADER = "[本次会议上下文]"


class SessionError(Exception):
    """Invalid session operation."""


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
    pending_feedback: list[dict[str, Any]] = field(default_factory=list)
    compact_before_index: int = 0
    evolve_offer_pending: bool = False
    evolve_offer_used: bool = False
    turn_mode: TurnMode = DEFAULT_TURN_MODE
    reasoning_effort: str = "high"  # "low" | "high" | "max"; REASONING-EFFORT.md
    active_shell: ShellId = DEFAULT_ACTIVE_SHELL
    project_root: str = ""
    project_id: str = ""
    project_plan_status: PlanStatus = ""
    project_plan_confirmed_at: str = ""
    project_phase_fingerprint: str = ""
    project_doc_fingerprint: str = ""
    project_delivery_profile: ProjectDeliveryProfile = DEFAULT_PROJECT_DELIVERY_PROFILE

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
            "project_plan_confirmed_at": self.project_plan_confirmed_at,
            "project_phase_fingerprint": self.project_phase_fingerprint,
            "project_doc_fingerprint": self.project_doc_fingerprint,
            "project_delivery_profile": self.project_delivery_profile,
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

        confirmed_at = payload.get("project_plan_confirmed_at", "")
        if not isinstance(confirmed_at, str):
            confirmed_at = ""

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

        return cls(
            topics=topics,
            llm_model=llm_model,
            llm_model_override=llm_model_override,
            execution_model=execution_model.strip(),
            planning_model=planning_model.strip(),
            updated_at=updated_at,
            phase=phase,
            workspace_evolved_approved=bool(payload.get("workspace_evolved_approved", False)),
            pending_feedback=pending_feedback,
            compact_before_index=int(payload.get("compact_before_index", 0) or 0),
            evolve_offer_pending=bool(payload.get("evolve_offer_pending", False)),
            evolve_offer_used=bool(payload.get("evolve_offer_used", False)),
            turn_mode=normalize_turn_mode(payload.get("turn_mode", DEFAULT_TURN_MODE)),
            reasoning_effort=normalize_reasoning_effort(
                payload.get("reasoning_effort", "high")
            ),
            active_shell=active_shell,
            project_root=project_root.strip(),
            project_id=project_id.strip(),
            project_plan_status=project_plan_status,
            project_plan_confirmed_at=confirmed_at,
            project_phase_fingerprint=phase_fp,
            project_doc_fingerprint=doc_fp,
            project_delivery_profile=project_delivery_profile,
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
    scaffold_tool_turn: bool = field(default=False, compare=False, repr=False)
    scaffold_check_status: str | None = field(default=None, compare=False, repr=False)
    scaffold_check_tool: str | None = field(default=None, compare=False, repr=False)
    # Ephemeral load warnings (bad jsonl/meta); not persisted. STABILIZATION §3.9.1
    corruption_notices: list[str] = field(default_factory=list, compare=False, repr=False)

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
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.goal_path.write_text(self.goal, encoding="utf-8")

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
        self.meta.turn_mode = normalize_turn_mode(mode)

    def append_message(self, message: dict[str, Any], *, persist: bool = True) -> None:
        self.messages.append(message)
        if persist:
            self._append_message_line(message)

    def save(self) -> None:
        """Persist goal, meta, and update agent ``state.json`` pointer."""
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.tool_outputs_dir.mkdir(parents=True, exist_ok=True)
        self.meta.updated_at = utc_now_iso()
        self.persist_goal()
        _write_meta(self.meta_path, self.meta)
        _write_messages_snapshot(self.messages_path, self.messages)
        if not is_internal_session_id(self.conversation_id):
            write_last_conversation_id(self.paths, self.conversation_id)

    def refresh_pending_feedback_from_disk(self) -> None:
        """Reload ``pending_feedback`` from disk (executor may update meta.json directly)."""
        if not self.meta_path.is_file():
            return
        loaded = _read_meta(self.meta_path)
        self.meta.pending_feedback = list(loaded.pending_feedback)

    @classmethod
    def load(cls, paths: AgentPaths, conversation_id: str) -> Session:
        session_dir = sessions_root(paths) / conversation_id
        if not session_dir.is_dir():
            raise SessionError(f"session does not exist: {conversation_id}")

        goal = ""
        if (session_dir / GOAL_FILENAME).is_file():
            goal = (session_dir / GOAL_FILENAME).read_text(encoding="utf-8")

        meta_issues: list[str] = []
        meta = _read_meta(session_dir / META_FILENAME, corruption_kinds=meta_issues)
        skipped_lines: list[int] = []
        messages = _read_messages(
            session_dir / MESSAGES_FILENAME,
            skipped_lines=skipped_lines,
        )
        from context import repair_orphaned_tool_calls

        repaired = repair_orphaned_tool_calls(messages)
        if repaired != messages:
            _write_messages_snapshot(session_dir / MESSAGES_FILENAME, repaired)
            messages = repaired
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
        )

    def _append_message_line(self, message: dict[str, Any]) -> None:
        self.session_dir.mkdir(parents=True, exist_ok=True)
        line = json.dumps(message, ensure_ascii=False) + "\n"
        with self.messages_path.open("a", encoding="utf-8") as handle:
            handle.write(line)


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


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


REASONING_EFFORT_LEVELS = ("low", "high", "max")


def normalize_reasoning_effort(raw: Any) -> str:
    """Validate and normalize reasoning_effort value; fallback to ``"high"``."""
    if isinstance(raw, str) and raw.casefold() in REASONING_EFFORT_LEVELS:
        return raw.lower()
    return "high"


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
        "high": "默认推理",
        "max": "最高推理强度",
    }.get(level, "")


def session_banner_event(session: Session) -> dict[str, Any]:
    from llm_client import llm_model_label, resolve_session_model

    model = session.meta.llm_model or resolve_session_model(list(session.meta.topics))
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
        from project_mode import project_dir, read_task_stats

        payload["project_id"] = session.meta.project_id
        payload["project_root"] = session.meta.project_root or None
        payload["project_plan_status"] = session.meta.project_plan_status or "draft"
        tasks_path = project_dir(session.paths, session.meta.project_id) / "TASKS.md"
        stats = read_task_stats(tasks_path)
        payload["project_tasks_done"] = stats.done
        payload["project_tasks_total"] = stats.total
        if session.meta.project_plan_status == "confirmed":
            payload["project_plan_label"] = f"{stats.open_count}/{stats.total} 未完成"
        elif session.meta.project_plan_status == "plan_dirty":
            payload["project_plan_label"] = "计划已变更 · 待确认"
        else:
            payload["project_plan_label"] = "计划待确认"
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


def _extract_user_messages(messages_path: Path) -> tuple[str, str, int]:
    """Return (first_user_content, last_user_content, message_count) from a jsonl file.

    Skips anchor / kernel / seed prefixes so title and preview are real user messages.
    """
    try:
        text = messages_path.read_text(encoding="utf-8")
    except OSError:
        return "", "", 0

    lines = [l for l in text.splitlines() if l.strip()]
    msg_count = len(lines)
    first_user = ""
    last_user = ""

    for line in lines:
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(msg, dict) and msg.get("role") == "user":
            content = str(msg.get("content", "")).strip()
            if content and not any(content.startswith(p) for p in _UI_SKIP_USER_PREFIXES):
                first_user = content
                break

    for line in reversed(lines):
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(msg, dict) and msg.get("role") == "user":
            content = str(msg.get("content", "")).strip()
            if content and not any(content.startswith(p) for p in _UI_SKIP_USER_PREFIXES):
                last_user = content
                break

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
        if meta_path.is_file():
            try:
                payload = json.loads(meta_path.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    updated_at = str(payload.get("updated_at", "") or "")
                    raw_pid = payload.get("project_id", "")
                    if isinstance(raw_pid, str):
                        project_id = raw_pid.strip()
            except (OSError, json.JSONDecodeError):
                pass
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
        preview = last_user[:120] if last_user else ""
        entries.append({
            "session_id": cid,
            "title": title,
            "preview": preview,
            "updated_at": updated_at,
            "message_count": msg_count,
            "project_id": project_id,
        })

    # D5/D6: one row per project — prefer project_sessions mapping, else newest updated_at
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

    merged = unbound + list(by_project.values())
    merged.sort(key=lambda e: e["updated_at"], reverse=True)
    return merged[:limit]


def find_latest_session_id(
    paths: AgentPaths,
    *,
    include_internal: bool = False,
) -> str | None:
    candidates = list_session_ids(paths, include_internal=include_internal)
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


def write_last_conversation_id(paths: AgentPaths, conversation_id: str) -> None:
    from paths import read_agent_state_payload, write_agent_state_payload

    payload = read_agent_state_payload(paths)
    payload[STATE_LAST_SESSION_KEY] = conversation_id
    write_agent_state_payload(paths, payload)


def create_new(
    paths: AgentPaths,
    *,
    conversation_id: str | None = None,
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

    meta = SessionMeta(
        topics=[],
        llm_model=resolve_session_model([]),
        updated_at=utc_now_iso(),
        phase="S4",
        workspace_evolved_approved=workspace_approved,
    )
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


def resume_latest(paths: AgentPaths) -> Session:
    """Resume the most recent session, or create one if none exist."""
    conversation_id = find_latest_session_id(paths)
    if conversation_id is None:
        return create_new(paths)
    return Session.load(paths, conversation_id)


def resume_or_create(paths: AgentPaths | None = None) -> Session:
    """Default CLI startup: prefer ``state.json`` pointer, else latest session."""
    agent_paths = paths or AgentPaths.discover()
    conversation_id = read_last_conversation_id(agent_paths)
    if conversation_id is not None and not is_internal_session_id(conversation_id):
        return Session.load(agent_paths, conversation_id)
    return resume_latest(agent_paths)


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


_UI_SKIP_USER_PREFIXES = (ANCHOR_HEADER, "[内核]", SEED_PREFIX)


def build_session_chat_history(session: Session) -> list[dict[str, str]]:
    """User/assistant lines for desktop chat hydration (DESKTOP §5.2 session.history)."""
    items: list[dict[str, str]] = []
    last_user: str | None = None

    for message in session.messages:
        role = message.get("role")
        content = message.get("content")
        if role == "user":
            if not isinstance(content, str):
                continue
            text = content.strip()
            if not text:
                continue
            if any(text.startswith(prefix) for prefix in _UI_SKIP_USER_PREFIXES):
                continue
            if text == last_user:
                continue
            last_user = text
            items.append({"role": "user", "text": text})
            continue

        if role == "assistant":
            if not isinstance(content, str):
                continue
            text = content.strip()
            if not text:
                continue
            items.append({"role": "assistant", "text": text})
            last_user = None

    return items


def session_history_event(session: Session) -> dict[str, Any]:
    return {
        "type": "session.history",
        "items": build_session_chat_history(session),
    }


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
    _write_meta(session.meta_path, session.meta)
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


def _write_meta(meta_path: Path, meta: SessionMeta) -> None:
    payload: dict[str, Any] = {}
    if meta_path.is_file():
        try:
            loaded = json.loads(meta_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                payload = loaded
        except (OSError, json.JSONDecodeError):
            payload = {}
    payload.update(meta.to_dict())
    meta_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


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


def _write_messages_snapshot(messages_path: Path, messages: list[dict[str, Any]]) -> None:
    lines = [json.dumps(message, ensure_ascii=False) for message in messages]
    content = "\n".join(lines)
    if content:
        content += "\n"
    messages_path.write_text(content, encoding="utf-8")


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
            {"role": "assistant", "text": "你好，需要什么？"},
        ]
        event = session_history_event(hist_session)
        assert event["type"] == "session.history" and len(event["items"]) == 2
        print("[PASS] T-905d: build_session_chat_history skips anchor/tool-only/kernel")


if __name__ == "__main__":
    _demo()
