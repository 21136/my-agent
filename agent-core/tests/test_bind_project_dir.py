from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from project_cli import bind_external_project_dir, import_external_project
from project_mode import ProjectModeError, project_dir
from session import create_new
from tests.isolation_helpers import make_temp_agent_paths
from tools.builtin import bind_project_dir
from tools.registry import ToolRegistry
from tools.schema import ToolErrorCode


class BindProjectDirTests(unittest.TestCase):
    def setUp(self) -> None:
        self.paths = make_temp_agent_paths(self, prefix="bind-dir-")
        self.external = Path(tempfile.mkdtemp(prefix="bind-dir-source-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.external, ignore_errors=True))
        (self.external / "README.md").write_text("# Existing\n", encoding="utf-8")
        (self.external / "app.py").write_text("print('hi')\n", encoding="utf-8")
        self.session = create_new(self.paths, conversation_id="bind-dir-session")

    def test_tool_dry_run_scan_only(self) -> None:
        result = bind_project_dir.run(
            {"path": str(self.external), "project_id": "bind-demo", "dry_run": True},
            paths=self.paths,
            session=self.session,
        )
        self.assertTrue(result.ok, result.error)
        assert result.data is not None
        self.assertEqual(result.data["action"], "scan")
        self.assertFalse(result.data["applied"])
        self.assertFalse((self.paths.workspace / "bind-demo").exists())
        self.assertEqual(self.session.meta.project_id, "")

    def test_tool_rejects_protected_roots(self) -> None:
        result = bind_project_dir.run(
            {"path": str(self.paths.agent_root), "project_id": "bad"},
            paths=self.paths,
            session=self.session,
        )
        self.assertFalse(result.ok)
        assert result.error is not None
        self.assertEqual(result.error.code, ToolErrorCode.PATH_DENIED)
        self.assertIn("禁止接入", result.error.message)

    def test_tool_apply_rejected_on_main_home_companion(self) -> None:
        self.assertEqual(self.session.meta.active_shell, "grow")
        result = bind_project_dir.run(
            {"path": str(self.external), "project_id": "bind-demo"},
            paths=self.paths,
            session=self.session,
        )
        self.assertFalse(result.ok)
        assert result.error is not None
        self.assertEqual(result.error.code, ToolErrorCode.PERMISSION_DENIED)
        self.assertIn("主界面小魔王", result.error.message)
        self.assertFalse((self.session.meta.project_id or "").strip())

    def test_tool_apply_binds_dedicated_project_session(self) -> None:
        self.session.meta.active_shell = "project"
        result = bind_project_dir.run(
            {"path": str(self.external), "project_id": "bind-demo"},
            paths=self.paths,
            session=self.session,
        )
        self.assertTrue(result.ok, result.error)
        assert result.data is not None
        self.assertEqual(result.data["action"], "imported")
        self.assertTrue(result.data["applied"])
        self.assertEqual(self.session.meta.project_id, "bind-demo")
        self.assertEqual(self.session.meta.active_shell, "project")
        self.assertEqual(self.session.next_work_intent, "execute")
        self.assertEqual(
            (project_dir(self.paths, "bind-demo") / "app.py").resolve(),
            (self.external / "app.py").resolve(),
        )

    def test_rebind_when_session_already_has_project(self) -> None:
        other = Path(tempfile.mkdtemp(prefix="bind-dir-other-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(other, ignore_errors=True))
        (other / "README.md").write_text("# Other\n", encoding="utf-8")

        self.session.meta.active_shell = "project"
        first = bind_external_project_dir(
            self.session, self.paths, str(self.external), project_id="first-app"
        )
        self.assertEqual(first["project_id"], "first-app")
        self.assertEqual(self.session.meta.project_id, "first-app")

        second = bind_external_project_dir(
            self.session, self.paths, str(other), project_id="second-app"
        )
        self.assertEqual(second["project_id"], "second-app")
        self.assertTrue(second["switched"])
        self.assertEqual(second["previous_project_id"], "first-app")
        self.assertEqual(self.session.meta.project_id, "second-app")

        # import_external_project shares the relaxed gate
        third_dir = Path(tempfile.mkdtemp(prefix="bind-dir-third-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(third_dir, ignore_errors=True))
        (third_dir / "x.txt").write_text("x\n", encoding="utf-8")
        notice = import_external_project(self.session, self.paths, str(third_dir), project_id="third-app")
        self.assertIn("third-app", notice)
        self.assertEqual(self.session.meta.project_id, "third-app")

    def test_already_bound_is_noop(self) -> None:
        self.session.meta.active_shell = "project"
        bind_external_project_dir(
            self.session, self.paths, str(self.external), project_id="same-app"
        )
        again = bind_external_project_dir(
            self.session, self.paths, str(self.external), project_id="same-app"
        )
        self.assertEqual(again["action"], "already_bound")
        self.assertFalse(again["applied"])
        self.assertEqual(self.session.meta.project_id, "same-app")

    def test_registry_exposes_builtin(self) -> None:
        reg = ToolRegistry.load(self.paths)
        names = {t.name for t in reg.builtins()}
        self.assertIn("bind_project_dir", names)
        builtin = reg.get_builtin("bind_project_dir")
        assert builtin is not None
        self.assertTrue(builtin.confirm)
        self.assertTrue(builtin.dry_run_supported)


if __name__ == "__main__":
    unittest.main()
