"""Project-configured LLM adapter for the dedicated self-evolution worker.

The adapter only selects an existing project model profile and constructs the
project's normal LLMClient. Outbound confirmation, candidate rebinding,
tool authorization, and cancellation remain enforced by the worker and client.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Callable, Mapping

from llm_client import LLMClient
from llm_models import get_registry
from llm_routing import resolve_model_id_for_role
from paths import AgentPaths
from self_evolution_worker import (
    DEFAULT_MAX_REQUESTS,
    MAX_REQUESTS,
    HostTool,
    SelfEvolutionWorkerContext,
    SelfEvolutionWorkerResult,
    run_self_evolution_worker,
)
from session import SessionMeta


@dataclass(frozen=True, slots=True)
class SelfEvolutionModelAdapter:
    """A dedicated project-path-bound LLM client and routed registry model."""

    client: LLMClient
    model_id: str


def create_self_evolution_model_adapter(
    paths: AgentPaths,
    session_meta: SessionMeta,
) -> SelfEvolutionModelAdapter:
    """Build an adapter using the configured subagent role and project paths.

    No credentials are loaded here and no request is made. The existing
    ``LLMClient.chat_confirmed_tools`` resolves the registry profile and secret
    only after its per-request confirmation and scope checks.
    """
    if not isinstance(paths, AgentPaths) or not isinstance(session_meta, SessionMeta):
        raise TypeError("project paths and session metadata are required")
    model_id = resolve_model_id_for_role("explore", session_meta, paths=paths)
    entry = get_registry(paths).resolve(model_id)
    if entry is None or entry.id != model_id or not entry.supports_tool_call:
        raise ValueError("configured self-evolution model is unavailable for tool calls")
    # Never accept an injected client: LLMClient stores cancellation and active
    # transport state on the instance, so sharing the chat client would let a
    # dedicated worker's cancellation replace the main turn's cancellation.
    selected_client = LLMClient(paths=paths)
    if selected_client.paths != paths:
        raise ValueError("dedicated self-evolution client must use the project paths")
    return SelfEvolutionModelAdapter(client=selected_client, model_id=model_id)


def run_self_evolution_worker_with_project_model(
    task: str,
    *,
    context: SelfEvolutionWorkerContext,
    tools: tuple[HostTool, ...],
    paths: AgentPaths,
    session_meta: SessionMeta,
    max_requests: int = DEFAULT_MAX_REQUESTS,
    on_event: Callable[[str, Mapping[str, Any]], None] | None = None,
) -> SelfEvolutionWorkerResult:
    """Run the worker through the real project client using configured routing."""
    if type(context) is not SelfEvolutionWorkerContext:
        raise TypeError("trusted self-evolution worker context is required")
    adapter = create_self_evolution_model_adapter(paths, session_meta)
    routed_context = replace(context, model_id=adapter.model_id)
    return run_self_evolution_worker(
        task,
        client=adapter.client,
        context=routed_context,
        tools=tools,
        max_requests=max_requests,
        on_event=on_event,
    )

def build_trusted_worker_capabilities(
    paths: AgentPaths,
    session_meta: SessionMeta,
    *,
    tools: tuple[HostTool, ...],
    outbound_request_confirm_fn: Callable[[str, bool], str],
    process_tree_stopped: Callable[[Any], bool],
    authorized_skill_ids: tuple[str, ...] = (),
    skill_install_approval_fn: Callable[[dict[str, Any]], bool] | None = None,
    skill_install_provider: Any | None = None,
    max_requests: int = DEFAULT_MAX_REQUESTS,
):
    """Assemble a Host capability bundle from backend-owned dependencies.

    This callable is intended for trusted server/Host factories only. It has no
    renderer-facing inputs. The optional Skill approval callback and fetch provider
    are copied only when explicitly supplied by the trusted backend; missing approval
    never falls back to outbound-request confirmation. Each call creates a fresh
    LLMClient, and experimental worker start remains disabled.
    """
    from self_evolution_host import TrustedWorkerCapabilities

    if (not isinstance(paths, AgentPaths) or not isinstance(session_meta, SessionMeta)
            or not isinstance(tools, tuple) or not tools
            or any(type(tool) is not HostTool for tool in tools)
            or not callable(outbound_request_confirm_fn)
            or not callable(process_tree_stopped)
            or type(authorized_skill_ids) is not tuple
            or any(type(item) is not str for item in authorized_skill_ids)
            or (skill_install_approval_fn is not None and not callable(skill_install_approval_fn))
            or (skill_install_provider is not None
                and not callable(getattr(skill_install_provider, "fetch", None)))
            or type(max_requests) is not int or not 1 <= max_requests <= MAX_REQUESTS):
        raise TypeError("trusted project worker dependencies are required")
    adapter = create_self_evolution_model_adapter(paths, session_meta)
    from skill_runtime import normalize_host_authorized_skill_ids

    skills_root = paths.evolve / "skills"
    granted_skill_ids = normalize_host_authorized_skill_ids(skills_root, authorized_skill_ids)
    return TrustedWorkerCapabilities(
        client=adapter.client,
        model_id=adapter.model_id,
        outbound_request_confirm_fn=outbound_request_confirm_fn,
        tools=tools,
        process_tree_stopped=process_tree_stopped,
        max_requests=max_requests,
        skills_root=skills_root,
        authorized_skill_ids=granted_skill_ids,
        skill_install_approval_fn=skill_install_approval_fn,
        skill_install_provider=skill_install_provider,
        # Production start remains explicitly disabled. A controlled test may
        # replace this frozen field on a disposable Host fixture only.
        enable_experimental_start=False,
    )
