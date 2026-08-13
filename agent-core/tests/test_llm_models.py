"""LLM model registry tests."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from llm_models import ModelRegistry, get_registry
from tests.isolation_helpers import make_temp_agent_paths


class LlmModelRegistryTests(unittest.TestCase):
    def test_builtin_includes_sophnet(self) -> None:
        registry = get_registry()
        flash = registry.get("sophnet-deepseek-v4-flash")
        self.assertIsNotNone(flash)
        assert flash is not None
        self.assertEqual(flash.provider_model, "DeepSeek-V4-Flash")
        self.assertEqual(
            flash.chat_completions_url(),
            "https://www.sophnet.com/api/open-apis/v1/chat/completions",
        )
        pro = registry.get("sophnet-deepseek-v4-pro")
        self.assertIsNotNone(pro)
        assert pro is not None
        self.assertEqual(pro.provider_model, "DeepSeek-V4-Pro")
        self.assertEqual(pro.api_key_env, "SOPHNET_API_KEY")
        self.assertEqual(flash.api_key_env, "SOPHNET_API_KEY")

    def test_builtin_includes_0x567(self) -> None:
        registry = get_registry()
        pro = registry.get("0x567-pro")
        self.assertIsNotNone(pro)
        assert pro is not None
        self.assertEqual(pro.max_input_tokens, 1_000_000)
        self.assertEqual(
            pro.chat_completions_url(),
            "https://api-cdn.0x567.com/v1/chat/completions",
        )
        self.assertEqual(pro.api_key_env, "OX567_API_KEY")
        self.assertEqual(pro.provider_model, "gpt-5.4")
        flash = registry.get("0x567-flash")
        self.assertIsNotNone(flash)
        assert flash is not None
        self.assertEqual(flash.provider_model, "gpt-5.6-luna")
        self.assertEqual(flash.max_input_tokens, 372_000)
        resolved = registry.resolve("0x567")
        self.assertIsNotNone(resolved)
        assert resolved is not None
        self.assertEqual(resolved.id, "0x567-pro")

    def test_user_json_overrides_model(self) -> None:
        paths = make_temp_agent_paths(self)
        paths.data.mkdir(parents=True, exist_ok=True)
        (paths.data / "llm_models.json").write_text(
            json.dumps(
                {
                    "models": [
                        {
                            "registryId": "custom-flash",
                            "name": "Custom Flash",
                            "vendor": "Test",
                            "apiKeyEnv": "CUSTOM_API_KEY",
                            "baseUrl": "https://example.com/api",
                            "modelId": "custom-model-v1",
                            "tier": "flash",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        registry = ModelRegistry.load(paths)
        entry = registry.get("custom-flash")
        self.assertIsNotNone(entry)
        assert entry is not None
        self.assertEqual(entry.provider_model, "custom-model-v1")
        resolved = registry.resolve("custom-flash")
        self.assertIsNotNone(resolved)
        assert resolved is not None
        self.assertEqual(resolved.id, "custom-flash")
        self.assertIsNone(registry.resolve("gpt-4"))

    def test_context_limit_from_registry(self) -> None:
        paths = make_temp_agent_paths(self)
        paths.data.mkdir(parents=True, exist_ok=True)
        (paths.data / "llm_models.json").write_text(
            json.dumps(
                {
                    "models": [
                        {
                            "registryId": "tiny-flash",
                            "name": "Tiny",
                            "vendor": "Test",
                            "apiKeyEnv": "CUSTOM_API_KEY",
                            "baseUrl": "https://example.com",
                            "modelId": "tiny",
                            "maxInputTokens": 4096,
                            "tier": "flash",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        registry = ModelRegistry.load(paths)
        entry = registry.get("tiny-flash")
        self.assertIsNotNone(entry)
        assert entry is not None
        self.assertEqual(entry.max_input_tokens, 4096)

    def test_luna_context_limit(self) -> None:
        from llm_client import resolve_context_limit

        self.assertEqual(resolve_context_limit("0x567-flash"), 372_000)
        self.assertEqual(resolve_context_limit("gpt-5.6-luna"), 372_000)
        self.assertEqual(resolve_context_limit("luna"), 372_000)

    def test_user_json_default_model_ids(self) -> None:
        paths = make_temp_agent_paths(self)
        paths.data.mkdir(parents=True, exist_ok=True)
        (paths.data / "llm_models.json").write_text(
            json.dumps(
                {
                    "defaultFlashId": "0x567-flash",
                    "defaultProId": "0x567-pro",
                    "models": [],
                }
            ),
            encoding="utf-8",
        )
        registry = ModelRegistry.load(paths)
        self.assertEqual(registry.default_flash_id, "0x567-flash")
        self.assertEqual(registry.default_pro_id, "0x567-pro")


if __name__ == "__main__":
    unittest.main()
