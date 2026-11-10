"""messages.raw.jsonl keeps every record that reached messages.jsonl, even after it is rewritten."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from typing import Any

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from agent import Agent
from raw_log import RAW_MESSAGES_FILENAME, raw_log_path, record_append
from session import Session, create_new

from tests.isolation_helpers import make_temp_agent_paths


def _entries(session: Session) -> list[dict[str, Any]]:
    text = raw_log_path(session.messages_path).read_text(encoding="utf-8")
    return [json.loads(line) for line in text.split("\n") if line]


class RawMessageLogTests(unittest.TestCase):
    def _session(self) -> Session:
        return create_new(make_temp_agent_paths(self))

    def test_appended_messages_are_logged_in_order(self) -> None:
        session = self._session()
        session.append_message({"role": "user", "content": "hello"})
        session.append_message({"role": "assistant", "content": "hi", "reasoning_content": "thought"})

        entries = _entries(session)
        self.assertEqual([entry["op"] for entry in entries], ["append", "append"])
        self.assertEqual([entry["index"] for entry in entries], [0, 1])
        self.assertEqual(entries[1]["message"]["reasoning_content"], "thought")

    def test_patched_reply_keeps_the_original_text(self) -> None:
        session = self._session()
        session.append_message({"role": "user", "content": "do it"})
        session.append_message({"role": "assistant", "content": "ORIGINAL REPLY"})
        agent = Agent.create(session, confirm_fn=lambda _p, _a: "y")

        agent._patch_last_assistant_message("ORIGINAL REPLY\n\ngate notice")
        session.save()

        self.assertNotIn("ORIGINAL REPLY\"}", session.messages_path.read_text(encoding="utf-8"))
        rewrite = _entries(session)[-1]
        self.assertEqual(rewrite["op"], "rewrite")
        self.assertEqual(rewrite["index"], 1)
        self.assertEqual(rewrite["before"], [{"role": "assistant", "content": "ORIGINAL REPLY"}])
        self.assertEqual(rewrite["after"][0]["content"], "ORIGINAL REPLY\n\ngate notice")

    def test_message_dropped_by_load_repair_is_kept(self) -> None:
        session = self._session()
        session.append_message({"role": "user", "content": "kept"})
        orphan = {"role": "tool", "tool_call_id": "missing", "content": "ORPHAN OUTPUT"}
        session.append_message(orphan)
        session.append_message({"role": "assistant", "content": "after"})

        reloaded = Session.load(session.paths, session.conversation_id)
        reloaded.ensure_messages_loaded()

        self.assertNotIn("ORPHAN OUTPUT", reloaded.messages_path.read_text(encoding="utf-8"))
        rewrite = _entries(reloaded)[-1]
        self.assertEqual((rewrite["op"], rewrite["index"]), ("rewrite", 1))
        self.assertEqual(rewrite["before"], [orphan])
        self.assertEqual(rewrite["after"], [])

    def test_unpersisted_messages_are_logged_when_saved(self) -> None:
        session = self._session()
        session.append_message({"role": "user", "content": "on disk"})
        session.append_message({"role": "assistant", "content": "memory only"}, persist=False)
        session.save()

        last = _entries(session)[-1]
        self.assertEqual((last["op"], last["index"], last["before"]), ("rewrite", 1, []))
        self.assertEqual(last["after"], [{"role": "assistant", "content": "memory only"}])

    def test_save_without_changes_adds_nothing(self) -> None:
        session = self._session()
        session.append_message({"role": "user", "content": "hello"})
        before = raw_log_path(session.messages_path).read_bytes()
        session.save()
        session.save()
        self.assertEqual(raw_log_path(session.messages_path).read_bytes(), before)

    def test_session_from_before_the_log_is_seeded_on_first_write(self) -> None:
        session = self._session()
        session.append_message({"role": "user", "content": "old one"})
        session.append_message({"role": "assistant", "content": "old two\u2028same line"})
        raw_log_path(session.messages_path).unlink()

        session.append_message({"role": "user", "content": "new"})

        entries = _entries(session)
        self.assertEqual([entry["op"] for entry in entries], ["seed", "seed", "append"])
        self.assertEqual(entries[1]["message"]["content"], "old two\u2028same line")
        self.assertEqual(entries[2]["index"], 2)

    def test_partial_last_line_does_not_swallow_the_next_entry(self) -> None:
        session = self._session()
        session.append_message({"role": "user", "content": "first"})
        with raw_log_path(session.messages_path).open("ab") as handle:
            handle.write(b'{"ts": "cut off by a cra')

        record_append(session.messages_path, {"role": "user", "content": "second"}, index=1)

        lines = raw_log_path(session.messages_path).read_text(encoding="utf-8").split("\n")
        self.assertEqual(json.loads(lines[2])["message"]["content"], "second")

    def test_log_lives_beside_messages_jsonl(self) -> None:
        session = self._session()
        session.append_message({"role": "user", "content": "hello"})
        self.assertTrue((session.session_dir / RAW_MESSAGES_FILENAME).is_file())


if __name__ == "__main__":
    unittest.main()
