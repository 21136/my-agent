"""Unwired host-only binding of a persisted user message to a requested reply target.

This issues process-local capabilities, not a viewed receipt or authority to access a
card. The caller is responsible for host authentication and a session lease; target
identity/currentness must still be verified atomically at the eventual viewed write.
Capabilities deliberately cannot be reconstructed from message metadata on restart.
"""
from __future__ import annotations

import copy
import re
import threading
import uuid
from typing import Any, Callable

from session import (HOST_INTERNAL_METADATA_KEY, Session, _exact_json_record,
                     _messages_unusable_for_ref, _session_messages_lock)

_TARGET_KEYS = frozenset({'session_id', 'presentation_id', 'card_id', 'result_id', 'requirement_id'})
_CARD = re.compile(r'^wc-[0-9a-f]{64}$')


def _target(value: Any, session_id: str) -> dict[str, str]:
    if type(value) is not dict or set(value) != _TARGET_KEYS:
        raise ValueError('exact reply target required')
    if any(type(value[key]) is not str or not value[key] or len(value[key]) > 256 for key in _TARGET_KEYS):
        raise ValueError('invalid reply target')
    if value['session_id'] != session_id or _CARD.fullmatch(value['card_id']) is None:
        raise ValueError('reply target is outside the bound session')
    try:
        if str(uuid.UUID(value['presentation_id'])) != value['presentation_id']:
            raise ValueError('noncanonical presentation')
    except (ValueError, AttributeError) as exc:
        raise ValueError('invalid presentation') from exc
    return dict(value)


class HostReplyBindingAuthority:
    """Internal host integration only; never hand the authority to client code."""

    def __init__(self, session: Session, current_session: Callable[[], object]):
        if type(session) is not Session or not callable(current_session):
            raise TypeError('exact session and host binding callback required')
        self._session = session
        self._current_session = current_session
        self._bindings: dict[object, tuple[str, int, dict[str, Any], dict[str, str], str]] = {}
        self._pending_existing: dict[object, tuple[int, str, dict[str, str]]] = {}
        self._persisted_positions: set[int] = set()
        self._lock = threading.Lock()

    def _bound(self) -> bool:
        try:
            return self._current_session() is self._session
        except Exception:
            return False

    def append_bound_user_message(self, text: str, target: dict[str, str]) -> object | None:
        """Append once and mint only after checking exact local/disk history.

        None means the write/binding was not confirmed, not that the write failed;
        never automatically replay an uncertain message append.
        """
        checked = _target(target, self._session.conversation_id)
        if type(text) is not str or not text.strip():
            raise ValueError('nonempty user message required')
        with self._lock:
            if not self._bound():
                return None
            message = {'role': 'user', 'content': text, HOST_INTERNAL_METADATA_KEY: {
                'version': 1, 'request_id': str(uuid.uuid4()), 'reply_target': checked,
            }}
            try:
                self._session.append_message(message)
                with _session_messages_lock(self._session.session_dir):
                    if (not self._bound() or self._session._messages_unpersisted
                            or self._session._messages_corrupt or _messages_unusable_for_ref(self._session)
                            or not self._session.messages
                            or not _exact_json_record(self._session.messages[-1], message)):
                        return None
                    receipt = object()
                    self._bindings[receipt] = ("appended", len(self._session.messages) - 1,
                                               copy.deepcopy(message), copy.deepcopy(checked), text)
                    return receipt
            except Exception:
                return None


    def prepare_user_message_binding(self, expected_text: str, target: dict[str, str]) -> object | None:
        """Mint a one-turn ingress ticket immediately before the existing append.

        The ticket captures the next message position and is process-local. The
        ordinary dialog path must prepare it after semantic matching, append the
        user text once, then call bind_persisted_user_message with that index.
        """
        checked = _target(target, self._session.conversation_id)
        if type(expected_text) is not str or not expected_text.strip() or len(expected_text.encode("utf-8")) > 24_000:
            raise ValueError("bounded exact user text required")
        try:
            self._session.ensure_messages_loaded()
        except Exception:
            return None
        with self._lock:
            if not self._bound() or self._pending_existing:
                return None
            try:
                with _session_messages_lock(self._session.session_dir):
                    self._session._initialize_messages_version_locked()
                    if (not self._bound() or self._session._messages_unpersisted
                            or self._session._messages_corrupt or _messages_unusable_for_ref(self._session)):
                        return None
                    ticket = object()
                    self._pending_existing[ticket] = (len(self._session.messages), expected_text, copy.deepcopy(checked))
                    return ticket
            except Exception:
                return None

    def bind_persisted_user_message(self, ingress_ticket: object, message_index: int) -> object | None:
        """Bind the exact newly appended user record without appending a duplicate.

        A ticket must be prepared before the ordinary dialog append. Only the exact
        next record position/text and a fully durable Session history can produce
        a capability; client-supplied metadata is rejected and never read as target.
        """
        if type(ingress_ticket) is not object or type(message_index) is not int or message_index < 0:
            return None
        try:
            self._session.ensure_messages_loaded()
        except Exception:
            return None
        with self._lock:
            pending = self._pending_existing.pop(ingress_ticket, None)
            if pending is None or not self._bound():
                return None
            expected_index, expected_text, target = pending
            if message_index != expected_index:
                return None
            try:
                with _session_messages_lock(self._session.session_dir):
                    self._session._initialize_messages_version_locked()
                    if (not self._bound() or self._session._messages_unpersisted
                            or self._session._messages_corrupt or _messages_unusable_for_ref(self._session)
                            or message_index != len(self._session.messages) - 1
                            or message_index >= len(self._session.messages)
                            or message_index in self._persisted_positions):
                        return None
                    message = self._session.messages[message_index]
                    if (type(message) is not dict or message.get("role") != "user"
                            or message.get("content") != expected_text
                            or HOST_INTERNAL_METADATA_KEY in message or "reply_target" in message):
                        return None
                    receipt = object()
                    self._bindings[receipt] = (
                        "persisted", message_index, copy.deepcopy(message), copy.deepcopy(target), expected_text,
                    )
                    self._persisted_positions.add(message_index)
                    return receipt
            except Exception:
                return None

    def consume_persisted_user_message_binding(self, receipt: object, *, expected_text: str) -> dict[str, str] | None:
        """Consume one exact existing-message capability; replay always fails closed."""
        if type(receipt) is not object or type(expected_text) is not str:
            return None
        with self._lock:
            bound = self._bindings.pop(receipt, None)
            if bound is None:
                return None
            kind, index, message, target, saved_text = bound
            if kind != "persisted" or saved_text != expected_text or not self._bound():
                return None
            try:
                with _session_messages_lock(self._session.session_dir):
                    if (not self._bound() or self._session._messages_unpersisted
                            or self._session._messages_corrupt or _messages_unusable_for_ref(self._session)
                            or index < 0 or index >= len(self._session.messages)):
                        return None
                    current = self._session.messages[index]
                    if (not _exact_json_record(current, message)
                            or type(current) is not dict or current.get("role") != "user"
                            or current.get("content") != expected_text
                            or HOST_INTERNAL_METADATA_KEY in current or "reply_target" in current):
                        return None
                    return copy.deepcopy(target)
            except Exception:
                return None

    def validate(self, receipt: object) -> dict[str, str] | None:
        """Verify a process-issued capability and its exact persisted message only."""
        if type(receipt) is not object:
            return None
        with self._lock:
            bound = self._bindings.get(receipt)
            if bound is None or not self._bound():
                return None
            kind, index, message, target, _text = bound
            if kind != "appended":
                return None
            try:
                with _session_messages_lock(self._session.session_dir):
                    if (not self._bound() or self._session._messages_unpersisted
                            or self._session._messages_corrupt or _messages_unusable_for_ref(self._session)
                            or index >= len(self._session.messages)
                            or not _exact_json_record(self._session.messages[index], message)):
                        return None
                    return copy.deepcopy(target)
            except Exception:
                return None
