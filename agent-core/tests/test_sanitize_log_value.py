"""IT-60 / T-110: evolve_log secrets redaction via sanitize_log_value."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from tools.logging import EvolveLog, read_events, sanitize_log_value
from tools.schema import tool_ok

_REDACTED = "[redacted]"


class SanitizeLogValueTests(unittest.TestCase):
    def test_redacts_api_key(self) -> None:
        payload = {"api_key": "sk-live-secret", "query": "ok"}
        redacted = sanitize_log_value(payload)
        self.assertEqual(redacted["api_key"], _REDACTED)
        self.assertEqual(redacted["query"], "ok")

    def test_redacts_nested_password_and_token(self) -> None:
        payload = {
            "headers": {"User-Agent": "agent/1"},
            "config": {"password": "p", "token": "t", "safe": 1},
        }
        redacted = sanitize_log_value(payload)
        self.assertEqual(redacted["headers"]["User-Agent"], "agent/1")
        self.assertEqual(redacted["config"]["password"], _REDACTED)
        self.assertEqual(redacted["config"]["token"], _REDACTED)
        self.assertEqual(redacted["config"]["safe"], 1)

    def test_truncates_long_strings(self) -> None:
        long_args = {"content": "x" * 800}
        sanitized = sanitize_log_value(long_args)
        self.assertIsInstance(sanitized["content"], str)
        self.assertIn("…(+300 chars)", sanitized["content"])
        self.assertNotIn("x" * 800, sanitized["content"])


class EvolveLogSanitizeTests(unittest.TestCase):
    def test_log_tool_call_never_persists_api_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "evolve_log.jsonl"
            log = EvolveLog(log_path)
            secret = "sk-it60-must-not-appear"
            log.log_tool_call(
                tool="http_get",
                arguments={"url": "https://example.com", "api_key": secret},
                result=tool_ok("http_get", {"status": 200}, duration_ms=1),
                conversation_id="_sanitize_test",
            )

            raw = log_path.read_text(encoding="utf-8")
            self.assertNotIn(secret, raw)

            events = read_events(log_path)
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["arguments"]["api_key"], _REDACTED)
            self.assertEqual(events[0]["arguments"]["url"], "https://example.com")

    def test_log_guard_event_redacts_sensitive_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "evolve_log.jsonl"
            log = EvolveLog(log_path)
            log.log_guard_event(
                guard_type="inline_write_max",
                conversation_id="_sanitize_guard",
                secret="top-level-secret",
                nested={"api_key": "nested-secret"},
            )

            raw = log_path.read_text(encoding="utf-8")
            self.assertNotIn("top-level-secret", raw)
            self.assertNotIn("nested-secret", raw)

            event = read_events(log_path)[0]
            self.assertEqual(event["event"], "guard")
            self.assertEqual(event["secret"], _REDACTED)
            self.assertEqual(event["nested"]["api_key"], _REDACTED)


if __name__ == "__main__":
    unittest.main()
