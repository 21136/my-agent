"""Desktop/session control messages not tied to project mode."""

from __future__ import annotations

from typing import Any

from paths import AgentPaths
from session import Session


class SessionApiError(Exception):
    """Invalid session control request."""


def dispatch_session_message(
    session: Session,
    paths: AgentPaths,
    message: dict[str, Any],
) -> dict[str, Any]:
    msg_type = message.get("type")
    if msg_type == "session.restart_today":
        from session_restart import SessionRestartError, perform_session_restart_today

        try:
            updated, events, _notice = perform_session_restart_today(paths, session, message)
        except SessionRestartError as exc:
            raise SessionApiError(str(exc)) from exc
        return {"_session": updated, "_events": events}

    raise SessionApiError(f"unknown session message type: {msg_type}")
