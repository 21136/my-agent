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
from llm_models import ModelEntry, is_tokeness_gateway
from session import SessionMeta, normalize_reasoning_effort


def _entry(
    *,
    entry_id: str,
    vendor: str,
    base_url: str = "https://example.test",
) -> ModelEntry:
    return ModelEntry(
        id=entry_id,
        name=entry_id,
        vendor=vendor,
        base_url=base_url,
        provider_model="gpt-5.6-luna",
    )


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

    def test_0x567_maps_max_to_high(self) -> None:
        self.assertEqual(_api_reasoning_effort("max", "0x567"), "high")

    def test_0x567_uses_top_level_reasoning_effort(self) -> None:
        payload: dict[str, object] = {"model": "gpt-5.6-luna"}
        _apply_reasoning_effort_to_payload(
            payload,
            "medium",
            _entry(entry_id="0x567-flash", vendor="0x567", base_url="https://api-cdn.0x567.com"),
        )
        self.assertEqual(payload.get("reasoning_effort"), "medium")
        self.assertNotIn("thinking", payload)

    def test_tokeness_matches_0x567_reasoning_effort(self) -> None:
        self.assertEqual(_api_reasoning_effort("medium", "Tokeness"), "medium")
        self.assertEqual(_api_reasoning_effort("max", "Tokeness"), "high")
        payload: dict[str, object] = {"model": "gpt-5.6-luna"}
        tokeness = _entry(
            entry_id="tokeness-luna",
            vendor="Tokeness",
            base_url="https://n.tokeness.dev",
        )
        _apply_reasoning_effort_to_payload(payload, "high", tokeness)
        self.assertEqual(payload.get("reasoning_effort"), "high")
        self.assertNotIn("thinking", payload)

    def test_tokeness_tools_force_reasoning_none(self) -> None:
        payload: dict[str, object] = {"model": "gpt-5.6-luna", "tools": [{"type": "function"}]}
        tokeness = _entry(
            entry_id="tokeness-luna",
            vendor="Tokeness",
            base_url="https://n.tokeness.dev",
        )
        _apply_reasoning_effort_to_payload(payload, "medium", tokeness, has_tools=True)
        self.assertEqual(payload.get("reasoning_effort"), "none")
        self.assertNotIn("thinking", payload)

    def test_tokeness_detected_by_base_url(self) -> None:
        custom = _entry(
            entry_id="custom-luna",
            vendor="OpenAI",
            base_url="https://n.tokeness.dev",
        )
        self.assertTrue(is_tokeness_gateway(custom))
        payload: dict[str, object] = {"model": "gpt-5.6-luna"}
        _apply_reasoning_effort_to_payload(payload, "medium", custom, has_tools=True)
        self.assertEqual(payload.get("reasoning_effort"), "none")

    def test_deepseek_uses_thinking_object(self) -> None:
        payload: dict[str, object] = {"model": "deepseek-v4-flash"}
        _apply_reasoning_effort_to_payload(
            payload,
            "medium",
            _entry(entry_id="deepseek-v4-flash", vendor="DeepSeek"),
        )
        self.assertEqual(
            payload.get("thinking"),
            {"type": "enabled", "reasoning_effort": "high"},
        )
        self.assertNotIn("reasoning_effort", payload)


if __name__ == "__main__":
    unittest.main()
