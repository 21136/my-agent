"""Activity router project signals (T-1104 / T-1804-06)."""

from __future__ import annotations

import secrets
import shutil
import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from activity_router import compute_activity_route, compute_session_route
from paths import AgentPaths
from session import create_new


class ActivityRouterProjectTests(unittest.TestCase):
    """T-1804-06: project markers and bound sessions route to project shell."""

    def setUp(self) -> None:
        self.paths = AgentPaths.discover()
        self.session = create_new(
            self.paths,
            conversation_id=f"_test_activity_router_{secrets.token_hex(4)}",
        )
        self.addCleanup(self._cleanup)

    def _cleanup(self) -> None:
        shutil.rmtree(self.session.session_dir, ignore_errors=True)

    def _route(
        self,
        user_text: str,
        *,
        intent: str = "execute",
        pending_proposals: int = 0,
    ):
        return compute_activity_route(
            user_text=user_text,
            intent=intent,
            session=self.session,
            paths=self.paths,
            pending_proposals=pending_proposals,
        )

    def test_project_markers_route_to_project(self) -> None:
        cases = (
            "项目 新建 stab-demo",
            "项目 打开 stab-demo",
            "做项目 stab-demo",
            "项目模式",
            "看看 workspace/stab-r1-demo/TASKS.md",
        )
        for text in cases:
            with self.subTest(text=text):
                route = self._route(text)
                self.assertEqual(route.shell, "project")
                self.assertIn("项目", route.reason)

    def test_bound_project_session_continues_project(self) -> None:
        self.session.meta.project_root = "workspace/stab-r1-demo"
        self.session.meta.project_id = "stab-r1-demo"
        self.session.meta.active_shell = "project"
        self.session.meta.project_plan_status = "confirmed"

        route = self._route("继续填 TASKS.md", intent="execute")
        self.assertEqual(route.shell, "project")
        self.assertIn("项目", route.reason)

    def test_plan_gate_open_routes_to_project_over_proposals(self) -> None:
        self.session.meta.project_root = "workspace/stab-r1-demo"
        self.session.meta.project_id = "stab-r1-demo"
        self.session.meta.active_shell = "project"
        self.session.meta.project_plan_status = "draft"

        route = self._route("开始写代码", pending_proposals=5)
        self.assertEqual(route.shell, "project")
        self.assertIn("计划待确认", route.reason)

    def test_execute_workspace_path_routes_to_project(self) -> None:
        route = self._route("改 workspace/stab-r1-demo/main.py", intent="execute")
        self.assertEqual(route.shell, "project")
        self.assertIn("workspace", route.reason)

    def test_explicit_grow_signal_overrides_bound_project(self) -> None:
        self.session.meta.project_root = "workspace/stab-r1-demo"
        self.session.meta.project_id = "stab-r1-demo"
        self.session.meta.active_shell = "project"
        self.session.meta.project_plan_status = "confirmed"

        route = self._route("帮我 write_evolve 新工具 proposal", intent="execute")
        self.assertEqual(route.shell, "grow")

    def test_compute_session_route_resumes_project_shell(self) -> None:
        self.session.meta.project_root = "workspace/stab-r1-demo"
        self.session.meta.project_id = "stab-r1-demo"
        self.session.meta.active_shell = "project"

        route = compute_session_route(self.session, self.paths)
        self.assertEqual(route.shell, "project")
        self.assertIn("stab-r1-demo", route.reason)


if __name__ == "__main__":
    unittest.main()
