"""Phase 42 Track J — per-role LLM model routing (LLM-ROUTING.md)."""

from __future__ import annotations

import os
from typing import Literal

from llm_models import ModelRegistry, get_registry
from session import SessionMeta

LlmRole = Literal[
    "main_turn",
    "plan_partner",
    "explore",
    "checker",
    "topic_routing",
    "evolve_checkpoint",
    "audit",
]

_ROLE_ENV: dict[LlmRole, tuple[str, ...]] = {
    "plan_partner": ("PLAN_PARTNER_MODEL", "PLAN_AGENT_MODEL"),
    "explore": ("SUBAGENT_EXPLORE_MODEL",),
    "checker": ("CHECKER_MODEL",),
    "topic_routing": ("PLAN_SPAWN_MODEL",),
}

_ROLE_DEFAULT_TIER: dict[LlmRole, str] = {
    "main_turn": "flash",
    "plan_partner": "pro",
    "explore": "flash",
    "checker": "flash",
    "topic_routing": "flash",
    "evolve_checkpoint": "flash",
    "audit": "pro",
}


def _meta_session_model(meta: SessionMeta) -> str:
    return (meta.execution_model or "").strip() or (meta.llm_model or "").strip()


def _resolve_raw_id(raw: str, registry: ModelRegistry) -> str | None:
    key = (raw or "").strip()
    if not key:
        return None
    entry = registry.resolve(key)
    return entry.id if entry is not None else key


def _env_override(role: LlmRole) -> str:
    for name in _ROLE_ENV.get(role, ()):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def _default_for_tier(tier: str, registry: ModelRegistry) -> str:
    if tier == "pro":
        return registry.default_pro_id
    return registry.default_flash_id


def resolve_model_id_for_role(
    role: LlmRole,
    meta: SessionMeta | None = None,
    *,
    registry: ModelRegistry | None = None,
) -> str:
    """Resolve registry model id for a harness role (J0–J5)."""
    reg = registry or get_registry()
    session_meta = meta or SessionMeta()

    env_raw = _env_override(role)
    if env_raw:
        resolved = _resolve_raw_id(env_raw, reg)
        if resolved:
            return resolved

    raw = ""
    if role == "main_turn":
        raw = _meta_session_model(session_meta)
    elif role == "plan_partner":
        raw = (session_meta.planning_model or "").strip()
    elif role in {"explore", "checker"}:
        raw = _meta_session_model(session_meta)
    elif role == "topic_routing":
        raw = _meta_session_model(session_meta)

    if raw:
        resolved = _resolve_raw_id(raw, reg)
        if resolved:
            return resolved

    return _default_for_tier(_ROLE_DEFAULT_TIER[role], reg)
