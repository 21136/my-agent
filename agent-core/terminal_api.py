"""WebSocket read-only adapter for persistent interactive terminal sessions."""

from __future__ import annotations

import importlib.util
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

_AGENT_CORE = Path(__file__).resolve().parent
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from paths import AgentPaths


class TerminalApiError(ValueError):
    """Raised for invalid terminal.* client messages or tool responses."""


ConfirmFn = Callable[[str, bool], str]


def _load_interactive_terminal(paths: AgentPaths):
    main_py = paths.evolve / "tools" / "common" / "interactive_terminal" / "main.py"
    if not main_py.is_file():
        raise TerminalApiError("interactive_terminal tool not found")
    spec = importlib.util.spec_from_file_location("interactive_terminal_terminal_api", main_py)
    if spec is None or spec.loader is None:
        raise TerminalApiError("failed to load interactive_terminal")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_STALE_ACTIVE_SEC = 3600


def _int_or_none(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_iso_timestamp(value: Any) -> float | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except (TypeError, ValueError, OSError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _active_heartbeat_is_stale(
    last_activity_at: Any,
    created_at: Any = "",
    *,
    now: float | None = None,
) -> bool:
    now_ts = time.time() if now is None else now
    stamp = _parse_iso_timestamp(last_activity_at or created_at)
    if stamp is None:
        return False
    return (now_ts - stamp) >= _STALE_ACTIVE_SEC


def _snapshot(raw: Any, *, now: float | None = None) -> dict[str, Any] | None:
    """Flatten the evolved tool's nested result without exposing process paths."""
    if not isinstance(raw, dict):
        return None
    state = raw.get("state")
    if not isinstance(state, dict):
        state = raw
    session_id = raw.get("session_id") or state.get("session_id")
    if not isinstance(session_id, str) or not session_id.strip():
        return None
    state_name = state.get("state")
    if not isinstance(state_name, str) or not state_name.strip():
        state_name = "unknown"
    state_name = state_name.strip()
    reason = state.get("reason") if isinstance(state.get("reason"), str) else None
    if state_name in {"starting", "running"}:
        stale = _active_heartbeat_is_stale(
            state.get("last_activity_at"),
            state.get("created_at"),
            now=now,
        )
        if stale or not bool(state.get("alive")):
            state_name = "lost"
            if not reason:
                reason = (
                    "session heartbeat is stale; worker is no longer trusted"
                    if stale
                    else "worker process is no longer alive"
                )
    return {
        "session_id": session_id.strip(),
        "command": str(state.get("command") or ""),
        "cwd": str(state.get("cwd") or "."),
        "state": state_name,
        "alive": bool(state.get("alive")) and state_name in {"starting", "running"},
        "exit_code": _int_or_none(state.get("exit_code")),
        "signal": state.get("signal") if isinstance(state.get("signal"), str) else None,
        "reason": reason,
        "created_at": str(state.get("created_at") or ""),
        "last_activity_at": str(state.get("last_activity_at") or ""),
        "output_bytes": _int_or_none(state.get("output_bytes")) or 0,
        "output_start": _int_or_none(state.get("output_start")) or 0,
    }


def _request_id(message: dict[str, Any]) -> str | None:
    value = message.get("request_id")
    return value.strip() if isinstance(value, str) and value.strip() else None


def _event(event_type: str, message: dict[str, Any], **payload: Any) -> dict[str, Any]:
    result: dict[str, Any] = {"type": event_type, **payload}
    request_id = _request_id(message)
    if request_id is not None:
        result["request_id"] = request_id
    return result


def _close_preview(session: dict[str, Any], *, force: bool) -> str:
    lines = [
        "关闭交互终端",
        f"命令：{session.get('command') or '未记录'}",
        f"工作目录：{session.get('cwd') or '.'}",
        f"会话 ID：{session.get('session_id') or '未知'}",
    ]
    if force:
        lines.append("方式：强制关闭并结束子进程树")
    else:
        lines.append("方式：请求终端正常关闭")
    return "\n".join(lines)


def _terminal_output(module: Any, message: dict[str, Any]) -> dict[str, Any]:
    session_id = message.get("session_id")
    if not isinstance(session_id, str) or not session_id.strip():
        raise TerminalApiError("terminal.output requires session_id")
    payload: dict[str, Any] = {
        "action": "read",
        "session_id": session_id.strip(),
    }
    for key in ("cursor", "max_chars"):
        if key in message:
            payload[key] = message[key]
    result = module.interactive_terminal(payload)
    if not isinstance(result, dict):
        return _event(
            "terminal.output.done",
            message,
            ok=False,
            session_id=session_id.strip(),
            output="",
            cursor=0,
            next_cursor=0,
            cursor_reset=False,
            truncated=False,
            error="terminal output returned an invalid response",
        )
    response: dict[str, Any] = {
        "ok": bool(result.get("ok")),
        "session_id": session_id.strip(),
        "output": result.get("output") if isinstance(result.get("output"), str) else "",
        "cursor": _int_or_none(result.get("cursor")) or 0,
        "next_cursor": _int_or_none(result.get("next_cursor")) or 0,
        "cursor_reset": bool(result.get("cursor_reset")),
        "truncated": bool(result.get("truncated")),
    }
    snapshot = _snapshot(result)
    if snapshot is not None:
        response["session"] = snapshot
    if not response["ok"]:
        response["error"] = str(result.get("error") or "terminal output unavailable")
    return _event("terminal.output.done", message, **response)


def _terminal_close(
    module: Any,
    message: dict[str, Any],
    *,
    confirm_fn: ConfirmFn | None,
) -> dict[str, Any]:
    session_id = message.get("session_id")
    if not isinstance(session_id, str) or not session_id.strip():
        raise TerminalApiError("terminal.close requires session_id")
    normalized_id = session_id.strip()
    raw_force = message.get("force", False)
    if not isinstance(raw_force, bool):
        raise TerminalApiError("terminal.close force must be boolean")
    force = raw_force
    status = module.interactive_terminal({"action": "status", "session_id": normalized_id})
    if not isinstance(status, dict) or not status.get("ok"):
        error = str(status.get("error") if isinstance(status, dict) else "terminal status unavailable")
        return _event(
            "terminal.close.done",
            message,
            ok=False,
            session_id=normalized_id,
            error=error,
        )
    session = _snapshot(status)
    if session is None:
        return _event(
            "terminal.close.done",
            message,
            ok=False,
            session_id=normalized_id,
            error="terminal status is invalid",
        )
    if session["state"] not in {"starting", "running"}:
        return _event(
            "terminal.close.done",
            message,
            ok=False,
            session_id=normalized_id,
            session=session,
            error=f"session is not active: {session['state']}",
        )
    if confirm_fn is None:
        raise TerminalApiError("terminal.close requires the confirmation pipeline")
    choice = str(confirm_fn(_close_preview(session, force=force), False)).strip().lower()
    if choice not in {"y", "yes"}:
        return _event(
            "terminal.close.done",
            message,
            ok=False,
            session_id=normalized_id,
            session=session,
            error="terminal close rejected by user",
        )
    result = module.interactive_terminal(
        {
            "action": "close",
            "session_id": normalized_id,
            "force": force,
        }
    )
    if not isinstance(result, dict):
        return _event(
            "terminal.close.done",
            message,
            ok=False,
            session_id=normalized_id,
            error="terminal close returned an invalid response",
        )
    response: dict[str, Any] = {
        "ok": bool(result.get("ok")),
        "session_id": normalized_id,
        "state": result.get("state") if isinstance(result.get("state"), str) else "closing",
    }
    if not response["ok"]:
        response["error"] = str(result.get("error") or "terminal close failed")
    return _event("terminal.close.done", message, **response)


def dispatch_terminal_message(
    paths: AgentPaths,
    message: dict[str, Any],
    *,
    confirm_fn: ConfirmFn | None = None,
) -> dict[str, Any]:
    """Handle terminal UI requests and return stable Desktop event payloads."""
    message_type = message.get("type")
    if message_type not in {"terminal.list", "terminal.output", "terminal.close"}:
        raise TerminalApiError(f"unknown terminal message: {message.get('type')}")

    module = _load_interactive_terminal(paths)
    if message_type == "terminal.output":
        return _terminal_output(module, message)
    if message_type == "terminal.close":
        return _terminal_close(module, message, confirm_fn=confirm_fn)

    result = module.interactive_terminal({"action": "list"})
    if not isinstance(result, dict) or not result.get("ok"):
        raise TerminalApiError(
            str(result.get("error") if isinstance(result, dict) else "terminal.list failed")
        )

    raw_sessions = result.get("sessions")
    if not isinstance(raw_sessions, list):
        raw_sessions = []
    sessions = [item for raw in raw_sessions if (item := _snapshot(raw)) is not None]
    return _event("terminal.list.done", message, ok=True, sessions=sessions)
