from __future__ import annotations

import secrets
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))
_TESTS = Path(__file__).resolve().parent
if str(_TESTS) not in sys.path:
    sys.path.insert(0, str(_TESTS))

from isolation_helpers import temporary_agent_paths
from server import WsBridge, _AutoRecognitionClient
from session import create_new
from work_record_auto_recognition_preference import AutoRecognitionPreferenceStore
from work_records_luna_intent import MODEL_ID, _SYSTEM


@pytest.fixture
def paths():
    with temporary_agent_paths(prefix="auto-recognition-cross-bridge-") as isolated:
        yield isolated


def _new_session(paths, label: str):
    conversation_id = f"_auto_recognition_{label}_{secrets.token_hex(4)}"
    return create_new(paths, conversation_id=conversation_id)


def _revoke(bridge: WsBridge, session) -> dict[str, object]:
    import uuid

    return bridge.set_auto_recognition({
        "type": "work-record.auto-recognition.set",
        "session_id": session.conversation_id,
        "request_id": str(uuid.uuid4()),
        "action": "revoke",
    }, session)


def _messages(text: str) -> list[dict[str, str]]:
    import json

    return [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": json.dumps({
            "text": text,
            "recognition_view": [],
            "current_project_available": False,
        }, separators=(",", ":"))},
    ]


def _bridge_and_candidate(paths, session):
    bridge = WsBridge(emit=lambda _event: None, paths=paths)
    bridge._session_provider = lambda: session
    bridge.emit_session_state(session)
    candidate = SimpleNamespace(
        session=session,
        text="private synthetic current-session message",
        cancel_generation=bridge._post_turn_generation,
    )
    return bridge, candidate


def test_other_bridge_revoke_blocks_recognition_before_external_send(paths, monkeypatch):
    import server

    session = _new_session(paths, "before-send")
    first, candidate = _bridge_and_candidate(paths, session)
    second, _ = _bridge_and_candidate(paths, session)
    assert first._auto_recognition_session is session
    assert second._auto_recognition_session is session
    assert _revoke(second, session)["status"] == "ok"
    assert AutoRecognitionPreferenceStore(paths).read_enabled() is False

    entry = SimpleNamespace(id="0x567-flash", vendor="0x567", provider_model="gpt-6-luna")
    monkeypatch.setattr(server, "_auto_recognition_target", lambda _paths: (entry, "https://api.0x567.com/v1"))
    sent = []

    class ConfirmedClient:
        def chat_confirmed_json(self, _messages, *, expected_preview_json, confirm_fn, **_kwargs):
            if confirm_fn(expected_preview_json, False) == "y":
                sent.append(True)
            return '{"action":"none"}'

    adapter = _AutoRecognitionClient(ConfirmedClient(), first, candidate)
    adapter.chat(
        _messages(candidate.text), model=MODEL_ID, tools=[], temperature=0.0,
        response_format={"type": "json_object"},
    )

    assert sent == []
    assert first._auto_recognition_session is None


def test_other_bridge_revoke_after_confirmation_blocks_final_send(paths, monkeypatch):
    import server

    session = _new_session(paths, "before-post")
    first, candidate = _bridge_and_candidate(paths, session)
    second, _ = _bridge_and_candidate(paths, session)
    entry = SimpleNamespace(id="0x567-flash", vendor="0x567", provider_model="gpt-6-luna")
    monkeypatch.setattr(server, "_auto_recognition_target", lambda _paths: (entry, "https://api.0x567.com/v1"))
    sent = []

    class ConfirmedClient:
        def chat_confirmed_json(
            self, _messages, *, expected_preview_json, confirm_fn, before_send_fn, **_kwargs,
        ):
            assert confirm_fn(expected_preview_json, False) == "y"
            assert _revoke(second, session)["status"] == "ok"
            if before_send_fn():
                sent.append(True)
            return '{"action":"none"}'

    adapter = _AutoRecognitionClient(ConfirmedClient(), first, candidate)
    adapter.chat(
        _messages(candidate.text), model=MODEL_ID, tools=[], temperature=0.0,
        response_format={"type": "json_object"},
    )

    assert sent == []
    assert first._auto_recognition_session is None
    assert AutoRecognitionPreferenceStore(paths).read_enabled() is False
