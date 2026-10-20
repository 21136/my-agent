"""On-demand project thread discovery for explicit cross-chat handoff."""

from __future__ import annotations

import importlib.util
import secrets
import sys
import unittest
from pathlib import Path
from types import ModuleType

_AGENT_CORE = Path(__file__).resolve().parents[1]
_ROOT = _AGENT_CORE.parent
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from project_cli import bind_project_session
from project_mode import create_project, normalize_project_id, project_dir
from project_switch import archive_project_thread, record_project_session
from session import create_new
from tests.isolation_helpers import make_temp_agent_paths
from tools.executor import ExecutorSession, ToolExecutor
from tools.registry import ToolRegistry

_TOOL_PATH = _ROOT / "evolve" / "tools" / "common" / "project_catalog" / "main.py"
_SPEC = importlib.util.spec_from_file_location("project_catalog_tool_under_test", _TOOL_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_PROJECT_CATALOG: ModuleType = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_PROJECT_CATALOG)


class ProjectCatalogHistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.paths = make_temp_agent_paths(self, copy_tool_dirs=("common/project_catalog",))
        token = secrets.token_hex(4)
        self.project_id = f"handoff-demo-{token}"
        self.other_project_id = f"handoff-other-{token}"
        self.session_ids: list[str] = []
        create_project(self.paths, self.project_id)
        create_project(self.paths, self.other_project_id)

    def _session(self, project_id: str, user_message: str):
        session = create_new(self.paths)
        self.session_ids.append(session.conversation_id)
        bind_project_session(session, project_id)
        session.append_message({"role": "user", "content": user_message})
        session.save()
        return session

    def test_explicit_history_query_returns_only_selected_projects_live_and_archived_lines(self) -> None:
        old = self._session(self.project_id, "修复终端失败的旧任务")
        live = self._session(self.project_id, "继续整理当前工具体验")
        other = self._session(self.other_project_id, "另一项目的私有会话")
        archive_project_thread(self.paths, self.project_id, old.conversation_id)
        record_project_session(self.paths, self.project_id, live.conversation_id)
        archive_project_thread(self.paths, self.other_project_id, other.conversation_id)
        record_project_session(self.paths, self.other_project_id, other.conversation_id)

        result = _PROJECT_CATALOG.run_project_catalog(
            {"include_threads": True, "project_id": self.project_id, "_bound_project_id": self.project_id},
            paths=self.paths,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["project_id"], self.project_id)
        threads = result["threads"]
        self.assertEqual({item["session_id"] for item in threads}, {old.conversation_id, live.conversation_id})
        self.assertEqual(
            {item["session_id"]: item["state"] for item in threads},
            {live.conversation_id: "active", old.conversation_id: "archived"},
        )
        old_item = next(item for item in threads if item["session_id"] == old.conversation_id)
        self.assertEqual(old_item["first_user_message"], "修复终端失败的旧任务")
        self.assertEqual(old_item["last_user_message"], "修复终端失败的旧任务")
        self.assertNotIn(other.conversation_id, {item["session_id"] for item in threads})

    def test_history_mode_requires_a_registered_project_id(self) -> None:
        missing = _PROJECT_CATALOG.run_project_catalog(
            {"include_threads": True}, paths=self.paths
        )
        unknown = _PROJECT_CATALOG.run_project_catalog(
            {"include_threads": True, "project_id": "missing-project", "_bound_project_id": "missing-project"},
            paths=self.paths,
        )
        self.assertFalse(missing["ok"])
        self.assertIn("project_id", missing["error"])
        self.assertFalse(unknown["ok"])
        self.assertIn("not found", unknown["error"].lower())

        unbound = _PROJECT_CATALOG.run_project_catalog(
            {"include_threads": True, "project_id": self.project_id},
            paths=self.paths,
        )
        mismatched = _PROJECT_CATALOG.run_project_catalog(
            {"include_threads": True, "project_id": self.other_project_id, "_bound_project_id": self.project_id},
            paths=self.paths,
        )
        self.assertFalse(unbound["ok"])
        self.assertIn("project-bound", unbound["error"])
        self.assertFalse(mismatched["ok"])
        self.assertIn("current bound project", mismatched["error"])

    def test_history_mode_ignores_stale_entries_bound_to_another_project(self) -> None:
        foreign = self._session(self.other_project_id, "不应出现在目标项目历史中")
        archive_project_thread(self.paths, self.project_id, foreign.conversation_id)

        result = _PROJECT_CATALOG.run_project_catalog(
            {"include_threads": True, "project_id": self.project_id, "_bound_project_id": self.project_id},
            paths=self.paths,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["threads"], [])

    def test_executor_binding_overrides_model_supplied_project_id_context(self) -> None:
        live = self._session(self.project_id, "当前项目会话")
        record_project_session(self.paths, self.project_id, live.conversation_id)
        registry = ToolRegistry.load(self.paths)
        executor = ToolExecutor(
            registry=registry,
            session=ExecutorSession.load(
                live.session_dir,
                allowed_evolved={"project_catalog"},
            ),
            confirm_fn=lambda _preview, _allow: "y",
        )

        own = executor.run(
            "run_evolved",
            {
                "tool_name": "project_catalog",
                "arguments": {
                    "include_threads": True,
                    "project_id": self.project_id,
                    "_bound_project_id": self.other_project_id,
                },
            },
        )
        foreign = executor.run(
            "run_evolved",
            {
                "tool_name": "project_catalog",
                "arguments": {
                    "include_threads": True,
                    "project_id": self.other_project_id,
                    "_bound_project_id": self.other_project_id,
                },
            },
        )

        self.assertTrue(own.ok)
        self.assertEqual(own.data["threads"][0]["session_id"], live.conversation_id)
        self.assertFalse(foreign.ok)
        self.assertIn("must match the current bound project", str(foreign.error.details))

    def test_history_mode_ignores_internal_archived_sessions(self) -> None:
        internal_id = f"_internal_handoff_{secrets.token_hex(4)}"
        internal = create_new(self.paths, conversation_id=internal_id)
        self.session_ids.append(internal_id)
        bind_project_session(internal, self.project_id)
        internal.append_message({"role": "user", "content": "内部测试会话不可见"})
        internal.save()
        archive_project_thread(self.paths, self.project_id, internal_id)

        result = _PROJECT_CATALOG.run_project_catalog(
            {"include_threads": True, "project_id": self.project_id, "_bound_project_id": self.project_id},
            paths=self.paths,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["threads"], [])

    def test_default_catalog_shape_does_not_include_thread_history(self) -> None:
        result = _PROJECT_CATALOG.run_project_catalog({}, paths=self.paths)
        self.assertTrue(result["ok"])
        self.assertTrue(any(item["project_id"] == self.project_id for item in result["projects"]))
        self.assertNotIn("threads", result)

    def tearDown(self) -> None:
        from shutil import rmtree

        for pid in (self.project_id, self.other_project_id):
            rmtree(project_dir(self.paths, normalize_project_id(pid)), ignore_errors=True)
        for session_id in self.session_ids:
            rmtree(self.paths.data / "sessions" / session_id, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
