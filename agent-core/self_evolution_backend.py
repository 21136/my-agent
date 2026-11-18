"""Trusted acquisition of Self-Evolution backend dependencies for a Host caller.

The builder performs only the dependencies with real local providers: a Windows
current-user DPAPI key, a root-bound native ACL verifier, and an initialized
EvolutionStore. It requires every Host/model/approval/semantic/process capability
to be supplied by trusted caller code. Static Host/store gates remain authoritative.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from paths import AgentPaths
from self_evolution_bootstrap import (
    SelfEvolutionBootstrapDependencies,
    assemble_self_evolution_bootstrap,
)
from self_evolution_host import SelfEvolutionHostConfig
from self_evolution_key import (
    HostIntegrityKeyCorrupt,
    HostIntegrityKeyUnavailable,
    load_or_create_host_integrity_key,
)
from self_evolution_store import EvolutionStore, StoreError
from self_evolution_workspace import SelfEvolutionWorkspace
from self_evolution_windows_acl import (
    PrivateStorageAclVerifier,
    WindowsAclError,
    create_private_storage_acl_verifier,
)
from self_evolution_worker import HostTool
from session import SessionMeta


class SelfEvolutionBackendBuildError(RuntimeError):
    """Sanitized refusal with stable reason codes and no private values."""

    def __init__(self, *reason_codes: str) -> None:
        self.reason_codes = tuple(dict.fromkeys(reason_codes))
        super().__init__("self_evolution_backend_build_refused:" + ",".join(self.reason_codes))


@dataclass(frozen=True, slots=True)
class SelfEvolutionExperimentalStorage:
    """Trusted, initialized storage primitives; this does not enable a Host."""

    store: EvolutionStore = field(repr=False)
    integrity_key: bytes = field(repr=False)
    workspace: SelfEvolutionWorkspace = field(repr=False)
    acl_verifier: PrivateStorageAclVerifier = field(repr=False)
    storage_root: Path
    key_path: Path = field(repr=False)


def prepare_experimental_self_evolution_storage(
    paths: AgentPaths,
    host_config: SelfEvolutionHostConfig,
    *,
    allow_initial_trust_root_enrollment: bool = False,
) -> SelfEvolutionExperimentalStorage:
    """Public, callback-free preparation for an explicitly approved target."""
    return _prepare_experimental_self_evolution_storage(
        paths, host_config, storage_root=None,
        allow_initial_trust_root_enrollment=allow_initial_trust_root_enrollment,
        require_disposable_target=True,
    )


def _prepare_experimental_self_evolution_storage(
    paths: AgentPaths,
    host_config: SelfEvolutionHostConfig,
    *,
    storage_root: str | os.PathLike[str] | None,
    allow_initial_trust_root_enrollment: bool,
    require_disposable_target: bool,
) -> SelfEvolutionExperimentalStorage:
    """Prepare real DPAPI/SQLite/workspace data from fixed trusted config only.

    Deliberately does not require Host tools, callbacks, intent detection, model
    access, or a verifier resolver. It never starts a worker or changes Host
    production readiness. The storage override is bounded under ``paths.data``;
    repository and candidate paths come only from the immutable Host config.
    """
    if type(paths) is not AgentPaths:
        raise SelfEvolutionBackendBuildError("backend_agent_paths_unavailable")
    if type(host_config) is not SelfEvolutionHostConfig:
        raise SelfEvolutionBackendBuildError("host_config_unavailable")
    if type(allow_initial_trust_root_enrollment) is not bool:
        raise SelfEvolutionBackendBuildError("initial_enrollment_policy_invalid")
    try:
        data_root = Path(paths.data).resolve(strict=True)
        configured_stable = Path(host_config.stable_repository)
        _reject_existing_reparse_path(configured_stable)
        stable = configured_stable.resolve(strict=True)
        agent_root = Path(paths.agent_root).resolve(strict=True)
        requested_root = (Path(storage_root) if storage_root is not None
                          else data_root / "self-evolution")
        if not requested_root.is_absolute():
            raise SelfEvolutionBackendBuildError("backend_storage_root_not_absolute")
        canonical_root = Path(os.path.abspath(requested_root))
        relative = canonical_root.relative_to(data_root)
        if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
            raise SelfEvolutionBackendBuildError("backend_storage_root_invalid")
        _reject_existing_reparse_components(data_root, canonical_root)
        trusted_root = Path(host_config.trust_root)
        if not trusted_root.is_absolute():
            raise SelfEvolutionBackendBuildError("backend_trust_root_not_absolute")
        trusted_root = Path(os.path.abspath(trusted_root))
        _reject_existing_reparse_path(trusted_root)
        if require_disposable_target:
            _validate_disposable_target(stable, agent_root, canonical_root, trusted_root)
        elif stable != agent_root:
            raise SelfEvolutionBackendBuildError("backend_stable_repository_mismatch")
        trusted_root.mkdir(parents=True, exist_ok=True)
    except SelfEvolutionBackendBuildError:
        raise
    except (OSError, RuntimeError, ValueError, TypeError) as exc:
        raise SelfEvolutionBackendBuildError("backend_storage_root_invalid") from exc

    key_path = canonical_root / "host-integrity-key.dpapi.json"
    try:
        integrity_key = load_or_create_host_integrity_key(key_path)
        verifier = create_private_storage_acl_verifier(canonical_root.resolve(strict=True))
        key_proof = verifier.audit(key_path)
        if key_proof.expected_type != "file":
            raise WindowsAclError("Host key path is not a regular file")
    except HostIntegrityKeyCorrupt as exc:
        raise SelfEvolutionBackendBuildError("host_integrity_key_corrupt") from exc
    except HostIntegrityKeyUnavailable as exc:
        raise SelfEvolutionBackendBuildError("host_integrity_key_unavailable") from exc
    except WindowsAclError as exc:
        raise SelfEvolutionBackendBuildError("private_storage_acl_unavailable") from exc
    except Exception as exc:
        raise SelfEvolutionBackendBuildError("private_storage_initialization_failed") from exc

    try:
        store = EvolutionStore(canonical_root, trusted_acl_verifier=verifier)
        root_proof = store.audit_private_storage_path(canonical_root)
        database_proof = store.audit_private_storage_path(store.path)
        if (root_proof.expected_type != "directory" or database_proof.expected_type != "file"
                or root_proof.root_identity != verifier.root_identity
                or database_proof.root_identity != verifier.root_identity):
            raise StoreError("private storage proof binding mismatch")
    except Exception as exc:
        raise SelfEvolutionBackendBuildError("persistent_store_initialization_failed") from exc

    try:
        workspace = SelfEvolutionWorkspace(
            host_config.stable_repository, host_config.trust_root, integrity_key,
            enrollment_path=canonical_root / "trust-root-enrollment.json",
            allow_initial_enrollment=allow_initial_trust_root_enrollment,
        )
    except Exception as exc:
        raise SelfEvolutionBackendBuildError("candidate_workspace_data_prep_failed") from exc
    return SelfEvolutionExperimentalStorage(
        store=store, integrity_key=integrity_key, workspace=workspace,
        acl_verifier=verifier, storage_root=canonical_root, key_path=key_path,
    )


@dataclass(frozen=True, slots=True)
class SelfEvolutionBackendBuildResult:
    """Real initialized dependencies plus remaining, source-owned Host blockers."""

    dependencies: SelfEvolutionBootstrapDependencies = field(repr=False)
    acl_verifier: PrivateStorageAclVerifier = field(repr=False)
    storage_root: Path
    key_path: Path = field(repr=False)
    store_path: Path
    workspace: SelfEvolutionWorkspace = field(repr=False)
    missing_reasons: tuple[str, ...]
    worker_start_enabled: bool = False


def build_self_evolution_backend_dependencies(
    *,
    paths: AgentPaths,
    host_config: SelfEvolutionHostConfig,
    session_meta: SessionMeta,
    trusted_verifier_resolver: Callable[..., Any],
    tools: tuple[HostTool, ...],
    outbound_request_confirm_fn: Callable[[str, bool], str],
    process_tree_stopped: Callable[[Any], bool],
    intent_detector: Callable[[str], Any],
    storage_root: str | os.PathLike[str] | None = None,
    authorized_skill_ids: tuple[str, ...] = (),
    skill_install_approval_fn: Callable[[dict[str, Any]], bool] | None = None,
    skill_install_provider: Any | None = None,
    allow_initial_trust_root_enrollment: bool = False,
) -> SelfEvolutionBackendBuildResult:
    """Build actual persistent backend dependencies without authorizing a run.

    The caller must pass the active trusted AgentPaths, SessionMeta and fixed
    Host policy, plus its session/run-bound confirmation, semantic detector,
    fixed verifier resolver, HostTool closures and process-tree proof. No values
    are inferred from renderer/model input and no callbacks are fabricated.

    The store root defaults to ``paths.data/self-evolution`` and is required to
    remain beneath that AgentPaths data root. The key is protected by current-user
    DPAPI and shares a native ACL verifier with the initialized SQLite store. A
    damaged key/database is preserved and returned as a refusal; it is never reset.
    ``missing_reasons`` reports remaining static production gates. A nonempty list
    means callers must not construct or enable a Host.
    """
    reasons = _validate_trusted_inputs(
        paths=paths, host_config=host_config, session_meta=session_meta,
        trusted_verifier_resolver=trusted_verifier_resolver, tools=tools,
        outbound_request_confirm_fn=outbound_request_confirm_fn,
        process_tree_stopped=process_tree_stopped, intent_detector=intent_detector,
        authorized_skill_ids=authorized_skill_ids,
        skill_install_approval_fn=skill_install_approval_fn,
        skill_install_provider=skill_install_provider,
    )
    if reasons:
        raise SelfEvolutionBackendBuildError(*reasons)

    experimental_storage = _prepare_experimental_self_evolution_storage(
        paths, host_config, storage_root=storage_root,
        allow_initial_trust_root_enrollment=allow_initial_trust_root_enrollment,
        require_disposable_target=False,
    )
    integrity_key = experimental_storage.integrity_key
    verifier = experimental_storage.acl_verifier
    store = experimental_storage.store
    workspace = experimental_storage.workspace
    canonical_root = experimental_storage.storage_root
    key_path = experimental_storage.key_path

    dependencies = SelfEvolutionBootstrapDependencies(
        host_config=host_config,
        integrity_key=integrity_key,
        trusted_verifier_resolver=trusted_verifier_resolver,
        store=store,
        paths=paths,
        session_meta=session_meta,
        tools=tools,
        outbound_request_confirm_fn=outbound_request_confirm_fn,
        skill_install_approval_fn=skill_install_approval_fn,
        skill_install_provider=skill_install_provider,
        process_tree_stopped=process_tree_stopped,
        intent_detector=intent_detector,
        authorized_skill_ids=authorized_skill_ids,
    )
    try:
        bootstrap = assemble_self_evolution_bootstrap(dependencies)
    except Exception as exc:
        raise SelfEvolutionBackendBuildError("backend_dependency_validation_failed") from exc
    blockers = tuple(dict.fromkeys((
        *bootstrap.missing_reasons,
        "worker_start_disabled",
        "trusted_review_disclosure_callback_unavailable",
    )))
    return SelfEvolutionBackendBuildResult(
        dependencies=dependencies,
        acl_verifier=verifier,
        storage_root=canonical_root,
        key_path=key_path,
        store_path=store.path,
        workspace=workspace,
        missing_reasons=blockers,
        worker_start_enabled=False,
    )



_EXPERIMENTAL_TARGET_MARKER = ".self-evolution-experimental-target.json"
_EXPERIMENTAL_TARGET_SCHEMA = "self-evolution-experimental-target.v1"


def _validate_disposable_target(stable: Path, agent_root: Path, storage_root: Path, trust_root: Path) -> None:
    """Bind only an explicitly marked, standalone disposable repository."""
    if stable == agent_root or agent_root in stable.parents or stable in agent_root.parents:
        raise SelfEvolutionBackendBuildError("experimental_target_overlaps_application_root")
    for other, reason in ((storage_root, "experimental_target_overlaps_private_storage"),
                          (trust_root, "experimental_target_overlaps_candidate_root")):
        if stable == other or other in stable.parents or stable in other.parents:
            raise SelfEvolutionBackendBuildError(reason)
    if agent_root == trust_root or agent_root in trust_root.parents or trust_root in agent_root.parents:
        raise SelfEvolutionBackendBuildError("candidate_root_overlaps_application_root")
    if storage_root == trust_root or storage_root in trust_root.parents or trust_root in storage_root.parents:
        raise SelfEvolutionBackendBuildError("candidate_root_overlaps_private_storage")
    try:
        _reject_existing_reparse_path(stable)
        git_dir = stable / ".git"
        info = git_dir.lstat()
        if git_dir.is_symlink() or not git_dir.is_dir() or bool(getattr(info, "st_file_attributes", 0) & 0x400):
            raise SelfEvolutionBackendBuildError("experimental_target_not_standalone_git_repository")
        for name, expected_directory in (("HEAD", False), ("config", False), ("objects", True), ("refs", True)):
            component = git_dir / name
            component_info = component.lstat()
            if (component.is_symlink()
                    or bool(getattr(component_info, "st_file_attributes", 0) & 0x400)
                    or component.is_dir() is not expected_directory):
                raise SelfEvolutionBackendBuildError("experimental_target_not_standalone_git_repository")
        marker = stable / _EXPERIMENTAL_TARGET_MARKER
        marker_info = marker.lstat()
        if marker.is_symlink() or not marker.is_file() or marker_info.st_size > 4096:
            raise SelfEvolutionBackendBuildError("experimental_target_marker_invalid")
        payload = json.loads(marker.read_text(encoding="utf-8"))
        expected = {
            "schema": _EXPERIMENTAL_TARGET_SCHEMA,
            "purpose": "self_evolution_disposable_repository",
            "repository_root": str(stable),
        }
        if type(payload) is not dict or payload != expected:
            raise SelfEvolutionBackendBuildError("experimental_target_marker_mismatch")
    except SelfEvolutionBackendBuildError:
        raise
    except (OSError, RuntimeError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise SelfEvolutionBackendBuildError("experimental_target_not_standalone_git_repository") from exc


def _validate_trusted_inputs(
    *, paths: AgentPaths, host_config: SelfEvolutionHostConfig,
    session_meta: SessionMeta, trusted_verifier_resolver: Any, tools: Any,
    outbound_request_confirm_fn: Any, process_tree_stopped: Any,
    intent_detector: Any, authorized_skill_ids: Any,
    skill_install_approval_fn: Any, skill_install_provider: Any,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if type(paths) is not AgentPaths:
        reasons.append("backend_agent_paths_unavailable")
    if type(host_config) is not SelfEvolutionHostConfig:
        reasons.append("host_config_unavailable")
    elif type(paths) is AgentPaths:
        try:
            stable = Path(host_config.stable_repository).resolve(strict=True)
            agent_root = Path(paths.agent_root).resolve(strict=True)
            if stable != agent_root:
                reasons.append("backend_stable_repository_mismatch")
        except (OSError, RuntimeError, ValueError):
            reasons.append("backend_host_path_unavailable")
    if type(session_meta) is not SessionMeta:
        reasons.append("backend_session_metadata_unavailable")
    if not callable(trusted_verifier_resolver):
        reasons.append("trusted_verifier_resolver_unavailable")
    if (type(tools) is not tuple or not tools
            or any(type(tool) is not HostTool for tool in tools)
            or len({tool.name for tool in tools if type(tool) is HostTool}) != len(tools)):
        reasons.append("backend_worker_tools_unavailable")
    if not callable(outbound_request_confirm_fn):
        reasons.append("outbound_confirmation_source_unavailable")
    if not callable(process_tree_stopped):
        reasons.append("process_tree_safety_callback_unavailable")
    if not callable(intent_detector):
        reasons.append("backend_intent_detector_unavailable")
    if (type(authorized_skill_ids) is not tuple
            or any(type(skill_id) is not str for skill_id in authorized_skill_ids)):
        reasons.append("authorized_skill_ids_invalid")
    if skill_install_approval_fn is not None and not callable(skill_install_approval_fn):
        reasons.append("skill_install_approval_invalid")
    if (skill_install_provider is not None
            and not callable(getattr(skill_install_provider, "fetch", None))):
        reasons.append("skill_install_provider_invalid")
    return tuple(dict.fromkeys(reasons))


def _reject_existing_reparse_path(target_root: Path) -> None:
    """Reject pre-existing linked components for an explicitly trusted path."""
    import stat

    absolute = Path(os.path.abspath(target_root))
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            continue
        if current.is_symlink() or bool(getattr(info, "st_file_attributes", 0) & 0x400):
            raise SelfEvolutionBackendBuildError("backend_trust_root_reparse_point")
        if current != absolute and not stat.S_ISDIR(info.st_mode):
            raise SelfEvolutionBackendBuildError("backend_trust_root_component_invalid")


def _reject_existing_reparse_components(data_root: Path, target_root: Path) -> None:
    """Reject linked roots before the key module is allowed to create anything."""
    import stat

    current = data_root
    info = current.lstat()
    if current.is_symlink() or bool(getattr(info, "st_file_attributes", 0) & 0x400):
        raise SelfEvolutionBackendBuildError("backend_data_root_reparse_point")
    relative = target_root.relative_to(data_root)
    for part in relative.parts:
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            continue
        if (current.is_symlink()
                or bool(getattr(info, "st_file_attributes", 0) & 0x400)):
            raise SelfEvolutionBackendBuildError("backend_storage_root_reparse_point")
        if current != target_root and not stat.S_ISDIR(info.st_mode):
            raise SelfEvolutionBackendBuildError("backend_storage_root_component_invalid")
