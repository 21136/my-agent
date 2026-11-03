"""Focused project-mode guards for test-tool intent and resolved working_dir scope."""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from unittest.mock import patch
from pathlib import Path
from types import SimpleNamespace

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

from tests.isolation_helpers import temporary_agent_paths
from tools.executor import ExecutorSession, ToolExecutor, _validate_project_mode_call
from tools.registry import ToolRegistry


_TEST_TOOLS = ("structured_test", "run_project_tests")


class ProjectModeTestToolScopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.agent_context = temporary_agent_paths(prefix="project-test-scope-")
        self.paths = self.agent_context.__enter__()
        self.addCleanup(self.agent_context.__exit__, None, None, None)
        self.project = self.paths.workspace / "current"
        self.project.mkdir()
        self.outside = self.paths.workspace / "other"
        self.outside.mkdir()
        self.session = SimpleNamespace(
            active_shell="project",
            project_root="workspace/current",
            ordinary_work_intent="execute",
        )

    def _check(self, evolved_tool: str, working_dir: str, *, intent: str = "execute"):
        self.session.ordinary_work_intent = intent
        with patch("project_mode.AgentPaths.discover", return_value=self.paths):
            return _validate_project_mode_call(
                self.session,
                "run_evolved",
                {
                    "tool_name": evolved_tool,
                    "arguments": {"working_dir": working_dir},
                },
                agent_paths=self.paths,
            )

    def test_read_only_intent_blocks_both_project_test_runners(self) -> None:
        for tool in _TEST_TOOLS:
            with self.subTest(tool=tool):
                result = self._check(tool, "workspace/current", intent="read_only")
                self.assertIsNotNone(result)
                self.assertIn("只读", result.error.message)

    def test_project_test_runners_reject_resolved_working_dir_outside_project(self) -> None:
        for tool in _TEST_TOOLS:
            with self.subTest(tool=tool):
                result = self._check(tool, "workspace/other")
                self.assertIsNotNone(result)

    def test_project_test_runners_keep_agent_root_relative_working_dir_semantics(self) -> None:
        for tool in _TEST_TOOLS:
            with self.subTest(tool=tool):
                result = self._check(tool, "workspace/current")
                self.assertIsNone(result)

    def test_executor_uses_registered_tool_directory_for_child_root_check(self) -> None:
        with temporary_agent_paths(
            copy_tool_dirs=("project/structured_test",),
            prefix="project-test-runner-root-",
        ) as child_paths:
            child_tool = ToolRegistry.load(child_paths).get_evolved("structured_test")
            self.assertIsNotNone(child_tool)
            assert child_tool is not None
            registry = ToolRegistry(agent_paths=self.paths, evolved=[child_tool])
            session = ExecutorSession(
                active_shell="project",
                project_id="current",
                project_root="workspace/current",
                ordinary_work_intent="execute",
                allowed_evolved={"structured_test"},
            )
            executor = ToolExecutor(registry=registry, session=session)
            result = executor.validate(
                "run_evolved",
                {
                    "tool_name": "structured_test",
                    "arguments": {"working_dir": "workspace/current"},
                },
            )
        self.assertIsNotNone(result)
        self.assertIn("路径映射", result.error.message)

    def test_project_test_rejects_registry_and_child_discovery_root_mismatch(self) -> None:
        # run_evolved starts a child whose project-test script calls
        # AgentPaths.discover(). A different discovered root must fail closed.
        with temporary_agent_paths(prefix="project-test-child-root-") as child_paths:
            with patch("project_mode.AgentPaths.discover", return_value=child_paths):
                result = _validate_project_mode_call(
                    self.session,
                    "run_evolved",
                    {
                        "tool_name": "structured_test",
                        "arguments": {"working_dir": "workspace/current"},
                    },
                    agent_paths=self.paths,
                    runner_discovery_start=child_paths.evolve / "tools" / "project" / "structured_test",
                )
        self.assertIsNotNone(result)
        self.assertIn("路径映射", result.error.message)

    def test_project_test_rejects_same_root_with_different_path_mapping(self) -> None:
        # Managed/fixed mappings can share agent_root but map workspace paths
        # differently from a freshly discovered child AgentPaths.
        child_paths = SimpleNamespace(
            agent_root=self.paths.agent_root,
            resolve_under_agent=lambda raw, *, must_exist=False: self.outside,
        )
        with patch("project_mode.AgentPaths.discover", return_value=child_paths):
            result = _validate_project_mode_call(
                self.session,
                "run_evolved",
                {
                    "tool_name": "structured_test",
                    "arguments": {"working_dir": "workspace/current"},
                },
                agent_paths=self.paths,
                runner_discovery_start=self.outside,
            )
        self.assertIsNotNone(result)
        self.assertIn("路径映射", result.error.message)

    def test_project_test_runners_reject_traversal_and_junction_escape(self) -> None:
        traversal = self._check("run_project_tests", "workspace/current/../other")
        self.assertIsNotNone(traversal)

        link = self.project / "outside-link"
        created = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(self.outside)],
            capture_output=True,
            text=True,
            check=False,
        )
        if created.returncode == 0:
            self.addCleanup(link.rmdir)
            self.assertTrue(link.is_junction())
        else:
            try:
                link.symlink_to(self.outside, target_is_directory=True)
            except OSError:
                self.skipTest("this Windows environment cannot create a symlink or junction")
            self.addCleanup(link.unlink)
        junction_escape = self._check("structured_test", "workspace/current/outside-link")
        self.assertIsNotNone(junction_escape)

    def test_project_test_scope_also_checks_json_encoded_inner_arguments(self) -> None:
        payload = {
            "tool_name": "run_project_tests",
            "arguments": json.dumps({"working_dir": "workspace/other"}),
        }
        with patch("project_mode.AgentPaths.discover", return_value=self.paths):
            result = _validate_project_mode_call(
                self.session,
                "run_evolved",
                payload,
                agent_paths=self.paths,
            )
        self.assertIsNotNone(result)

    def test_unbound_project_window_blocks_project_test_runners(self) -> None:
        self.session.project_root = ""
        for tool in _TEST_TOOLS:
            with self.subTest(tool=tool):
                result = self._check(tool, "workspace/current")
                self.assertIsNotNone(result)


if __name__ == "__main__":
    unittest.main()
