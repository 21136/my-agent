from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

from paths import AgentPaths
from self_evolution_host import SelfEvolutionHostConfig
from self_evolution_store import EvolutionStore
from self_evolution_types import EvolutionRequest
from self_evolution_worker import HostTool
from self_evolution_bootstrap import (
    SelfEvolutionBootstrapDependencies,
    SelfEvolutionBootstrapRefused,
    assemble_self_evolution_bootstrap,
)
from session import SessionMeta


def _paths(tmp_path: Path) -> AgentPaths:
    root = tmp_path / "project"
    (root / "evolve").mkdir(parents=True)
    (root / "evolve" / "_index.toml").write_text("format = 1\n", encoding="utf-8")
    paths = AgentPaths.from_root(root)
    paths.data.mkdir(parents=True, exist_ok=True)
    (paths.data / "llm_models.json").write_text(json.dumps({"models": [{
        "registryId": "0x567-flash", "name": "isolated test", "vendor": "0x567",
        "baseUrl": "https://offline.invalid", "modelId": "offline-model",
        "apiKeyEnv": "BOOTSTRAP_TEST_KEY", "supportsToolCall": True, "tier": "flash",
    }]}), encoding="utf-8")
    (paths.data / "llm_role_models.json").write_text(json.dumps({
        "format": 1, "preferences": {"main": "0x567-flash", "subagent": "0x567-flash"},
        "explicit_roles": ["subagent"],
    }), encoding="utf-8")
    return paths


def _config(tmp_path: Path) -> SelfEvolutionHostConfig:
    stable = tmp_path / "stable"
    trust = tmp_path / "trust"
    stable.mkdir()
    trust.mkdir()
    return SelfEvolutionHostConfig(
        stable_repository=stable,
        trust_root=trust,
        attribution_paths=("agent.py",),
        verification_source_paths=("agent.py",),
        verification_commands={"syntax": ("python", "-m", "py_compile", "agent.py")},
    )


def _tools() -> tuple[HostTool, ...]:
    return (HostTool("observe_candidate", "Observe candidate", lambda _ctx, _args: {"status": "observed"}),)


def _dependencies(tmp_path: Path, *, detector=None, store=None):
    paths = _paths(tmp_path)
    state = tmp_path / "isolated-state"
    state.mkdir()
    if store is None:
        store = EvolutionStore(state, trusted_acl_verifier=lambda candidate: candidate.resolve().is_relative_to(tmp_path.resolve()))
    return SelfEvolutionBootstrapDependencies(
        host_config=_config(tmp_path),
        integrity_key=b"isolated-test-integrity-key-32-bytes",
        trusted_verifier_resolver=lambda *_args: None,
        store=store,
        paths=paths,
        session_meta=SessionMeta(),
        tools=_tools(),
        outbound_request_confirm_fn=lambda _preview, _allow: "n",
        process_tree_stopped=lambda _identity: False,
        intent_detector=detector,
    )


def test_default_bootstrap_is_disabled_and_reports_production_blockers():
    bootstrap = assemble_self_evolution_bootstrap()

    assert bootstrap.enabled is False
    assert bootstrap.intent_detector is None
    assert bootstrap.context_provider is None
    assert "backend_dependencies_unavailable" in bootstrap.missing_reasons
    with pytest.raises(SelfEvolutionBootstrapRefused) as refused:
        bootstrap.host_factory()
    assert refused.value.reason_codes == bootstrap.missing_reasons


def test_trusted_dependency_assembly_is_lazy_and_detector_is_fail_closed(tmp_path):
    detector_calls: list[str] = []

    def detector(raw_text: str):
        detector_calls.append(raw_text)
        if raw_text == "evolve safely":
            return EvolutionRequest("evolve safely", ("keep tests passing",))
        if raw_text == "bad refs":
            return EvolutionRequest("bad refs", context_refs=("renderer://capability",))
        return None

    dependencies = _dependencies(tmp_path, detector=detector)
    bootstrap = assemble_self_evolution_bootstrap(dependencies)

    assert bootstrap.enabled is False
    assert bootstrap.context_provider is not None
    assert callable(bootstrap.host_factory)
    assert detector_calls == []  # assembly must not infer intent or touch a model/network
    assert bootstrap.intent_detector("evolve safely") == EvolutionRequest(
        "evolve safely", ("keep tests passing",),
    )
    assert bootstrap.intent_detector("ordinary chat") is None
    assert bootstrap.intent_detector("bad refs") is None
    assert detector_calls == ["evolve safely", "ordinary chat", "bad refs"]
    assert {
        "full_candidate_coverage_unavailable",
        "candidate_creation_safety_gate_closed",
        "host_production_gate_closed",
        "process_tree_containment_unavailable",
    }.issubset(bootstrap.missing_reasons)


def test_host_factory_refuses_before_reconciliation_or_model_client_creation(tmp_path):
    dependencies = _dependencies(tmp_path, detector=lambda _text: None)
    reconciliations: list[bool] = []
    with patch.object(EvolutionStore, "reconcile_startup_writers", autospec=True,
                      side_effect=lambda _self: reconciliations.append(True)), \
         patch("self_evolution_model_adapter.build_trusted_worker_capabilities",
               side_effect=AssertionError("must not build model capability while gates are closed"),
               create=True):
        bootstrap = assemble_self_evolution_bootstrap(dependencies)
        with pytest.raises(SelfEvolutionBootstrapRefused) as refused:
            bootstrap.host_factory()

    assert "production_store_safety_unavailable" in refused.value.reason_codes
    assert reconciliations == []


def test_missing_backend_dependencies_are_named_and_never_treated_as_renderer_input(tmp_path):
    incomplete = SelfEvolutionBootstrapDependencies(
        host_config=_config(tmp_path), integrity_key=None,
        trusted_verifier_resolver=None, store=None, paths=None,
        session_meta=None, tools=None, outbound_request_confirm_fn=None,
        process_tree_stopped=None, intent_detector=None,
    )
    bootstrap = assemble_self_evolution_bootstrap(incomplete)

    assert bootstrap.intent_detector is None
    assert {
        "host_integrity_key_unavailable",
        "trusted_verifier_resolver_unavailable",
        "persistent_store_unavailable",
        "project_model_configuration_unavailable",
        "backend_worker_tools_unavailable",
        "outbound_confirmation_source_unavailable",
        "process_tree_safety_callback_unavailable",
    }.issubset(bootstrap.missing_reasons)
    with pytest.raises(SelfEvolutionBootstrapRefused):
        bootstrap.host_factory()


def test_detector_exception_and_invalid_output_fail_closed(tmp_path):
    def broken(_text):
        raise RuntimeError("sensitive backend detail")

    bootstrap = assemble_self_evolution_bootstrap(_dependencies(tmp_path, detector=broken))
    assert bootstrap.intent_detector("evolve") is None
    assert "sensitive backend detail" not in repr(bootstrap)

    invalid = assemble_self_evolution_bootstrap(
        _dependencies(tmp_path / "invalid", detector=lambda _text: {"user_goal": "forged"}),
    )
    assert invalid.intent_detector("evolve") is None




def test_factory_composes_real_backend_host_and_capabilities_only_after_gate_attestation(tmp_path, monkeypatch):
    import self_evolution_bootstrap
    from llm_client import LLMClient
    from self_evolution_host import SelfEvolutionHost

    dependencies = _dependencies(tmp_path, detector=lambda _text: None)
    # This bypasses only the readiness predicate so the composition itself can be
    # exercised in an isolated temp root. It does not mutate production gates or
    # claim that the returned Host is production-ready.
    monkeypatch.setattr(self_evolution_bootstrap, "_missing_reasons", lambda _deps: ())
    bootstrap = assemble_self_evolution_bootstrap(dependencies)

    host = bootstrap.host_factory()

    assert type(host) is SelfEvolutionHost
    assert host.worker_bound is True
    assert host.production_ready is False
    assert host._context_provider is bootstrap.context_provider
    assert host._worker_capabilities.enable_experimental_start is False
    assert host._worker_capabilities.tools is dependencies.tools
    assert host._worker_capabilities.outbound_request_confirm_fn is dependencies.outbound_request_confirm_fn
    assert host._worker_capabilities.process_tree_stopped is dependencies.process_tree_stopped
    assert isinstance(host._worker_capabilities.client, LLMClient)
    assert host._worker_capabilities.client.paths is dependencies.paths


def test_renderer_shaped_mapping_cannot_supply_bootstrap_capabilities():
    with pytest.raises(TypeError, match="trusted SelfEvolutionBootstrapDependencies"):
        assemble_self_evolution_bootstrap({
            "store": object(),
            "process_tree_stopped": lambda _identity: True,
            "enable_experimental_start": True,
        })
