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
IMAGE_GENERATION_KEY_ENV = "LLM_0x567_KEY"


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



def stable_product_root_for_sandbox(agent_root: Path) -> Path | None:
    """If *agent_root* is the registered dual-lane sandbox worktree, return stable root.

    Layout: ``<stable>/.worktrees/sandbox``. Secrets stay per-workspace, but the
    sandbox sidecar should still be able to read LLM keys already saved on stable
    when its own ``data/llm_secrets.json`` is missing that key.
    """
    root = Path(agent_root).resolve()
    try:
        if root.name.casefold() != "sandbox":
            return None
        if root.parent.name.casefold() != ".worktrees":
            return None
        stable = root.parent.parent
        from paths import _is_agent_root

        if not _is_agent_root(stable):
            return None
        return stable
    except OSError:
        return None


def get_llm_secret(env_name: str, paths: AgentPaths | None = None) -> str | None:
    key = env_name.strip()
    if not key:
        return None
    agent_paths = paths or AgentPaths.discover()
    value = load_secrets(agent_paths).get(key)
    if value:
        return value
    stable = stable_product_root_for_sandbox(agent_paths.agent_root)
    if stable is None:
        return None
    # Read-only fallback: never invent a second store for writes.
    fallback = load_secrets(AgentPaths.from_root(stable)).get(key)
    return fallback if fallback else None


def resolve_secret_source(env_name: str, paths: AgentPaths) -> str | None:
    """Return ``env`` | ``file`` when configured, else ``None``."""
    key = env_name.strip()
    if not key:
        return None
    if os.environ.get(key, "").strip():
        return "env"
    # Local file first, then dual-lane stable fallback (same as get_llm_secret).
    if load_secrets(paths).get(key):
        return "file"
    stable = stable_product_root_for_sandbox(paths.agent_root)
    if stable is not None and load_secrets(AgentPaths.from_root(stable)).get(key):
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
    envs.add(IMAGE_GENERATION_KEY_ENV)
    return frozenset(envs)
