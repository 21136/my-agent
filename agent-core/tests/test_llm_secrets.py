"""LLM secrets persistence tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from llm_models import invalidate_registry_cache
from llm_secrets import (
    clear_llm_secret,
    get_llm_secret,
    mask_secret,
    set_llm_secret,
)
from tests.isolation_helpers import make_temp_agent_paths


class LlmSecretsTests(unittest.TestCase):
    def test_save_and_load_secret(self) -> None:
        paths = make_temp_agent_paths(self)
        set_llm_secret(paths,  "SOPHNET_API_KEY", "sk-test-secret-key")
        self.assertEqual(get_llm_secret("SOPHNET_API_KEY", paths), "sk-test-secret-key")
        invalidate_registry_cache()
        clear_llm_secret(paths,  "SOPHNET_API_KEY")
        self.assertIsNone(get_llm_secret("SOPHNET_API_KEY", paths))

    def test_mask_secret(self) -> None:
        self.assertEqual(mask_secret("short"), "****")
        self.assertTrue(mask_secret("abcdefghijklmnop").endswith("mnop"))


if __name__ == "__main__":
    unittest.main()
