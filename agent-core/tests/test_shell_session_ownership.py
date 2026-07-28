"""BUG-020 / STD-001: park_session must not pollute shell_sessions via activity_router."""

from __future__ import annotations

import secrets
import unittest

from activity_router import ActivityRoute, compute_activity_route, should_persist_activity_shell
from session import create_new
from shell_switch import (
    lookup_shell_owner,
    park_session,
    read_shell_sessions,
    record_shell_session,
    switch_shell,
)
from tests.isolation_helpers import make_temp_agent_paths


class ShellSessionOwnershipTests(unittest.TestCase):
    def setUp(self) -> None:
        self.paths = make_temp_agent_paths(self)
        token = secrets.token_hex(4)
        self.grow = create_new(self.paths, conversation_id=f"_bug020_grow_{token}")
        self.grow.meta.active_shell = "grow"
        self.grow.save()
        record_shell_session(self.paths, "grow", self.grow.conversation_id)

        self.daily = create_new(self.paths, conversation_id=f"_bug020_daily_{token}")
        self.daily.meta.active_shell = "daily"
        self.daily.save()
        record_shell_session(self.paths, "daily", self.daily.conversation_id)

    def test_park_with_flipped_meta_does_not_pollute_daily(self) -> None:
        """Reproduce STD-001: grow meta marked daily must still park under grow."""
        before = read_shell_sessions(self.paths)
        self.assertEqual(before["grow"], self.grow.conversation_id)
        self.assertEqual(before["daily"], self.daily.conversation_id)
        self.assertNotEqual(before["grow"], before["daily"])

        self.grow.meta.active_shell = "daily"
        self.grow.save()
        park_session(self.paths, self.grow)

        after = read_shell_sessions(self.paths)
        self.assertEqual(after["grow"], self.grow.conversation_id)
        self.assertEqual(after["daily"], self.daily.conversation_id)
        self.assertEqual(lookup_shell_owner(self.paths, self.grow.conversation_id), "grow")
        # Heal drifted meta
        self.assertEqual(self.grow.meta.active_shell, "grow")

    def test_switch_grow_to_daily_keeps_distinct_ids_after_flip(self) -> None:
        self.grow.meta.active_shell = "daily"
        self.grow.save()
        loaded, replaced = switch_shell(self.paths, self.grow, "daily")
        self.assertTrue(replaced)
        self.assertEqual(loaded.conversation_id, self.daily.conversation_id)
        mapping = read_shell_sessions(self.paths)
        self.assertEqual(mapping["grow"], self.grow.conversation_id)
        self.assertEqual(mapping["daily"], self.daily.conversation_id)

    def test_should_not_persist_grow_to_daily_soft_route(self) -> None:
        route = ActivityRoute("daily", (), "对话 / 方案")
        self.assertFalse(should_persist_activity_shell("grow", route))
        self.assertTrue(should_persist_activity_shell("daily", ActivityRoute("grow", (), "养 agent")))
        self.assertTrue(should_persist_activity_shell("grow", ActivityRoute("project", (), "项目")))

    def test_qa_on_grow_session_route_suggests_daily_but_owner_stays_grow(self) -> None:
        route = compute_activity_route(
            user_text="2+2",
            intent="qa",
            session=self.grow,
            paths=self.paths,
            pending_proposals=0,
        )
        self.assertEqual(route.shell, "daily")
        self.assertFalse(should_persist_activity_shell(self.grow.meta.active_shell, route))


if __name__ == "__main__":
    unittest.main()
