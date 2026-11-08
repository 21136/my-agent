"""Content-free timing events for the main user-message/model path."""
from __future__ import annotations

import hashlib
import logging
import uuid


_ALLOWED_PHASES = frozenset({
    "message_handler_entered",
    "turn_lock_acquired",
    "agent_turn_started",
    "user_message_persisted",
    "model_request_started",
    "model_first_delta",
    "model_request_finished",
})
_LOGGER = logging.getLogger("my_agent.sidecar")


def is_valid_user_request_id(message_type: object, request_id: object) -> bool:
    """True only for canonical user.message UUIDs eligible for timing logs."""
    if message_type != "user.message" or type(request_id) is not str:
        return False
    try:
        return str(uuid.UUID(request_id)) == request_id
    except (ValueError, AttributeError):
        return False


def log_main_request_timing(phase: str, request_id: str | None, duration_ms: int) -> None:
    """Log allowlisted stage/duration and a one-way request correlation tag only."""
    if phase not in _ALLOWED_PHASES or type(duration_ms) is not int or duration_ms < 0:
        return
    if type(request_id) is not str:
        return
    try:
        if str(uuid.UUID(request_id)) != request_id:
            return
        correlation = hashlib.sha256(request_id.encode("ascii")).hexdigest()[:12]
    except (ValueError, UnicodeEncodeError):
        return
    _LOGGER.info(
        "event=main_request_timing phase=%s correlation=%s duration_ms=%d",
        phase, correlation, duration_ms,
    )

