from __future__ import annotations

import asyncio
import secrets
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))
_TESTS = Path(__file__).resolve().parent
if str(_TESTS) not in sys.path:
    sys.path.insert(0, str(_TESTS))

from isolation_helpers import temporary_agent_paths
from server import WsBridge, WsSessionHandler
from session import create_new
from work_record_auto_recognition_preference import AutoRecognitionPreferenceStore


@pytest.fixture
def paths():
    with temporary_agent_paths(prefix="auto-recognition-pref-") as isolated:
        yield isolated


def _session(paths, label: str):
    return create_new(paths, conversation_id=f"_auto_recognition_{label}_{secrets.token_hex(4)}")


def _toggle(bridge: WsBridge, session, action: str):
    return bridge.set_auto_recognition({
        "type": "work-record.auto-recognition.set",
        "session_id": session.conversation_id,
        "request_id": str(uuid.uuid4()),
        "action": action,
    }, session)


def test_session_auto_recognition_defaults_on_without_reconsent(paths):
    session = _session(paths, "default")
    events = []
    bridge = WsBridge(emit=events.append, paths=paths)
    bridge._session_provider = lambda: session

    bridge.emit_session_state(session)

    assert bridge._auto_recognition_session is session
    state = next(event for event in events if event["type"] == "work-record.auto-recognition.state")
    assert state == {"type": "work-record.auto-recognition.state", "session_id": session.conversation_id, "enabled": True}
    assert not any(event.get("type") == "confirm.request" for event in events)
    assert AutoRecognitionPreferenceStore(paths).read_enabled() is True


def test_revoke_is_durable_and_prevents_automatic_reauthorization_on_session_switch(paths):
    first, second = _session(paths, "one"), _session(paths, "two")
    current = [first]
    bridge = WsBridge(emit=lambda _event: None, paths=paths)
    bridge._session_provider = lambda: current[0]
    bridge.emit_session_state(first)
    assert _toggle(bridge, first, "revoke")["enabled"] is False

    bridge.begin_session_binding_change()
    current[0] = second
    bridge.end_session_binding_change()

    assert bridge._auto_recognition_session is None
    assert AutoRecognitionPreferenceStore(paths).read_enabled() is False
    assert _toggle(bridge, second, "enable")["enabled"] is True
    assert AutoRecognitionPreferenceStore(paths).read_enabled() is True


def test_wrong_session_toggle_is_rejected_and_does_not_grant(paths):
    session = _session(paths, "bound")
    other = _session(paths, "other")
    bridge = WsBridge(emit=lambda _event: None, paths=paths)
    bridge._session_provider = lambda: session
    bridge.emit_session_state(session)
    message = {
        "type": "work-record.auto-recognition.set",
        "session_id": other.conversation_id,
        "request_id": str(uuid.uuid4()),
        "action": "enable",
    }

    result = bridge.set_auto_recognition(message, other)

    assert result["status"] == "rejected"
    assert bridge._auto_recognition_session is session


def test_global_role_model_preferences_are_read_and_written_over_websocket_dispatch(paths):
    from llm_role_model_preferences import load_role_model_preferences

    session = _session(paths, "role-api")
    bridge = WsBridge(emit=lambda _event: None, paths=paths)
    handler = WsSessionHandler(paths)
    repl = SimpleNamespace(session=session)
    events = []
    bridge.emit = events.append

    async def dispatch():
        await handler._dispatch({"type": "llm_role_models.get"}, repl, bridge)
        await handler._dispatch({"type": "llm_role_models.set", "role": "main", "model": "0x567-flash"}, repl, bridge)
    asyncio.run(dispatch())

    assert [event["type"] for event in events] == ["llm_role_models.state", "llm_role_models.done"]
    assert events[-1]["preferences"]["main"] == "0x567-flash"
    assert load_role_model_preferences(paths)["main"] == "0x567-flash"


def test_invalid_role_model_selection_returns_rejected_websocket_event(paths):
    session = _session(paths, "role-invalid")
    events = []
    bridge = WsBridge(emit=events.append, paths=paths)
    handler = WsSessionHandler(paths)

    asyncio.run(handler._dispatch(
        {"type": "llm_role_models.set", "role": "main", "model": "not-in-catalog"},
        SimpleNamespace(session=session), bridge,
    ))

    assert events[-1]["type"] in {"llm_role_models.done", "llm_role_models.state"}
    assert events[-1]["status"] == "rejected"
    assert "preferences" in events[-1]


def test_explicit_global_models_reach_session_banner_and_terminal_planner(paths):
    from llm_role_model_preferences import dispatch_llm_role_models_message
    from session import session_banner_event
    from terminal_plan import resolve_terminal_planning_profile
    from llm_routing import resolve_model_id_for_role

    session = _session(paths, "global-routing")
    dispatch_llm_role_models_message(paths, {"type": "llm_role_models.set", "role": "main", "model": "0x567-flash"})
    dispatch_llm_role_models_message(paths, {"type": "llm_role_models.set", "role": "subagent", "model": "0x567-pro"})

    assert session_banner_event(session, paths=paths)["llm_model"] == "0x567-flash"
    for role in ("explore", "checker", "deliverable_review", "topic_routing"):
        assert resolve_model_id_for_role(role, session.meta, paths=paths) == "0x567-pro"
    assert resolve_terminal_planning_profile(session, paths=paths).model_id == "0x567-pro"


def test_corrupt_auto_recognition_preference_fails_closed(paths):
    session = _session(paths, "corrupt-pref")
    database = paths.data / "auto-recognition-preference.sqlite3"
    database.parent.mkdir(parents=True, exist_ok=True)
    database.write_bytes(b"not a sqlite database")
    bridge = WsBridge(emit=lambda _event: None, paths=paths)
    bridge._session_provider = lambda: session

    bridge.emit_session_state(session)

    assert bridge._auto_recognition_session is None
    assert bridge._auto_recognition_preference_failed is True


def test_confirmed_json_final_authorization_guard_blocks_http_post(paths, monkeypatch):
    import json
    import llm_client
    from llm_client import LLMClient, LLMOutboundConfigurationChangedError
    from llm_models import ModelEntry

    entry = ModelEntry(
        id="0x567-flash", name="test model", vendor="0x567",
        base_url="https://api.0x567.com", provider_model="gpt-6-luna", api_key="local-test-key",
    )

    class Registry:
        @staticmethod
        def resolve(model_id):
            return entry if model_id == entry.id else None

    calls = []

    class HttpClient:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def post(self, *_args, **_kwargs):
            calls.append("post")
            raise AssertionError("authorization guard must stop before POST")

    monkeypatch.setattr(llm_client.ModelRegistry, "load", lambda _paths: Registry())
    monkeypatch.setattr(llm_client.httpx, "Client", lambda **_kwargs: HttpClient())
    messages = [{"role": "user", "content": "test payload"}]
    provider_payload = {
        "model": entry.provider_model, "messages": messages, "temperature": 0.0,
        "stream": False, "response_format": {"type": "json_object"},
    }
    preview = json.dumps({
        "model_id": entry.id, "vendor": entry.vendor,
        "endpoint": entry.chat_completions_url(), "session_id": "test-session",
        "provider_payload": provider_payload,
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)

    def confirm(_preview, _allow_all):
        calls.append("confirm")
        return "y"

    def final_guard():
        calls.append("guard")
        return False

    with pytest.raises(LLMOutboundConfigurationChangedError, match="no request was sent"):
        LLMClient(paths=paths).chat_confirmed_json(
            messages, model_id=entry.id, session_id="test-session",
            expected_preview_json=preview, confirm_fn=confirm, before_send_fn=final_guard,
        )
    assert calls == ["confirm", "guard"]


def test_other_bridge_revoke_invalidates_current_automatic_recognition_authorization(paths):
    from types import SimpleNamespace
    from server import _auto_recognition_binding

    session = _session(paths, "cross-bridge")
    current = [session]
    first = WsBridge(emit=lambda _event: None, paths=paths)
    first._session_provider = lambda: current[0]
    first.emit_session_state(session)
    second = WsBridge(emit=lambda _event: None, paths=paths)
    second._session_provider = lambda: current[0]
    second.emit_session_state(session)
    candidate = SimpleNamespace(session=session, cancel_generation=first._post_turn_generation)

    assert _auto_recognition_binding(first, candidate)
    assert _toggle(second, session, "revoke")["status"] == "ok"
    assert not _auto_recognition_binding(first, candidate)
    assert first._auto_recognition_session is None


def test_failed_preference_write_rejects_enable_and_keeps_session_unauthorized(paths, monkeypatch):
    import work_record_auto_recognition_preference as preference

    session = _session(paths, "write-failure")
    bridge = WsBridge(emit=lambda _event: None, paths=paths)
    bridge._session_provider = lambda: session
    bridge.emit_session_state(session)
    assert bridge._auto_recognition_session is session

    def fail_write(_store, _enabled):
        raise preference.AutoRecognitionPreferenceError("injected preference write failure")

    monkeypatch.setattr(preference.AutoRecognitionPreferenceStore, "write_enabled", fail_write)
    result = _toggle(bridge, session, "revoke")

    assert result["status"] == "rejected"
    assert result["enabled"] is False
    assert bridge._auto_recognition_session is None
    assert bridge._auto_recognition_preference_failed is True
