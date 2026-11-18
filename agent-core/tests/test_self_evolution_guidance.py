from __future__ import annotations

import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from self_evolution_guidance import GuidanceInbox  # noqa: E402


class GuidanceInboxTests(unittest.TestCase):
    def setUp(self) -> None:
        self.identity = ("session-1", "task-1", "attempt-1")
        self.inbox = GuidanceInbox(self.identity, max_messages=2, max_chars=64, max_message_chars=64)

    def test_rejects_other_session_task_or_attempt(self) -> None:
        for identity in (("session-2", "task-1", "attempt-1"),
                         ("session-1", "task-2", "attempt-1"),
                         ("session-1", "task-1", "attempt-2")):
            self.assertEqual(self.inbox.receive(identity, "m1", "hint").status, "denied")
        self.assertEqual(self.inbox.pending_count, 0)

    def test_delivers_multiple_messages_in_order_only_after_resolution(self) -> None:
        self.assertEqual(self.inbox.receive(self.identity, "m1", "first").status, "received_pending_delivery")
        self.assertEqual(self.inbox.receive(self.identity, "m2", "second").status, "received_pending_delivery")
        batch = self.inbox.claim(self.identity)
        self.assertEqual([item.text for item in batch.items], ["first", "second"])
        self.assertEqual([item.status for item in self.inbox.snapshot()], ["in_delivery", "in_delivery"])
        self.inbox.resolve(batch.token, "delivered")
        self.assertEqual([item.status for item in self.inbox.snapshot()], ["delivered", "delivered"])
        self.assertEqual([item.text for item in self.inbox.snapshot()], ["", ""])
        self.assertEqual(self.inbox.receive(self.identity, "m1", "replay").delivery_status, "delivered")

    def test_deduplicates_message_ids_without_replacing_original(self) -> None:
        self.assertEqual(self.inbox.receive(self.identity, "m1", "first").status, "received_pending_delivery")
        self.assertEqual(self.inbox.receive(self.identity, "m1", "replacement").status, "duplicate")
        self.assertEqual(self.inbox.snapshot()[0].text, "first")

    def test_capacity_rejects_without_eviction_or_partial_insert(self) -> None:
        self.inbox.receive(self.identity, "m1", "x" * 64)
        rejected = self.inbox.receive(self.identity, "m2", "two")
        self.assertEqual(rejected.status, "capacity_exceeded")
        self.assertEqual([item.message_id for item in self.inbox.snapshot()], ["m1"])

    def test_close_discards_pending_and_blocks_future_receive_and_claim(self) -> None:
        self.inbox.receive(self.identity, "m1", "hint")
        self.inbox.close("cancelled")
        self.assertEqual(self.inbox.receive(self.identity, "m2", "later").status, "closed")
        self.assertIsNone(self.inbox.claim(self.identity))
        self.assertEqual(self.inbox.snapshot()[0].status, "discarded")
        self.assertEqual(self.inbox.snapshot()[0].text, "")

    def test_failed_or_ambiguous_delivery_is_not_requeued_or_claimable(self) -> None:
        self.inbox.receive(self.identity, "m1", "hint")
        batch = self.inbox.claim(self.identity)
        self.inbox.resolve(batch.token, "unknown")
        self.assertIsNone(self.inbox.claim(self.identity))
        self.assertEqual(self.inbox.snapshot()[0].status, "delivery_unknown")
        self.assertEqual(self.inbox.snapshot()[0].text, "")

    def test_process_restart_does_not_recover_pending_messages(self) -> None:
        self.inbox.receive(self.identity, "m1", "not durable")
        restarted = GuidanceInbox(self.identity, max_messages=2, max_chars=64, max_message_chars=64)
        self.assertEqual(restarted.pending_count, 0)
        self.assertIsNone(restarted.claim(self.identity))

    def test_invalid_or_oversized_input_fails_closed(self) -> None:
        self.assertEqual(self.inbox.receive(self.identity, "", "hint").status, "invalid")
        self.assertEqual(self.inbox.receive(self.identity, "m2", "x" * 65).status, "invalid")
        self.assertEqual(self.inbox.pending_count, 0)


if __name__ == "__main__":
    unittest.main()
