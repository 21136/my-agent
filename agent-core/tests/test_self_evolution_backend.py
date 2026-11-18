from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

pytestmark = pytest.mark.skipif(os.name != "nt", reason="requires Windows DPAPI and native ACLs")


def _trusted_inputs(paths, storage_root):
    from self_evolution_host import SelfEvolutionHostConfig
    from self_evolution_worker import HostTool
    from session import SessionMeta

    trust_root = paths.agent_root.parent / f"{paths.agent_root.name}-self-evolution-candidates"
    trust_root.mkdir(parents=True, exist_ok=True)
    config = SelfEvolutionHostConfig(
        stable_repository=paths.agent_root, trust_root=trust_root,
        attribution_paths=("agent-core/server.py",),
        verification_source_paths=("agent-core/server.py",),
        verification_commands={"syntax": (sys.executable, "-m", "py_compile", "agent-core/server.py")},
    )
    tools = (HostTool("observe_candidate", "Observe candidate", lambda _ctx, _args: {"status": "observed"}),)
    return {
        "paths": paths, "host_config": config, "session_meta": SessionMeta(),
        "trusted_verifier_resolver": lambda *_args: None, "tools": tools,
        "outbound_request_confirm_fn": lambda _preview, _allow: "n",
        "process_tree_stopped": lambda _identity: False,
        "intent_detector": lambda _raw: None,
        "storage_root": storage_root,
        "allow_initial_trust_root_enrollment": True,
    }



def _approved_disposable_config(paths):
    import json
    import subprocess
    from dataclasses import replace

    from self_evolution_host import SelfEvolutionHostConfig

    source = _trusted_inputs(paths, paths.data / "self-evolution") ["host_config"]
    target = paths.agent_root.parent / f"approved-disposable-{paths.agent_root.name}"
    target.mkdir()
    subprocess.run(["git", "init", "--quiet", str(target)], check=True)
    (target / "seed.txt").write_text("disposable target\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(target), "add", "seed.txt"], check=True)
    subprocess.run(["git", "-C", str(target), "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "--quiet", "-m", "seed"], check=True)
    marker = {
        "schema": "self-evolution-experimental-target.v1",
        "purpose": "self_evolution_disposable_repository",
        "repository_root": str(target.resolve()),
    }
    (target / ".self-evolution-experimental-target.json").write_text(
        json.dumps(marker, sort_keys=True), encoding="utf-8",
    )
    trust_root = target.parent / f"{target.name}-candidates"
    trust_root.mkdir()
    return replace(source, stable_repository=target, trust_root=trust_root)


def test_builder_uses_real_dpapi_key_acl_verifier_and_initialized_store(tmp_path):
    from self_evolution_backend import build_self_evolution_backend_dependencies
    from self_evolution_bootstrap import SelfEvolutionBootstrapDependencies
    from self_evolution_store import EvolutionStore
    from self_evolution_windows_acl import PrivateStorageAclVerifier
    from tests.isolation_helpers import temporary_agent_paths

    with temporary_agent_paths() as paths:
        inputs = _trusted_inputs(paths, paths.data / "self-evolution")
        result = build_self_evolution_backend_dependencies(**inputs)

        assert type(result.dependencies) is SelfEvolutionBootstrapDependencies
        assert type(result.dependencies.store) is EvolutionStore
        assert result.dependencies.paths is paths
        assert result.dependencies.session_meta is not None
        assert type(result.acl_verifier) is PrivateStorageAclVerifier
        assert result.dependencies.integrity_key is not None
        from self_evolution_workspace import SelfEvolutionWorkspace
        assert type(result.workspace) is SelfEvolutionWorkspace
        assert result.workspace._stable_repository == paths.agent_root
        assert result.workspace._trust_root == Path(inputs["host_config"].trust_root)
        assert result.workspace._trust_root_enrollment_path == result.storage_root / "trust-root-enrollment.json"
        assert result.workspace._allow_initial_enrollment is True
        assert len(result.dependencies.integrity_key) == 32
        assert result.key_path.is_file() and result.store_path.is_file()
        assert "production_store_safety_unavailable" in result.missing_reasons
        assert "candidate_creation_safety_gate_closed" in result.missing_reasons
        assert "full_candidate_coverage_unavailable" in result.missing_reasons
        assert "process_tree_containment_unavailable" in result.missing_reasons
        assert "host_production_gate_closed" in result.missing_reasons
        assert "trusted_review_disclosure_callback_unavailable" in result.missing_reasons
        assert "worker_start_disabled" in result.missing_reasons
        assert result.worker_start_enabled is False
        restarted = build_self_evolution_backend_dependencies(**_trusted_inputs(paths, paths.data / "self-evolution"))
        assert restarted.dependencies.integrity_key == result.dependencies.integrity_key
        assert restarted.store_path == result.store_path


def test_builder_rejects_missing_trusted_callbacks_before_creating_storage(tmp_path):
    from self_evolution_backend import SelfEvolutionBackendBuildError, build_self_evolution_backend_dependencies
    from tests.isolation_helpers import temporary_agent_paths

    with temporary_agent_paths() as paths:
        storage_root = paths.data / "must-not-create"
        inputs = _trusted_inputs(paths, storage_root.parent / "candidate-parent")
        inputs["storage_root"] = storage_root
        inputs["intent_detector"] = None

        with pytest.raises(SelfEvolutionBackendBuildError) as caught:
            build_self_evolution_backend_dependencies(**inputs)

        assert "backend_intent_detector_unavailable" in caught.value.reason_codes
        assert not storage_root.exists()


def test_builder_never_replaces_a_corrupt_key_or_opens_store(tmp_path):
    from self_evolution_backend import SelfEvolutionBackendBuildError, build_self_evolution_backend_dependencies
    from tests.isolation_helpers import temporary_agent_paths

    with temporary_agent_paths() as paths:
        storage_root = paths.data / "self-evolution"
        storage_root.mkdir()
        key_path = storage_root / "host-integrity-key.dpapi.json"
        original = b"corrupt protected key"
        key_path.write_bytes(original)

        with pytest.raises(SelfEvolutionBackendBuildError) as caught:
            build_self_evolution_backend_dependencies(**_trusted_inputs(paths, storage_root))

        assert caught.value.reason_codes == ("host_integrity_key_corrupt",)
        assert key_path.read_bytes() == original
        assert not (storage_root / "self-evolution.sqlite3").exists()


def test_builder_preserves_existing_corrupt_sqlite_instead_of_rebuilding(tmp_path):
    from self_evolution_backend import SelfEvolutionBackendBuildError, build_self_evolution_backend_dependencies
    from self_evolution_key import load_or_create_host_integrity_key
    from tests.isolation_helpers import temporary_agent_paths

    with temporary_agent_paths() as paths:
        storage_root = paths.data / "self-evolution"
        storage_root.mkdir()
        load_or_create_host_integrity_key(storage_root / "host-integrity-key.dpapi.json")
        database = storage_root / "self-evolution.sqlite3"
        original = b"not sqlite; preserve evidence"
        database.write_bytes(original)

        with pytest.raises(SelfEvolutionBackendBuildError) as caught:
            build_self_evolution_backend_dependencies(**_trusted_inputs(paths, storage_root))

        assert caught.value.reason_codes == ("persistent_store_initialization_failed",)
        assert database.read_bytes() == original


def test_builder_rejects_mismatched_or_ambiguous_trusted_host_configuration(tmp_path):
    from dataclasses import replace
    from self_evolution_backend import SelfEvolutionBackendBuildError, build_self_evolution_backend_dependencies
    from tests.isolation_helpers import temporary_agent_paths

    with temporary_agent_paths() as paths:
        inputs = _trusted_inputs(paths, paths.data / "self-evolution")
        foreign_stable = tmp_path / "foreign-stable"
        foreign_stable.mkdir()
        inputs["host_config"] = replace(inputs["host_config"], stable_repository=foreign_stable)
        with pytest.raises(SelfEvolutionBackendBuildError) as caught:
            build_self_evolution_backend_dependencies(**inputs)
        assert caught.value.reason_codes == ("backend_stable_repository_mismatch",)
        assert not (paths.data / "self-evolution").exists()




def test_experimental_storage_helper_needs_no_worker_callbacks_and_binds_fixed_paths():
    from self_evolution_backend import prepare_experimental_self_evolution_storage
    from self_evolution_workspace import SelfEvolutionWorkspace
    from tests.isolation_helpers import temporary_agent_paths

    with temporary_agent_paths() as paths:
        config = _approved_disposable_config(paths)
        result = prepare_experimental_self_evolution_storage(
            paths, config, allow_initial_trust_root_enrollment=True,
        )
        assert type(result.workspace) is SelfEvolutionWorkspace
        assert result.workspace._stable_repository == config.stable_repository
        assert result.workspace._trust_root == config.trust_root
        assert result.workspace._trust_root_enrollment_path == result.storage_root / "trust-root-enrollment.json"
        assert result.store.path == result.storage_root / "self-evolution.sqlite3"
        assert len(result.integrity_key) == 32


def test_experimental_storage_helper_rejects_host_root_mismatch_before_storage_creation():
    from dataclasses import replace
    from self_evolution_backend import SelfEvolutionBackendBuildError, prepare_experimental_self_evolution_storage
    from tests.isolation_helpers import temporary_agent_paths

    with temporary_agent_paths() as paths:
        config = _approved_disposable_config(paths)
        foreign = paths.agent_root.parent / f"foreign-agent-root-{paths.agent_root.name}"
        foreign.mkdir()
        bad_config = replace(config, stable_repository=foreign)
        with pytest.raises(SelfEvolutionBackendBuildError) as caught:
            prepare_experimental_self_evolution_storage(paths, bad_config)
        assert caught.value.reason_codes == ("experimental_target_not_standalone_git_repository",)
        assert not (paths.data / "self-evolution").exists()
