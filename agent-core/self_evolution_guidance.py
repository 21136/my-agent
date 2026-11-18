"""Bounded, process-local delivery primitive for untrusted worker guidance.

Accepted messages are not durable. A process restart loses this inbox; callers
must not report pending or in-delivery messages as delivered after restart.
"""
from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass
from typing import Literal

GuidanceIdentity = tuple[str, str, str]
GuidanceStatus = Literal[
    "received_pending_delivery", "in_delivery", "delivered", "delivery_unknown",
    "delivery_failed", "discarded",
]


@dataclass(frozen=True, slots=True)
class GuidanceReceipt:
    status: str
    message_id: str
    delivery_status: str = "not_delivered"


@dataclass(frozen=True, slots=True)
class GuidanceMessage:
    message_id: str
    text: str
    status: GuidanceStatus


@dataclass(frozen=True, slots=True)
class GuidanceBatch:
    token: str
    items: tuple[GuidanceMessage, ...]


@dataclass
class _Entry:
    message_id: str
    text: str
    status: GuidanceStatus = "received_pending_delivery"


class GuidanceInbox:
    """Single-attempt bounded FIFO. It never authorizes tools or persists data."""

    def __init__(self, identity: GuidanceIdentity, *, max_messages: int = 16,
                 max_chars: int = 32_000, max_message_chars: int = 8_000) -> None:
        if (type(identity) is not tuple or len(identity) != 3
                or any(type(value) is not str or not value.strip() for value in identity)):
            raise ValueError("valid session/task/attempt identity required")
        if (type(max_messages) is not int or not 1 <= max_messages <= 64
                or type(max_chars) is not int or not 1 <= max_chars <= 262_144
                or type(max_message_chars) is not int or not 1 <= max_message_chars <= max_chars):
            raise ValueError("invalid guidance inbox bounds")
        self._identity = identity
        self._max_messages = max_messages
        self._max_chars = max_chars
        self._max_message_chars = max_message_chars
        self._entries: list[_Entry] = []
        self._claimed_token: str | None = None
        self._closed_reason: str | None = None
        self._lock = threading.RLock()

    def receive(self, identity: GuidanceIdentity, message_id: str, text: str) -> GuidanceReceipt:
        with self._lock:
            if identity != self._identity:
                return GuidanceReceipt("denied", message_id if type(message_id) is str else "")
            if self._closed_reason is not None:
                return GuidanceReceipt("closed", message_id if type(message_id) is str else "")
            if (type(message_id) is not str or not message_id.strip() or len(message_id) > 256
                    or type(text) is not str or not text.strip() or len(text) > self._max_message_chars):
                return GuidanceReceipt("invalid", message_id if type(message_id) is str else "")
            existing = next((entry for entry in self._entries if entry.message_id == message_id), None)
            if existing is not None:
                return GuidanceReceipt("duplicate", message_id, existing.status)
            if (len(self._entries) >= self._max_messages
                    or sum(len(entry.text) for entry in self._entries) + len(text) > self._max_chars):
                return GuidanceReceipt("capacity_exceeded", message_id)
            self._entries.append(_Entry(message_id, text))
            return GuidanceReceipt("received_pending_delivery", message_id, "pending")

    def claim(self, identity: GuidanceIdentity) -> GuidanceBatch | None:
        with self._lock:
            if identity != self._identity or self._closed_reason is not None or self._claimed_token is not None:
                return None
            pending = [entry for entry in self._entries if entry.status == "received_pending_delivery"]
            if not pending:
                return None
            token = uuid.uuid4().hex
            self._claimed_token = token
            for entry in pending:
                entry.status = "in_delivery"
            return GuidanceBatch(token, tuple(GuidanceMessage(entry.message_id, entry.text, entry.status)
                                              for entry in pending))

    def resolve(self, token: str, outcome: Literal["delivered", "unknown", "failed"]) -> bool:
        with self._lock:
            if type(token) is not str or token != self._claimed_token or outcome not in {"delivered", "unknown", "failed"}:
                return False
            status: GuidanceStatus = {"delivered": "delivered", "unknown": "delivery_unknown",
                                      "failed": "delivery_failed"}[outcome]
            for entry in self._entries:
                if entry.status == "in_delivery":
                    entry.status = status
                    entry.text = ""
            self._claimed_token = None
            return True

    def close(self, reason: str) -> None:
        with self._lock:
            if self._closed_reason is not None:
                return
            self._closed_reason = reason if type(reason) is str and reason else "closed"
            for entry in self._entries:
                if entry.status == "received_pending_delivery":
                    entry.status = "discarded"
                elif entry.status == "in_delivery":
                    entry.status = "delivery_unknown"
                entry.text = ""
            self._claimed_token = None

    @property
    def pending_count(self) -> int:
        with self._lock:
            return sum(entry.status == "received_pending_delivery" for entry in self._entries)

    def snapshot(self) -> tuple[GuidanceMessage, ...]:
        with self._lock:
            return tuple(GuidanceMessage(entry.message_id, entry.text, entry.status) for entry in self._entries)
