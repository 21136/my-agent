"""UI-5972 · project session switch performance helpers."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
_TESTS = Path(__file__).resolve().parent
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))
if str(_TESTS) not in sys.path:
    sys.path.insert(0, str(_TESTS))

from session import (
    Session,
    _count_file_newlines,
    _extract_user_messages,
    _read_messages_tail,
    build_session_chat_history_from_path,
    create_new,
    desktop_switch_message_cap,
    session_history_event,
    session_history_max_items,
)
from isolation_helpers import temporary_agent_paths


class SessionSwitchPerfTests(unittest.TestCase):
    def test_count_file_newlines(self) -> None:
        with temporary_agent_paths() as paths:
            path = paths.data / "sessions" / "_line_count_" / "messages.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('{"role":"user","content":"a"}\n' * 5, encoding="utf-8")
            self.assertEqual(_count_file_newlines(path), 5)

    def test_extract_user_messages_tail_read(self) -> None:
        with temporary_agent_paths() as paths:
            path = paths.data / "sessions" / "_tail_read_" / "messages.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            lines = []
            for index in range(300):
                lines.append(
                    json.dumps({"role": "user", "content": f"msg-{index}"}, ensure_ascii=False)
                )
            lines[0] = json.dumps({"role": "user", "content": "first-real"}, ensure_ascii=False)
            lines[-1] = json.dumps({"role": "user", "content": "last-real"}, ensure_ascii=False)
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            first, last, count = _extract_user_messages(path)
            self.assertEqual(count, 300)
            self.assertEqual(first, "first-real")
            self.assertEqual(last, "last-real")

    def test_session_history_event_truncates(self) -> None:
        with temporary_agent_paths() as paths:
            session = create_new(paths, conversation_id="_history_cap_")
            for index in range(125):
                session.append_message({"role": "user", "content": f"u-{index}"})
                session.append_message({"role": "assistant", "content": f"a-{index}"})
            import os

            old = os.environ.get("MY_AGENT_SESSION_HISTORY_MAX_ITEMS")
            os.environ["MY_AGENT_SESSION_HISTORY_MAX_ITEMS"] = "200"
            try:
                self.assertEqual(session_history_max_items(), 200)
                event = session_history_event(session)
            finally:
                if old is None:
                    os.environ.pop("MY_AGENT_SESSION_HISTORY_MAX_ITEMS", None)
                else:
                    os.environ["MY_AGENT_SESSION_HISTORY_MAX_ITEMS"] = old
            self.assertTrue(event.get("truncated"))
            self.assertEqual(event.get("total_items"), 250)
            self.assertEqual(event.get("omitted_count"), 50)
            self.assertEqual(len(event["items"]), 200)
            self.assertEqual(event["items"][0]["text"], "u-25")
            self.assertEqual(event["items"][-1]["text"], "a-124")

    def test_build_session_chat_history_skips_kernel_lines(self) -> None:
        with temporary_agent_paths() as paths:
            session = create_new(paths, conversation_id="_history_skip_")
            session.append_message({"role": "user", "content": "[内核] segment"})
            session.append_message({"role": "user", "content": "real question"})
            session.append_message({"role": "assistant", "content": "answer"})
            items, total = build_session_chat_history_from_path(session.messages_path)
            self.assertEqual(total, 2)
            self.assertEqual(len(items), 2)
            self.assertEqual(items[0]["text"], "real question")

    def test_deferred_switch_load_and_history(self) -> None:
        with temporary_agent_paths() as paths:
            session = create_new(paths, conversation_id="_defer_switch_")
            for index in range(150):
                session.append_message({"role": "user", "content": f"u-{index}"})
                session.append_message({"role": "assistant", "content": f"a-{index}"})
            loaded = Session.load(paths, "_defer_switch_", message_cap=0)
            self.assertEqual(desktop_switch_message_cap(), 0)
            self.assertFalse(loaded._messages_fully_loaded)
            self.assertEqual(loaded.messages, [])
            self.assertEqual(loaded.messages_total_count, 300)
            event = session_history_event(loaded)
            cap = session_history_max_items()
            self.assertEqual(len(event["items"]), cap)
            self.assertTrue(event.get("truncated"))
            self.assertEqual(event["items"][0]["text"], "u-50")
            loaded.ensure_messages_loaded()
            self.assertTrue(loaded._messages_fully_loaded)
            self.assertEqual(len(loaded.messages), 300)

    def test_deferred_session_memory_event_does_not_report_zero_tokens(self) -> None:
        from context import session_memory_event

        with temporary_agent_paths() as paths:
            session = create_new(paths, conversation_id="_defer_mem_")
            session.append_message({"role": "user", "content": "hello " * 200})
            session.append_message({"role": "assistant", "content": "world " * 200})
            loaded = Session.load(paths, "_defer_mem_", message_cap=0)
            self.assertFalse(loaded._messages_fully_loaded)
            quick_evt = session_memory_event(loaded, quick=True)
            full_evt = session_memory_event(loaded, quick=False)
            self.assertGreater(quick_evt["token_usage"], 0)
            self.assertGreater(full_evt["token_usage"], 0)
            self.assertEqual(full_evt["message_count"], 2)

    def test_deferred_compacted_session_memory_uses_payload_estimate(self) -> None:
        from context import session_memory_event

        with temporary_agent_paths() as paths:
            session = create_new(paths, conversation_id="_defer_compact_mem_")
            for index in range(12):
                session.append_message({"role": "user", "content": f"user turn {index} " + ("x" * 400)})
                session.append_message({"role": "assistant", "content": f"assistant reply {index} " + ("y" * 400)})
            session.meta.compact_before_index = len(session.messages) - 4
            session.digest_path.write_text(
                "# 压缩 1\n\n## 目标\nDemo\n\n## 已做\nEarlier\n",
                encoding="utf-8",
            )
            session.save()
            loaded = Session.load(paths, "_defer_compact_mem_", message_cap=0)
            self.assertFalse(loaded._messages_fully_loaded)
            quick_evt = session_memory_event(loaded, quick=True)
            full_evt = session_memory_event(loaded, quick=False)
            self.assertEqual(quick_evt["token_usage"], full_evt["token_usage"])
            self.assertEqual(quick_evt["memory_mode"], "compact")

    def test_build_state_light_skips_auto_fix(self) -> None:
        from unittest.mock import patch

        from plan_agent import get_plan_agent
        from project_mode import create_project

        with temporary_agent_paths() as paths:
            pid = "light-build"
            create_project(paths, pid)
            session = create_new(paths, conversation_id="_light_build_")
            session.meta.project_id = pid
            session.meta.project_root = f"workspace/{pid}"
            session.meta.active_shell = "project"
            agent = get_plan_agent(paths, pid)
            with patch.object(agent, "auto_fix") as auto_fix:
                agent.build_state(session, light=True)
                auto_fix.assert_not_called()
            with patch.object(agent, "auto_fix", return_value=[]) as auto_fix:
                agent.build_state(session, light=False)
                auto_fix.assert_called_once()

    def test_read_messages_tail(self) -> None:
        with temporary_agent_paths() as paths:
            path = paths.data / "sessions" / "_tail_msgs_" / "messages.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            lines = [json.dumps({"role": "user", "content": f"m-{i}"}) for i in range(20)]
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            messages, total = _read_messages_tail(path, 5)
            self.assertEqual(total, 20)
            self.assertEqual(len(messages), 5)
            self.assertEqual(messages[-1]["content"], "m-19")


if __name__ == "__main__":
    unittest.main()
