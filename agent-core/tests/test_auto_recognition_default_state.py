from __future__ import annotations

import secrets
import sys
from pathlib import Path

import pytest

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))
_TESTS = Path(__file__).resolve().parent
if str(_TESTS) not in sys.path:
    sys.path.insert(0, str(_TESTS))

from isolation_helpers import temporary_agent_paths
from server import WsBridge, _AutoRecognitionClient
from session import create_new
from work_record_auto_recognition_preference import AutoRecognitionPreferenceStore


@pytest.fixture
def paths():
    with temporary_agent_paths(prefix="auto-recognition-") as isolated:
        yield isolated


def _new_session(paths, label: str):
    session_id = f"_auto_recognition_{label}_{secrets.token_hex(4)}"
    return create_new(paths, conversation_id=session_id)


def _set_action(bridge: WsBridge, session, action: str) -> dict[str, object]:
    import uuid

    return bridge.set_auto_recognition({
        "type": "work-record.auto-recognition.set",
        "session_id": session.conversation_id,
        "request_id": str(uuid.uuid4()),
        "action": action,
    }, session)


def test_session_state_defaults_recognition_on_without_explicit_consent_prompt(paths) -> None:
    session = _new_session(paths, "initial")
    current = [session]
    events: list[dict[str, object]] = []
    bridge = WsBridge(emit=events.append, paths=paths)
    bridge._session_provider = lambda: current[0]

    bridge.emit_session_state(session)

    assert bridge._auto_recognition_session is session
    assert events[0]["type"] == "session.banner"
    assert events[1] == {
        "type": "work-record.auto-recognition.state",
        "session_id": session.conversation_id,
        "enabled": True,
    }
    assert not any(event.get("type") == "confirm.request" for event in events)


def test_revoke_persists_across_session_switch_until_explicit_reenable(paths) -> None:
    first = _new_session(paths, "first")
    second = _new_session(paths, "second")
    current = [first]
    events: list[dict[str, object]] = []
    bridge = WsBridge(emit=events.append, paths=paths)
    bridge._session_provider = lambda: current[0]

    bridge.emit_session_state(first)
    revoked = _set_action(bridge, first, "revoke")
    assert revoked["status"] == "ok" and revoked["enabled"] is False

    bridge.begin_session_binding_change()
    current[0] = second
    bridge.end_session_binding_change()
    assert bridge._auto_recognition_session is None
    assert events[-1] == {
        "type": "work-record.auto-recognition.state",
        "session_id": second.conversation_id,
        "enabled": False,
    }

    enabled = _set_action(bridge, second, "enable")
    assert enabled["status"] == "ok" and enabled["enabled"] is True
    bridge.begin_session_binding_change()
    current[0] = first
    bridge.end_session_binding_change()
    assert bridge._auto_recognition_session is first
    assert events[-1] == {
        "type": "work-record.auto-recognition.state",
        "session_id": first.conversation_id,
        "enabled": True,
    }


def test_revoke_survives_a_new_bridge_and_manual_enable_survives_restart(paths) -> None:
    first = _new_session(paths, "persist-first")
    second = _new_session(paths, "persist-second")
    current = [first]
    bridge = WsBridge(emit=lambda _event: None, paths=paths)
    bridge._session_provider = lambda: current[0]
    bridge.emit_session_state(first)
    assert bridge._auto_recognition_session is first
    assert _set_action(bridge, first, "revoke")["enabled"] is False

    import subprocess
    import sys as _sys
    probe = (
        "import sys; sys.path.insert(0, sys.argv[1]); "
        "from paths import AgentPaths; "
        "from work_record_auto_recognition_preference import AutoRecognitionPreferenceStore; "
        "print(int(AutoRecognitionPreferenceStore(AgentPaths.from_root(sys.argv[2])).read_enabled()))"
    )
    child = subprocess.run(
        [_sys.executable, "-c", probe, str(_AGENT_CORE), str(paths.agent_root)],
        check=True, capture_output=True, text=True, timeout=10,
    )
    assert child.stdout.strip() == "0"

    # A new bridge models a disconnected/restarted sidecar; opt-out must be read durably.
    restarted = WsBridge(emit=lambda _event: None, paths=paths)
    restarted._session_provider = lambda: second
    restarted.emit_session_state(second)
    assert restarted._auto_recognition_session is None

    assert _set_action(restarted, second, "enable")["enabled"] is True
    child = subprocess.run(
        [_sys.executable, "-c", probe, str(_AGENT_CORE), str(paths.agent_root)],
        check=True, capture_output=True, text=True, timeout=10,
    )
    assert child.stdout.strip() == "1"
    after_enable = WsBridge(emit=lambda _event: None, paths=paths)
    after_enable._session_provider = lambda: first
    after_enable.emit_session_state(first)
    assert after_enable._auto_recognition_session is first


def test_corrupt_preference_database_keeps_recognition_closed(paths) -> None:
    session = _new_session(paths, "corrupt")
    db_path = paths.data / "auto-recognition-preference.sqlite3"
    db_path.write_bytes(b"not a sqlite database")
    bridge = WsBridge(emit=lambda _event: None, paths=paths)
    bridge._session_provider = lambda: session

    bridge.emit_session_state(session)

    assert bridge._auto_recognition_session is None
    assert bridge._auto_recognition_preference_failed is True
    assert _set_action(bridge, session, "enable")["enabled"] is False
    bridge.sync_auto_recognition_session(session)
    assert bridge._auto_recognition_session is None


def test_preference_write_failure_revokes_and_stays_closed_for_session_switch(paths, monkeypatch) -> None:
    import work_record_auto_recognition_preference as preference

    first = _new_session(paths, "write-fail-first")
    second = _new_session(paths, "write-fail-second")
    current = [first]
    bridge = WsBridge(emit=lambda _event: None, paths=paths)
    bridge._session_provider = lambda: current[0]
    bridge.emit_session_state(first)

    def fail_write(_self, _enabled):
        raise preference.AutoRecognitionPreferenceError("injected write failure")

    monkeypatch.setattr(preference.AutoRecognitionPreferenceStore, "write_enabled", fail_write)
    result = _set_action(bridge, first, "revoke")
    assert result["status"] == "rejected" and result["enabled"] is False
    assert bridge._auto_recognition_session is None

    current[0] = second
    bridge.begin_session_binding_change()
    bridge.end_session_binding_change()
    assert bridge._auto_recognition_session is None
    assert bridge._auto_recognition_preference_failed is True


def test_independent_sqlite_connections_serialize_preference_transactions(paths) -> None:
    from concurrent.futures import ThreadPoolExecutor

    stores = [AutoRecognitionPreferenceStore(paths) for _ in range(8)]
    assert stores[0].read_enabled() is True
    with ThreadPoolExecutor(max_workers=len(stores)) as workers:
        results = list(workers.map(lambda pair: pair[0].write_enabled(pair[1]), zip(stores, [False, True] * 4)))
    assert results == [None] * len(stores)
    assert type(AutoRecognitionPreferenceStore(paths).read_enabled()) is bool


def test_pending_revoke_linearizes_before_session_rebind(paths, monkeypatch) -> None:
    import threading
    import work_record_auto_recognition_preference as preference

    first = _new_session(paths, "pending-first")
    second = _new_session(paths, "pending-second")
    current = [first]
    bridge = WsBridge(emit=lambda _event: None, paths=paths)
    bridge._session_provider = lambda: current[0]
    bridge.emit_session_state(first)
    entered_write = threading.Event()
    release_write = threading.Event()
    original_write = preference.AutoRecognitionPreferenceStore.write_enabled

    def delayed_write(store, enabled):
        if enabled is False:
            entered_write.set()
            assert release_write.wait(2), "revoke write was not released"
        return original_write(store, enabled)

    monkeypatch.setattr(preference.AutoRecognitionPreferenceStore, "write_enabled", delayed_write)
    result = []
    revoke = threading.Thread(target=lambda: result.append(_set_action(bridge, first, "revoke")))
    revoke.start()
    assert entered_write.wait(2)

    def switch_session():
        bridge.begin_session_binding_change()
        current[0] = second
        bridge.end_session_binding_change()

    switch = threading.Thread(target=switch_session)
    switch.start()
    release_write.set()
    revoke.join(3)
    switch.join(3)

    assert not revoke.is_alive() and not switch.is_alive()
    assert result[0]["status"] == "ok" and result[0]["enabled"] is False
    assert bridge._auto_recognition_session is None
    assert AutoRecognitionPreferenceStore(paths).read_enabled() is False


def test_enable_write_failure_keeps_existing_durable_opt_out(paths, monkeypatch) -> None:
    import work_record_auto_recognition_preference as preference

    session = _new_session(paths, "enable-fail")
    bridge = WsBridge(emit=lambda _event: None, paths=paths)
    bridge._session_provider = lambda: session
    bridge.emit_session_state(session)
    assert _set_action(bridge, session, "revoke")["status"] == "ok"
    assert bridge._auto_recognition_session is None

    def fail_enable(_self, _enabled):
        raise preference.AutoRecognitionPreferenceError("injected enable failure")

    monkeypatch.setattr(preference.AutoRecognitionPreferenceStore, "write_enabled", fail_enable)
    result = _set_action(bridge, session, "enable")
    assert result["status"] == "rejected" and result["enabled"] is False
    assert bridge._auto_recognition_session is None
    assert AutoRecognitionPreferenceStore(paths).read_enabled() is False


def test_other_bridge_opt_out_blocks_active_bridge_before_external_send(paths, monkeypatch) -> None:
    from types import SimpleNamespace
    import server
    from work_records_luna_intent import MODEL_ID, _SYSTEM

    session = _new_session(paths, "cross-bridge")
    first = WsBridge(emit=lambda _event: None, paths=paths)
    first._session_provider = lambda: session
    first.emit_session_state(session)
    second = WsBridge(emit=lambda _event: None, paths=paths)
    second._session_provider = lambda: session
    second.emit_session_state(session)
    assert first._auto_recognition_session is session
    assert second._auto_recognition_session is session

    result = _set_action(second, session, "revoke")
    assert result["status"] == "ok" and result["enabled"] is False
    assert AutoRecognitionPreferenceStore(paths).read_enabled() is False

    entry = SimpleNamespace(id="0x567-flash", vendor="0x567", provider_model="gpt-6-luna")
    monkeypatch.setattr(server, "_auto_recognition_target", lambda _paths: (entry, "https://api.0x567.com/v1"))
    sent = []

    class ExternalClient:
        def chat_confirmed_json(self, messages, *, expected_preview_json, confirm_fn, **_kwargs):
            if confirm_fn(expected_preview_json, False) == "y":
                sent.append(messages)
            return '{"action":"none"}'

    candidate = SimpleNamespace(
        session=session,
        text="private current-session message",
        cancel_generation=first._post_turn_generation,
    )
    adapter = _AutoRecognitionClient(ExternalClient(), first, candidate)
    messages = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": '{"text":"private current-session message","recognition_view":[],"current_project_available":false}'},
    ]
    response = adapter.chat(messages, model=MODEL_ID, tools=[], temperature=0.0,
                            response_format={"type": "json_object"})

    assert response == '{"action":"none"}'
    assert sent == [], "a bridge that was not itself toggled off must still honor the shared durable opt-out"
    assert first._auto_recognition_session is None


def test_other_bridge_opt_out_after_confirm_but_before_post_cancels_send(paths, monkeypatch) -> None:
    from types import SimpleNamespace
    import server
    from work_records_luna_intent import MODEL_ID, _SYSTEM

    session = _new_session(paths, "cross-bridge-post")
    first = WsBridge(emit=lambda _event: None, paths=paths)
    first._session_provider = lambda: session
    first.emit_session_state(session)
    second = WsBridge(emit=lambda _event: None, paths=paths)
    second._session_provider = lambda: session
    second.emit_session_state(session)

    entry = SimpleNamespace(id="0x567-flash", vendor="0x567", provider_model="gpt-6-luna")
    monkeypatch.setattr(server, "_auto_recognition_target", lambda _paths: (entry, "https://api.0x567.com/v1"))
    sent = []

    class ExternalClient:
        def chat_confirmed_json(self, messages, *, expected_preview_json, confirm_fn,
                                before_send_fn, **_kwargs):
            assert confirm_fn(expected_preview_json, False) == "y"
            assert _set_action(second, session, "revoke")["status"] == "ok"
            if before_send_fn() is True:
                sent.append(messages)
            return '{"action":"none"}'

    candidate = SimpleNamespace(
        session=session,
        text="private current-session message",
        cancel_generation=first._post_turn_generation,
    )
    adapter = _AutoRecognitionClient(ExternalClient(), first, candidate)
    messages = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": '{"text":"private current-session message","recognition_view":[],"current_project_available":false}'},
    ]
    adapter.chat(messages, model=MODEL_ID, tools=[], temperature=0.0,
                 response_format={"type": "json_object"})

    assert sent == [], "the final pre-POST gate must observe another bridge's committed opt-out"
    assert first._auto_recognition_session is None


def test_confirmed_json_runs_final_guard_before_http_post(paths, monkeypatch) -> None:
    import json
    import llm_client
    from llm_client import LLMClient, LLMOutboundConfigurationChangedError
    from llm_models import ModelEntry

    entry = ModelEntry(
        id="0x567-flash", name="test luna", vendor="0x567",
        base_url="https://api.0x567.com", provider_model="gpt-6-luna", api_key="test-only",
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
            raise AssertionError("final guard must block the HTTP post")

    monkeypatch.setattr(llm_client.ModelRegistry, "load", lambda _paths: Registry())
    monkeypatch.setattr(llm_client.httpx, "Client", lambda **_kwargs: HttpClient())
    messages = [{"role": "user", "content": "safe test payload"}]
    provider_payload = {
        "model": entry.provider_model, "messages": messages, "temperature": 0.0,
        "stream": False, "response_format": {"type": "json_object"},
    }
    preview = json.dumps({
        "model_id": entry.id, "vendor": entry.vendor, "endpoint": entry.chat_completions_url(),
        "session_id": "test-session", "provider_payload": provider_payload,
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)

    def confirm(_preview, _allow_all):
        calls.append("confirm")
        return "y"

    def final_guard():
        calls.append("guard")
        return False

    client = LLMClient(paths=paths)
    with pytest.raises(LLMOutboundConfigurationChangedError, match="no request was sent"):
        client.chat_confirmed_json(
            messages, model_id=entry.id, session_id="test-session",
            expected_preview_json=preview, confirm_fn=confirm, before_send_fn=final_guard,
        )
    assert calls == ["confirm", "guard"]
