"""Persist LLM API keys under agent data/ (desktop UI · not committed)."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

_AGENT_CORE = Path(__file__).resolve().parent
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from paths import AgentPaths

SECRETS_FILENAME = "llm_secrets.json"
SECRETS_VERSION = 1


class LlmSecretsError(Exception):
    """Invalid llm_secrets operation."""


def secrets_file(paths: AgentPaths) -> Path:
    return paths.data / SECRETS_FILENAME


def mask_secret(value: str) -> str:
    text = value.strip()
    if not text:
        return ""
    if len(text) <= 8:
        return "****"
    return f"{text[:4]}…{text[-4:]}"


def _read_payload(paths: AgentPaths) -> dict[str, Any]:
    path = secrets_file(paths)
    if not path.is_file():
        return {"version": SECRETS_VERSION, "keys": {}}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": SECRETS_VERSION, "keys": {}}
    if not isinstance(loaded, dict):
        return {"version": SECRETS_VERSION, "keys": {}}
    keys_raw = loaded.get("keys", {})
    keys: dict[str, str] = {}
    if isinstance(keys_raw, dict):
        for name, value in keys_raw.items():
            if isinstance(name, str) and isinstance(value, str) and value.strip():
                keys[name.strip()] = value.strip()
    version = loaded.get("version", SECRETS_VERSION)
    try:
        version_int = int(version)
    except (TypeError, ValueError):
        version_int = SECRETS_VERSION
    return {"version": version_int, "keys": keys}


def _write_payload(paths: AgentPaths, payload: dict[str, Any]) -> None:
    paths.data.mkdir(parents=True, exist_ok=True)
    path = secrets_file(paths)
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=paths.data,
            delete=False,
            suffix=".tmp",
        ) as tmp:
            json.dump(payload, tmp, ensure_ascii=False, indent=2)
            tmp.write("\n")
            tmp_path = Path(tmp.name)
        tmp_path.replace(path)
    finally:
        if tmp_path is not None and tmp_path.is_file() and not path.samefile(tmp_path):
            try:
                tmp_path.unlink()
            except OSError:
                pass


def load_secrets(paths: AgentPaths) -> dict[str, str]:
    return dict(_read_payload(paths)["keys"])


def get_llm_secret(env_name: str, paths: AgentPaths | None = None) -> str | None:
    key = env_name.strip()
    if not key:
        return None
    agent_paths = paths or AgentPaths.discover()
    value = load_secrets(agent_paths).get(key)
    return value if value else None


def resolve_secret_source(env_name: str, paths: AgentPaths) -> str | None:
    """Return ``env`` | ``file`` when configured, else ``None``."""
    key = env_name.strip()
    if not key:
        return None
    if os.environ.get(key, "").strip():
        return "env"
    if get_llm_secret(key, paths):
        return "file"
    return None


def set_llm_secret(paths: AgentPaths, env_name: str, value: str) -> None:
    key = env_name.strip()
    if not key:
        raise LlmSecretsError("env name is required")
    if not key.replace("_", "").isalnum():
        raise LlmSecretsError(f"invalid env name: {key!r}")
    cleaned = value.strip()
    if not cleaned:
        raise LlmSecretsError("API key cannot be empty")
    payload = _read_payload(paths)
    keys = dict(payload["keys"])
    keys[key] = cleaned
    payload["keys"] = keys
    payload["version"] = SECRETS_VERSION
    _write_payload(paths, payload)


def clear_llm_secret(paths: AgentPaths, env_name: str) -> None:
    key = env_name.strip()
    if not key:
        raise LlmSecretsError("env name is required")
    payload = _read_payload(paths)
    keys = dict(payload["keys"])
    keys.pop(key, None)
    payload["keys"] = keys
    payload["version"] = SECRETS_VERSION
    _write_payload(paths, payload)


def allowed_secret_envs(paths: AgentPaths) -> frozenset[str]:
    from llm_models import get_registry

    registry = get_registry(paths)
    envs: set[str] = set()
    for entry in registry.models:
        if entry.api_key_env.strip():
            envs.add(entry.api_key_env.strip())
    return frozenset(envs)
