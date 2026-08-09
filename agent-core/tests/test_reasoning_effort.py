"""Reasoning effort defaults and provider mapping."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from llm_client import (
    _api_reasoning_effort,
    _apply_reasoning_effort_to_payload,
    _normalize_reasoning_effort,
)
from session import SessionMeta, normalize_reasoning_effort


class ReasoningEffortTests(unittest.TestCase):
    def test_default_is_medium(self) -> None:
        self.assertEqual(SessionMeta().reasoning_effort, "medium")
        self.assertEqual(normalize_reasoning_effort(None), "medium")
        self.assertEqual(_normalize_reasoning_effort(None), "medium")

    def test_deepseek_maps_medium_to_high(self) -> None:
        self.assertEqual(_api_reasoning_effort("medium", "DeepSeek"), "high")
        self.assertEqual(_api_reasoning_effort("medium", "Sophnet"), "high")

    def test_0x567_passes_medium(self) -> None:
        self.assertEqual(_api_reasoning_effort("medium", "0x567"), "medium")

    def test_0x567_uses_top_level_reasoning_effort(self) -> None:
        payload: dict[str, object] = {"model": "gpt-5.6-luna"}
        _apply_reasoning_effort_to_payload(payload, "medium", "0x567")
        self.assertEqual(payload.get("reasoning_effort"), "medium")
        self.assertNotIn("thinking", payload)

    def test_deepseek_uses_thinking_object(self) -> None:
        payload: dict[str, object] = {"model": "deepseek-v4-flash"}
        _apply_reasoning_effort_to_payload(payload, "medium", "DeepSeek")
        self.assertEqual(
            payload.get("thinking"),
            {"type": "enabled", "reasoning_effort": "high"},
        )
        self.assertNotIn("reasoning_effort", payload)


if __name__ == "__main__":
    unittest.main()
