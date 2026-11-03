from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from llm_client import DEFAULT_MODEL_CODING
from llm_models import get_registry
from llm_routing import resolve_model_id_for_role
from session import SessionMeta
from tests.isolation_helpers import make_temp_agent_paths


class LlmRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.paths = make_temp_agent_paths(self)

    def test_main_turn_uses_global_pro_default(self) -> None:
        self.assertEqual(
            resolve_model_id_for_role("main_turn", SessionMeta(), paths=self.paths),
            "0x567-pro",
        )

    def test_global_main_default_overrides_legacy_session_selections(self) -> None:
        cases = (
            SessionMeta(llm_model="deepseek-v4-pro", llm_model_override=True),
            SessionMeta(llm_model="deepseek-v4-pro", execution_model="deepseek-v4-flash"),
        )
        for meta in cases:
            with self.subTest(meta=meta):
                self.assertEqual(resolve_model_id_for_role("main_turn", meta, paths=self.paths), "0x567-pro")

    def test_subagent_environment_override_applies_until_global_choice_is_explicit(self) -> None:
        with patch.dict(os.environ, {"SUBAGENT_EXPLORE_MODEL": "deepseek-v4-pro"}, clear=False):
            self.assertEqual(
                resolve_model_id_for_role("explore", SessionMeta(), paths=self.paths),
                DEFAULT_MODEL_CODING,
            )

    def test_checker_environment_override(self) -> None:
        with patch.dict(os.environ, {"CHECKER_MODEL": "deepseek-v4-pro"}, clear=False):
            self.assertEqual(
                resolve_model_id_for_role("checker", SessionMeta(), paths=self.paths),
                DEFAULT_MODEL_CODING,
            )

    def test_non_global_roles_keep_their_existing_defaults(self) -> None:
        registry = get_registry(self.paths)
        self.assertEqual(
            resolve_model_id_for_role("audit", SessionMeta(), paths=self.paths),
            registry.default_pro_id,
        )
        self.assertEqual(
            resolve_model_id_for_role("evolve_checkpoint", SessionMeta(), paths=self.paths),
            registry.default_flash_id,
        )


if __name__ == "__main__":
    unittest.main()
