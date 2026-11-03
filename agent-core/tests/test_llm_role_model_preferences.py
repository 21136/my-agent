from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from llm_role_model_preferences import (
    DEFAULT_ROLE_MODELS,
    LlmRoleModelPreferenceError,
    dispatch_llm_role_models_message,
    load_role_model_preferences,
)
from llm_models import get_registry
from llm_routing import resolve_model_id_for_role
from session import SessionMeta
from tests.isolation_helpers import make_temp_agent_paths


class LlmRoleModelPreferenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.paths = make_temp_agent_paths(self)

    def _set(self, role: str, model: str) -> dict[str, object]:
        return dispatch_llm_role_models_message(self.paths, {
            "type": "llm_role_models.set", "role": role, "model": model,
        })[0]

    def test_defaults_and_global_changes_apply_to_existing_session_calls(self) -> None:
        session = SessionMeta(llm_model="legacy-model", llm_model_override=True)
        self.assertEqual(load_role_model_preferences(self.paths), DEFAULT_ROLE_MODELS)
        self.assertEqual(resolve_model_id_for_role("main_turn", session, paths=self.paths), "0x567-pro")
        for role in ("explore", "checker", "deliverable_review", "topic_routing"):
            with self.subTest(role=role):
                self.assertEqual(resolve_model_id_for_role(role, session, paths=self.paths), "0x567-flash")

        self.assertEqual(self._set("main", "0x567-flash")["status"], "ok")
        self.assertEqual(resolve_model_id_for_role("main_turn", session, paths=self.paths), "0x567-flash")
        self.assertEqual(self._set("subagent", "0x567-pro")["status"], "ok")
        for role in ("explore", "checker", "deliverable_review", "topic_routing"):
            with self.subTest(role=role):
                self.assertEqual(resolve_model_id_for_role(role, session, paths=self.paths), "0x567-pro")

    def test_explicit_choice_beats_legacy_env_and_is_persisted(self) -> None:
        self._set("subagent", "0x567-pro")
        with patch.dict("os.environ", {"SUBAGENT_EXPLORE_MODEL": "0x567-flash"}, clear=False):
            self.assertEqual(resolve_model_id_for_role("explore", SessionMeta(), paths=self.paths), "0x567-pro")
        stored = json.loads((self.paths.data / "llm_role_models.json").read_text(encoding="utf-8"))
        self.assertEqual(stored["preferences"]["subagent"], "0x567-pro")
        self.assertEqual(stored["explicit_roles"], ["subagent"])

    def test_invalid_model_is_rejected_without_changing_preferences(self) -> None:
        self._set("main", "0x567-flash")
        result = self._set("main", "not-a-catalog-model")
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(load_role_model_preferences(self.paths)["main"], "0x567-flash")

    def test_corrupt_preferences_reject_updates(self) -> None:
        target = self.paths.data / "llm_role_models.json"
        target.write_text("{broken", encoding="utf-8")
        result = self._set("main", "0x567-flash")
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["preferences"], DEFAULT_ROLE_MODELS)

    def test_failed_persistence_does_not_change_the_saved_preference(self) -> None:
        self._set("main", "0x567-pro")
        with patch(
            "llm_role_model_preferences._save_role_model_preferences",
            side_effect=LlmRoleModelPreferenceError("cannot persist"),
        ):
            result = self._set("main", "0x567-flash")
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(load_role_model_preferences(self.paths)["main"], "0x567-pro")

    def test_unrelated_roles_keep_their_existing_route(self) -> None:
        self._set("main", "0x567-flash")
        self._set("subagent", "0x567-pro")
        self.assertEqual(resolve_model_id_for_role("audit", SessionMeta(), paths=self.paths), get_registry(self.paths).default_pro_id)
        self.assertEqual(resolve_model_id_for_role("evolve_checkpoint", SessionMeta(), paths=self.paths), get_registry(self.paths).default_flash_id)


if __name__ == "__main__":
    unittest.main()
