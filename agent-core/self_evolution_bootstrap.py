"""Fail-closed assembly boundary for the Self-Evolution server integration.

This module never discovers paths, reads credentials, creates a store or starts
work. A trusted backend may explicitly assemble the dependency bundle; the Host
factory still refuses until every production safety prerequisite is available.
Renderer/model input is limited to raw text passed to the optional intent
proposal callback and can never supply Host capabilities.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class SelfEvolutionBootstrapDependencies:
    """Backend-owned dependencies for a future trusted server factory.

    Construct this only in trusted server code. In particular, do not deserialize
    this type or any of its callbacks/capabilities from renderer or model input.
    The integrity key and callbacks are supplied by the caller; optional Skill
    approval/provider callbacks are likewise backend-owned and are never inferred
    from outbound confirmation. This module never reads credentials or persistent
    state to find them.
    """

    host_config: Any = None
    integrity_key: bytes | None = field(default=None, repr=False)
    trusted_verifier_resolver: Any = field(default=None, repr=False)
    store: Any = field(default=None, repr=False)
    paths: Any = field(default=None, repr=False)
    session_meta: Any = field(default=None, repr=False)
    tools: Any = field(default=None, repr=False)
    outbound_request_confirm_fn: Any = field(default=None, repr=False)
    # Optional trusted installer policy/provider. Missing values disable only the
    # worker Skill-install capability; they are never inferred from other callbacks.
    skill_install_approval_fn: Callable[[dict[str, Any]], bool] | None = field(default=None, repr=False)
    skill_install_provider: Any = field(default=None, repr=False)
    process_tree_stopped: Any = field(default=None, repr=False)
    intent_detector: Any = field(default=None, repr=False)
    authorized_skill_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (type(self.authorized_skill_ids) is not tuple
                or any(type(item) is not str for item in self.authorized_skill_ids)):
            raise TypeError("authorized_skill_ids must be a trusted tuple of strings")
        if self.skill_install_approval_fn is not None and not callable(self.skill_install_approval_fn):
            raise TypeError("skill installation approval must be a trusted callable")
        if (self.skill_install_provider is not None
                and not callable(getattr(self.skill_install_provider, "fetch", None))):
            raise TypeError("Skill fetch provider must expose fetch(url, timeout, max_bytes)")


class SelfEvolutionBootstrapRefused(RuntimeError):
    """Sanitized refusal carrying stable, non-sensitive prerequisite codes."""

    error_code = "self_evolution_bootstrap_unavailable"

    def __init__(self, reason_codes: tuple[str, ...]) -> None:
        self.reason_codes = tuple(dict.fromkeys(reason_codes))
        super().__init__(self.error_code + ":" + ",".join(self.reason_codes))


@dataclass(frozen=True, slots=True)
class SelfEvolutionBootstrap:
    """Explicit assembly result intended for later run_server wiring.

    ``host_factory`` has no renderer/model arguments. It creates a Host only when
    all current production gates are independently available; today those gates
    intentionally remain closed. ``intent_detector`` only proposes a typed
    request and never authorizes a run or performs confirmation itself.
    """

    context_provider: Any | None
    intent_detector: Callable[[str], Any] | None
    missing_reasons: tuple[str, ...]
    _dependencies: SelfEvolutionBootstrapDependencies | None = field(default=None, repr=False)

    @property
    def enabled(self) -> bool:
        """Worker start is deliberately not enabled by bootstrap assembly."""
        return False

    def host_factory(self):
        """Create a trusted Host, or refuse after any Host/store side effects."""
        reasons = _missing_reasons(self._dependencies)
        if reasons:
            raise SelfEvolutionBootstrapRefused(reasons)

        # Local imports avoid touching model/project configuration at module import
        # and are reached only after the production readiness gates pass.
        from self_evolution_host import SelfEvolutionHost
        from self_evolution_model_adapter import build_trusted_worker_capabilities

        dependencies = self._dependencies
        assert dependencies is not None
        capabilities = build_trusted_worker_capabilities(
            dependencies.paths,
            dependencies.session_meta,
            tools=dependencies.tools,
            outbound_request_confirm_fn=dependencies.outbound_request_confirm_fn,
            process_tree_stopped=dependencies.process_tree_stopped,
            authorized_skill_ids=dependencies.authorized_skill_ids,
            skill_install_approval_fn=dependencies.skill_install_approval_fn,
            skill_install_provider=dependencies.skill_install_provider,
        )
        return SelfEvolutionHost(
            dependencies.host_config,
            integrity_key=dependencies.integrity_key,
            context_provider=self.context_provider,
            trusted_verifier_resolver=dependencies.trusted_verifier_resolver,
            store=dependencies.store,
            worker_capabilities=capabilities,
        )


def assemble_self_evolution_bootstrap(
    dependencies: SelfEvolutionBootstrapDependencies | None = None,
) -> SelfEvolutionBootstrap:
    """Assemble a disabled-by-default backend seam without starting I/O/work.

    Creating the context provider is process-local only. The detector is a lazy,
    bounded wrapper around an explicitly injected backend callable; no callback
    runs during assembly, and exceptions or untrusted output map to ``None``.
    """
    if dependencies is not None and type(dependencies) is not SelfEvolutionBootstrapDependencies:
        raise TypeError("trusted SelfEvolutionBootstrapDependencies are required")

    provider = None
    detector = None
    if dependencies is not None:
        from self_evolution_host import HostRunContextProvider

        provider = HostRunContextProvider()
        if callable(dependencies.intent_detector):
            detector = _bounded_intent_detector(dependencies.intent_detector)

    return SelfEvolutionBootstrap(
        context_provider=provider,
        intent_detector=detector,
        missing_reasons=_missing_reasons(dependencies),
        _dependencies=dependencies,
    )


def _missing_reasons(
    dependencies: SelfEvolutionBootstrapDependencies | None,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if dependencies is None:
        reasons.append("backend_dependencies_unavailable")
        values = None
    else:
        values = dependencies

    from self_evolution_host import SelfEvolutionHostConfig
    from self_evolution_store import EvolutionStore
    from self_evolution_worker import HostTool
    from paths import AgentPaths
    from session import SessionMeta

    if values is None or type(values.host_config) is not SelfEvolutionHostConfig:
        reasons.append("host_config_unavailable")
    if (values is None or not isinstance(values.integrity_key, bytes)
            or len(values.integrity_key) < 32):
        reasons.append("host_integrity_key_unavailable")
    if values is None or not callable(values.trusted_verifier_resolver):
        reasons.append("trusted_verifier_resolver_unavailable")
    if values is None or type(values.store) is not EvolutionStore:
        reasons.append("persistent_store_unavailable")
    else:
        # EvolutionStore currently has no production-ready lifecycle gate proving
        # ACL/SQLite-handle safety over the database, WAL/SHM and recovery period.
        reasons.append("production_store_safety_unavailable")
    if (values is None or not isinstance(values.paths, AgentPaths)
            or not isinstance(values.session_meta, SessionMeta)):
        reasons.append("project_model_configuration_unavailable")
    if (values is None or not isinstance(values.tools, tuple) or not values.tools
            or any(type(tool) is not HostTool for tool in values.tools)
            or len({tool.name for tool in values.tools}) != len(values.tools)):
        reasons.append("backend_worker_tools_unavailable")
    if values is None or not callable(values.outbound_request_confirm_fn):
        reasons.append("outbound_confirmation_source_unavailable")
    if values is None or not callable(values.process_tree_stopped):
        reasons.append("process_tree_safety_callback_unavailable")
    if values is None or not callable(values.intent_detector):
        reasons.append("backend_intent_detector_unavailable")

    # Read the actual source gates without constructing a Host, workspace, store,
    # model client or candidate. These gates are intentionally not override inputs.
    from self_evolution_workspace import _SAFE_CANDIDATE_CREATION_AVAILABLE

    if not _SAFE_CANDIDATE_CREATION_AVAILABLE:
        reasons.append("candidate_creation_safety_gate_closed")
    if not _class_gate("production_ready"):
        reasons.append("host_production_gate_closed")
    if not _class_gate("full_candidate_coverage_available"):
        reasons.append("full_candidate_coverage_unavailable")
    if not _class_gate("os_sandboxed"):
        reasons.append("process_tree_containment_unavailable")
    return tuple(dict.fromkeys(reasons))


def _class_gate(property_name: str) -> bool:
    """Evaluate a Host's source-owned property without constructing a Host."""
    try:
        from self_evolution_host import SelfEvolutionHost

        prop = getattr(SelfEvolutionHost, property_name)
        return prop.fget(None) is True
    except Exception:
        return False


def _bounded_intent_detector(detector: Callable[[str], Any]) -> Callable[[str], Any]:
    def detect(raw_text: str):
        from self_evolution_types import EvolutionRequest

        if type(raw_text) is not str or len(raw_text) > 8192:
            return None
        try:
            request = detector(raw_text)
        except Exception:
            return None
        if (type(request) is not EvolutionRequest or request.context_refs
                or len(request.user_goal) > 8192 or len(request.acceptance_points) > 32
                or any(len(point) > 2048 for point in request.acceptance_points)):
            return None
        try:
            encoded_size = len((raw_text + request.user_goal + "".join(request.acceptance_points)).encode("utf-8"))
        except UnicodeError:
            return None
        return request if encoded_size <= 32768 else None

    return detect




def create_experimental_host_context_provider(*, enabled: bool = False):
    """Create the process-local Host context provider only for explicit opt-in.

    This does not enable production readiness or infer self-evolution intent; the
    existing registered function-call tool remains the sole start route.
    """
    if type(enabled) is not bool:
        raise TypeError("experimental profile switch must be boolean")
    if not enabled:
        return None
    import os
    if os.name != "nt":
        raise SelfEvolutionBootstrapRefused(("windows_experimental_profile_required",))
    from self_evolution_host import HostRunContextProvider
    return HostRunContextProvider()
