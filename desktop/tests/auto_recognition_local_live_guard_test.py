"""Offline contract test for the opt-in live fixture's exact provider guard."""
from __future__ import annotations

import json
import pathlib
import sys
import unittest
from types import SimpleNamespace

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "agent-core"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from work_records_luna_intent import WorkRecordsLunaIntent
from auto_recognition_local_live_guard import (
    confirmed_preview,
    confirmed_request_matches,
    expected_safe_messages,
    fixture_root_is_safe,
    live_provider_requested,
)


class FixtureBoundaryTests(unittest.TestCase):
    def test_only_a_named_direct_child_of_temp_is_a_valid_fixture_root(self) -> None:
        temp = pathlib.Path("/isolated-temp")
        self.assertTrue(fixture_root_is_safe(temp / "synthetic-auto-live-test", temp))
        self.assertFalse(fixture_root_is_safe(temp, temp))
        self.assertFalse(fixture_root_is_safe(temp / "other", temp))
        self.assertFalse(fixture_root_is_safe(temp / "nested" / "synthetic-auto-live-test", temp))
        self.assertFalse(fixture_root_is_safe(pathlib.Path("/elsewhere/synthetic-auto-live-test"), temp))

    def test_provider_mode_requires_an_explicit_fixture_argument(self) -> None:
        self.assertFalse(live_provider_requested([]))
        self.assertTrue(live_provider_requested(["--allow-real-provider"]))
        with self.assertRaises(ValueError):
            live_provider_requested(["unexpected"])
        with self.assertRaises(ValueError):
            live_provider_requested(["--allow-real-provider", "unexpected"])


class ConfirmedPayloadGuardTests(unittest.TestCase):
    def test_expected_envelope_tracks_real_messages_and_rejects_mutations(self) -> None:
        user_text = '请把合成简历中的个人介绍精简为三句话。这是一个独立事项。'
        intent = WorkRecordsLunaIntent(SimpleNamespace(chat=lambda *_args, **_kwargs: None))
        entry = SimpleNamespace(
            id="0x567-flash", vendor="0x567", provider_model="gpt-6-luna",
            chat_completions_url=lambda: "https://api-cdn.0x567.com/v1/chat/completions",
        )
        session_id = "synthetic-auto-live-session"
        source_messages, safe_messages = expected_safe_messages(intent, user_text)
        source_payload = json.loads(source_messages[1]["content"])
        safe_payload = json.loads(safe_messages[1]["content"])
        self.assertIn("current_project_available", source_payload)
        self.assertIs(source_payload["current_project_available"], False)
        self.assertNotIn("current_project_available", safe_payload)

        preview_json = json.dumps(
            confirmed_preview(entry, session_id, safe_messages),
            ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
        )
        self.assertTrue(confirmed_request_matches(
            safe_messages, model_id=entry.id, session_id=session_id,
            expected_preview_json=preview_json, entry=entry, intent=intent,
            expected_user_text=user_text,
        ))

        altered_messages = [safe_messages[0], dict(safe_messages[1])]
        altered_payload = json.loads(altered_messages[1]["content"])
        altered_payload["text"] += " altered"
        altered_messages[1]["content"] = json.dumps(altered_payload, ensure_ascii=False, separators=(",", ":"))
        self.assertFalse(confirmed_request_matches(
            altered_messages, model_id=entry.id, session_id=session_id,
            expected_preview_json=preview_json, entry=entry, intent=intent,
            expected_user_text=user_text,
        ))
        self.assertFalse(confirmed_request_matches(
            safe_messages, model_id=entry.id, session_id=session_id,
            expected_preview_json=preview_json + " ", entry=entry, intent=intent,
            expected_user_text=user_text,
        ))


if __name__ == "__main__":
    unittest.main()
