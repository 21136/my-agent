"""IT-55 / IT-56 session data corruption.

T-1823-02: bad jsonl lines surface via ``Session.corruption_notices``.
T-1823-04: bad / missing meta.json surfaces via the same list.
T-1823-05: bad state.json surfaces via ``AgentPaths.corruption_notices``.
"""

from __future__ import annotations

import json
import secrets
import shutil
import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from paths import AgentPaths, format_state_corruption_notice
from project_switch import read_project_sessions
from session import (
    Session,
    _read_messages,
    _read_meta,
    corruption_notice_events,
    create_new,
    read_last_conversation_id,
    resume_or_create,
)
from shell_switch import read_shell_sessions, record_shell_session, switch_shell

from tests.isolation_helpers import make_temp_agent_paths


def _write_messages_jsonl(path: Path, *lines: str) -> None:
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


class BadJsonlLineCurrentBehaviorTests(unittest.TestCase):
    """IT-55 · T-1806-07 / T-1823-02: bad ``messages.jsonl`` lines skipped + noticed."""

    def setUp(self) -> None:
        self.paths = AgentPaths.discover()

    def _temp_session(self) -> tuple[Session, Path]:
        session_id = f"_corrupt_jsonl_{secrets.token_hex(4)}"
        session = create_new(self.paths, conversation_id=session_id)
        self.addCleanup(shutil.rmtree, session.session_dir, True)
        return session, session.session_dir

    def test_read_messages_skips_invalid_json_line(self) -> None:
        session, session_dir = self._temp_session()
        _write_messages_jsonl(
            session_dir / "messages.jsonl",
            json.dumps({"role": "user", "content": "keep-me"}, ensure_ascii=False),
            "NOT VALID JSON {{{",
            json.dumps({"role": "assistant", "content": "also-keep"}, ensure_ascii=False),
        )

        messages = _read_messages(session_dir / "messages.jsonl")
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0]["content"], "keep-me")
        self.assertEqual(messages[1]["content"], "also-keep")

    def test_read_messages_skips_non_object_json(self) -> None:
        session, session_dir = self._temp_session()
        _write_messages_jsonl(
            session_dir / "messages.jsonl",
            json.dumps(["not", "a", "message"], ensure_ascii=False),
            json.dumps({"role": "user", "content": "ok"}, ensure_ascii=False),
        )

        messages = _read_messages(session_dir / "messages.jsonl")
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["content"], "ok")

    def test_session_load_survives_corrupt_jsonl_without_traceback(self) -> None:
        session, session_dir = self._temp_session()
        _write_messages_jsonl(
            session_dir / "messages.jsonl",
            json.dumps({"role": "user", "content": "before"}, ensure_ascii=False),
            "{broken",
            json.dumps({"role": "user", "content": "after"}, ensure_ascii=False),
        )

        loaded = Session.load(self.paths, session.conversation_id)
        self.assertEqual(len(loaded.messages), 2)
        self.assertEqual(loaded.messages[0]["content"], "before")
        self.assertEqual(loaded.messages[1]["content"], "after")

    def test_all_bad_jsonl_lines_yield_empty_history(self) -> None:
        session, session_dir = self._temp_session()
        _write_messages_jsonl(
            session_dir / "messages.jsonl",
            "line-one-bad",
            "{still-bad",
        )

        loaded = Session.load(self.paths, session.conversation_id)
        self.assertEqual(loaded.messages, [])

    def test_it55_bad_jsonl_should_surface_notice_to_user(self) -> None:
        """T-1823-02: skipped lines must be visible via corruption_notices + turn.notice."""
        session, session_dir = self._temp_session()
        _write_messages_jsonl(
            session_dir / "messages.jsonl",
            json.dumps({"role": "user", "content": "valid"}, ensure_ascii=False),
            "CORRUPT-LINE",
        )

        loaded = Session.load(self.paths, session.conversation_id)
        notices = getattr(loaded, "corruption_notices", None)
        self.assertIsNotNone(notices)
        self.assertTrue(notices)
        self.assertIn("1 行损坏已跳过", notices[0])
        self.assertIn("行号: 2", notices[0])

        events = corruption_notice_events(loaded)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "turn.notice")
        self.assertEqual(events[0]["level"], "warn")
        self.assertEqual(events[0]["text"], notices[0])


class BadMetaJsonNoticeTests(unittest.TestCase):
    """IT-55 · T-1823-04: structural meta.json failure → corruption_notices."""

    def setUp(self) -> None:
        self.paths = AgentPaths.discover()

    def _temp_session(self) -> tuple[Session, Path]:
        session_id = f"_corrupt_meta_{secrets.token_hex(4)}"
        session = create_new(self.paths, conversation_id=session_id)
        session.meta.topics = ["keep-topic"]
        session.meta.active_shell = "grow"
        session.meta.project_id = "demo-proj"
        session.save()
        self.addCleanup(shutil.rmtree, session.session_dir, True)
        return session, session.session_dir

    def test_unreadable_meta_surfaces_notice(self) -> None:
        session, session_dir = self._temp_session()
        (session_dir / "meta.json").write_text("{broken-meta", encoding="utf-8")

        loaded = Session.load(self.paths, session.conversation_id)
        self.assertEqual(loaded.meta.topics, [])
        self.assertTrue(loaded.corruption_notices)
        self.assertIn("损坏", loaded.corruption_notices[0])
        self.assertIn("绑定可能已丢失", loaded.corruption_notices[0])
        events = corruption_notice_events(loaded)
        self.assertEqual(events[0]["type"], "turn.notice")
        self.assertEqual(events[0]["level"], "warn")

    def test_non_object_meta_surfaces_notice(self) -> None:
        session, session_dir = self._temp_session()
        (session_dir / "meta.json").write_text(
            json.dumps(["not", "meta"], ensure_ascii=False),
            encoding="utf-8",
        )

        loaded = Session.load(self.paths, session.conversation_id)
        self.assertTrue(loaded.corruption_notices)
        self.assertIn("损坏", loaded.corruption_notices[0])

    def test_missing_meta_surfaces_notice(self) -> None:
        session, session_dir = self._temp_session()
        (session_dir / "meta.json").unlink()

        loaded = Session.load(self.paths, session.conversation_id)
        self.assertTrue(loaded.corruption_notices)
        self.assertIn("缺失", loaded.corruption_notices[0])
        self.assertIn("绑定可能已丢失", loaded.corruption_notices[0])

    def test_valid_meta_partial_fields_no_notice(self) -> None:
        session, session_dir = self._temp_session()
        (session_dir / "meta.json").write_text(
            json.dumps({"topics": ["ok"], "phase": "S4"}, ensure_ascii=False),
            encoding="utf-8",
        )

        loaded = Session.load(self.paths, session.conversation_id)
        self.assertEqual(loaded.meta.topics, ["ok"])
        self.assertEqual(loaded.corruption_notices, [])

    def test_read_meta_without_kinds_stays_silent(self) -> None:
        session, session_dir = self._temp_session()
        (session_dir / "meta.json").write_text("{broken", encoding="utf-8")
        meta = _read_meta(session_dir / "meta.json")
        self.assertEqual(meta.topics, [])

    def test_meta_and_jsonl_notices_coexist(self) -> None:
        session, session_dir = self._temp_session()
        (session_dir / "meta.json").write_text("{broken", encoding="utf-8")
        _write_messages_jsonl(
            session_dir / "messages.jsonl",
            json.dumps({"role": "user", "content": "ok"}, ensure_ascii=False),
            "BAD-LINE",
        )

        loaded = Session.load(self.paths, session.conversation_id)
        self.assertEqual(len(loaded.corruption_notices), 2)
        self.assertTrue(any("meta.json" in text for text in loaded.corruption_notices))
        self.assertTrue(any("messages.jsonl" in text for text in loaded.corruption_notices))
        self.assertEqual(len(corruption_notice_events(loaded)), 2)


class BadStateJsonCurrentBehaviorTests(unittest.TestCase):
    """IT-56 · T-1806-08 / T-1823-05: corrupt ``data/state.json`` → ``{}`` + notice."""

    def setUp(self) -> None:
        self.paths = make_temp_agent_paths(self)
        self.state_path = self.paths.data / "state.json"

    def _write_corrupt_state(self, text: str) -> None:
        self.paths.data.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(text, encoding="utf-8")

    def test_read_last_conversation_id_survives_corrupt_state(self) -> None:
        self._write_corrupt_state("{not-json")
        self.assertIsNone(read_last_conversation_id(self.paths))

        self._write_corrupt_state(json.dumps(["array"], ensure_ascii=False))
        self.assertIsNone(read_last_conversation_id(self.paths))

    def test_shell_and_project_indexes_survive_corrupt_state(self) -> None:
        self._write_corrupt_state("CORRUPT")
        self.assertEqual(read_shell_sessions(self.paths), {})
        self.assertEqual(read_project_sessions(self.paths), {})

    def test_switch_shell_survives_corrupt_state_without_traceback(self) -> None:
        session_id = f"_state_switch_{secrets.token_hex(4)}"
        session = create_new(self.paths, conversation_id=session_id)
        session.meta.active_shell = "grow"
        session.save()

        self._write_corrupt_state("{broken-state")
        switched, replaced = switch_shell(self.paths, session, "daily")
        self.assertTrue(switched.conversation_id)
        self.assertIsInstance(replaced, bool)
        self.assertEqual(switched.meta.active_shell, "daily")

    def test_resume_or_create_survives_corrupt_state(self) -> None:
        self._write_corrupt_state("{broken-state")
        loaded = resume_or_create(self.paths)
        self.assertTrue(loaded.conversation_id)

    def test_record_shell_session_rewrites_valid_state_after_corruption(self) -> None:
        session_id = f"_state_rewrite_{secrets.token_hex(4)}"
        self._write_corrupt_state("<<<")
        record_shell_session(self.paths, "grow", session_id)

        payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertIsInstance(payload, dict)
        self.assertEqual(payload.get("shell_sessions", {}).get("grow"), session_id)

    def test_it56_bad_state_should_surface_notice_to_user(self) -> None:
        """T-1823-05: corrupt state.json must warn via paths.corruption_notices."""
        self._write_corrupt_state("{broken")
        resumed = resume_or_create(self.paths)

        notices = getattr(self.paths, "corruption_notices", None)
        self.assertIsNotNone(notices)
        self.assertTrue(notices)
        self.assertEqual(notices[0], format_state_corruption_notice())
        self.assertIn("state.json", notices[0])

        events = corruption_notice_events(resumed)
        self.assertTrue(any(e.get("text") == notices[0] for e in events))
        self.assertTrue(all(e.get("type") == "turn.notice" for e in events))


if __name__ == "__main__":
    unittest.main()
