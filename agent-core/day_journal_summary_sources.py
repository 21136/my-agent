"""Evidence-backed, local-only source selection for day-journal summaries.

Only explicitly addressed, dated project work-record requirement events are eligible.
Chat messages and DayJournal entries are intentionally not source material.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from session import Session
from work_records import WorkRecordStore, _digest

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_MAX_TEXT = 4000


@dataclass(frozen=True)
class DayJournalSourceSnapshot:
    status: str
    day: str
    project_id: str
    session_id: str
    item_id: str
    event_id: str
    _sources_json: str
    fingerprint: str

    @property
    def sources(self) -> tuple[dict[str, str], ...]:
        """Return detached copies so callers cannot mutate the bound snapshot."""
        return tuple(json.loads(self._sources_json))

    @property
    def metadata(self) -> dict[str, str]:
        """Non-content metadata suitable for logs/previews."""
        return {"status": self.status, "day": self.day, "project_id": self.project_id,
                "session_id": self.session_id, "item_id": self.item_id,
                "event_id": self.event_id, "fingerprint": self.fingerprint}


class DayJournalSummarySourceSelector:
    """Select/revalidate one exact dated requirement event in a bound Session.

    Caller supplies stable item/event IDs; this does not enumerate sessions or
    messages. ``WorkRecordStore.get_item`` reads its size-bounded current-session
    sidecar. The event's persisted ``at`` means "recorded at",
    not original work time. Eligible wording only asserts that a requirement
    was recorded, never that it was completed or verified.
    """

    def __init__(self, store: WorkRecordStore, session: Session, *, timezone_name: str = "Asia/Shanghai") -> None:
        if not isinstance(session, Session) or not isinstance(store, WorkRecordStore):
            raise TypeError("a real Session and its WorkRecordStore are required")
        if store._session is not session:
            raise ValueError("work-record store must be bound to this exact Session")
        self._store = store
        self._session = session
        try:
            self._zone = ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError:
            if timezone_name != "Asia/Shanghai":
                raise ValueError(f"unknown timezone: {timezone_name!r}")
            from datetime import timedelta
            self._zone = timezone(timedelta(hours=8), "Asia/Shanghai")

    def select(self, *, day: str, project_id: str, item_id: str, event_id: str) -> DayJournalSourceSnapshot:
        day_key = self._day(day)
        binding = {"day": day_key, "project_id": str(project_id),
                   "session_id": self._session.conversation_id,
                   "item_id": str(item_id), "event_id": str(event_id)}
        if not self._valid_binding(project_id, item_id, event_id):
            return self._insufficient(binding)
        try:
            item = self._store.get_item(item_id)
            evidence = self._resolve(item, binding)
        except Exception:
            evidence = None
        if evidence is None:
            return self._insufficient(binding)
        source = {"source_id": f"work-record:{item_id}:{event_id}", "text": evidence["text"]}
        digest_data = {"binding": binding, "source": source, "record_digest": evidence["record_digest"]}
        return self._snapshot("ready", binding, (source,), self._fingerprint(digest_data))

    def validate_items(self, snapshot: DayJournalSourceSnapshot, items: list[dict[str, Any]]) -> bool:
        """Validate against an already locked, detached full-store snapshot."""
        if not isinstance(snapshot, DayJournalSourceSnapshot) or snapshot.status != "ready":
            return False
        if not isinstance(items, list):
            return False
        try:
            item = next((row for row in items if isinstance(row, dict) and row.get("item_id") == snapshot.item_id), None)
            binding = {"day": snapshot.day, "project_id": snapshot.project_id,
                       "session_id": snapshot.session_id, "item_id": snapshot.item_id,
                       "event_id": snapshot.event_id}
            evidence = self._resolve(item, binding)
            if evidence is None:
                return False
            source = {"source_id": f"work-record:{snapshot.item_id}:{snapshot.event_id}", "text": evidence["text"]}
            digest_data = {"binding": binding, "source": source, "record_digest": evidence["record_digest"]}
            current = self._snapshot("ready", binding, (source,), self._fingerprint(digest_data))
            return current == snapshot
        except Exception:
            return False

    def validate(self, snapshot: DayJournalSourceSnapshot) -> bool:
        """Re-read exact persisted evidence; do not accept a caller's bool/claim."""
        if not isinstance(snapshot, DayJournalSourceSnapshot):
            return False
        try:
            current = self.select(day=snapshot.day, project_id=snapshot.project_id,
                                  item_id=snapshot.item_id, event_id=snapshot.event_id)
        except Exception:
            return False
        return current.status == "ready" and snapshot.status == "ready" and current == snapshot

    def _resolve(self, item: Any, binding: dict[str, str]) -> dict[str, str] | None:
        if (not isinstance(item, dict) or item.get("item_id") != binding["item_id"]
                or item.get("scope") != {"kind": "project", "project_id": binding["project_id"]}):
            return None
        events = [event for event in item.get("history", []) if isinstance(event, dict)
                  and event.get("event_id") == binding["event_id"]
                  and event.get("kind") == "requirement_added"]
        if len(events) != 1:
            return None
        event = events[0]
        payload = event.get("payload")
        if not isinstance(payload, dict) or not isinstance(payload.get("requirement_id"), str):
            return None
        requirements = [req for req in item.get("requirements", []) if isinstance(req, dict)
                        and req.get("requirement_id") == payload["requirement_id"]]
        if len(requirements) != 1:
            return None
        requirement = requirements[0]
        text = requirement.get("text")
        if not isinstance(text, str) or not text.strip() or len(text) > _MAX_TEXT or payload.get("text") != text:
            return None
        expected_digest = _digest({"kind": "requirement_added", "text": text, "source_ref": requirement.get("source_ref")})
        if event.get("digest") != expected_digest:
            return None
        at = event.get("at")
        if isinstance(at, bool) or not isinstance(at, (int, float)):
            return None
        try:
            recorded_day = datetime.fromtimestamp(at, timezone.utc).astimezone(self._zone).date().isoformat()
        except (OverflowError, OSError, ValueError):
            return None
        if recorded_day != binding["day"]:
            return None
        source_text = f"记录了需求（记录时间，不代表原始发生时间、完成或验证）：{text}"
        return {"text": source_text, "record_digest": _digest({"item": item, "event": event})}

    def _valid_binding(self, project_id: Any, item_id: Any, event_id: Any) -> bool:
        session_id = self._session.conversation_id
        actual_project = getattr(self._session.meta, "project_id", "")
        return (all(isinstance(value, str) and _ID.fullmatch(value)
                    for value in (project_id, item_id, event_id, session_id))
                and bool(actual_project) and project_id == actual_project)

    @staticmethod
    def _day(value: str) -> str:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError("day must be ISO YYYY-MM-DD")
        return value

    @staticmethod
    def _fingerprint(value: Any) -> str:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _insufficient(self, binding: dict[str, str]) -> DayJournalSourceSnapshot:
        return self._snapshot("insufficient", binding, (), self._fingerprint({"status": "insufficient", **binding}))

    @staticmethod
    def _snapshot(status: str, binding: dict[str, str], sources: tuple[dict[str, str], ...], fingerprint: str) -> DayJournalSourceSnapshot:
        encoded = json.dumps(sources, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return DayJournalSourceSnapshot(status=status, **binding, _sources_json=encoded, fingerprint=fingerprint)





