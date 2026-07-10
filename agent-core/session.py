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

ANCHOR_HEADER = "[本次会议上下文]"


class SessionError(Exception):
    """Invalid session operation."""


@dataclass
class SessionMeta:
    """Persisted session metadata (RUNTIME.md §2.2 meta.json)."""

    topics: list[str] = field(default_factory=list)
    llm_model: str = ""
    updated_at: str = ""
    phase: SessionPhase = "S1"
    workspace_evolved_approved: bool = False
    pending_feedback: list[dict[str, Any]] = field(default_factory=list)
    compact_before_index: int = 0
    evolve_offer_pending: bool = False
    evolve_offer_used: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "topics": list(self.topics),
            "llm_model": self.llm_model,
            "updated_at": self.updated_at,
            "phase": self.phase,
            "workspace_evolved_approved": self.workspace_evolved_approved,
            "pending_feedback": list(self.pending_feedback),
            "compact_before_index": self.compact_before_index,
            "evolve_offer_pending": self.evolve_offer_pending,
            "evolve_offer_used": self.evolve_offer_used,
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

        updated_at = payload.get("updated_at", "")
        if not isinstance(updated_at, str):
            updated_at = ""

        return cls(
            topics=topics,
            llm_model=llm_model,
            updated_at=updated_at,
            phase=phase,
            workspace_evolved_approved=bool(payload.get("workspace_evolved_approved", False)),
            pending_feedback=pending_feedback,
            compact_before_index=int(payload.get("compact_before_index", 0) or 0),
            evolve_offer_pending=bool(payload.get("evolve_offer_pending", False)),
            evolve_offer_used=bool(payload.get("evolve_offer_used", False)),
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
        self.meta.llm_model = resolve_session_model(cleaned)
        if phase is not None:
            self.meta.phase = phase

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

        meta = _read_meta(session_dir / META_FILENAME)
        messages = _read_messages(session_dir / MESSAGES_FILENAME)
        return cls(
            conversation_id=conversation_id,
            session_dir=session_dir,
            goal=goal,
            meta=meta,
            messages=messages,
            paths=paths,
        )

    def _append_message_line(self, message: dict[str, Any]) -> None:
        self.session_dir.mkdir(parents=True, exist_ok=True)
        line = json.dumps(message, ensure_ascii=False) + "\n"
        with self.messages_path.open("a", encoding="utf-8") as handle:
            handle.write(line)


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


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
    state_path = paths.data / STATE_FILENAME
    if not state_path.is_file():
        return None
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
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
    state_path = paths.data / STATE_FILENAME
    payload: dict[str, Any] = {}
    if state_path.is_file():
        try:
            loaded = json.loads(state_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                payload = loaded
        except (OSError, json.JSONDecodeError):
            payload = {}
    payload[STATE_LAST_SESSION_KEY] = conversation_id
    paths.data.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def create_new(
    paths: AgentPaths,
    *,
    conversation_id: str | None = None,
) -> Session:
    """Start a fresh session (``新会话``); empty goal/topics/messages."""
    cid = conversation_id or generate_conversation_id()
    session_dir = sessions_root(paths) / cid
    if session_dir.exists():
        raise SessionError(f"session already exists: {cid}")

    meta = SessionMeta(
        topics=[],
        llm_model=resolve_session_model([]),
        updated_at=utc_now_iso(),
        phase="S1",
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


def _read_meta(meta_path: Path) -> SessionMeta:
    if not meta_path.is_file():
        return SessionMeta(llm_model=resolve_session_model([]), updated_at=utc_now_iso())
    try:
        payload = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return SessionMeta(llm_model=resolve_session_model([]), updated_at=utc_now_iso())
    if not isinstance(payload, dict):
        return SessionMeta(llm_model=resolve_session_model([]), updated_at=utc_now_iso())
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


def _read_messages(messages_path: Path) -> list[dict[str, Any]]:
    if not messages_path.is_file():
        return []
    messages: list[dict[str, Any]] = []
    for line in messages_path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text:
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            messages.append(payload)
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
        (root / "evolve" / "_index.toml").write_text("", encoding="utf-8")
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
        (empty_root / "evolve" / "_index.toml").write_text("", encoding="utf-8")
        (empty_root / "workspace").mkdir()
        empty_paths = AgentPaths.from_root(empty_root)
        fresh = resume_latest(empty_paths)
        assert fresh.messages == []
        assert fresh.meta.phase == "S1"
        print("[PASS] resume_latest creates session when none exist")

        anchor = build_anchor_message(loaded)
        assert anchor["role"] == "user"
        assert "demo-alpha" in anchor["content"]
        assert "Ship session persistence" in anchor["content"]
        assert "workflow" in anchor["content"]
        assert anchor["content"].startswith(ANCHOR_HEADER)
        print("[PASS] build_anchor_message template")

        goal_session = create_new(paths, conversation_id="demo-goal")
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


if __name__ == "__main__":
    _demo()
