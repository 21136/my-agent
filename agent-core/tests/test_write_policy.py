"""Unit tests for write_policy (Phase 42 Track H · IT-421～424)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from tool_proxies import rewrite_proxy_tool_call
from tools.executor import ExecutorSession, ToolExecutor, build_confirm_preview
from tools.registry import ToolRegistry
from write_policy import (
    is_sensitive_write_path,
    path_under_project,
    write_project_policy_enabled,
    write_requires_confirm,
)
from tests.isolation_helpers import temporary_agent_paths


class WritePolicyTests(unittest.TestCase):
    _ROOT = "workspace/demo"
    _SHELL = "project"

    def _confirm(
        self,
        *,
        tool: str,
        path: str,
        on_conflict: str = "skip",
        file_exists: bool | None = None,
        project_root: str | None = None,
        active_shell: str | None = None,
    ) -> tuple[bool, str]:
        return write_requires_confirm(
            tool=tool,  # type: ignore[arg-type]
            path=path,
            project_root=project_root if project_root is not None else self._ROOT,
            active_shell=active_shell if active_shell is not None else self._SHELL,
            on_conflict=on_conflict,
            file_exists=file_exists,
        )

    def test_policy_enabled(self) -> None:
        self.assertTrue(
            write_project_policy_enabled(
                project_root="workspace/a", active_shell="project"
            )
        )
        self.assertFalse(
            write_project_policy_enabled(project_root="workspace/a", active_shell="grow")
        )
        self.assertFalse(
            write_project_policy_enabled(project_root="", active_shell="project")
        )

    def test_path_under_project(self) -> None:
        self.assertTrue(path_under_project("workspace/demo/src/a.py", "workspace/demo"))
        self.assertFalse(path_under_project("agent-core/foo.py", "workspace/demo"))
        self.assertFalse(path_under_project("host:foo/bar", "workspace/demo"))

    def test_it421_project_patch_skips_confirm(self) -> None:
        needs, reason = self._confirm(
            tool="patch_file",
            path="workspace/demo/src/main.py",
        )
        self.assertFalse(needs)
        self.assertEqual(reason, "skip:project_patch")

    def test_it422_outside_project_confirms(self) -> None:
        needs, reason = self._confirm(
            tool="write_text",
            path="agent-core/foo.py",
            on_conflict="overwrite",
            file_exists=True,
        )
        self.assertTrue(needs)
        self.assertEqual(reason, "confirm:outside_project")

    def test_it423_tasks_md_confirms(self) -> None:
        needs, reason = self._confirm(
            tool="patch_file",
            path="workspace/demo/TASKS.md",
        )
        self.assertTrue(needs)
        self.assertEqual(reason, "confirm:plan_domain")

    def test_write_overwrite_existing_skips(self) -> None:
        needs, reason = self._confirm(
            tool="write_text",
            path="workspace/demo/README.md",
            on_conflict="overwrite",
            file_exists=True,
        )
        self.assertFalse(needs)
        self.assertEqual(reason, "skip:project_overwrite")

    def test_write_new_file_confirms(self) -> None:
        needs, reason = self._confirm(
            tool="write_text",
            path="workspace/demo/new.py",
            on_conflict="overwrite",
            file_exists=False,
        )
        self.assertTrue(needs)
        self.assertEqual(reason, "confirm:new_file")

    def test_dry_run_skips(self) -> None:
        needs, reason = write_requires_confirm(
            tool="write_text",
            path="agent-core/foo.py",
            project_root="",
            active_shell="grow",
            dry_run=True,
        )
        self.assertFalse(needs)
        self.assertEqual(reason, "skip:dry_run")

    def test_sensitive_env(self) -> None:
        self.assertTrue(is_sensitive_write_path("workspace/demo/.env"))
        needs, reason = self._confirm(
            tool="patch_file",
            path="workspace/demo/.env",
        )
        self.assertTrue(needs)
        self.assertEqual(reason, "confirm:sensitive")


class WritePolicyExecutorTests(unittest.TestCase):
    """IT-421～424 via ToolExecutor._needs_confirm."""

    def _executor(self, paths) -> ToolExecutor:
        registry = ToolRegistry.load(paths)
        return ToolExecutor(
            registry=registry,
            session=ExecutorSession(
                allowed_evolved={"write_text", "patch_file"},
                project_root="workspace/demo",
                project_id="demo",
                active_shell="project",
            ),
        )

    def _needs(
        self,
        executor: ToolExecutor,
        evolved_name: str,
        inner: dict,
    ) -> bool:
        evolved = executor.registry.get_evolved(evolved_name)
        builtin = executor.registry.get_builtin("run_evolved")
        assert evolved is not None and builtin is not None
        args = {"tool_name": evolved_name, "arguments": inner}
        return executor._needs_confirm(
            builtin,
            evolved,
            args,
            tool_name="run_evolved",
        )

    def test_it421_executor_patch_skips(self) -> None:
        with temporary_agent_paths(
            copy_tool_dirs=("common/write_text", "coding/patch_file"),
        ) as paths:
            proj = paths.workspace / "demo" / "src"
            proj.mkdir(parents=True)
            (proj / "main.py").write_text("x\n", encoding="utf-8")
            executor = self._executor(paths)
            self.assertFalse(
                self._needs(
                    executor,
                    "patch_file",
                    {
                        "path": "workspace/demo/src/main.py",
                        "replacement": "y\n",
                    },
                )
            )

    def test_it422_executor_outside_project_confirms(self) -> None:
        with temporary_agent_paths(
            copy_tool_dirs=("common/write_text", "coding/patch_file"),
        ) as paths:
            executor = self._executor(paths)
            self.assertTrue(
                self._needs(
                    executor,
                    "write_text",
                    {
                        "path": "agent-core/foo.py",
                        "content": "hi",
                        "on_conflict": "overwrite",
                    },
                )
            )

    def test_it423_executor_tasks_confirms(self) -> None:
        with temporary_agent_paths(
            copy_tool_dirs=("common/write_text", "coding/patch_file"),
        ) as paths:
            (paths.workspace / "demo").mkdir(parents=True)
            (paths.workspace / "demo" / "TASKS.md").write_text("# T\n", encoding="utf-8")
            executor = self._executor(paths)
            self.assertTrue(
                self._needs(
                    executor,
                    "patch_file",
                    {
                        "path": "workspace/demo/TASKS.md",
                        "replacement": "# T2\n",
                    },
                )
            )

    def test_it424_proxy_matches_run_evolved(self) -> None:
        with temporary_agent_paths(
            copy_tool_dirs=("common/write_text", "coding/patch_file"),
        ) as paths:
            proj = paths.workspace / "demo" / "src"
            proj.mkdir(parents=True)
            (proj / "main.py").write_text("x\n", encoding="utf-8")
            executor = self._executor(paths)
            evolved = executor.registry.get_evolved("patch_file")
            builtin = executor.registry.get_builtin("run_evolved")
            assert evolved is not None and builtin is not None
            proxy_args = {
                "path": "workspace/demo/src/main.py",
                "replacement": "y\n",
            }
            tool_name, args = rewrite_proxy_tool_call("patch_file", proxy_args)
            self.assertEqual(tool_name, "run_evolved")
            direct = self._needs(executor, "patch_file", proxy_args)
            proxied = executor._needs_confirm(
                builtin,
                evolved,
                args,
                tool_name=tool_name,
            )
            self.assertEqual(direct, proxied)
            self.assertFalse(proxied)

    def test_confirm_preview_shows_write_policy(self) -> None:
        with temporary_agent_paths(
            copy_tool_dirs=("common/write_text", "coding/patch_file"),
        ) as paths:
            registry = ToolRegistry.load(paths)
            evolved = registry.get_evolved("write_text")
            assert evolved is not None
            preview = build_confirm_preview(
                "run_evolved",
                {
                    "tool_name": "write_text",
                    "arguments": {
                        "path": "agent-core/foo.py",
                        "content": "hi",
                        "on_conflict": "overwrite",
                    },
                },
                evolved=evolved,
                project_root="workspace/demo",
                active_shell="project",
                agent_paths=paths,
            )
            self.assertIn("Write policy: confirm:outside_project", preview)


if __name__ == "__main__":
    unittest.main()
