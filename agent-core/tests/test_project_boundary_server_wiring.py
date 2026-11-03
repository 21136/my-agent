from __future__ import annotations

import asyncio
import sys
import threading
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from day_journal import DayJournal
from server import WsBridge, WsSessionHandler, _record_project_switch_boundary, run_server
from paths import AgentPaths
from session import Session, SessionMeta


class ProjectBoundaryServerWiringTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="project-boundary-wiring-")
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.paths = AgentPaths(root, root / "workspace", root / "data", root / "evolve")
        for path in (self.paths.workspace, self.paths.data, self.paths.evolve):
            path.mkdir(parents=True, exist_ok=True)
        self.journal = DayJournal(root / "journal")

    def session(self, session_id: str, project_id: str) -> Session:
        session_dir = self.paths.data / "sessions" / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        session = Session(
            conversation_id=session_id,
            session_dir=session_dir,
            goal="synthetic",
            meta=SessionMeta(project_id=project_id, project_root=f"workspace/{project_id}"),
            messages=[],
            paths=self.paths,
        )
        session.save()
        return session

    def test_real_switch_records_explicit_event_injected_journal(self) -> None:
        session = self.session("session-a", "project-a")
        calls = []

        def recorder(session, *, occurred_at, event_id, journal):
            calls.append((session, occurred_at, event_id, journal))
            return __import__("day_journal_events").record_project_boundary(
                session, occurred_at=occurred_at, event_id=event_id, journal=journal
            )

        result = _record_project_switch_boundary(
            session=session,
            switch_event={"type": "project.switch.done", "action": "bind_current", "request_id": "req-1"},
            recorder=recorder,
            journal=self.journal,
        )
        self.assertTrue(result)
        self.assertEqual(len(calls), 1)
        self.assertIs(calls[0][0], session)
        self.assertEqual(calls[0][2:], ("req-1", self.journal))
        rows = self.journal.read_day(calls[0][1].astimezone(self.journal._zone).date())
        self.assertEqual(rows["segments"][0]["project_id"], "project-a")
        self.assertEqual(rows["segments"][0]["occurred_at"], calls[0][1].isoformat())

    def test_failed_noop_and_missing_switch_event_are_not_recorded(self) -> None:
        session = self.session("session-b", "project-b")
        calls = []
        recorder = lambda *args, **kwargs: calls.append((args, kwargs))
        for event in (
            None,
            {"type": "project.switch.preview", "action": "load_session", "request_id": "req-2"},
            {"type": "project.switch.done", "action": "noop", "request_id": "req-3"},
        ):
            self.assertFalse(_record_project_switch_boundary(
                session=session, switch_event=event, recorder=recorder, journal=self.journal
            ))
        self.assertEqual(calls, [])

    def test_recorder_failure_is_reported_without_raising_or_changing_switch_result(self) -> None:
        session = self.session("session-c", "project-c")
        notices = []

        def fail(*_args, **_kwargs):
            raise OSError("journal unavailable")

        result = _record_project_switch_boundary(
            session=session,
            switch_event={"type": "project.switch.done", "action": "load_session", "request_id": "req-4"},
            recorder=fail,
            journal=self.journal,
            report=notices.append,
        )
        self.assertFalse(result)
        self.assertEqual(len(notices), 1)
        self.assertIn("switch succeeded", notices[0].lower())
        self.assertIn("recording failed", notices[0].lower())
        self.assertNotIn("journal unavailable", notices[0])

    def test_handler_rebinds_to_persisted_session_before_recording(self) -> None:
        original = self.session("session-old", "project-old")
        updated = self.session("session-new", "project-new")
        observed = []
        observed_thread_ids = []
        events = []
        bridge = WsBridge(emit=events.append, paths=self.paths)
        repl = SimpleNamespace(session=original)

        def rebind_agent():
            repl.agent = SimpleNamespace(
                executor=SimpleNamespace(
                    session=SimpleNamespace(refresh_bound_project_meta=lambda: None)
                ),
            )

        repl._rebind_agent = rebind_agent
        repl._rebind_agent()

        def recorder(session, *, occurred_at, event_id, journal):
            observed.append(session)
            observed_thread_ids.append(threading.get_ident())
            return __import__("day_journal_events").record_project_boundary(
                session, occurred_at=occurred_at, event_id=event_id, journal=journal
            )

        handler = WsSessionHandler(
            self.paths, project_boundary_recorder=recorder, project_boundary_journal=self.journal
        )
        payload = {
            "_session": updated,
            "_events": [{"type": "project.switch.done", "action": "load_session", "request_id": "req-rebind"}],
        }

        async def dispatch() -> None:
            enable = asyncio.create_task(
                handler._handle_project_journal_command(handler.PROJECT_JOURNAL_ENABLE_PHRASE, bridge, original.conversation_id)
            )
            for _ in range(100):
                if bridge._pending_confirm_id:
                    break
                await asyncio.sleep(0.01)
            self.assertTrue(bridge._pending_confirm_id, "enable confirmation was not shown")
            self.assertTrue(bridge.deliver_confirm(bridge._pending_confirm_id, "y"))
            await enable
            self.assertTrue(bridge.project_journal_enabled)
            with patch("project_api.dispatch_project_message", return_value=payload):
                await handler._dispatch_project({"type": "project.switch"}, repl, bridge)

        caller_thread = threading.get_ident()
        asyncio.run(dispatch())
        self.assertIs(repl.session, updated)
        self.assertEqual(observed, [updated])
        self.assertEqual(len(observed_thread_ids), 1)
        self.assertNotEqual(observed_thread_ids[0], caller_thread)
        self.assertEqual(updated.meta.project_id, "project-new")
        self.assertTrue(any(event.get("type") == "project.switch.done" for event in events))

    def test_run_server_passes_explicit_boundary_dependencies_to_handler(self) -> None:
        context = SimpleNamespace(
            installation_id="install", release_id="release", binding_digest="binding",
            runtime_digest="runtime", nonce="nonce",
        )
        captured = {}
        recorder = object()
        journal = object()

        class FakeServer:
            sockets = [SimpleNamespace(getsockname=lambda: ("127.0.0.1", 12345))]

            async def serve_forever(self):
                return None

        class FakeServe:
            async def __aenter__(self):
                return FakeServer()

            async def __aexit__(self, *_args):
                return False

        def handler_factory(*args, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(handle=lambda _websocket: None)

        async def start() -> int:
            with patch("server.AgentPaths.from_managed_readonly", return_value=self.paths), \
                 patch("server.WsSessionHandler", side_effect=handler_factory), \
                 patch("server.serve", side_effect=lambda *_args, **_kwargs: FakeServe()):
                return await run_server(
                    "127.0.0.1", 0, managed_readonly_context=context,
                    project_boundary_recorder=recorder, project_boundary_journal=journal,
                )

        self.assertEqual(asyncio.run(start()), 0)
        self.assertIs(captured["project_boundary_recorder"], recorder)
        self.assertIs(captured["project_boundary_journal"], journal)

    def test_default_opt_out_does_not_record(self) -> None:
        session = self.session("session-d", "project-d")
        self.assertFalse(_record_project_switch_boundary(
            session=session,
            switch_event={"type": "project.switch.done", "action": "bind_current", "request_id": "req-5"},
            recorder=None,
            journal=None,
        ))
        self.assertIsNone(self.journal.read_day(__import__("datetime").date.today()))


if __name__ == "__main__":
    unittest.main()
