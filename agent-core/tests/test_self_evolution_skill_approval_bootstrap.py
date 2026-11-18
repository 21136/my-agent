from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

from paths import AgentPaths
from self_evolution_bootstrap import (
    SelfEvolutionBootstrapDependencies,
    assemble_self_evolution_bootstrap,
)
from self_evolution_host import SelfEvolutionHostConfig
from self_evolution_store import EvolutionStore
from self_evolution_worker import HostTool
from session import SessionMeta


class _FakeSkillProvider:
    def fetch(self, url: str, *, timeout: float, max_bytes: int) -> bytes:
        raise AssertionError("provider must not be invoked during bootstrap assembly")


def _dependencies(tmp_path: Path, *, approval=None, provider=None):
    project = tmp_path / "project"
    (project / "evolve" / "skills").mkdir(parents=True)
    (project / "evolve" / "_index.toml").write_text("format = 1\n", encoding="utf-8")
    paths = AgentPaths.from_root(project)
    paths.data.mkdir(parents=True, exist_ok=True)
    (paths.data / "llm_models.json").write_text(json.dumps({"models": [{
        "registryId": "0x567-flash", "name": "offline", "vendor": "0x567",
        "baseUrl": "https://offline.invalid", "modelId": "offline-model",
        "apiKeyEnv": "SKILL_APPROVAL_TEST_KEY", "supportsToolCall": True, "tier": "flash",
    }]}), encoding="utf-8")
    (paths.data / "llm_role_models.json").write_text(json.dumps({
        "format": 1, "preferences": {"main": "0x567-flash", "subagent": "0x567-flash"},
        "explicit_roles": ["subagent"],
    }), encoding="utf-8")

    stable = tmp_path / "stable"
    trust_root = tmp_path / "trust"
    stable.mkdir()
    trust_root.mkdir()
    state_root = tmp_path / "state"
    state_root.mkdir()
    store = EvolutionStore(
        state_root,
        trusted_acl_verifier=lambda candidate: candidate.resolve().is_relative_to(tmp_path.resolve()),
    )
    return SelfEvolutionBootstrapDependencies(
        host_config=SelfEvolutionHostConfig(
            stable_repository=stable, trust_root=trust_root,
            attribution_paths=("agent.py",), verification_source_paths=("agent.py",),
            verification_commands={"syntax": (sys.executable, "-m", "py_compile", "agent.py")},
        ),
        integrity_key=b"skill-approval-bootstrap-test-key-32bytes",
        trusted_verifier_resolver=lambda *_args: None,
        store=store,
        paths=paths,
        session_meta=SessionMeta(),
        tools=(HostTool("observe_candidate", "Observe candidate", lambda *_args: {"status": "observed"}),),
        outbound_request_confirm_fn=lambda _preview, _allow_all: "n",
        process_tree_stopped=lambda _identity: False,
        intent_detector=lambda _text: None,
        skill_install_approval_fn=approval,
        skill_install_provider=provider,
    )


def test_bootstrap_passes_explicit_skill_approval_and_provider_without_enabling_production(tmp_path, monkeypatch):
    import self_evolution_bootstrap

    approval = lambda payload: payload.get("skill_id") == "approved-by-host"
    provider = _FakeSkillProvider()
    dependencies = _dependencies(tmp_path, approval=approval, provider=provider)
    monkeypatch.setattr(self_evolution_bootstrap, "_missing_reasons", lambda _deps: ())
    bootstrap = assemble_self_evolution_bootstrap(dependencies)

    host = bootstrap.host_factory()

    caps = host._worker_capabilities
    assert caps.skill_install_approval_fn is approval
    assert caps.skill_install_provider is provider
    assert caps.enable_experimental_start is False
    assert host.production_ready is False
    assert bootstrap.enabled is False


def test_missing_skill_approval_stays_missing_and_outbound_confirmation_does_not_substitute(tmp_path, monkeypatch):
    import self_evolution_bootstrap
    from self_evolution_host import SelfEvolutionHost

    dependencies = _dependencies(tmp_path)
    monkeypatch.setattr(self_evolution_bootstrap, "_missing_reasons", lambda _deps: ())
    host = assemble_self_evolution_bootstrap(dependencies).host_factory()

    assert host._worker_capabilities.skill_install_approval_fn is None
    assert host._worker_capabilities.skill_install_provider is None
    assert host._worker_capabilities.enable_experimental_start is False
    assert host.production_ready is False
    tool = SelfEvolutionHost._build_worker_skill_install_tool(
        host, SimpleNamespace(identity=object()), object(), approval=None,
        provider=host._worker_capabilities.skill_install_provider,
    )
    assert tool is None


def test_bootstrap_dependencies_validate_explicit_approval_and_provider_types(tmp_path):
    with pytest.raises(TypeError, match="skill installation approval"):
        _dependencies(tmp_path / "bad-approval", approval="yes")
    with pytest.raises(TypeError, match="Skill fetch provider"):
        _dependencies(tmp_path / "bad-provider", provider=object())
