"""WebSocket API for LLM API key management (desktop model settings)."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

_AGENT_CORE = Path(__file__).resolve().parent
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from llm_models import get_registry, invalidate_registry_cache, models_list_event
from llm_secrets import (
    LlmSecretsError,
    allowed_secret_envs,
    clear_llm_secret,
    get_llm_secret,
    mask_secret,
    resolve_secret_source,
    set_llm_secret,
)
from paths import AgentPaths


def _slot_label(env: str, model_names: list[str]) -> str:
    if model_names:
        return "、".join(model_names)
    return env


def llm_keys_state_payload(paths: AgentPaths) -> dict[str, Any]:
    registry = get_registry(paths)
    slots: dict[str, dict[str, Any]] = {}
    for entry in registry.models:
        env = entry.api_key_env.strip()
        if not env:
            continue
        slot = slots.setdefault(env, {"env": env, "models": []})
        slot["models"].append(entry.name)

    keys: list[dict[str, Any]] = []
    for env in sorted(slots):
        slot = slots[env]
        model_names = list(dict.fromkeys(slot["models"]))
        source = resolve_secret_source(env, paths)
        raw = get_llm_secret(env, paths)
        if source == "env":
            raw = os.environ.get(env, "").strip() or raw
        keys.append(
            {
                "env": env,
                "label": _slot_label(env, model_names),
                "models": model_names,
                "configured": source is not None,
                "masked": mask_secret(raw) if raw else None,
                "source": source,
            }
        )

    return {"type": "llm_keys.state", "keys": keys}


def dispatch_llm_keys_message(
    paths: AgentPaths,
    message: dict[str, Any],
) -> list[dict[str, Any]]:
    msg_type = message.get("type")
    if not isinstance(msg_type, str):
        raise LlmSecretsError("message type is required")

    if msg_type == "llm_keys.list":
        return [llm_keys_state_payload(paths)]

    allowed = allowed_secret_envs(paths)

    if msg_type == "llm_keys.set":
        env = message.get("env")
        value = message.get("value")
        if not isinstance(env, str) or not env.strip():
            raise LlmSecretsError("llm_keys.set requires env")
        if not isinstance(value, str):
            raise LlmSecretsError("llm_keys.set requires value")
        env_key = env.strip()
        if env_key not in allowed:
            raise LlmSecretsError(f"unsupported secret env: {env_key!r}")
        set_llm_secret(paths,  env_key, value)
        invalidate_registry_cache()
        return [
            {"type": "llm_keys.updated"},
            llm_keys_state_payload(paths),
            models_list_event(paths),
        ]

    if msg_type == "llm_keys.clear":
        env = message.get("env")
        if not isinstance(env, str) or not env.strip():
            raise LlmSecretsError("llm_keys.clear requires env")
        env_key = env.strip()
        if env_key not in allowed:
            raise LlmSecretsError(f"unsupported secret env: {env_key!r}")
        clear_llm_secret(paths,  env_key)
        invalidate_registry_cache()
        return [
            {"type": "llm_keys.updated"},
            llm_keys_state_payload(paths),
            models_list_event(paths),
        ]

    raise LlmSecretsError(f"unknown llm_keys message type: {msg_type!r}")
