"""Outgoing GPT-6 model and tool-call payload compatibility tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from llm_client import LLMClient, LLMConfig, StreamHandlers
from llm_models import ModelEntry


class _FakeStreamResponse:
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


class _FakeHttpClient:
    def __init__(self, captured: dict[str, object]) -> None:
        self.captured = captured

    def stream(self, method, url, headers=None, json=None):
        self.captured["method"] = method
        self.captured["url"] = url
        self.captured["json"] = json
        return _FakeStreamResponse(str(json["model"]))

    def __enter__(self):
        return self

    def __exit__(self, *args: object) -> None:
        return None


class Gpt6RequestPayloadTests(unittest.TestCase):
    def _capture_request(
        self,
        registry_id: str,
        provider_model: str,
        reasoning_effort: str | None,
    ) -> dict[str, object]:
        captured: dict[str, object] = {}
        entry = ModelEntry(
            id=registry_id,
            name=provider_model,
            vendor="0x567",
            base_url="https://api-cdn.0x567.com",
            provider_model=provider_model,
            api_key="test-key",
            supports_tool_call=True,
        )

        class _Registry:
            default_flash_id = "0x567-flash"

            @staticmethod
            def resolve(model_id: str) -> ModelEntry | None:
                return entry if model_id == registry_id else None

        client = LLMClient(
            LLMConfig(
                api_key="test-key",
                base_url="https://api-cdn.0x567.com",
                model="0x567-flash",
                model_coding="0x567-pro",
                timeout_sec=30.0,
                context_limit_override=None,
            )
        )

        with patch("llm_client.get_registry", return_value=_Registry()):
            with patch(
                "llm_client.make_httpx_client",
                return_value=_FakeHttpClient(captured),
            ):
                client.chat(
                    [{"role": "user", "content": "test"}],
                    model=registry_id,
                    tools=[{"type": "function", "function": {"name": "ping"}}],
                    reasoning_effort=reasoning_effort,
                    stream=StreamHandlers(),
                )
        return captured

    def test_chat_omits_reasoning_effort_for_gpt6_with_tools(self) -> None:
        for registry_id, provider_model in (
            ("0x567-flash", "gpt-6-luna"),
            ("0x567-pro", "gpt-6-sol"),
        ):
            with self.subTest(registry_id=registry_id):
                captured = self._capture_request(registry_id, provider_model, "medium")
                payload = captured.get("json")
                self.assertIsInstance(payload, dict)
                assert isinstance(payload, dict)
                self.assertEqual(payload.get("model"), provider_model)
                self.assertNotIn("reasoning_effort", payload)
                self.assertIn("tools", payload)

    def test_chat_omits_reasoning_effort_for_gpt6_tools_when_effort_unset(self) -> None:
        for registry_id, provider_model in (
            ("0x567-flash", "gpt-6-luna"),
            ("0x567-pro", "gpt-6-sol"),
        ):
            with self.subTest(registry_id=registry_id):
                captured = self._capture_request(registry_id, provider_model, None)
                payload = captured.get("json")
                self.assertIsInstance(payload, dict)
                assert isinstance(payload, dict)
                self.assertEqual(payload.get("model"), provider_model)
                self.assertNotIn("reasoning_effort", payload)


if __name__ == "__main__":
    unittest.main()
