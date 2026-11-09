"""Companion batch project trash and unbound project.open."""

from __future__ import annotations

import secrets
import sys
import unittest
import uuid
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from project_cli import bind_project_session, clear_session_project_binding
from project_mode import create_project, normalize_project_id, project_dir
from project_switch import lookup_project_session
from project_workspace_trash import resolve_trash_project_ids, trash_workspace_projects
from session import create_new
from tests.isolation_helpers import make_temp_agent_paths
from tools.executor import ExecutorSession, ToolExecutor
from tools.registry import ToolRegistry
from work_card_disposition import WorkCardDisposition
from work_card_ledger import WorkCardLedger
from work_records import WorkRecordStore
from work_record_cards import build_project_work_record_cards


class TrashWorkspaceProjectsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.paths = make_temp_agent_paths(self)
        token = secrets.token_hex(2)
        self.pid_a = f"random-number-a-{token}"
        self.pid_b = f"random-number-b-{token}"

    def test_resolve_glob_patterns(self) -> None:
        create_project(self.paths, self.pid_a)
        create_project(self.paths, self.pid_b)
        create_project(self.paths, "other-app")
        selected = resolve_trash_project_ids(self.paths, glob_pattern="random-number*")
        self.assertEqual(
            selected,
            sorted([normalize_project_id(self.pid_a), normalize_project_id(self.pid_b)]),
        )

    def test_batch_soft_trash_from_bound_companion_clears_binding(self) -> None:
        create_project(self.paths, self.pid_a)
        create_project(self.paths, self.pid_b)
        session = create_new(self.paths, conversation_id=f"_trash_{secrets.token_hex(3)}")
        bind_project_session(session, self.pid_b)
        session.save()

        payload = trash_workspace_projects(
            self.paths,
            glob_pattern="random-number*",
            bound_session=session,
        )
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["cleared_session_binding"], normalize_project_id(self.pid_b))
        self.assertFalse((session.meta.project_id or "").strip())
        self.assertFalse(project_dir(self.paths, self.pid_a).exists())
        self.assertFalse(project_dir(self.paths, self.pid_b).exists())

    def test_trash_cascades_work_cards(self) -> None:
        pid = f"cascade-{secrets.token_hex(2)}"
        create_project(self.paths, pid)
        session = create_new(self.paths, conversation_id=f"20261109-{secrets.token_hex(4)}")
        bind_project_session(session, pid)
        session.save()
        store = WorkRecordStore(session)
        ledger = WorkCardLedger(session, store)
        snapshot = build_project_work_record_cards(store, pid)
        saved = ledger.save_snapshot(str(uuid.uuid4()), snapshot)
        self.assertEqual(saved.status, "saved")

        payload = trash_workspace_projects(
            self.paths,
            project_ids=[pid],
            request_id=str(uuid.uuid4()),
            bound_session=session,
        )
        row = payload["results"][0]
        self.assertEqual(row["status"], "trashed")
        self.assertGreaterEqual(row["work_cards_trashed"], 1)

    def test_companion_open_does_not_bind(self) -> None:
        pid = f"open-{secrets.token_hex(2)}"
        create_project(self.paths, pid)
        session = create_new(self.paths, conversation_id=f"_open_{secrets.token_hex(3)}")
        registry = ToolRegistry.load(self.paths)
        executor = ToolExecutor(
            registry=registry,
            session=ExecutorSession.load(session.session_dir),
            confirm_fn=lambda _p, _a: "y",
        )
        result = executor.run("propose_context_switch", {"action": "project.open", "target": pid})
        self.assertTrue(result.ok, result.error.message if result.error else None)
        from session import Session

        loaded = Session.load(self.paths, session.conversation_id)
        self.assertFalse((loaded.meta.project_id or "").strip())
        self.assertEqual(
            lookup_project_session(self.paths, normalize_project_id(pid)),
            session.conversation_id,
        )

    def test_clear_session_project_binding_helper(self) -> None:
        pid = f"rel-{secrets.token_hex(2)}"
        create_project(self.paths, pid)
        session = create_new(self.paths, conversation_id=f"_rel_{secrets.token_hex(3)}")
        bind_project_session(session, pid)
        self.assertTrue(clear_session_project_binding(session))
        session.save()
        self.assertEqual(session.meta.active_shell, "grow")
        self.assertFalse(session.meta.project_id)


if __name__ == "__main__":
    unittest.main()
