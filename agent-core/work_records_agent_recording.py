from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Callable

from work_records import WorkRecordConflict, WorkRecordStore


@dataclass(frozen=True)
class AgentRecordingOutcome:
    turn: Any | None = None
    reply: str | None = None
    item_id: str | None = None
    storage_error: str | None = None


class WorkRecordAgentRecording:
    """Opt-in, single-session recorder around one ordinary Agent turn.

    Recognition is an injected dependency; this module intentionally ships no
    natural-language model or keyword heuristic. It never retries ``execute``.
    Event preflight blocks visible same-session replays, not crash- or
    multi-process races; it does not promise exactly-once execution.
    """

    def __init__(
        self, *, agent: Any, store: WorkRecordStore,
        recognizer: Callable[..., dict[str, Any]],
        idempotency_key_provider: Callable[[str], str | None] | None = None,
    ):
        if not isinstance(store, WorkRecordStore):
            raise TypeError("recording requires a WorkRecordStore")
        if getattr(store, "_session", None) is not getattr(agent, "session", None):
            raise ValueError("Agent and WorkRecordStore must share the exact Session object")
        if not callable(getattr(agent, "run_turn", None)) or not callable(recognizer):
            raise TypeError("agent.run_turn and recognizer are required")
        self._agent = agent
        self._store = store
        self._recognizer = recognizer
        self._idempotency_key_provider = idempotency_key_provider

    def run_turn(self, user_text: str, *, execute: Callable[[], Any]) -> AgentRecordingOutcome:
        try:
            items = self._store.list_items()
            recognition_view = [
                {"item_id": item.get("item_id"), "title": item.get("title", ""), "scope": item.get("scope", {})}
                for item in items
            ]
            decision = self._recognizer(user_text, recognition_view)
        except Exception as exc:
            return self._uncertain(type(exc).__name__)
        if not isinstance(decision, dict):
            return self._uncertain("invalid_decision")
        action = decision.get("action")
        if action == "none" or action == "recall":
            return self._execute_once(execute)
        if action != "record":
            return self._uncertain("uncertain")

        title, requirement_text, scope = decision.get("title"), decision.get("requirement"), decision.get("scope")
        requested_item_id = decision.get("item_id")
        # Luna's strict schema emits item_id=null for new items; treat it like omission.
        has_item_id = requested_item_id is not None
        if (not isinstance(requirement_text, str) or not requirement_text.strip()
                or not self._valid_scope(scope)
                or (not has_item_id and (not isinstance(title, str) or not title.strip()))):
            return self._uncertain("invalid_record")

        if has_item_id:
            # An explicit target is a trust boundary: never turn a stale, foreign,
            # missing, or scope-mismatched ID into a new item or an Agent execution.
            if not isinstance(requested_item_id, str) or not requested_item_id.strip():
                return self._uncertain("invalid_item_id")
            try:
                existing = self._store.get_item(requested_item_id)
            except Exception as exc:
                return self._uncertain(type(exc).__name__)
            if (not isinstance(existing, dict)
                    or existing.get("item_id") != requested_item_id
                    or not self._scope_matches(existing.get("scope"), scope)):
                return self._uncertain("untrusted_item_id")
            item_id = requested_item_id
        else:
            item_id = None

        supplied_event_id = decision.get("event_id")
        if supplied_event_id is not None and (not isinstance(supplied_event_id, str) or not supplied_event_id.strip()):
            return self._uncertain("invalid_event_id")
        if supplied_event_id is not None and not has_item_id:
            return self._event_review(replay=False)
        try:
            request_key = (
                self._idempotency_key_provider(user_text)
                if self._idempotency_key_provider is not None else None
            )
        except Exception:
            request_key = None
        if not isinstance(request_key, str) or not request_key.strip():
            try:
                turn = execute()
            except Exception:
                raise
            return AgentRecordingOutcome(
                turn=turn,
                storage_error="WorkRecordIdempotencyRequired",
            )
        request_key = request_key.strip()
        event_id = supplied_event_id or request_key
        event_state = self._event_state(items, event_id, requirement_text)
        if event_state == "replay":
            return self._event_review(replay=True)
        if event_state == "conflict":
            return self._event_review(replay=False)
        requirement_id = None
        storage_error = None
        try:
            if item_id is None:
                item = self._store.create_item_with_requirement(
                    title=title,
                    scope=scope,
                    requirement=requirement_text,
                    event_id=event_id,
                    idempotency_key=request_key,
                )
                item_id = item["item_id"]
                requirement_id = item["requirements"][0]["requirement_id"]
            else:
                requirement = self._store.add_requirement(
                    item_id,
                    requirement_text,
                    event_id=event_id,
                    idempotency_key=request_key,
                )
                requirement_id = requirement["requirement_id"]
        except WorkRecordConflict:
            return self._uncertain("event_conflict")
        except Exception as exc:
            storage_error = type(exc).__name__

        experimental_turn_id = "experimental-turn:" + uuid.uuid4().hex
        try:
            turn = execute()
        except Exception:
            if item_id is not None and requirement_id is not None:
                self._record_unknown(item_id, requirement_id, experimental_turn_id)
            raise
        if item_id is not None and requirement_id is not None:
            storage_error = self._record_unknown(item_id, requirement_id, experimental_turn_id) or storage_error
        return AgentRecordingOutcome(turn=turn, item_id=item_id, storage_error=storage_error)

    @staticmethod
    def _event_state(items: list[dict[str, Any]], event_id: str, requirement_text: str) -> str | None:
        """Return replay/conflict for an event already visible in this session."""
        for item in items:
            history = item.get("history")
            if not isinstance(history, list):
                return "conflict"
            for event in history:
                if not isinstance(event, dict):
                    return "conflict"
                if event.get("event_id") != event_id:
                    continue
                payload = event.get("payload")
                if (event.get("kind") == "requirement_added"
                        and isinstance(payload, dict)
                        and payload.get("text") == requirement_text.strip()):
                    return "replay"
                return "conflict"
        return None

    @staticmethod
    def _event_review(*, replay: bool) -> AgentRecordingOutcome:
        if replay:
            reply = "\u8be5\u4e8b\u4ef6 ID \u5df2\u8bb0\u5f55\u8fc7\u76f8\u540c\u8981\u6c42\uff1b\u672c\u6b21\u672a\u91cd\u590d\u6dfb\u52a0\u8981\u6c42\uff0c\u4e5f\u672a\u518d\u6b21\u6267\u884c\u3002\u8bf7\u5148\u6838\u5bf9\u5df2\u6709\u6267\u884c\u4e0e\u9a8c\u8bc1\u7ed3\u679c\u3002"
        else:
            reply = "\u4e8b\u4ef6 ID \u7f3a\u5c11\u53ef\u4fe1\u7684\u5df2\u6709\u4e8b\u9879\u7ed1\u5b9a\uff0c\u6216\u4e0e\u5148\u524d\u4e8b\u4ef6\u5185\u5bb9\u51b2\u7a81\uff1b\u672c\u6b21\u672a\u65b0\u5efa\u6216\u6267\u884c\u3002\u8bf7\u6838\u5bf9\u4e8b\u9879\u53ca\u4e8b\u4ef6 ID \u540e\u91cd\u8bd5\u3002"
        return AgentRecordingOutcome(reply=reply)

    @staticmethod
    def _scope_matches(existing_scope: Any, requested_scope: Any) -> bool:
        if requested_scope == {"kind": "independent"}:
            return existing_scope == {"kind": "independent", "object_ref": None}
        if (isinstance(requested_scope, dict) and requested_scope.get("kind") == "project"
                and isinstance(requested_scope.get("project_id"), str)):
            return existing_scope == {
                "kind": "project", "project_id": requested_scope["project_id"],
            }
        return False

    def _valid_scope(self, scope: Any) -> bool:
        if scope == {"kind": "independent"}:
            return True
        project_id = getattr(self._agent.session.meta, "project_id", "")
        return (
            isinstance(scope, dict) and scope.get("kind") == "project"
            and isinstance(project_id, str) and bool(project_id)
            and scope.get("project_id") == project_id
        )

    @staticmethod
    def _execute_once(execute: Callable[[], Any]) -> AgentRecordingOutcome:
        return AgentRecordingOutcome(turn=execute())

    @staticmethod
    def _uncertain(reason: str) -> AgentRecordingOutcome:
        return AgentRecordingOutcome(
            reply="\u6211\u65e0\u6cd5\u53ef\u9760\u5224\u65ad\u8fd9\u662f\u5426\u662f\u4e00\u9879\u660e\u786e\u5de5\u4f5c\u8981\u6c42\uff0c\u6216\u65e0\u6cd5\u786e\u8ba4\u5b83\u5c5e\u4e8e\u5f53\u524d\u9879\u76ee\u8fd8\u662f\u72ec\u7acb\u4e8b\u9879\u3002\u8bf7\u8865\u5145\u4e8b\u9879\u5185\u5bb9\u4e0e\u8303\u56f4\uff1b\u5c1a\u672a\u8c03\u7528 Agent\uff0c\u4e5f\u672a\u6267\u884c\u64cd\u4f5c。",
            storage_error=reason if reason not in {"uncertain", "invalid_record"} else None,
        )

    def _record_unknown(self, item_id: str, requirement_id: str, experimental_turn_id: str) -> str | None:
        try:
            self._store.record_result(
                item_id, requirement_id, run_id=experimental_turn_id, result_id=str(uuid.uuid4()),
                status="unknown",
                summary=f"{experimental_turn_id}; lifecycle_run_id=unavailable; Agent reply is not execution evidence.",
                verification={"status": "not_recorded", "summary": "未独立核验"},
            )
            return None
        except Exception as exc:
            return type(exc).__name__
