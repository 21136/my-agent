"""Phase 36 M1: project thread archive + 新开线 (IT-170 / IT-171)."""

from __future__ import annotations

import secrets
import shutil
import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from paths import AgentPaths, read_agent_state_payload
from project_api import perform_project_thread_new
from project_cli import ParsedProjectCommand, confirm_project_plan, run_project_command
from project_mode import create_project, normalize_project_id, project_dir
from project_switch import (
    PROJECT_THREAD_ARCHIVE_KEY,
    read_project_sessions,
    read_project_thread_archive,
    start_new_project_thread,
)
from session import create_new, session_history_event

from tests.isolation_helpers import make_temp_agent_paths


class ProjectThreadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.paths = make_temp_agent_paths(self, copy_tool_dirs=("common/write_text",))
        token = secrets.token_hex(4)
        self.project_id = f"thread-demo-{token}"
        self.other_project_id = f"thread-other-{token}"
        create_project(self.paths, self.project_id)
        create_project(self.paths, self.other_project_id)
        self.session = create_new(
            self.paths,
            conversation_id=f"_test_thread_{secrets.token_hex(4)}",
        )
        self._extra_session_ids: list[str] = []

    def tearDown(self) -> None:
        for pid in (self.project_id, self.other_project_id):
            shutil.rmtree(project_dir(self.paths, normalize_project_id(pid)), ignore_errors=True)
        session_ids = [self.session.conversation_id, *self._extra_session_ids]
        for sid in session_ids:
            shutil.rmtree(self.paths.data / "sessions" / sid, ignore_errors=True)

    def _open_project(self, project_id: str | None = None) -> None:
        pid = normalize_project_id(project_id or self.project_id)
        run_project_command(
            self.session,
            self.paths,
            ParsedProjectCommand(kind="open", project_id=pid),
            output_fn=lambda _line: None,
        )
        self.assertEqual(self.session.meta.project_id, pid)

    def test_read_project_thread_archive_missing_key(self) -> None:
        """IT-170: missing archive key behaves as empty."""
        self.assertEqual(read_project_thread_archive(self.paths), {})

    def test_new_thread_archives_old_line_and_keeps_binding(self) -> None:
        """IT-170: 新开线 archives previous live session and clears history."""
        self._open_project()
        old_id = self.session.conversation_id
        self.session.messages.append({"role": "user", "content": "DIRTY-CONTEXT"})
        self.session.save()

        updated, message = start_new_project_thread(self.paths, self.session)
        self._extra_session_ids.append(updated.conversation_id)

        self.assertNotEqual(updated.conversation_id, old_id)
        self.assertEqual(updated.meta.project_id, normalize_project_id(self.project_id))
        self.assertEqual(updated.messages, [])
        self.assertIn(old_id, message)

        pid = normalize_project_id(self.project_id)
        archive = read_project_thread_archive(self.paths)
        self.assertEqual(archive.get(pid), [old_id])
        self.assertEqual(read_project_sessions(self.paths).get(pid), updated.conversation_id)

        history = session_history_event(updated)
        texts = [str(item.get("text", "")) for item in history.get("items", [])]
        self.assertFalse(any("DIRTY-CONTEXT" in text for text in texts))

    def test_new_thread_preserves_plan_status_and_other_projects(self) -> None:
        """IT-171: confirmed plan survives; other project mappings unchanged."""
        self._open_project()
        confirm_project_plan(self.session)
        self.session.save()
        old_id = self.session.conversation_id

        result = run_project_command(
            self.session,
            self.paths,
            ParsedProjectCommand(kind="switch", project_id=self.other_project_id),
            output_fn=lambda _line: None,
        )
        if result.session is not None:
            self.session = result.session
        other_live_id = self.session.conversation_id
        if other_live_id != old_id:
            self._extra_session_ids.append(other_live_id)

        result = run_project_command(
            self.session,
            self.paths,
            ParsedProjectCommand(kind="switch", project_id=self.project_id),
            output_fn=lambda _line: None,
        )
        if result.session is not None:
            self.session = result.session

        updated, _msg = start_new_project_thread(self.paths, self.session)
        self._extra_session_ids.append(updated.conversation_id)

        self.assertEqual(updated.meta.project_plan_status, "confirmed")
        self.assertTrue(updated.meta.project_plan_confirmed_at)
        self.assertEqual(
            read_project_sessions(self.paths).get(normalize_project_id(self.other_project_id)),
            other_live_id,
        )

    def test_cli_new_thread_command(self) -> None:
        self._open_project()
        old_id = self.session.conversation_id
        result = run_project_command(
            self.session,
            self.paths,
            ParsedProjectCommand(kind="new_thread"),
            output_fn=lambda _line: None,
        )
        self.assertIsNotNone(result.session)
        assert result.session is not None
        self._extra_session_ids.append(result.session.conversation_id)
        self.assertNotEqual(result.session.conversation_id, old_id)
        self.assertEqual(result.session.meta.project_id, normalize_project_id(self.project_id))

    def test_perform_project_thread_new_emits_done(self) -> None:
        self._open_project()
        old_id = self.session.conversation_id
        updated, events = perform_project_thread_new(self.session, self.paths, {})
        self._extra_session_ids.append(updated.conversation_id)

        types = [event.get("type") for event in events]
        self.assertIn("project.thread.new.done", types)
        done = next(event for event in events if event.get("type") == "project.thread.new.done")
        self.assertTrue(done.get("session_replaced"))
        self.assertEqual(done.get("previous_session_id"), old_id)
        self.assertIn("session.history", types)

    def test_archive_persisted_in_state_json(self) -> None:
        self._open_project()
        old_id = self.session.conversation_id
        updated, _msg = start_new_project_thread(self.paths, self.session)
        self._extra_session_ids.append(updated.conversation_id)

        payload = read_agent_state_payload(self.paths)
        archive = payload.get(PROJECT_THREAD_ARCHIVE_KEY, {})
        pid = normalize_project_id(self.project_id)
        self.assertIsInstance(archive, dict)
        self.assertEqual(archive.get(pid), [old_id])


if __name__ == "__main__":
    unittest.main()
