"""Durable, per-independent-item card publication state.

The work-record result and its pending intent are committed atomically by WorkRecordStore.
This module only advances presentation state; it never executes the captured task.
"""
from __future__ import annotations

from typing import Any

from work_records import WorkRecordStore


class IndependentWorkCardPublicationError(RuntimeError):
    """Independent card publication state cannot be safely confirmed."""


class IndependentWorkCardPublicationOutbox:
    def __init__(self, session, store: WorkRecordStore) -> None:
        if type(store) is not WorkRecordStore or store._session is not session:
            raise IndependentWorkCardPublicationError("outbox requires the exact bound WorkRecordStore")
        self._session = session
        self._store = store
        self._session_id = store._conversation_id

    def list_pending(self) -> list[dict[str, Any]]:
        try:
            return self._store.list_independent_publication_intents()
        except Exception as exc:
            raise IndependentWorkCardPublicationError("independent pending state cannot be confirmed") from exc

    def list_all(self) -> list[dict[str, Any]]:
        try:
            return self._store.list_independent_publication_intents(include_queued=True)
        except Exception as exc:
            raise IndependentWorkCardPublicationError("independent publication history cannot be confirmed") from exc

    def validate(self, intent: dict[str, Any]) -> bool:
        if (type(intent) is not dict or intent.get("session_id") != self._session_id
                or type(intent.get("latest_item_id")) is not str):
            return False
        try:
            return self._store.validate_independent_publication_intent(intent)
        except Exception:
            return False

    def remaining_seconds(self, intent: dict[str, Any]) -> float:
        if not self.validate(intent):
            raise IndependentWorkCardPublicationError("independent intent changed before deadline check")
        try:
            return self._store.independent_publication_intent_remaining_seconds(intent)
        except Exception as exc:
            raise IndependentWorkCardPublicationError("independent publication deadline cannot be confirmed") from exc

    def bind_card(self, *, intent_id: str, generation: int, card_id: str) -> str:
        try:
            return self._store.bind_independent_publication_card(
                intent_id=intent_id, generation=generation, card_id=card_id,
            )
        except Exception:
            return "uncertain"

    def mark_queued(self, *, intent_id: str, generation: int, card_id: str) -> str:
        try:
            return self._store.mark_independent_publication_queued(
                intent_id=intent_id, generation=generation, card_id=card_id,
            )
        except Exception:
            return "uncertain"
