from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from main import ConversationRepl, WorkRecordControllerBindings
from server import WsBridge, _build_repl
from paths import AgentPaths
from session import Session, SessionMeta


class WorkRecordSessionWiringTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="synthetic-work-record-session-")
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.paths = AgentPaths(root, root / "workspace", root / "data", root / "evolve")
        for path in (self.paths.workspace, self.paths.data, self.paths.evolve):
            path.mkdir(parents=True, exist_ok=True)

    def session(self, conversation_id: str) -> Session:
        session_dir = self.paths.data / "sessions" / conversation_id
        session_dir.mkdir(parents=True, exist_ok=True)
        return Session(
            conversation_id=conversation_id,
            session_dir=session_dir,
            goal="synthetic only",
            meta=SessionMeta(),
            messages=[],
            paths=self.paths,
        )

    @staticmethod
    def repl(session: Session, **kwargs) -> ConversationRepl:
        return ConversationRepl.from_session(
            session,
            input_fn=lambda _prompt: "",
            output_fn=lambda _text: None,
            **kwargs,
        )

    def test_default_is_unbound_and_does_not_call_factory(self) -> None:
        repl = self.repl(self.session("default"))
        self.assertFalse(repl.agent.enable_work_record_recall)
        self.assertIsNone(repl.agent.work_record_recall_controller)
        self.assertFalse(repl.agent.enable_work_record_recording)
        self.assertIsNone(repl.agent.work_record_recording_controller)

    def test_explicit_factory_binds_controllers_to_current_agent_and_rebinds(self) -> None:
        observed = []

        def factory(session, agent):
            store = SimpleNamespace(_session=session)
            recall = SimpleNamespace(session=session, agent=agent, _agent=agent, _store=store)
            recording = SimpleNamespace(session=session, agent=agent, _agent=agent, _store=store)
            observed.append((session, agent, recall, recording))
            return WorkRecordControllerBindings(recall=recall, recording=recording)

        first = self.session("first")
        repl = self.repl(first, work_record_controller_factory=factory)
        first_agent = repl.agent
        self.assertTrue(first_agent.enable_work_record_recall)
        self.assertTrue(first_agent.enable_work_record_recording)
        self.assertIs(first_agent.work_record_recall_controller.session, first)
        self.assertIs(first_agent.work_record_recording_controller.agent, first_agent)

        second = self.session("second")
        repl.session = second
        repl._rebind_agent()

        self.assertEqual(len(observed), 2)
        self.assertIs(observed[0][0], first)
        self.assertIs(observed[0][1], first_agent)
        self.assertIs(observed[1][0], second)
        self.assertIs(repl.agent.session, second)
        self.assertIs(repl.agent.work_record_recall_controller, observed[1][2])
        self.assertIs(repl.agent.work_record_recording_controller, observed[1][3])
        self.assertIsNot(repl.agent.work_record_recall_controller, first_agent.work_record_recall_controller)

    def test_rebind_rejects_cached_controller_from_previous_session(self) -> None:
        first_bindings = None

        def factory(session, agent):
            nonlocal first_bindings
            if first_bindings is not None:
                return first_bindings
            store = SimpleNamespace(_session=session)
            first_bindings = WorkRecordControllerBindings(
                recall=SimpleNamespace(_agent=agent, _store=store),
            )
            return first_bindings

        first = self.session("cached-first")
        repl = self.repl(first, work_record_controller_factory=factory)
        first_agent = repl.agent
        repl.session = self.session("cached-second")

        with self.assertRaisesRegex(ValueError, "current Agent and Session"):
            repl._rebind_agent()

        self.assertIsNot(repl.agent, first_agent)
        self.assertIs(repl.agent.session, repl.session)
        self.assertFalse(repl.agent.enable_work_record_recall)
        self.assertIsNone(repl.agent.work_record_recall_controller)

    def test_server_repl_builder_forwards_explicit_factory(self) -> None:
        session = self.session("server-builder")
        calls = []

        def factory(current_session, agent):
            marker = SimpleNamespace(
                session=current_session,
                agent=agent,
                _agent=agent,
                _store=SimpleNamespace(_session=current_session),
            )
            calls.append((current_session, agent))
            return WorkRecordControllerBindings(recall=marker)

        bridge = WsBridge(emit=lambda _event: None, paths=self.paths)
        repl = _build_repl(
            session,
            self.paths,
            bridge,
            work_record_controller_factory=factory,
        )

        self.assertEqual(len(calls), 1)
        self.assertIs(calls[0][0], session)
        self.assertIs(calls[0][1], repl.agent)
        self.assertTrue(repl.agent.enable_work_record_recall)
        self.assertFalse(repl.agent.enable_work_record_recording)

    def test_default_desktop_server_binds_recall_to_current_session_without_provider_call(self) -> None:
        session = self.session("server-default-recall")
        bridge = WsBridge(emit=lambda _event: None, paths=self.paths)
        repl = _build_repl(session, self.paths, bridge)

        controller = repl.agent.work_record_recall_controller
        self.assertTrue(repl.agent.enable_work_record_recall)
        self.assertIs(controller._agent, repl.agent)
        self.assertIs(controller._store._session, session)
        self.assertEqual(controller._store.list_items(), [])

    def test_empty_factory_result_keeps_controllers_disabled(self) -> None:
        repl = self.repl(
            self.session("empty-factory"),
            work_record_controller_factory=lambda _session, _agent: WorkRecordControllerBindings(),
        )
        self.assertFalse(repl.agent.enable_work_record_recall)
        self.assertFalse(repl.agent.enable_work_record_recording)
        self.assertIsNone(repl.agent.work_record_recall_controller)
        self.assertIsNone(repl.agent.work_record_recording_controller)


if __name__ == "__main__":
    unittest.main()
