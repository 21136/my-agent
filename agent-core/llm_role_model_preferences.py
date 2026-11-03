"""Durable global model selections for the main and subagent roles."""
from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

from llm_models import get_registry
from paths import AgentPaths

DEFAULT_ROLE_MODELS = {"main": "0x567-pro", "subagent": "0x567-flash"}
_ROLE_KEYS = frozenset(DEFAULT_ROLE_MODELS)
_LOCK = threading.RLock()


class LlmRoleModelPreferenceError(ValueError):
    """Invalid or unavailable global role model preference."""


def _preference_path(paths: AgentPaths) -> Path:
    return paths.data / "llm_role_models.json"


def load_role_model_settings(paths: AgentPaths | None = None) -> dict[str, Any]:
    agent_paths = paths or AgentPaths.discover()
    target = _preference_path(agent_paths)
    with _LOCK:
        try:
            raw = target.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {"preferences": dict(DEFAULT_ROLE_MODELS), "explicit_roles": []}
        except OSError as exc:
            raise LlmRoleModelPreferenceError("无法读取全局模型选择") from exc
        try:
            payload = json.loads(raw)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise LlmRoleModelPreferenceError("全局模型选择文件无效") from exc

        preferences = payload.get("preferences") if type(payload) is dict else None
        explicit_roles = payload.get("explicit_roles") if type(payload) is dict else None
        if (
            type(payload) is not dict
            or payload.get("format") != 1
            or type(preferences) is not dict
            or set(preferences) != _ROLE_KEYS
            or any(type(preferences.get(role)) is not str or not preferences[role].strip() for role in _ROLE_KEYS)
            or type(explicit_roles) is not list
            or any(type(role) is not str or role not in _ROLE_KEYS for role in explicit_roles)
            or len(set(explicit_roles)) != len(explicit_roles)
        ):
            raise LlmRoleModelPreferenceError("全局模型选择文件无效")
        return {
            "preferences": {role: preferences[role] for role in DEFAULT_ROLE_MODELS},
            "explicit_roles": list(explicit_roles),
        }


def load_role_model_preferences(paths: AgentPaths | None = None) -> dict[str, str]:
    return load_role_model_settings(paths)["preferences"]


def _save_role_model_preferences(
    paths: AgentPaths,
    preferences: dict[str, str],
    explicit_roles: list[str],
) -> None:
    target = _preference_path(paths)
    payload = json.dumps(
        {"format": 1, "preferences": preferences, "explicit_roles": explicit_roles},
        ensure_ascii=False,
        indent=2,
    ) + "\n"
    temporary: str | None = None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", newline="\n", dir=target.parent,
            prefix=f".{target.name}.", suffix=".tmp", delete=False,
        ) as stream:
            temporary = stream.name
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        temporary = None
    except OSError as exc:
        raise LlmRoleModelPreferenceError("无法保存全局模型选择") from exc
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def _state_payload(
    paths: AgentPaths,
    *,
    status: str = "ok",
    message: str = "",
    event_type: str = "state",
) -> dict[str, Any]:
    return {
        "type": f"llm_role_models.{event_type}",
        "status": status,
        **load_role_model_settings(paths),
        "models": get_registry(paths).list_for_ui(paths),
        **({"message": message} if message else {}),
    }


def dispatch_llm_role_models_message(paths: AgentPaths, message: dict[str, Any]) -> list[dict[str, Any]]:
    msg_type = message.get("type")
    if msg_type == "llm_role_models.get":
        return [_state_payload(paths)]
    if msg_type != "llm_role_models.set":
        raise LlmRoleModelPreferenceError("未知的全局模型选择操作")

    role = message.get("role")
    model_id = message.get("model")
    if type(role) is not str or role not in _ROLE_KEYS or type(model_id) is not str or not model_id.strip():
        return [_state_payload(paths, status="rejected", message="角色或模型无效")]
    registry = get_registry(paths)
    entry = registry.resolve(model_id.strip())
    if entry is None:
        return [_state_payload(paths, status="rejected", message="所选模型不在当前模型目录中")]

    with _LOCK:
        current_settings: dict[str, Any] = {"preferences": dict(DEFAULT_ROLE_MODELS), "explicit_roles": []}
        try:
            current_settings = load_role_model_settings(paths)
            updated_preferences = dict(current_settings["preferences"])
            updated_preferences[role] = entry.id
            updated_explicit_roles = sorted(set(current_settings["explicit_roles"]) | {role})
            _save_role_model_preferences(paths, updated_preferences, updated_explicit_roles)
        except LlmRoleModelPreferenceError as exc:
            return [{
                "type": "llm_role_models.done",
                "status": "rejected",
                "message": str(exc),
                **current_settings,
                "models": registry.list_for_ui(paths),
            }]
    return [_state_payload(paths, event_type="done")]
