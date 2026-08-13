"""LLM model registry — multi-provider profiles (flash / pro / vendor-specific)."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

_AGENT_CORE = Path(__file__).resolve().parent
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from paths import AgentPaths

ModelTier = Literal["flash", "pro"]

CHAT_COMPLETIONS_SUFFIX = "/v1/chat/completions"
DEFAULT_FLASH_ID = "deepseek-v4-flash"
DEFAULT_PRO_ID = "deepseek-v4-pro"


@dataclass(frozen=True, slots=True)
class ModelEntry:
    """One callable model profile (registry id ≠ provider ``model`` field)."""

    id: str
    name: str
    vendor: str
    base_url: str
    provider_model: str
    api_key_env: str = ""
    api_key: str = ""
    max_input_tokens: int = 128_000
    max_output_tokens: int = 8192
    supports_tool_call: bool = True
    tier: ModelTier = "flash"
    aliases: tuple[str, ...] = ()

    def resolve_api_key(self, paths: AgentPaths | None = None) -> str | None:
        if self.api_key_env:
            value = os.environ.get(self.api_key_env)
            if value and value.strip():
                return value.strip()
            from llm_secrets import get_llm_secret

            stored = get_llm_secret(self.api_key_env, paths)
            if stored:
                return stored
        if self.api_key and self.api_key.strip():
            return self.api_key.strip()
        return None

    def chat_completions_url(self) -> str:
        base = self.base_url.rstrip("/")
        if base.endswith(CHAT_COMPLETIONS_SUFFIX):
            return base
        return f"{base}{CHAT_COMPLETIONS_SUFFIX}"


@dataclass(frozen=True, slots=True)
class ModelRegistry:
    models: tuple[ModelEntry, ...]
    default_flash_id: str
    default_pro_id: str
    alias_map: dict[str, str]

    def get(self, model_id: str) -> ModelEntry | None:
        key = model_id.strip()
        if not key:
            return None
        for entry in self.models:
            if entry.id == key:
                return entry
        return None

    def resolve(self, raw: str) -> ModelEntry | None:
        key = raw.strip()
        if not key:
            return None
        exact = self.get(key)
        if exact is not None:
            return exact
        alias = self.alias_map.get(key.casefold().replace("_", "-"))
        if alias:
            return self.get(alias)
        return None

    def list_for_ui(self, paths: AgentPaths | None = None) -> list[dict[str, Any]]:
        agent_paths = paths
        return [
            {
                "id": entry.id,
                "name": entry.name,
                "vendor": entry.vendor,
                "tier": entry.tier,
                "max_input_tokens": entry.max_input_tokens,
                "supports_tool_call": entry.supports_tool_call,
                "api_key_env": entry.api_key_env,
                "configured": entry.resolve_api_key(agent_paths) is not None,
            }
            for entry in self.models
        ]

    @classmethod
    def load(cls, paths: AgentPaths | None = None) -> ModelRegistry:
        agent_paths = paths or AgentPaths.discover()
        user_path = agent_paths.data / "llm_models.json"
        user_models: list[dict[str, Any]] = []
        json_default_flash: str | None = None
        json_default_pro: str | None = None
        if user_path.is_file():
            try:
                payload = json.loads(user_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                payload = {}
            if isinstance(payload, dict):
                raw_models = payload.get("models", [])
                if isinstance(raw_models, list):
                    user_models = [item for item in raw_models if isinstance(item, dict)]
                json_default_flash = _optional_str(
                    payload.get("defaultFlashId", payload.get("default_flash_id"))
                )
                json_default_pro = _optional_str(
                    payload.get("defaultProId", payload.get("default_pro_id"))
                )
        entries = _merge_model_entries(_builtin_models(), user_models)
        default_flash = _env_default_id(
            "LLM_MODEL",
            json_default_flash or DEFAULT_FLASH_ID,
        )
        default_pro = _env_default_id(
            "LLM_MODEL_CODING",
            json_default_pro or DEFAULT_PRO_ID,
        )
        alias_map = _build_alias_map(entries, default_flash, default_pro)
        if default_flash not in {entry.id for entry in entries}:
            default_flash = DEFAULT_FLASH_ID
        if default_pro not in {entry.id for entry in entries}:
            default_pro = DEFAULT_PRO_ID
        return cls(
            models=tuple(entries),
            default_flash_id=default_flash,
            default_pro_id=default_pro,
            alias_map=alias_map,
        )


def get_registry(paths: AgentPaths | None = None) -> ModelRegistry:
    if paths is not None:
        return ModelRegistry.load(paths)
    return _cached_registry_for_discovered()


@lru_cache(maxsize=1)
def _cached_registry_for_discovered() -> ModelRegistry:
    return ModelRegistry.load()


def invalidate_registry_cache() -> None:
    _cached_registry_for_discovered.cache_clear()


def models_list_event(paths: AgentPaths | None = None) -> dict[str, Any]:
    agent_paths = paths or AgentPaths.discover()
    registry = get_registry(agent_paths)
    return {
        "type": "session.models",
        "models": registry.list_for_ui(agent_paths),
        "default_flash_id": registry.default_flash_id,
        "default_pro_id": registry.default_pro_id,
    }


def _env_default_id(env_name: str, fallback: str) -> str:
    raw = os.environ.get(env_name)
    if raw and raw.strip():
        return raw.strip()
    return fallback


def _optional_str(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _builtin_models() -> list[ModelEntry]:
    deepseek_base = os.environ.get("LLM_BASE_URL", "https://api.deepseek.com").rstrip("/")
    if deepseek_base.endswith(CHAT_COMPLETIONS_SUFFIX):
        deepseek_base = deepseek_base[:-len(CHAT_COMPLETIONS_SUFFIX)]
    ox567_base = os.environ.get("OX567_BASE_URL", "https://api-cdn.0x567.com").rstrip("/")
    if ox567_base.endswith(CHAT_COMPLETIONS_SUFFIX):
        ox567_base = ox567_base[:-len(CHAT_COMPLETIONS_SUFFIX)]
    ox567_pro_model = (
        os.environ.get("OX567_MODEL_PRO")
        or os.environ.get("OX567_MODEL")
        or "gpt-5.4"
    ).strip()
    ox567_flash_model = os.environ.get("OX567_MODEL_FLASH", "gpt-5.6-luna").strip()
    return [
        ModelEntry(
            id=DEFAULT_FLASH_ID,
            name="DeepSeek Flash",
            vendor="DeepSeek",
            base_url=deepseek_base,
            provider_model=os.environ.get("LLM_MODEL", DEFAULT_FLASH_ID),
            api_key_env="LLM_API_KEY",
            max_input_tokens=128_000,
            tier="flash",
            aliases=("flash", "v4-flash"),
        ),
        ModelEntry(
            id=DEFAULT_PRO_ID,
            name="DeepSeek Pro",
            vendor="DeepSeek",
            base_url=deepseek_base,
            provider_model=os.environ.get("LLM_MODEL_CODING", DEFAULT_PRO_ID),
            api_key_env="LLM_API_KEY",
            max_input_tokens=1_000_000,
            tier="pro",
            aliases=("pro", "v4-pro"),
        ),
        ModelEntry(
            id="sophnet-deepseek-v4-flash",
            name="DeepSeek-V4-Flash",
            vendor="Sophnet",
            base_url="https://www.sophnet.com/api/open-apis",
            provider_model="DeepSeek-V4-Flash",
            api_key_env="SOPHNET_API_KEY",
            max_input_tokens=128_000,
            tier="flash",
            aliases=("sophnet", "sophnet-flash"),
        ),
        ModelEntry(
            id="sophnet-deepseek-v4-pro",
            name="DeepSeek-V4-Pro",
            vendor="Sophnet",
            base_url="https://www.sophnet.com/api/open-apis",
            provider_model="DeepSeek-V4-Pro",
            api_key_env="SOPHNET_API_KEY",
            max_input_tokens=1_000_000,
            tier="pro",
            aliases=("sophnet-pro"),
        ),
        ModelEntry(
            id="0x567-pro",
            name="0x567 GPT-5.4 (1M)",
            vendor="0x567",
            base_url=ox567_base,
            provider_model=ox567_pro_model,
            api_key_env="OX567_API_KEY",
            max_input_tokens=1_000_000,
            max_output_tokens=65_536,
            tier="pro",
            aliases=("0x567", "567-pro", "567"),
        ),
        ModelEntry(
            id="0x567-flash",
            name="0x567 Luna (372k)",
            vendor="0x567",
            base_url=ox567_base,
            provider_model=ox567_flash_model,
            api_key_env="OX567_API_KEY",
            max_input_tokens=372_000,
            tier="flash",
            aliases=("567-flash", "luna"),
        ),
    ]


def _merge_model_entries(
    builtin: list[ModelEntry],
    user_models: list[dict[str, Any]],
) -> list[ModelEntry]:
    by_id: dict[str, ModelEntry] = {entry.id: entry for entry in builtin}
    for raw in user_models:
        entry = _parse_model_entry(raw)
        if entry is not None:
            by_id[entry.id] = entry
    return list(by_id.values())


def _parse_model_entry(raw: dict[str, Any]) -> ModelEntry | None:
    registry_id = raw.get("registryId") or raw.get("registry_id")
    provider_model = raw.get("modelId") or raw.get("model_id")
    if not isinstance(registry_id, str) or not registry_id.strip():
        # Legacy/user shorthand: id = provider model when registryId omitted
        legacy_id = raw.get("id")
        if isinstance(legacy_id, str) and legacy_id.strip():
            registry_id = _slug_registry_id(raw.get("vendor"), legacy_id)
            provider_model = legacy_id.strip()
        else:
            return None
    else:
        registry_id = registry_id.strip()
        if not isinstance(provider_model, str) or not provider_model.strip():
            legacy_id = raw.get("id")
            provider_model = legacy_id if isinstance(legacy_id, str) else registry_id
    provider_model = str(provider_model).strip()

    name = raw.get("name")
    vendor = raw.get("vendor")
    if not isinstance(name, str) or not name.strip():
        name = registry_id
    if not isinstance(vendor, str) or not vendor.strip():
        vendor = "custom"

    base_url = raw.get("baseUrl") or raw.get("base_url") or raw.get("url") or ""
    if not isinstance(base_url, str) or not base_url.strip():
        return None
    base_url = _normalize_base_url(base_url.strip())

    api_key_env = raw.get("apiKeyEnv") or raw.get("api_key_env") or ""
    if not isinstance(api_key_env, str):
        api_key_env = ""
    api_key = raw.get("apiKey") or raw.get("api_key") or ""
    if not isinstance(api_key, str):
        api_key = ""

    tier_raw = raw.get("tier", "flash")
    tier: ModelTier = "pro" if str(tier_raw).casefold() == "pro" else "flash"

    aliases_raw = raw.get("aliases", [])
    aliases: list[str] = []
    if isinstance(aliases_raw, list):
        aliases = [str(item).strip() for item in aliases_raw if str(item).strip()]

    max_input = _int_field(raw, "maxInputTokens", "max_input_tokens", 128_000)
    max_output = _int_field(raw, "maxOutputTokens", "max_output_tokens", 8192)
    supports_tool_call = bool(raw.get("supportsToolCall", raw.get("supports_tool_call", True)))

    return ModelEntry(
        id=registry_id,
        name=name.strip(),
        vendor=vendor.strip(),
        base_url=base_url,
        provider_model=provider_model,
        api_key_env=api_key_env.strip(),
        api_key=api_key.strip(),
        max_input_tokens=max_input,
        max_output_tokens=max_output,
        supports_tool_call=supports_tool_call,
        tier=tier,
        aliases=tuple(aliases),
    )


def _slug_registry_id(vendor: Any, model_name: str) -> str:
    vendor_part = ""
    if isinstance(vendor, str) and vendor.strip():
        vendor_part = vendor.strip().casefold().replace(" ", "-")
    model_part = model_name.strip().casefold().replace(" ", "-").replace("_", "-")
    if vendor_part:
        return f"{vendor_part}-{model_part}"
    return model_part


def _normalize_base_url(url: str) -> str:
    if url.endswith(CHAT_COMPLETIONS_SUFFIX):
        return url[:-len(CHAT_COMPLETIONS_SUFFIX)]
    return url.rstrip("/")


def _int_field(raw: dict[str, Any], camel: str, snake: str, default: int) -> int:
    value = raw.get(camel, raw.get(snake, default))
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _build_alias_map(
    entries: list[ModelEntry],
    default_flash_id: str,
    default_pro_id: str,
) -> dict[str, str]:
    alias_map: dict[str, str] = {}
    for entry in entries:
        for alias in entry.aliases:
            alias_map[alias.casefold().replace("_", "-")] = entry.id
    occupied = {entry.id.casefold().replace("_", "-") for entry in entries}
    for entry in entries:
        provider_key = entry.provider_model.casefold().replace("_", "-")
        if provider_key in occupied:
            continue
        if provider_key in alias_map:
            continue
        alias_map[provider_key] = entry.id
    alias_map["flash"] = default_flash_id
    alias_map["v4-flash"] = default_flash_id
    alias_map["pro"] = default_pro_id
    alias_map["v4-pro"] = default_pro_id
    return alias_map
