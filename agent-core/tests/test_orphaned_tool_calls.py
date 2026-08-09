"""IT-42 / BUG-005: repair orphaned assistant tool_calls in session history."""

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

from context import build_llm_messages, repair_orphaned_tool_calls, repair_tool_messages
from paths import AgentPaths
from session import Session, create_new


def _assistant_tool_call(*, call_id: str, name: str = "grep") -> dict:
    return {
        "role": "assistant",
        "content": "calling tool",
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": "{}"},
            }
        ],
    }


def _tool_reply(call_id: str, *, content: str = '{"ok": true}') -> dict:
    return {"role": "tool", "tool_call_id": call_id, "content": content}


class RepairOrphanedToolCallsTests(unittest.TestCase):
    def test_inserts_missing_tool_reply_before_user(self) -> None:
        """T-1806-04 / context.py demo: orphan assistant tool_calls get placeholders."""
        broken = [
            _assistant_tool_call(call_id="call_x"),
            {"role": "user", "content": "next question"},
        ]
        fixed = repair_orphaned_tool_calls(broken)
        self.assertEqual(len(fixed), 3)
        self.assertEqual(fixed[0]["role"], "assistant")
        self.assertEqual(fixed[1]["role"], "tool")
        self.assertEqual(fixed[1]["tool_call_id"], "call_x")
        self.assertIn("session recovered", fixed[1]["content"])
        self.assertEqual(fixed[2], broken[1])

    def test_idempotent_when_tool_replies_present(self) -> None:
        intact = [
            _assistant_tool_call(call_id="call_ok"),
            _tool_reply("call_ok"),
            {"role": "user", "content": "thanks"},
        ]
        self.assertEqual(repair_orphaned_tool_calls(intact), intact)

    def test_repairs_only_missing_tool_call_ids(self) -> None:
        broken = [
            {
                "role": "assistant",
                "content": "two tools",
                "tool_calls": [
                    {
                        "id": "call_a",
                        "type": "function",
                        "function": {"name": "read_file", "arguments": "{}"},
                    },
                    {
                        "id": "call_b",
                        "type": "function",
                        "function": {"name": "grep", "arguments": "{}"},
                    },
                ],
            },
            _tool_reply("call_a"),
            {"role": "user", "content": "continue"},
        ]
        fixed = repair_orphaned_tool_calls(broken)
        tool_ids = [m["tool_call_id"] for m in fixed if m.get("role") == "tool"]
        self.assertEqual(tool_ids, ["call_a", "call_b"])
        self.assertIn("session recovered", fixed[2]["content"])

    def test_empty_messages_returns_empty_list(self) -> None:
        self.assertEqual(repair_orphaned_tool_calls([]), [])

    def test_drops_stray_tool_after_final_assistant(self) -> None:
        broken = [
            {
                "role": "assistant",
                "content": "calling",
                "tool_calls": [
                    {
                        "id": "call_ok",
                        "type": "function",
                        "function": {"name": "grep", "arguments": "{}"},
                    }
                ],
            },
            _tool_reply("call_ok"),
            {"role": "assistant", "content": "done"},
            _tool_reply("call_late"),
            {"role": "user", "content": "?"},
        ]
        fixed = repair_tool_messages(broken)
        roles = [m["role"] for m in fixed]
        self.assertEqual(roles, ["assistant", "tool", "assistant", "user"])
        self.assertEqual(fixed[1]["tool_call_id"], "call_ok")

    def test_repairs_invalid_tool_call_arguments(self) -> None:
        broken_args = (
            '{"arguments": {"action":"logs","name":"svc","tail_lines":"4}, '
            '"tool_name": "run_service"}'
        )
        broken = [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_bad",
                        "type": "function",
                        "function": {"name": "run_evolved", "arguments": broken_args},
                    }
                ],
            },
            _tool_reply("call_bad", content='{"ok": false}'),
        ]
        fixed = repair_tool_messages(broken)
        args = fixed[0]["tool_calls"][0]["function"]["arguments"]
        self.assertEqual(json.loads(args), {})


class BuildLlmMessagesRepairTests(unittest.TestCase):
    def test_build_llm_messages_applies_repair(self) -> None:
        paths = AgentPaths.discover()
        session = create_new(paths, conversation_id=f"_orphan_llm_{secrets.token_hex(4)}")
        self.addCleanup(shutil.rmtree, session.session_dir, True)
        session.messages = [
            _assistant_tool_call(call_id="call_build"),
            {"role": "user", "content": "follow-up"},
        ]
        llm_messages = build_llm_messages(session)
        self.assertEqual(len(llm_messages), 3)
        self.assertEqual(llm_messages[1]["role"], "tool")
        self.assertEqual(llm_messages[1]["tool_call_id"], "call_build")


class SessionLoadRepairTests(unittest.TestCase):
    def test_session_load_repairs_and_persists_messages_jsonl(self) -> None:
        paths = AgentPaths.discover()
        conversation_id = f"_orphan_load_{secrets.token_hex(4)}"
        session = create_new(paths, conversation_id=conversation_id)
        self.addCleanup(shutil.rmtree, session.session_dir, True)

        broken = [
            _assistant_tool_call(call_id="call_disk"),
            {"role": "user", "content": "resume chat"},
        ]
        session.messages_path.write_text(
            "\n".join(json.dumps(m, ensure_ascii=False) for m in broken) + "\n",
            encoding="utf-8",
        )

        loaded = Session.load(paths, conversation_id)
        self.assertEqual(len(loaded.messages), 3)
        self.assertEqual(loaded.messages[1]["role"], "tool")
        self.assertEqual(loaded.messages[1]["tool_call_id"], "call_disk")

        on_disk = [
            json.loads(line)
            for line in loaded.messages_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertEqual(on_disk, loaded.messages)

        reloaded = Session.load(paths, conversation_id)
        self.assertEqual(reloaded.messages, loaded.messages)


if __name__ == "__main__":
    unittest.main()
