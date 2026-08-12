"""LLM chat message assembly for prompt-cache vendors."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from llm_client import (
    as_cached_system_message,
    build_chat_messages,
    cached_prompt_tokens,
    vendor_supports_system_prompt_cache,
)


class LlmClientCacheMessageTests(unittest.TestCase):
    def test_vendor_support(self):
        self.assertFalse(vendor_supports_system_prompt_cache("0x567"))
        self.assertFalse(vendor_supports_system_prompt_cache("deepseek"))

    def test_0x567_split_system_with_cache_marker(self) -> None:
        messages = build_chat_messages(
            [{"role": "user", "content": "hi"}],
            system_prompt="STATIC\n---\nDYNAMIC",
            static_system="STATIC",
            dynamic_system="DYNAMIC",
            vendor="0x567",
        )
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0], {"role": "system", "content": "STATIC\n---\nDYNAMIC"})
        self.assertEqual(messages[1], {"role": "user", "content": "hi"})

    def test_0x567_static_only(self) -> None:
        messages = build_chat_messages(
            [],
            system_prompt="ONLY",
            static_system="ONLY",
            dynamic_system="",
            vendor="0x567",
        )
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["role"], "system")

    def test_deepseek_uses_full_prompt_order(self) -> None:
        full = "A\n---\nB\n---\nC"
        messages = build_chat_messages(
            [{"role": "user", "content": "x"}],
            system_prompt=full,
            static_system="A\n---\nC",
            dynamic_system="B",
            vendor="deepseek",
        )
        self.assertEqual(messages[0], {"role": "system", "content": full})

    def test_as_cached_system_message_shape(self) -> None:
        msg = as_cached_system_message("hello")
        self.assertEqual(msg, {"role": "system", "content": "hello"})

    def test_cached_prompt_tokens_reads_details(self) -> None:
        usage = {
            "prompt_tokens": 1000,
            "prompt_tokens_details": {"cached_tokens": 640},
        }
        self.assertEqual(cached_prompt_tokens(usage), 640)

    def test_cached_prompt_tokens_reads_cache_read_input_tokens(self) -> None:
        usage = {"prompt_tokens": 1000, "cache_read_input_tokens": 512}
        self.assertEqual(cached_prompt_tokens(usage), 512)

    def test_llm_usage_event_builds_desktop_payload(self) -> None:
        from llm_client import llm_usage_event

        event = llm_usage_event(
            {
                "prompt_tokens": 1000,
                "completion_tokens": 50,
                "total_tokens": 1050,
                "prompt_tokens_details": {"cached_tokens": 640},
            }
        )
        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event["type"], "llm.usage")
        self.assertEqual(event["prompt_tokens"], 1000)
        self.assertEqual(event["cached_tokens"], 640)
        self.assertEqual(event["cache_ratio"], 0.64)
        self.assertEqual(event["completion_tokens"], 50)

    def test_llm_usage_event_returns_none_without_prompt_tokens(self) -> None:
        from llm_client import llm_usage_event

        self.assertIsNone(llm_usage_event(None))
        self.assertIsNone(llm_usage_event({}))


if __name__ == "__main__":
    unittest.main()
