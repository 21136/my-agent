"""The image-generation key is configurable through existing local key settings."""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from llm_models_api import dispatch_llm_keys_message, llm_keys_state_payload
from llm_secrets import allowed_secret_envs, get_llm_secret, set_llm_secret
from tests.isolation_helpers import make_temp_agent_paths


class ImageGenerationKeySettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.paths = make_temp_agent_paths(self)

    def test_key_slot_is_visible_masked_and_accepted_by_existing_settings_api(self) -> None:
        env_name = "LLM_0x567_KEY"
        self.assertIn(env_name, allowed_secret_envs(self.paths))
        before = llm_keys_state_payload(self.paths)
        image_slot = next(item for item in before["keys"] if item["env"] == env_name)
        self.assertEqual(image_slot["label"], "0x567 图片生成")
        self.assertFalse(image_slot["configured"])

        result = dispatch_llm_keys_message(
            self.paths,
            {"type": "llm_keys.set", "env": env_name, "value": "image-secret-test-value"},
        )
        state = next(item for item in result if item["type"] == "llm_keys.state")
        image_slot = next(item for item in state["keys"] if item["env"] == env_name)
        self.assertTrue(image_slot["configured"])
        self.assertEqual(image_slot["source"], "file")
        self.assertEqual(image_slot["masked"], "imag…alue")
        self.assertNotIn("image-secret-test-value", json.dumps(state))
        self.assertEqual(get_llm_secret(env_name, self.paths), "image-secret-test-value")

    def test_environment_key_takes_precedence_and_is_masked(self) -> None:
        env_name = "LLM_0x567_KEY"
        set_llm_secret(self.paths, env_name, "stored-image-secret-value")
        with patch.dict(os.environ, {env_name: "environment-image-secret"}):
            state = llm_keys_state_payload(self.paths)
        image_slot = next(item for item in state["keys"] if item["env"] == env_name)
        self.assertEqual(image_slot["source"], "env")
        self.assertEqual(image_slot["masked"], "envi…cret")
        self.assertNotIn("environment-image-secret", json.dumps(state))

    def test_existing_key_clear_action_removes_image_key(self) -> None:
        env_name = "LLM_0x567_KEY"
        set_llm_secret(self.paths, env_name, "clear-me-image-key")
        dispatch_llm_keys_message(
            self.paths,
            {"type": "llm_keys.clear", "env": env_name},
        )
        self.assertIsNone(get_llm_secret(env_name, self.paths))


if __name__ == "__main__":
    unittest.main()
