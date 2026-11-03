"""Ordinary chat request persistence starts before fallible turn preflight."""

from __future__ import annotations

import sys
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from agent import Agent
from session import Session, SessionMeta, _read_messages, utc_now_iso
from tests.isolation_helpers import temporary_agent_paths
from tools.executor import ExecutorSession, ToolExecutor
from tools.registry import ToolRegistry


class _UnusedLLM:
    def chat(self, *_args, **_kwargs):
        raise AssertionError("preflight failure must not invoke the model")


class OrdinaryTurnPreflightPersistenceTests(unittest.TestCase):
    def test_valid_user_request_survives_preappend_preflight_exception_once(self) -> None:
        with temporary_agent_paths(prefix="ordinary-turn-preflight-") as paths:
            session_dir = paths.data / "sessions" / "synthetic-current"
            session = Session(
                conversation_id="synthetic-current",
                session_dir=session_dir,
                goal="synthetic test",
                meta=SessionMeta(
                    topics=[],
                    llm_model="0x567-pro",
                    updated_at=utc_now_iso(),
                    phase="S4",
                    turn_mode="ask",
                ),
                messages=[],
                paths=paths,
            )
            session.save()
            other_dir = paths.data / "sessions" / "synthetic-other"
            other = Session(
                conversation_id="synthetic-other",
                session_dir=other_dir,
                goal="synthetic control",
                meta=SessionMeta(updated_at=utc_now_iso(), phase="S4"),
                messages=[],
                paths=paths,
            )
            other.save()
            other_before = other.messages_path.read_bytes()

            executor = ToolExecutor(
                registry=ToolRegistry.load(paths),
                session=ExecutorSession(session_dir=session_dir),
                confirm_fn=lambda _request: True,
            )
            agent = Agent(session=session, executor=executor, llm=_UnusedLLM())
            request = "合成请求：检查普通消息是否留存；不要执行任何操作。"
            hook_calls = []
            agent.work_card_result_reply_turn = SimpleNamespace(
                before_user_append=lambda *_args: hook_calls.append("before") or object(),
                after_user_append=lambda *_args: hook_calls.append("after"),
            )
            persisted_receipts = []

            def on_persisted(persisted_session):
                self.assertIs(persisted_session, session)
                rows_at_receipt = _read_messages(session.messages_path)
                self.assertTrue(any(
                    message.get("role") == "user" and message.get("content") == request
                    for message in rows_at_receipt
                ), "persistence receipt must follow the durable user append")
                persisted_receipts.append(persisted_session.conversation_id)

            with patch.object(
                agent,
                "_sync_turn_mode",
                side_effect=RuntimeError("synthetic preflight failure"),
            ):
                with self.assertRaisesRegex(RuntimeError, "synthetic preflight failure"):
                    agent.run_turn(request, on_external_user_message_persisted=on_persisted)

            current_messages = _read_messages(session.messages_path)
            matching_users = [
                message
                for message in current_messages
                if message.get("role") == "user" and message.get("content") == request
            ]
            self.assertEqual(len(matching_users), 1)
            self.assertEqual(persisted_receipts, [session.conversation_id])
            self.assertEqual(hook_calls, [], "failed preflight must not trigger result-view ACK hooks")
            self.assertFalse(
                any(message.get("role") == "assistant" for message in current_messages),
                "failed preflight must not be turned into an ACK/refusal assistant record",
            )
            self.assertEqual(other.messages_path.read_bytes(), other_before)
            self.assertFalse(
                any(
                    message.get("role") == "user" and message.get("content") == request
                    for message in _read_messages(other.messages_path)
                ),
                "a request must never leak into another conversation",
            )


if __name__ == "__main__":
    unittest.main()
