from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from session import SessionMeta
from tools.executor import ExecutorSession


EVOLUTION_ID = "22222222-2222-4222-8222-222222222222"
CONVERSATION_ID = "33333333-3333-4333-8333-333333333333"
HOST_RUN_ID = "44444444-4444-4444-8444-444444444444"


def test_session_meta_persists_evolution_binding_and_legacy_defaults_empty():
    meta = SessionMeta.from_dict({"evolution_id": EVOLUTION_ID})
    assert getattr(meta, "evolution_id", None) == EVOLUTION_ID
    assert SessionMeta.from_dict(meta.to_dict()).evolution_id == EVOLUTION_ID
    legacy = SessionMeta.from_dict({"topics": ["coding"]})
    assert legacy.evolution_id == ""


def test_executor_session_loads_and_refreshes_only_the_binding_reference(tmp_path):
    meta_path = tmp_path / "meta.json"
    meta_path.write_text('{"evolution_id":"' + EVOLUTION_ID + '","project_id":"p"}', encoding="utf-8")
    executor_session = ExecutorSession.load(tmp_path)
    assert getattr(executor_session, "evolution_id", None) == EVOLUTION_ID
    assert executor_session.project_id == "p"
    meta_path.write_text('{"evolution_id":""}', encoding="utf-8")
    executor_session.refresh_bound_project_meta()
    assert executor_session.evolution_id == ""
    assert not hasattr(executor_session, "host_token")


def test_attach_requires_supervisor_confirmation_before_persisting(tmp_path):
    try:
        from evolution.context import attach_evolution, EvolutionContextError
    except ImportError:
        attach_evolution = None
        EvolutionContextError = RuntimeError
    assert callable(attach_evolution), "evolution.context.attach_evolution must be available"
    calls = []
    session = SimpleNamespace(conversation_id=CONVERSATION_ID, meta=SessionMeta(), save=lambda: calls.append("save"))
    class DeniedClient:
        def inspect(self, evolution_id, conversation_id, host_run_id):
            return {"status": "failed", "result": None, "error": {"code": "permission_denied"}}
    try:
        attach_evolution(session, EVOLUTION_ID, client=DeniedClient(), host_run_id=HOST_RUN_ID)
        assert False, "unverified binding must not attach"
    except EvolutionContextError:
        pass
    assert session.meta.evolution_id == ""
    assert calls == []

    class VerifiedClient:
        def inspect(self, evolution_id, conversation_id, host_run_id):
            return {"status": "succeeded", "result": {"evolution_id": evolution_id, "conversation_id": conversation_id}}
    attach_evolution(session, EVOLUTION_ID, client=VerifiedClient(), host_run_id=HOST_RUN_ID)
    assert session.meta.evolution_id == EVOLUTION_ID
    assert calls == ["save"]


def test_attach_preserves_native_legacy_conversation_id_after_supervisor_confirmation():
    from evolution.context import attach_evolution

    legacy_conversation_id = "20261026-1a2b3c4d"
    calls = []
    session = SimpleNamespace(conversation_id=legacy_conversation_id, meta=SessionMeta(), save=lambda: calls.append("save"))

    class VerifiedClient:
        def inspect(self, evolution_id, conversation_id, host_run_id):
            assert (evolution_id, conversation_id, host_run_id) == (EVOLUTION_ID, legacy_conversation_id, HOST_RUN_ID)
            return {"status": "succeeded", "result": {"evolution_id": evolution_id, "conversation_id": conversation_id}}

    attach_evolution(session, EVOLUTION_ID, client=VerifiedClient(), host_run_id=HOST_RUN_ID)
    assert session.meta.evolution_id == EVOLUTION_ID
    assert calls == ["save"]

def test_overlay_is_bounded_and_does_not_render_untrusted_or_private_fields():
    try:
        from evolution.context import format_evolution_overlay
    except ImportError:
        format_evolution_overlay = None
    assert callable(format_evolution_overlay), "evolution.context.format_evolution_overlay must be available"
    text = format_evolution_overlay({
        "evolution_id": EVOLUTION_ID,
        "conversation_id": CONVERSATION_ID,
        "state_revision": 4,
        "current_candidate_id": "sha256:" + "a" * 64,
        "current_instance_id": None,
        "pending_operation_ids": [],
        "unknown_operation_ids": ["55555555-5555-4555-8555-555555555555"],
        "isolation_mode": "local_managed",
        "candidate_status": "failed",
        "prototype_control_verified": False,
        "scripted_recovery_verified": False,
        "agent_recovery_verified": False,
        "independent_review": "incomplete",
        "host_token": "private-token-must-not-appear",
        "candidate_text": "untrusted instructions must-not-appear",
    })
    assert EVOLUTION_ID in text and "failed" in text and "independent_review=incomplete" in text
    assert "private-token" not in text and "untrusted instructions" not in text



def test_ephemeral_projection_overlay_enters_original_session_prompt_without_becoming_meta():
    from loader import build_system_prompt
    from paths import AgentPaths
    from session import Session
    session = Session(conversation_id=CONVERSATION_ID, session_dir=Path(CONVERSATION_ID), goal="continue this project", meta=SessionMeta(evolution_id=EVOLUTION_ID), messages=[], paths=AgentPaths.discover())
    session._turn_evolution_overlay = "[evolution-projection-test] candidate=sha256:abc status=failed"
    prompt = build_system_prompt(session).prompt
    assert "[evolution-projection-test]" in prompt
    assert "_turn_evolution_overlay" not in session.meta.to_dict()

def test_lifecycle_hex_run_id_normalizes_only_to_a_valid_protocol_uuid():
    try:
        from evolution.context import normalize_host_run_id
    except ImportError:
        normalize_host_run_id = None
    assert callable(normalize_host_run_id), "trusted lifecycle run-id normalizer must be available"
    assert normalize_host_run_id("44444444444444448444444444444444") == HOST_RUN_ID
    assert normalize_host_run_id("a" * 32) == ""
    assert normalize_host_run_id("not-a-run-id") == ""

def test_agent_binding_sync_clears_authorization_from_a_different_evolution():
    from agent import Agent
    from paths import AgentPaths
    from session import Session
    from tools.executor import ExecutorSession, ToolExecutor
    from tools.registry import ToolRegistry
    paths = AgentPaths.discover()
    session = Session(conversation_id=CONVERSATION_ID, session_dir=Path(CONVERSATION_ID), goal="", meta=SessionMeta(evolution_id=EVOLUTION_ID), messages=[], paths=paths)
    executor_session = ExecutorSession(evolution_id="66666666-6666-4666-8666-666666666666", evolution_authorization_id="55555555-5555-4555-8555-555555555555")
    executor = ToolExecutor(registry=ToolRegistry.load(paths), session=executor_session)
    agent = Agent(session=session, executor=executor, llm=object())
    assert executor.session.evolution_id == EVOLUTION_ID
    assert executor.session.evolution_authorization_id == ""
    replacement_id = "77777777-7777-4777-8777-777777777777"
    executor.session.evolution_authorization_id = "55555555-5555-4555-8555-555555555555"
    session.meta.evolution_id = replacement_id
    agent._sync_turn_mode()
    assert executor.session.evolution_id == replacement_id
    assert executor.session.evolution_authorization_id == ""
