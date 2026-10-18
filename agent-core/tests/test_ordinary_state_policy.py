# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from project_cli import (
    confirm_direct_implement,
    confirm_project_design,
    confirm_project_plan,
    organize_project_plan,
    parse_project_command,
    run_project_command,
)
from project_mode import project_dir
from session import create_new
from tests.isolation_helpers import make_temp_agent_paths


class OrdinaryProjectStatePolicyTests(unittest.TestCase):
    def _new(self, name: str):
        paths = make_temp_agent_paths(self, prefix="ordinary-state-")
        session = create_new(paths, conversation_id=name)
        command = parse_project_command(f"项目 新建 {name}")
        assert command is not None
        run_project_command(session, paths, command, output_fn=lambda _text: None)
        return session, paths

    def test_direct_implement_arms_request_without_persisting_stage_or_plan_gate(self) -> None:
        session, _paths = self._new("ordinary-direct")
        before = (
            session.meta.project_workflow_stage,
            session.meta.project_plan_status,
            session.meta.project_entry,
        )
        message = confirm_direct_implement(session)
        self.assertIn("直接实现", message)
        self.assertEqual(
            (
                session.meta.project_workflow_stage,
                session.meta.project_plan_status,
                session.meta.project_entry,
            ),
            before,
        )
        self.assertEqual(session.next_work_intent, "execute")

    def test_organize_keeps_ordinary_stage_as_legacy_snapshot(self) -> None:
        session, paths = self._new("ordinary-organize")
        before = (session.meta.project_workflow_stage, session.meta.project_plan_status)
        organize_project_plan(session, paths)
        self.assertEqual(
            (session.meta.project_workflow_stage, session.meta.project_plan_status),
            before,
        )
        self.assertFalse((project_dir(paths, "ordinary-organize") / "TASKS.md").is_file())

    def test_confirm_design_records_fact_without_advancing_stage(self) -> None:
        session, paths = self._new("ordinary-design")
        organize_project_plan(session, paths)
        self.assertEqual(session.meta.project_workflow_stage, "requirements")
        session.meta.project_workflow_stage = "documentation"
        before_status = session.meta.project_plan_status
        confirm_project_design(session, paths)
        self.assertEqual(session.meta.project_workflow_stage, "documentation")
        self.assertEqual(session.meta.project_plan_status, before_status)
        self.assertTrue(session.meta.project_design_confirmed_at)

    def test_project_state_marks_legacy_needs_fields_as_nonblocking(self) -> None:
        from project_api import project_state_payload

        session, paths = self._new("ordinary-state-payload")
        payload = project_state_payload(session, paths)
        self.assertEqual(payload["ordinary_permission_source"], "request_intent_path_risk")
        self.assertFalse(payload["ordinary_stage_gates_blocking"])
        self.assertNotIn("needs_plan_confirm", payload)
        self.assertIn("needs_design_confirm", payload)
        self.assertIn("needs_documentation", payload)



    def test_explicit_plan_confirmation_records_approval_without_stage_transition(self) -> None:
        session, _paths = self._new("ordinary-confirm")
        session.meta.project_workflow_stage = "design"
        confirm_project_plan(session)
        self.assertEqual(session.meta.project_workflow_stage, "design")
        self.assertEqual(session.meta.project_plan_status, "confirmed")
        self.assertTrue(session.meta.project_plan_confirmed_at)
        self.assertEqual(session.next_work_intent, "execute")


if __name__ == "__main__":
    unittest.main()
