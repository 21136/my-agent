"""Instance path propagation for the LLM client and Agent factory."""

from __future__ import annotations

import inspect
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

import agent as agent_module
import llm_client as llm_client_module
from agent import Agent
from llm_client import LLMClient, LLMConfig, StreamHandlers
from llm_models import ModelEntry


class _StreamResponse:
    status_code = 200

    def __init__(self, model: str) -> None:
        self.model = model

    def __enter__(self):
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def iter_lines(self):
        yield f'data: {{"model":"{self.model}","choices":[{{"delta":{{}},"finish_reason":"stop"}}]}}'
        yield "data: [DONE]"


class _HttpClient:
    def __init__(self, captured: dict[str, object]) -> None:
        self.captured = captured

    def stream(self, method, url, headers=None, json=None):
        self.captured.update(method=method, url=url, headers=headers, json=json)
        return _StreamResponse(str(json["model"]))

    def __enter__(self):
        return self

    def __exit__(self, *args: object) -> None:
        return None


class LlmClientInstancePathTests(unittest.TestCase):
    @staticmethod
    def _config() -> LLMConfig:
        return LLMConfig(
            api_key=None,
            base_url="https://unused.invalid",
            model="0x567-flash",
            model_coding="0x567-pro",
            timeout_sec=1.0,
            context_limit_override=None,
        )

    @staticmethod
    def _registry(entry: ModelEntry):
        class Registry:
            default_flash_id = entry.id

            @staticmethod
            def resolve(model_id: str) -> ModelEntry | None:
                return entry if model_id == entry.id else None

        return Registry()

    @staticmethod
    def _entry() -> ModelEntry:
        return ModelEntry(
            id="0x567-flash",
            name="synthetic 0x567 flash",
            vendor="0x567",
            base_url="https://provider.invalid",
            provider_model="gpt-6-luna",
            api_key_env="OX567_API_KEY",
            supports_tool_call=True,
        )

    def _chat_with_registry(self, client: LLMClient, entry: ModelEntry, paths=...):
        captured: dict[str, object] = {}
        registry = self._registry(entry)
        registry_patch = patch("llm_client.get_registry", return_value=registry)
        resolver_patch = patch.object(ModelEntry, "resolve_api_key", autospec=True, return_value="synthetic-test-key")
        http_patch = patch(
            "llm_client.make_httpx_client",
            return_value=_HttpClient(captured),
        )
        with registry_patch as registry_mock, resolver_patch as resolver_mock, http_patch:
            client.chat(
                [{"role": "user", "content": "synthetic"}],
                model=entry.id,
                stream=StreamHandlers(),
            )
        return captured, registry_mock, resolver_mock

    def test_constructor_exposes_optional_paths_keyword(self) -> None:
        self.assertIn("paths", inspect.signature(LLMClient).parameters)

    def test_supplied_paths_reach_registry_and_key_resolver(self) -> None:
        paths = object()
        entry = self._entry()
        client = LLMClient(self._config(), paths=paths)

        captured, registry_mock, resolver_mock = self._chat_with_registry(client, entry)

        registry_mock.assert_called_once_with(paths)
        resolver_mock.assert_called_once_with(entry, paths)
        payload = captured["json"]
        self.assertIsInstance(payload, dict)
        self.assertEqual(payload["model"], "gpt-6-luna")
        self.assertEqual(captured["url"], "https://provider.invalid/v1/chat/completions")

    def test_no_path_preserves_zero_argument_registry_and_resolver_calls(self) -> None:
        entry = self._entry()
        client = LLMClient(self._config())

        _captured, registry_mock, resolver_mock = self._chat_with_registry(client, entry)

        registry_mock.assert_called_once_with()
        resolver_mock.assert_called_once_with(entry)

    def _create_agent(self, paths, injected_llm=None):
        executor_session = SimpleNamespace(
            evolution_id="",
            evolution_authorization_id="",
            evolution_stop_authorization_id="",
            evolution_stop_instance_id="",
        )
        executor = SimpleNamespace(session=executor_session)
        session = SimpleNamespace(
            paths=paths,
            session_dir=Path("synthetic-session"),
            meta=SimpleNamespace(evolution_id="", turn_mode="agent"),
        )
        with (
            patch.object(agent_module.ToolRegistry, "load", return_value=object()),
            patch.object(agent_module, "session_evolved_allowlist", return_value=()),
            patch.object(agent_module.ToolExecutor, "create", return_value=executor),
            patch.object(agent_module, "LLMClient") as client_factory,
        ):
            created = Agent.create(session, llm=injected_llm)
        return created, client_factory

    def test_agent_default_client_receives_session_paths(self) -> None:
        paths = object()

        created, client_factory = self._create_agent(paths)

        client_factory.assert_called_once_with(paths=paths)
        self.assertIs(created.llm, client_factory.return_value)

    def test_agent_preserves_falsey_injected_client(self) -> None:
        class FalseyClient:
            def __bool__(self) -> bool:
                return False

        injected = FalseyClient()
        created, client_factory = self._create_agent(object(), injected_llm=injected)

        client_factory.assert_not_called()
        self.assertIs(created.llm, injected)

    def test_agent_preserves_injected_client_without_constructing_default(self) -> None:
        paths = object()
        injected = object()

        created, client_factory = self._create_agent(paths, injected_llm=injected)

        client_factory.assert_not_called()
        self.assertIs(created.llm, injected)


if __name__ == "__main__":
    unittest.main()