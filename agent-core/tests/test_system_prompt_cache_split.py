"""System prompt static/dynamic split for prompt-cache (M0)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from loader import (
    SECTION_SEPARATOR,
    build_system_prompt,
    combine_system_prompt_parts,
    is_dynamic_system_section,
)
from session import Session, SessionMeta, utc_now_iso
from tests.isolation_helpers import temporary_agent_paths


class SystemPromptCacheSplitTests(unittest.TestCase):
    def test_dynamic_section_names(self) -> None:
        self.assertTrue(is_dynamic_system_section("session"))
        self.assertTrue(is_dynamic_system_section("digest"))
        self.assertFalse(is_dynamic_system_section("safety"))
        self.assertFalse(is_dynamic_system_section("topic_prompt:coding"))

    def test_static_excludes_turn_intent(self) -> None:
        with temporary_agent_paths() as paths:
            session = Session(
                conversation_id="cache-split-1",
                session_dir=paths.data / "sessions" / "cache-split-1",
                goal="cache probe",
                meta=SessionMeta(
                    topics=["coding"],
                    llm_model="0x567-pro",
                    updated_at=utc_now_iso(),
                    phase="S4",
                    turn_mode="agent",
                ),
                messages=[{"role": "user", "content": "fix bug"}],
                paths=paths,
            )
            session.turn_intent = "execute"
            loaded = build_system_prompt(session, paths=paths)

            self.assertIn("session", loaded.dynamic_section_names)
            self.assertIn("safety", loaded.static_section_names)
            self.assertIn("turn_intent: execute", loaded.dynamic_prompt)
            self.assertNotIn("turn_intent: execute", loaded.static_prompt)

    def test_turn_intent_change_only_updates_dynamic(self) -> None:
        with temporary_agent_paths() as paths:
            session = Session(
                conversation_id="cache-split-2",
                session_dir=paths.data / "sessions" / "cache-split-2",
                goal="cache probe",
                meta=SessionMeta(
                    topics=["coding"],
                    llm_model="0x567-pro",
                    updated_at=utc_now_iso(),
                    phase="S4",
                ),
                messages=[],
                paths=paths,
            )
            session.turn_intent = "qa"
            first = build_system_prompt(session, paths=paths)
            static_before = first.static_prompt

            session.turn_intent = "execute"
            second = build_system_prompt(session, paths=paths)

            self.assertEqual(static_before, second.static_prompt)
            self.assertNotEqual(first.dynamic_prompt, second.dynamic_prompt)
            self.assertIn("turn_intent: execute", second.dynamic_prompt)

    def test_prompt_preserves_original_section_order(self) -> None:
        with temporary_agent_paths() as paths:
            session = Session(
                conversation_id="cache-split-3",
                session_dir=paths.data / "sessions" / "cache-split-3",
                goal="cache probe",
                meta=SessionMeta(
                    topics=["coding"],
                    llm_model="0x567-pro",
                    updated_at=utc_now_iso(),
                    phase="S4",
                ),
                messages=[],
                paths=paths,
            )
            session.turn_intent = "qa"
            loaded = build_system_prompt(session, paths=paths)
            names = loaded.section_names

            if "session" in names and "safety" in names:
                self.assertLess(names.index("session"), names.index("safety"))
            parts = loaded.prompt.split(SECTION_SEPARATOR)
            self.assertEqual(len(parts), len(names))

    def test_include_overlay_false_is_all_static(self) -> None:
        with temporary_agent_paths() as paths:
            session = Session(
                conversation_id="cache-split-4",
                session_dir=paths.data / "sessions" / "cache-split-4",
                goal="",
                meta=SessionMeta(
                    topics=[],
                    llm_model="deepseek-v4-flash",
                    updated_at=utc_now_iso(),
                ),
                messages=[],
                paths=paths,
            )
            loaded = build_system_prompt(session, paths=paths, include_overlay=False)
            self.assertEqual(loaded.dynamic_prompt, "")
            self.assertEqual(loaded.prompt, loaded.static_prompt)
            self.assertEqual(loaded.dynamic_section_names, ())

    def test_combine_parts_matches_split_content(self) -> None:
        with temporary_agent_paths() as paths:
            session = Session(
                conversation_id="cache-split-5",
                session_dir=paths.data / "sessions" / "cache-split-5",
                goal="cache probe",
                meta=SessionMeta(
                    topics=["coding"],
                    llm_model="0x567-pro",
                    updated_at=utc_now_iso(),
                    phase="S4",
                ),
                messages=[],
                paths=paths,
            )
            session.turn_intent = "qa"
            loaded = build_system_prompt(session, paths=paths)
            combined = combine_system_prompt_parts(
                loaded.static_prompt,
                loaded.dynamic_prompt,
            )
            self.assertIn(loaded.static_prompt, combined)
            if loaded.dynamic_prompt:
                self.assertIn(loaded.dynamic_prompt, combined)


if __name__ == "__main__":
    unittest.main()
