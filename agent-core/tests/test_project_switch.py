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
from plan_agent import get_plan_agent
from project_api import perform_project_switch
from project_cli import ParsedProjectCommand, run_project_command
from project_mode import create_project, normalize_project_id, project_dir
from project_switch import PROJECT_SESSIONS_KEY, read_project_sessions
from server import WsBridge
from session import create_new, session_banner_event, session_history_event
from context import session_memory_event


class ProjectSwitchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.paths = AgentPaths.discover()
        token = secrets.token_hex(4)
        self.project_a = f"test-switch-a-{token}"
        self.project_b = f"test-switch-b-{token}"
        self.session = create_new(
            self.paths,
            conversation_id=f"_test_proj_switch_{secrets.token_hex(4)}",
        )
        self._extra_session_ids: list[str] = []
        self._state_before = self._read_state()
        self._project_sessions_before = dict(read_project_sessions(self.paths))
        self.addCleanup(self._cleanup)

    def _read_state(self) -> dict:
        state_path = self.paths.data / "state.json"
        if not state_path.is_file():
            return {}
        try:
            loaded = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return loaded if isinstance(loaded, dict) else {}

    def _cleanup(self) -> None:
        for pid in (self.project_a, self.project_b):
            shutil.rmtree(project_dir(self.paths, normalize_project_id(pid)), ignore_errors=True)
        session_ids = [self.session.conversation_id, *self._extra_session_ids]
        for sid in session_ids:
            shutil.rmtree(self.paths.data / "sessions" / sid, ignore_errors=True)

        mapping = dict(self._project_sessions_before)
        for pid in (self.project_a, self.project_b):
            mapping.pop(normalize_project_id(pid), None)
        payload = dict(self._state_before)
        if mapping:
            payload[PROJECT_SESSIONS_KEY] = mapping
        else:
            payload.pop(PROJECT_SESSIONS_KEY, None)
        state_path = self.paths.data / "state.json"
        if payload:
            state_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        elif state_path.is_file():
            state_path.unlink(missing_ok=True)

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

    def test_reopen_emits_persisted_plan_suggestions(self) -> None:
        """IT-5821: project resume restores the persisted adoption queue."""
        self._open_project_a()
        agent = get_plan_agent(self.paths, normalize_project_id(self.project_a))
        agent.park_gated_suggestion(
            {
                "id": "sug-resume-test",
                "kind": "apply_patch",
                "title": "恢复提案",
                "body": "重开项目后仍应可审阅",
                "risk": "gate",
                "action": "apply_patch",
                "payload": {"path": "DESIGN.md", "diff": ""},
            }
        )

        events: list[dict] = []
        WsBridge(emit=events.append, paths=self.paths).emit_session_state(self.session)

        plan_state = next(event for event in events if event.get("type") == "project.plan.state")
        ids = {item.get("id") for item in plan_state.get("suggestions", [])}
        self.assertIn("sug-resume-test", ids)


if __name__ == "__main__":
    unittest.main()
