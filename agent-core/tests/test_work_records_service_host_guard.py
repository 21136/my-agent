from __future__ import annotations

import sys
import uuid
from contextlib import contextmanager
from pathlib import Path

import pytest

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

from paths import AgentPaths
from session import Session, SessionMeta
from work_records import WorkRecordStore
from work_records_service import WorkRecordCaptureService


class FakeProvider:
    def __init__(self, response=None):
        self.calls = []
        self.response = response or (
            '{"action":"record","kind":"requirement","title":"new item",'
            '"requirement":"new requirement","scope":"independent","item_id":null}'
        )

    def chat(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        return self.response


def make_session(tmp_path):
    paths = AgentPaths(tmp_path, tmp_path / "workspace", tmp_path / "data", tmp_path / "evolve")
    session_dir = paths.data / "sessions" / "synthetic-session"
    session_dir.mkdir(parents=True)
    paths.workspace.mkdir()
    paths.evolve.mkdir()
    return Session("synthetic-session", session_dir, "", SessionMeta(), [], paths)


def install_model_registry(monkeypatch):
    monkeypatch.setattr(
        "work_records_service.get_registry",
        lambda *_a, **_k: type("Registry", (), {
            "get": lambda _self, _id: type("Model", (), {
                "id": "0x567-flash", "vendor": "0x567",
                "provider_model": "gpt-6-luna", "max_input_tokens": 96000,
            })()
        })(),
    )


def confirm_yes():
    return "yes", str(uuid.uuid4())


def test_binding_invalidated_during_outbound_confirmation_prevents_provider_call(tmp_path, monkeypatch):
    session = make_session(tmp_path)
    provider = FakeProvider()
    prompts = []
    binding = {"valid": True}

    def confirm(preview):
        prompts.append(preview)
        binding["valid"] = False
        return confirm_yes()

    service = WorkRecordCaptureService(
        session=session, client=provider, confirm=confirm,
        host_binding_is_valid=lambda: binding["valid"],
    )
    monkeypatch.setattr("work_records_service.get_registry", lambda *_a, **_k: pytest.fail("stale binding must stop before provider setup"))

    result = service.capture("synthetic text", request_id="11111111-1111-4111-8111-111111111111")

    assert result.status == "stale_context"
    assert len(prompts) == 1
    assert provider.calls == []
    assert WorkRecordStore(session).list_items() == []


def test_create_only_policy_rejects_append_before_storage_confirmation(tmp_path, monkeypatch):
    session = make_session(tmp_path)
    store = WorkRecordStore(session)
    existing = store.create_item(title="existing", scope={"kind": "independent"})
    provider = FakeProvider(
        '{"action":"record","kind":"requirement","title":"existing",'
        '"requirement":"append candidate","scope":"independent","item_id":"'
        + existing["item_id"] + '"}'
    )
    prompts = []
    service = WorkRecordCaptureService(
        session=session, client=provider,
        confirm=lambda preview: (prompts.append(preview) or confirm_yes()),
        write_policy="create_only",
    )
    install_model_registry(monkeypatch)

    result = service.capture("synthetic text", request_id="22222222-2222-4222-8222-222222222222")

    assert result.status == "append_not_allowed"
    assert len(prompts) == 1  # outbound confirmation only; no UI preview parsing
    assert len(store.get_item(existing["item_id"])["requirements"]) == 0


def test_commit_guard_revalidates_host_binding_immediately_before_local_write(tmp_path, monkeypatch):
    session = make_session(tmp_path)
    provider = FakeProvider()
    binding = {"valid": True, "guard_entered": False}

    @contextmanager
    def commit_guard():
        binding["guard_entered"] = True
        binding["valid"] = False  # model a host switch serialized at the commit boundary
        yield

    service = WorkRecordCaptureService(
        session=session, client=provider, confirm=lambda _preview: confirm_yes(),
        host_binding_is_valid=lambda: binding["valid"],
        commit_critical_section=commit_guard,
    )
    install_model_registry(monkeypatch)

    result = service.capture("synthetic text", request_id="33333333-3333-4333-8333-333333333333")

    assert binding["guard_entered"]
    assert result.status == "stale_context"
    assert WorkRecordStore(session).list_items() == []


def test_storage_rejection_does_not_write_or_retry_with_host_callbacks(tmp_path, monkeypatch):
    session = make_session(tmp_path)
    provider = FakeProvider()
    prompts = []
    answers = iter([confirm_yes(), ("no", str(uuid.uuid4()))])
    guard_entries = []

    @contextmanager
    def commit_guard():
        guard_entries.append(True)
        yield

    service = WorkRecordCaptureService(
        session=session, client=provider,
        confirm=lambda preview: (prompts.append(preview) or next(answers)),
        host_binding_is_valid=lambda: True,
        commit_critical_section=commit_guard,
    )
    install_model_registry(monkeypatch)

    result = service.capture("synthetic text", request_id="44444444-4444-4444-8444-444444444444")

    assert result.status == "storage_not_authorized"
    assert len(provider.calls) == 1
    assert len(prompts) == 2
    assert guard_entries == []
    assert WorkRecordStore(session).list_items() == []


def test_legacy_constructor_and_capture_call_remain_compatible(tmp_path, monkeypatch):
    session = make_session(tmp_path)
    provider = FakeProvider()
    answers = iter([confirm_yes(), confirm_yes()])
    service = WorkRecordCaptureService(session=session, client=provider, confirm=lambda _preview: next(answers))
    install_model_registry(monkeypatch)

    result = service.capture("synthetic text", request_id="55555555-5555-4555-8555-555555555555")

    assert result.status == "recorded"
    assert len(provider.calls) == 1
    assert len(WorkRecordStore(session).list_items()) == 1

def test_cancelled_during_storage_confirmation_prevents_local_commit_without_retry(tmp_path, monkeypatch):
    session = make_session(tmp_path)
    provider = FakeProvider()
    prompts = []
    binding = {"valid": True}
    guard_entries = []

    def confirm(preview):
        prompts.append(preview)
        if len(prompts) == 2:
            binding["valid"] = False  # host cancellation arrives while approval UI is open
        return confirm_yes()

    @contextmanager
    def commit_guard():
        guard_entries.append(True)
        yield

    service = WorkRecordCaptureService(
        session=session, client=provider, confirm=confirm,
        host_binding_is_valid=lambda: binding["valid"],
        commit_critical_section=commit_guard,
    )
    install_model_registry(monkeypatch)

    result = service.capture("synthetic text", request_id="66666666-6666-4666-8666-666666666666")

    assert result.status == "stale_context"
    assert len(provider.calls) == 1  # authorized classification is not retried
    assert len(prompts) == 2
    assert guard_entries == [True]
    assert WorkRecordStore(session).list_items() == []
