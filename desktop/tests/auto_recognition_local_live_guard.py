"""Pure exact-envelope checks shared by the opt-in fixture and offline unit test."""
from __future__ import annotations

import json
import pathlib
from typing import Any


def fixture_root_is_safe(root: Any, temp_root: Any) -> bool:
    """Accept only a synthetic-auto-live-* direct child of the OS temp root."""
    try:
        root_path = pathlib.Path(root).resolve()
        temp_path = pathlib.Path(temp_root).resolve()
        return root_path.parent == temp_path and root_path.name.startswith("synthetic-auto-live-")
    except (TypeError, ValueError, OSError):
        return False


def live_provider_requested(arguments: list[str]) -> bool:
    """Keep local fixture startup offline unless its caller opts in explicitly."""
    if arguments == []:
        return False
    if arguments == ["--allow-real-provider"]:
        return True
    raise ValueError("invalid fixture mode argument")


def expected_safe_messages(intent: Any, expected_user_text: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Generate source messages via the real intent builder, then mirror its sender's privacy projection."""
    source_messages = intent._messages(expected_user_text, [])
    if type(source_messages) is not list or len(source_messages) != 2:
        raise ValueError("unexpected intent message shape")
    if source_messages[0].get("role") != "system" or source_messages[1].get("role") != "user":
        raise ValueError("unexpected intent message roles")
    source_payload = json.loads(source_messages[1].get("content", "{}"))
    if (type(source_payload) is not dict
            or source_payload.get("text") != expected_user_text
            or source_payload.get("recognition_view") != []
            or source_payload.get("current_project_available") is not False):
        raise ValueError("unexpected _messages payload shape")
    # _AutoRecognitionClient intentionally excludes current_project_available from provider data.
    safe_payload = {
        "text": source_payload["text"],
        "recognition_view": source_payload["recognition_view"],
    }
    safe_messages = [
        source_messages[0],
        {"role": "user", "content": json.dumps(safe_payload, ensure_ascii=False, separators=(",", ":"))},
    ]
    return source_messages, safe_messages


def confirmed_preview(entry: Any, session_id: str, messages: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "model_id": entry.id,
        "vendor": entry.vendor,
        "endpoint": entry.chat_completions_url(),
        "session_id": session_id,
        "provider_payload": {
            "model": entry.provider_model,
            "messages": messages,
            "temperature": 0.0,
            "stream": False,
            "response_format": {"type": "json_object"},
        },
    }


def confirmed_request_matches(
    messages: Any, *, model_id: str, session_id: str, expected_preview_json: str,
    entry: Any, intent: Any, expected_user_text: str,
) -> bool:
    try:
        _source_messages, safe_messages = expected_safe_messages(intent, expected_user_text)
        if type(messages) is not list or messages != safe_messages or model_id != entry.id:
            return False
        if type(session_id) is not str or session_id != "synthetic-auto-live-session":
            return False
        preview = json.loads(expected_preview_json)
        expected = confirmed_preview(entry, session_id, safe_messages)
        canonical = json.dumps(expected, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        return preview == expected and expected_preview_json == canonical
    except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
        return False
