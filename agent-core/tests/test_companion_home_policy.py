"""Main home companion must not bind session project write lease."""

from __future__ import annotations

import secrets
import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from companion_home_policy import companion_home_rejects_session_bind
from project_cli import bind_project_session
from project_mode import ProjectModeError, create_project, normalize_project_id
from project_switch import execute_project_switch, plan_project_switch
from session import create_new
from tests.isolation_helpers import make_temp_agent_paths


class CompanionHomePolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.paths = make_temp_agent_paths(self)
        token = secrets.token_hex(2)
        self.pid = f"home-policy-{token}"
        create_project(self.paths, self.pid)
        self.session = create_new(self.paths, conversation_id=f"_home_{secrets.token_hex(3)}")

    def test_home_grow_session_registers_without_bind(self) -> None:
        self.assertTrue(companion_home_rejects_session_bind(self.session))
        plan = plan_project_switch(self.paths, self.session, self.pid)
        self.assertEqual(plan.action, "register_only")
        from project_switch import open_project_on_session

        opened, msg = open_project_on_session(self.paths, self.session, self.pid)
        self.assertFalse((opened.meta.project_id or "").strip(), msg)
        self.assertIn("登记", msg)

    def test_home_grow_erroneous_bind_blocks_cross_project_switch(self) -> None:
        other = f"home-other-{secrets.token_hex(2)}"
        create_project(self.paths, other)
        bind_project_session(self.session, self.pid)
        self.session.meta.active_shell = "grow"
        self.session.save()
        with self.assertRaises(ProjectModeError) as ctx:
            plan_project_switch(self.paths, self.session, other)
        self.assertIn("主界面小魔王", str(ctx.exception))

    def test_bind_current_coerced_to_register_only_on_home_shell(self) -> None:
        bind_project_session(self.session, self.pid)
        self.session.meta.active_shell = "grow"
        self.session.save()
        plan = plan_project_switch(self.paths, self.session, self.pid)
        self.assertEqual(plan.action, "noop")

        plan_forced = type(plan)(
            action="bind_current",
            project_id=normalize_project_id(self.pid),
            session_id=self.session.conversation_id,
            message="would bind",
        )
        session, msg = execute_project_switch(self.paths, self.session, plan_forced)
        self.assertFalse((session.meta.project_id or "").strip())
        self.assertIn("未绑定", msg)

    def test_project_shell_allows_bind_dir_path(self) -> None:
        self.session.meta.active_shell = "project"
        self.assertFalse(companion_home_rejects_session_bind(self.session))


if __name__ == "__main__":
    unittest.main()
