"""LLM client error handling — empty / invalid provider bodies."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from llm_client import LLMApiError, LLMClient, _load_response_json, _parse_completion


class _FakeResponse:
    def __init__(self, *, status_code: int, body: str) -> None:
        self.status_code = status_code
        self._body = body.encode("utf-8")

    def read(self) -> bytes:
        return self._body

    @property
    def text(self) -> str:
        return self._body.decode("utf-8")


class LlmClientErrorTests(unittest.TestCase):
    def test_empty_body_raises_llm_api_error(self) -> None:
        with self.assertRaises(LLMApiError) as ctx:
            _load_response_json(_FakeResponse(status_code=200, body=""))
        self.assertIn("empty response body", str(ctx.exception))

    def test_non_json_body_raises_llm_api_error(self) -> None:
        with self.assertRaises(LLMApiError) as ctx:
            _load_response_json(_FakeResponse(status_code=502, body="Bad Gateway"))
        self.assertIn("non-JSON body", str(ctx.exception))

    def test_provider_json_error_content_rejected(self) -> None:
        with self.assertRaises(LLMApiError) as ctx:
            _parse_completion(
                {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "Expecting value: line 1 column 1 (char 0)",
                            },
                            "finish_reason": "stop",
                        }
                    ],
                },
                fallback_model="deepseek-v4-flash",
            )
        self.assertIn("error text instead of a completion", str(ctx.exception))

    def test_parse_http_response_empty_body(self) -> None:
        from llm_client import LLMConfig, load_config

        cfg = load_config()
        client = LLMClient(cfg)
        with self.assertRaises(LLMApiError):
            client._parse_http_response(
                _FakeResponse(status_code=200, body=""),
                fallback_model=cfg.model,
            )


if __name__ == "__main__":
    unittest.main()
