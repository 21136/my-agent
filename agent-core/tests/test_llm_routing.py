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

    def test_explicit_session_model_beats_global_default(self) -> None:
        cases = (
            (SessionMeta(llm_model="deepseek-v4-pro", llm_model_override=True), "deepseek-v4-pro"),
            (SessionMeta(llm_model="deepseek-v4-pro", execution_model="deepseek-v4-flash"), "deepseek-v4-flash"),
        )
        for meta, expected in cases:
            with self.subTest(meta=meta):
                self.assertEqual(resolve_model_id_for_role("main_turn", meta, paths=self.paths), expected)

    def test_explicit_global_main_choice_beats_session_model(self) -> None:
        from llm_role_model_preferences import dispatch_llm_role_models_message

        dispatch_llm_role_models_message(self.paths, {
            "type": "llm_role_models.set", "role": "main", "model": "0x567-flash",
        })
        self.assertEqual(
            resolve_model_id_for_role(
                "main_turn", SessionMeta(llm_model="deepseek-v4-pro", llm_model_override=True),
                paths=self.paths,
            ),
            "0x567-flash",
        )

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

    def test_registered_main_session_model_does_not_leak_into_subagent_roles(self) -> None:
        from llm_role_model_preferences import DEFAULT_ROLE_MODELS

        # A registered model is valid for the main session, but that does not
        # make it an explicit subagent preference.
        meta = SessionMeta(execution_model="0x567-pro", llm_model="0x567-pro")
        for role in ("explore", "checker", "deliverable_review", "topic_routing"):
            with self.subTest(role=role):
                self.assertEqual(
                    resolve_model_id_for_role(role, meta, paths=self.paths),
                    DEFAULT_ROLE_MODELS["subagent"],
                )

    def test_resolved_subagent_model_reaches_transport_without_main_model_leak(self) -> None:
        from llm_client import LLMClient, LLMConfig, StreamHandlers

        class Response:
            status_code = 200

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

            def iter_lines(self):
                yield 'data: {"model":"gpt-6-luna","choices":[{"delta":{},"finish_reason":"stop"}]}'
                yield "data: [DONE]"

        class HttpClient:
            def __init__(self, captured):
                self.captured = captured

            def stream(self, method, url, headers=None, json=None):
                self.captured.update(method=method, url=url, json=json)
                return Response()

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

        captured = {}
        meta = SessionMeta(execution_model="0x567-pro", llm_model="0x567-pro")
        selected_id = resolve_model_id_for_role("explore", meta, paths=self.paths)
        config = LLMConfig(
            api_key=None, base_url="https://unused.invalid", model="0x567-flash",
            model_coding="0x567-pro", timeout_sec=1.0, context_limit_override=None,
        )
        client = LLMClient(config, paths=self.paths)
        with patch("llm_client.ModelEntry.resolve_api_key", return_value="synthetic-test-key"), patch(
            "llm_client.make_httpx_client", return_value=HttpClient(captured)
        ):
            client.chat(
                [{"role": "user", "content": "task"}],
                model=selected_id,
                stream=StreamHandlers(),
            )

        self.assertEqual(selected_id, "0x567-flash")
        self.assertEqual(captured["json"]["model"], "gpt-6-luna")

    def test_explicit_subagent_preference_overrides_main_session_model(self) -> None:
        from llm_role_model_preferences import dispatch_llm_role_models_message

        dispatch_llm_role_models_message(self.paths, {
            "type": "llm_role_models.set", "role": "subagent", "model": "0x567-pro",
        })
        meta = SessionMeta(execution_model="0x567-flash", llm_model="0x567-flash")
        for role in ("explore", "checker", "deliverable_review", "topic_routing"):
            with self.subTest(role=role):
                self.assertEqual(
                    resolve_model_id_for_role(role, meta, paths=self.paths),
                    "0x567-pro",
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
