"""Tokeness Luna chat payload integration tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from llm_client import LLMClient, LLMConfig, StreamHandlers


class TokenessChatPayloadTests(unittest.TestCase):
    def test_chat_forces_reasoning_none_when_tools_present(self) -> None:
        captured: dict[str, object] = {}

        class _FakeStreamResponse:
            status_code = 200

            def __enter__(self):
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def iter_lines(self):
                yield "data: [DONE]"

        class _FakeClient:
            def stream(self, method, url, headers=None, json=None):
                captured["json"] = json
                return _FakeStreamResponse()

            def __enter__(self):
                return self

            def __exit__(self, *args: object) -> None:
                return None

        cfg = LLMConfig(
            api_key="test-key",
            base_url="https://n.tokeness.dev",
            model="tokeness-luna",
            model_coding="tokeness-luna",
            timeout_sec=30.0,
            context_limit_override=None,
        )
        client = LLMClient(cfg)
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "ping",
                    "description": "health check",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ]

        with patch("llm_client.make_httpx_client", return_value=_FakeClient()):
            with patch("llm_client.get_registry") as mock_registry:
                entry = MagicMock()
                entry.id = "tokeness-luna"
                entry.provider_model = "gpt-5.6-luna"
                entry.vendor = "Tokeness"
                entry.base_url = "https://n.tokeness.dev"
                entry.supports_tool_call = True
                entry.resolve_api_key.return_value = "test-key"
                entry.chat_completions_url.return_value = "https://n.tokeness.dev/v1/chat/completions"
                mock_registry.return_value.resolve.return_value = entry
                mock_registry.return_value.default_flash_id = "tokeness-luna"
                client.chat(
                    [{"role": "user", "content": "hi"}],
                    tools=tools,
                    reasoning_effort="medium",
                    stream=StreamHandlers(),
                )

        payload = captured.get("json")
        self.assertIsInstance(payload, dict)
        assert isinstance(payload, dict)
        self.assertIn("tools", payload)
        self.assertEqual(payload.get("reasoning_effort"), "none")


if __name__ == "__main__":
    unittest.main()
