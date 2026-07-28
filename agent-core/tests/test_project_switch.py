"""IT-02 / T-1803-04: project.switch events via perform_project_switch."""

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

from paths import AgentPaths
from project_api import perform_project_switch
from project_cli import ParsedProjectCommand, run_project_command
from project_mode import create_project, normalize_project_id, project_dir
from project_switch import PROJECT_SESSIONS_KEY, read_project_sessions
from server import WsBridge
from session import create_new, session_banner_event, session_history_event
from context import session_memory_event

from tests.isolation_helpers import make_temp_agent_paths


class ProjectSwitchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.paths = make_temp_agent_paths(self)
        token = secrets.token_hex(4)
        self.project_a = f"test-switch-a-{token}"
        self.project_b = f"test-switch-b-{token}"
        self.session = create_new(
            self.paths,
            conversation_id=f"_test_proj_switch_{secrets.token_hex(4)}",
        )
        self._extra_session_ids: list[str] = []

    def _seed_project_b(self) -> None:
        create_project(self.paths, self.project_b)

    def _open_project_a(self) -> None:
        run_project_command(
            self.session,
            self.paths,
            ParsedProjectCommand(kind="new", project_id=self.project_a),
            output_fn=lambda _line: None,
        )
        self.assertEqual(self.session.meta.project_id, normalize_project_id(self.project_a))

    def test_perform_project_switch_emits_done(self) -> None:
        """T-1803-04: cross-project switch returns project.switch.done (S-09)."""
        self._seed_project_b()
        self._open_project_a()
        original_session_id = self.session.conversation_id

        updated, events = perform_project_switch(
            self.session,
            self.paths,
            {
                "project_id": self.project_b,
                "confirm": True,
                "request_id": "test-switch-req-1",
            },
        )
        self._extra_session_ids.append(updated.conversation_id)

        event_types = [event.get("type") for event in events]
        self.assertIn("project.switch.done", event_types)

        done = next(event for event in events if event.get("type") == "project.switch.done")
        pid_b = normalize_project_id(self.project_b)
        self.assertEqual(done.get("project_id"), pid_b)
        self.assertEqual(done.get("request_id"), "test-switch-req-1")
        self.assertTrue(done.get("session_replaced"))
        self.assertEqual(done.get("session_id"), updated.conversation_id)
        self.assertNotEqual(updated.conversation_id, original_session_id)
        self.assertEqual(updated.meta.project_id, pid_b)
        self.assertIn(done.get("action"), {"new_session", "load_session"})
        self.assertIn("project.state", event_types)
        self.assertIn("session.banner", event_types)

    def test_perform_project_switch_requests_confirm_without_flag(self) -> None:
        """Cross-project switch without confirm emits project.switch.request only."""
        self._seed_project_b()
        self._open_project_a()

        _updated, events = perform_project_switch(
            self.session,
            self.paths,
            {"project_id": self.project_b, "request_id": "test-switch-req-2"},
        )

        event_types = [event.get("type") for event in events]
        self.assertIn("project.switch.request", event_types)
        self.assertNotIn("project.switch.done", event_types)

        preview = next(
            event for event in events if event.get("type") == "project.switch.request"
        )
        self.assertTrue(preview.get("needs_confirm"))
        self.assertEqual(preview.get("request_id"), "test-switch-req-2")
        self.assertEqual(preview.get("project_id"), normalize_project_id(self.project_b))

    def test_session_replaced_emits_memory_and_history(self) -> None:
        """T-1803-05: session_replaced includes session.memory + session.history (IT-06)."""
        self._seed_project_b()
        self._open_project_a()
        self.session.messages.append({"role": "user", "content": "MARKER-SESSION-A"})
        self.session.save()

        updated_b, events = perform_project_switch(
            self.session,
            self.paths,
            {
                "project_id": self.project_b,
                "confirm": True,
                "request_id": "test-switch-req-3",
            },
        )
        self._extra_session_ids.append(updated_b.conversation_id)

        done = next(event for event in events if event.get("type") == "project.switch.done")
        self.assertTrue(done.get("session_replaced"))

        event_types = [event.get("type") for event in events]
        self.assertIn("session.memory", event_types)
        self.assertIn("session.history", event_types)

        memory_evt = next(event for event in events if event.get("type") == "session.memory")
        history_evt = next(event for event in events if event.get("type") == "session.history")
        self.assertEqual(memory_evt, session_memory_event(updated_b))
        self.assertEqual(history_evt, session_history_event(updated_b))
        self.assertIsInstance(history_evt.get("items"), list)

        updated_b.messages.append({"role": "user", "content": "MARKER-SESSION-B"})
        updated_b.save()

        resumed, resume_events = perform_project_switch(
            self.session,
            self.paths,
            {
                "project_id": self.project_b,
                "confirm": True,
                "request_id": "test-switch-req-4",
            },
        )
        self.assertEqual(resumed.conversation_id, updated_b.conversation_id)
        resume_types = [event.get("type") for event in resume_events]
        self.assertIn("session.memory", resume_types)
        self.assertIn("session.history", resume_types)
        history = next(event for event in resume_events if event.get("type") == "session.history")
        texts = [str(item.get("text", "")) for item in history.get("items", [])]
        self.assertTrue(any("MARKER-SESSION-B" in text for text in texts))

        bridge_events: list[dict] = []
        WsBridge(emit=bridge_events.append, paths=self.paths).emit_session_state(resumed)
        bridge_types = [event.get("type") for event in bridge_events]
        self.assertIn("session.banner", bridge_types)
        self.assertIn("session.memory", bridge_types)
        self.assertIn("session.history", bridge_types)
        self.assertEqual(
            next(event for event in bridge_events if event.get("type") == "session.banner"),
            session_banner_event(resumed),
        )
        self.assertEqual(
            next(event for event in bridge_events if event.get("type") == "session.memory"),
            session_memory_event(resumed),
        )
        self.assertEqual(
            next(event for event in bridge_events if event.get("type") == "session.history"),
            session_history_event(resumed),
        )


if __name__ == "__main__":
    unittest.main()
