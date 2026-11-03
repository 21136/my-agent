"""Phase 42 Track J — per-role LLM model routing (LLM-ROUTING.md)."""

from __future__ import annotations

import os
from typing import Literal

from llm_models import ModelRegistry, get_registry
from session import SessionMeta

LlmRole = Literal[
    "main_turn",
    "explore",
    "checker",
    "deliverable_review",
    "topic_routing",
    "evolve_checkpoint",
    "audit",
]

_ROLE_ENV: dict[LlmRole, tuple[str, ...]] = {
    "explore": ("SUBAGENT_EXPLORE_MODEL",),
    "checker": ("CHECKER_MODEL",),
    "deliverable_review": ("DELIVERABLE_REVIEW_MODEL",),
    "topic_routing": ("PLAN_SPAWN_MODEL",),
}

_ROLE_DEFAULT_MODEL_ID: dict[LlmRole, str] = {
    "main_turn": "0x567-pro",
    "explore": "0x567-flash",
    "checker": "0x567-flash",
    "deliverable_review": "0x567-flash",
}

_ROLE_DEFAULT_TIER: dict[LlmRole, str] = {
    "main_turn": "flash",
    "explore": "flash",
    "checker": "flash",
    "deliverable_review": "flash",
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


def _default_for_role(role: LlmRole, registry: ModelRegistry) -> str:
    explicit_id = _ROLE_DEFAULT_MODEL_ID.get(role)
    if explicit_id:
        entry = registry.resolve(explicit_id)
        if entry is not None:
            return entry.id
    return _default_for_tier(_ROLE_DEFAULT_TIER[role], registry)


def resolve_model_id_for_role(
    role: LlmRole,
    meta: SessionMeta | None = None,
    *,
    registry: ModelRegistry | None = None,
    paths=None,
) -> str:
    """Resolve registry model id for a harness role (J0–J5)."""
    reg = registry or get_registry(paths)
    session_meta = meta or SessionMeta()

    preference_role = "main" if role == "main_turn" else (
        "subagent" if role in {"explore", "checker", "deliverable_review", "topic_routing"} else ""
    )
    role_settings = None
    if preference_role:
        from llm_role_model_preferences import load_role_model_settings

        role_settings = load_role_model_settings(paths)
        if preference_role in role_settings["explicit_roles"]:
            selected_entry = reg.resolve(role_settings["preferences"][preference_role])
            if selected_entry is not None:
                return selected_entry.id

    env_raw = _env_override(role)
    if env_raw:
        resolved = _resolve_raw_id(env_raw, reg)
        if resolved:
            return resolved

    if role_settings is not None:
        selected_entry = reg.resolve(role_settings["preferences"][preference_role])
        if selected_entry is not None:
            return selected_entry.id

    raw = ""
    if role == "main_turn":
        # The main role honors explicit execution and session model selections.
        # Auto-selected session defaults (override=False) do not pin every role.
        raw = (session_meta.execution_model or "").strip()
        if not raw and session_meta.llm_model_override:
            raw = (session_meta.llm_model or "").strip()
    elif role == "topic_routing":
        raw = _meta_session_model(session_meta)
    # Explore/checker/deliverable-review are independent roles: their explicit
    # role env override was checked above; otherwise use their role-specific default.

    if raw:
        resolved = _resolve_raw_id(raw, reg)
        if resolved:
            return resolved

    return _default_for_role(role, reg)
