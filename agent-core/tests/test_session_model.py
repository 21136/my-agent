"""Session LLM model picker (flash / pro)."""

from __future__ import annotations

import secrets
import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from llm_client import (
    DEFAULT_MODEL,
    DEFAULT_MODEL_CODING,
    llm_model_label,
    normalize_session_model,
)
from session import Session, create_new, session_banner_event
from tests.isolation_helpers import make_temp_agent_paths


class SessionModelPickerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.paths = make_temp_agent_paths(self)

    def test_normalize_aliases(self) -> None:
        self.assertEqual(normalize_session_model("flash"), DEFAULT_MODEL)
        self.assertEqual(normalize_session_model("Pro"), DEFAULT_MODEL_CODING)
        self.assertEqual(normalize_session_model("deepseek-v4-flash"), DEFAULT_MODEL)
        self.assertEqual(normalize_session_model("deepseek-v4-pro"), DEFAULT_MODEL_CODING)
        self.assertIsNone(normalize_session_model("gpt-4"))

    def test_set_llm_model_persists_and_overrides_topics(self) -> None:
        session = create_new(self.paths, conversation_id=f"_m-{secrets.token_hex(3)}")
        session.set_llm_model("pro")
        session.save()

        reloaded = Session.load(self.paths, session.conversation_id)
        self.assertEqual(reloaded.meta.llm_model, DEFAULT_MODEL_CODING)
        self.assertTrue(reloaded.meta.llm_model_override)

        reloaded.set_topics(["coding"])
        self.assertEqual(reloaded.meta.llm_model, DEFAULT_MODEL_CODING)

        reloaded.set_llm_model("flash")
        reloaded.set_topics(["coding"])  # override still on → stay flash
        self.assertEqual(reloaded.meta.llm_model, DEFAULT_MODEL)

        reloaded.meta.llm_model_override = False
        reloaded.set_topics(["coding"])
        self.assertEqual(reloaded.meta.llm_model, DEFAULT_MODEL_CODING)

    def test_banner_includes_model(self) -> None:
        session = create_new(self.paths, conversation_id=f"_mb-{secrets.token_hex(3)}")
        session.set_llm_model("deepseek-v4-pro")
        banner = session_banner_event(session)
        self.assertEqual(banner["llm_model"], DEFAULT_MODEL_CODING)
        self.assertEqual(banner["llm_model_label"], "Pro")
        self.assertTrue(banner["llm_model_override"])
        self.assertEqual(llm_model_label(DEFAULT_MODEL), "Flash")


if __name__ == "__main__":
    unittest.main()
