"""Append-only evolve_log.jsonl (TOOLS.md §6, TASKS T-110)."""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_AGENT_CORE = Path(__file__).resolve().parents[1]
_TOOLS_DIR = Path(__file__).resolve().parent
if sys.path and Path(sys.path[0]).resolve() == _TOOLS_DIR.resolve():
    sys.path.pop(0)
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from paths import AgentPaths
from tools.schema import ToolResult, tool_ok

EVOLVE_LOG_FILENAME = "evolve_log.jsonl"
LOG_SCHEMA_VERSION = "1.0"
EVENT_TOOL_CALL = "tool_call"
EVENT_SESSION_WORKSPACE_APPROVED = "session_workspace_approved"
EVENT_SESSION_START = "session_start"
EVENT_SESSION_END = "session_end"
EVENT_TOPICS_CONFIRMED = "topics_confirmed"
EVENT_CHECKPOINT_OPENED = "checkpoint_opened"
EVENT_PROPOSAL_CREATED = "proposal_created"
EVENT_PROPOSAL_SUPERSEDED = "proposal_superseded"
EVENT_EVOLVE_ACCEPTED = "evolve_accepted"
EVENT_EVOLVE_REJECTED = "evolve_rejected"
EVENT_TOOL_SPEC_ACCEPTED = "tool_spec_accepted"
EVENT_ENTITY_USED = "entity_used"
EVENT_FEEDBACK_POSITIVE = "feedback_positive"
EVENT_FEEDBACK_NEGATIVE = "feedback_negative"
EVENT_MARKED_SUSPECT = "marked_suspect"
EVENT_AUDIT_COMPLETED = "audit_completed"
EVENT_SUBAGENT_RUN = "subagent_run"
EVENT_GUARD = "guard"
_DEFAULT_ARG_MAX_CHARS = 500
_SENSITIVE_KEYS = frozenset(
    {
        "api_key",
        "token",
        "password",
        "secret",
        "authorization",
    }
)


class EvolveLog:
    """Append JSON lines to ``data/evolve_log.jsonl``."""

    def __init__(self, path: Path) -> None:
        self.path = path.resolve()

    @classmethod
    def for_agent(cls, paths: AgentPaths) -> EvolveLog:
        paths.data.mkdir(parents=True, exist_ok=True)
        return cls(paths.data / EVOLVE_LOG_FILENAME)

    def append(self, event: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")

    def append_event(self, event_type: str, **fields: Any) -> None:
        payload = {
            "schema_version": LOG_SCHEMA_VERSION,
            "event": event_type,
            "ts": utc_now_iso(),
            **fields,
        }
        self.append(payload)

    def log_tool_call(
        self,
        *,
        tool: str,
        arguments: dict[str, Any],
        result: ToolResult,
        conversation_id: str | None = None,
        confirm: str | None = None,
        evolved_tool: str | None = None,
        dry_run: bool | None = None,
    ) -> None:
        """Record one executor tool invocation (no secrets / full file bodies)."""
        fields: dict[str, Any] = {
            "tool": tool,
            "arguments": sanitize_log_value(arguments),
            "ok": result.ok,
            "duration_ms": result.duration_ms,
            "truncated": result.truncated,
        }
        if conversation_id is not None:
            fields["conversation_id"] = conversation_id
        if confirm is not None:
            fields["confirm"] = confirm
        if evolved_tool is not None:
            fields["evolved_tool"] = evolved_tool
        if dry_run is not None:
            fields["dry_run"] = dry_run
        if result.output_path is not None:
            fields["output_path"] = result.output_path
        if result.error is not None:
            fields["error_code"] = result.error.code
            fields["error_message"] = result.error.message
        if isinstance(result.data, dict):
            for key in (
                "host_src_id",
                "host_src_rel",
                "host_dst_id",
                "host_dst_rel",
                "host_root_id",
            ):
                value = result.data.get(key)
                if isinstance(value, str) and value:
                    fields[key] = value
        self.append_event(EVENT_TOOL_CALL, **fields)

    def log_session_workspace_approved(
        self,
        *,
        conversation_id: str | None = None,
        tool_name: str | None = None,
    ) -> None:
        fields: dict[str, Any] = {}
        if conversation_id is not None:
            fields["conversation_id"] = conversation_id
        if tool_name is not None:
            fields["tool_name"] = tool_name
        self.append_event(EVENT_SESSION_WORKSPACE_APPROVED, **fields)

    def log_session_start(
        self,
        *,
        conversation_id: str,
        memory_ids_loaded: list[str],
        topics_available: list[str],
    ) -> None:
        self.append_event(
            EVENT_SESSION_START,
            conversation_id=conversation_id,
            memory_ids_loaded=memory_ids_loaded,
            topics_available=topics_available,
        )

    def log_topics_confirmed(
        self,
        *,
        conversation_id: str,
        topics_confirmed: list[str],
        prompt_files_loaded: list[str],
        evolved_tools_listed: list[str],
    ) -> None:
        self.append_event(
            EVENT_TOPICS_CONFIRMED,
            conversation_id=conversation_id,
            topics_confirmed=topics_confirmed,
            prompt_files_loaded=prompt_files_loaded,
            evolved_tools_listed=evolved_tools_listed,
        )

    def log_session_end(
        self,
        *,
        conversation_id: str,
        record_mode: str = "off",
    ) -> None:
        self.append_event(
            EVENT_SESSION_END,
            conversation_id=conversation_id,
            record_mode=record_mode,
        )

    def log_checkpoint_opened(
        self,
        *,
        conversation_id: str,
        triggered_by: str,
        trigger_phrase: str,
    ) -> None:
        """T-402+ only; callers must pass CheckpointGate.may_open_checkpoint() first."""
        self.append_event(
            EVENT_CHECKPOINT_OPENED,
            conversation_id=conversation_id,
            triggered_by=triggered_by,
            trigger_phrase=trigger_phrase,
        )

    def log_proposal_created(
        self,
        *,
        conversation_id: str,
        proposal_id: str,
        proposal_type: str,
        fingerprint: str,
        dedup: str = "ok",
        path: str | None = None,
    ) -> None:
        fields: dict[str, Any] = {
            "conversation_id": conversation_id,
            "proposal_id": proposal_id,
            "type": proposal_type,
            "fingerprint": fingerprint,
            "dedup": dedup,
        }
        if path is not None:
            fields["path"] = path
        self.append_event(EVENT_PROPOSAL_CREATED, **fields)

    def log_proposal_superseded(
        self,
        *,
        conversation_id: str,
        old_id: str,
        new_id: str,
    ) -> None:
        self.append_event(
            EVENT_PROPOSAL_SUPERSEDED,
            conversation_id=conversation_id,
            old_id=old_id,
            new_id=new_id,
        )

    def log_evolve_accepted(
        self,
        *,
        proposal_id: str,
        proposal_type: str,
        path: str,
        action: str,
        conversation_id: str | None = None,
    ) -> None:
        fields: dict[str, Any] = {
            "proposal_id": proposal_id,
            "type": proposal_type,
            "path": path,
            "action": action,
        }
        if conversation_id is not None:
            fields["conversation_id"] = conversation_id
        self.append_event(EVENT_EVOLVE_ACCEPTED, **fields)

    def log_evolve_rejected(
        self,
        *,
        proposal_id: str,
        conversation_id: str | None = None,
    ) -> None:
        fields: dict[str, Any] = {"proposal_id": proposal_id}
        if conversation_id is not None:
            fields["conversation_id"] = conversation_id
        self.append_event(EVENT_EVOLVE_REJECTED, **fields)

    def log_tool_spec_accepted(
        self,
        *,
        tool_name: str,
        proposal_id: str,
        note: str = "pending_implementation",
        conversation_id: str | None = None,
    ) -> None:
        fields: dict[str, Any] = {
            "tool_name": tool_name,
            "proposal_id": proposal_id,
            "note": note,
        }
        if conversation_id is not None:
            fields["conversation_id"] = conversation_id
        self.append_event(EVENT_TOOL_SPEC_ACCEPTED, **fields)

    def log_entity_used(
        self,
        *,
        entity_id: str,
        entity_type: str,
        level: str,
        reason: str,
        conversation_id: str | None = None,
    ) -> None:
        fields: dict[str, Any] = {
            "entity_id": entity_id,
            "type": entity_type,
            "level": level,
            "reason": reason,
        }
        if conversation_id is not None:
            fields["conversation_id"] = conversation_id
        self.append_event(EVENT_ENTITY_USED, **fields)

    def log_feedback_positive(
        self,
        *,
        entity_id: str,
        conversation_id: str | None = None,
        note: str | None = None,
    ) -> None:
        fields: dict[str, Any] = {"entity_id": entity_id}
        if conversation_id is not None:
            fields["conversation_id"] = conversation_id
        if note is not None:
            fields["note"] = note
        self.append_event(EVENT_FEEDBACK_POSITIVE, **fields)

    def log_feedback_negative(
        self,
        *,
        entity_id: str,
        conversation_id: str | None = None,
        note: str | None = None,
    ) -> None:
        fields: dict[str, Any] = {"entity_id": entity_id}
        if conversation_id is not None:
            fields["conversation_id"] = conversation_id
        if note is not None:
            fields["note"] = note
        self.append_event(EVENT_FEEDBACK_NEGATIVE, **fields)

    def log_marked_suspect(
        self,
        *,
        entity_id: str,
        failure_streak: int,
        conversation_id: str | None = None,
    ) -> None:
        fields: dict[str, Any] = {
            "entity_id": entity_id,
            "failure_streak": failure_streak,
        }
        if conversation_id is not None:
            fields["conversation_id"] = conversation_id
        self.append_event(EVENT_MARKED_SUSPECT, **fields)

    def log_audit_completed(
        self,
        *,
        findings_count: int,
        scope: str,
        topics: list[str] | None = None,
        conversation_id: str | None = None,
    ) -> None:
        fields: dict[str, Any] = {
            "findings_count": findings_count,
            "scope": scope,
        }
        if topics is not None:
            fields["topics"] = topics
        if conversation_id is not None:
            fields["conversation_id"] = conversation_id
        self.append_event(EVENT_AUDIT_COMPLETED, **fields)

    def log_subagent_run(
        self,
        *,
        kind: str,
        tool_rounds: int,
        truncated: bool,
        paths_cited: list[str],
        conversation_id: str | None = None,
        verdict: str | None = None,
        tool_name: str | None = None,
    ) -> None:
        fields: dict[str, Any] = {
            "kind": kind,
            "tool_rounds": tool_rounds,
            "truncated": truncated,
            "paths_cited": list(paths_cited),
        }
        if conversation_id is not None:
            fields["conversation_id"] = conversation_id
        if verdict is not None:
            fields["verdict"] = verdict
        if tool_name is not None:
            fields["tool_name"] = tool_name
        self.append_event(EVENT_SUBAGENT_RUN, **fields)

    def log_guard_event(
        self,
        *,
        guard_type: str,
        conversation_id: str | None = None,
        **fields: Any,
    ) -> None:
        payload: dict[str, Any] = {
            "guard_type": guard_type,
            **sanitize_log_value(fields),
        }
        if conversation_id is not None:
            payload["conversation_id"] = conversation_id
        self.append_event(EVENT_GUARD, **payload)


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def arg_max_chars() -> int:
    raw = __import__("os").environ.get("EVOLVE_LOG_ARG_MAX_CHARS", str(_DEFAULT_ARG_MAX_CHARS))
    try:
        value = int(raw)
    except ValueError:
        value = _DEFAULT_ARG_MAX_CHARS
    return max(32, value)


def sanitize_log_value(value: Any, *, _depth: int = 0) -> Any:
    """Redact secrets and truncate large strings (TOOLS.md §10)."""
    if _depth > 8:
        return "…"
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key).lower()
            if any(marker in key_text for marker in _SENSITIVE_KEYS):
                out[str(key)] = "[redacted]"
            else:
                out[str(key)] = sanitize_log_value(item, _depth=_depth + 1)
        return out
    if isinstance(value, list):
        return [sanitize_log_value(item, _depth=_depth + 1) for item in value]
    if isinstance(value, str):
        limit = arg_max_chars()
        if len(value) <= limit:
            return value
        return value[:limit] + f"…(+{len(value) - limit} chars)"
    return value


def read_events(path: Path, *, limit: int | None = None) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if limit is not None:
        lines = lines[-limit:]
    events: list[dict[str, Any]] = []
    for line in lines:
        payload = json.loads(line)
        if isinstance(payload, dict):
            events.append(payload)
    return events


def conversation_id_from_session(session_dir: Path | None) -> str | None:
    if session_dir is None:
        return None
    name = session_dir.name.strip()
    return name or None


def _demo() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        log_path = Path(tmp) / EVOLVE_LOG_FILENAME
        log = EvolveLog(log_path)

        log.log_tool_call(
            tool="grep",
            arguments={"pattern": "foo", "path": "workspace"},
            result=tool_ok("grep", {"matches": []}, duration_ms=3),
            conversation_id="demo",
            confirm="skipped",
        )
        log.log_session_workspace_approved(conversation_id="demo", tool_name="write_text")
        log.log_session_start(
            conversation_id="demo",
            memory_ids_loaded=["mem-a"],
            topics_available=["coding", "workflow"],
        )
        log.log_topics_confirmed(
            conversation_id="demo",
            topics_confirmed=["coding"],
            prompt_files_loaded=["prompts/coding.md"],
            evolved_tools_listed=["write_text"],
        )
        log.log_session_end(conversation_id="demo", record_mode="off")
        log.log_entity_used(
            entity_id="downloads-sort",
            entity_type="memory",
            level="L2",
            reason="read_file:memories/workflow/downloads-sort.md",
            conversation_id="demo",
        )
        log.log_subagent_run(
            kind="explore",
            tool_rounds=3,
            truncated=False,
            paths_cited=["docs/MAP.md"],
            conversation_id="demo",
        )

        events = read_events(log_path)
        assert len(events) == 7
        assert events[0]["event"] == EVENT_TOOL_CALL
        assert events[0]["tool"] == "grep"
        assert events[0]["ok"] is True
        assert events[1]["event"] == EVENT_SESSION_WORKSPACE_APPROVED
        assert events[1]["tool_name"] == "write_text"
        assert events[2]["event"] == EVENT_SESSION_START
        assert events[2]["memory_ids_loaded"] == ["mem-a"]
        assert events[3]["event"] == EVENT_TOPICS_CONFIRMED
        assert events[3]["topics_confirmed"] == ["coding"]
        assert events[4]["event"] == EVENT_SESSION_END
        assert events[5]["event"] == EVENT_ENTITY_USED
        assert events[5]["entity_id"] == "downloads-sort"
        assert events[5]["level"] == "L2"
        assert events[6]["event"] == EVENT_SUBAGENT_RUN
        assert events[6]["kind"] == "explore"
        print(f"[PASS] wrote {len(events)} evolve_log line(s)")
        print("[PASS] T-706: log_subagent_run appends subagent_run event")
        print("[PASS] T-602a: log_entity_used appends entity_used event")

        long_args = {"content": "x" * 800}
        sanitized = sanitize_log_value(long_args)
        assert isinstance(sanitized["content"], str)
        assert "…(+300 chars)" in sanitized["content"]
        print("[PASS] sanitize_log_value truncates long strings")

        secret_args = {"api_key": "sekret", "query": "ok"}
        redacted = sanitize_log_value(secret_args)
        assert redacted["api_key"] == "[redacted]"
        assert redacted["query"] == "ok"
        print("[PASS] sanitize_log_value redacts sensitive keys")

        secret_tool = "sk-demo-must-not-hit-disk"
        log.log_tool_call(
            tool="http_get",
            arguments={"url": "https://example.com", "api_key": secret_tool},
            result=tool_ok("http_get", {"status": 200}, duration_ms=1),
            conversation_id="demo",
        )
        disk_text = log_path.read_text(encoding="utf-8")
        assert secret_tool not in disk_text
        tool_events = [event for event in read_events(log_path) if event.get("event") == EVENT_TOOL_CALL]
        assert tool_events[-1]["arguments"]["api_key"] == "[redacted]"
        print("[PASS] IT-60: evolve_log tool_call redacts api_key on disk")


if __name__ == "__main__":
    _demo()
