"""Deliverable review can target an explicit project from ordinary chat."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from agent import build_builtin_tool_definition
from session import Session, create_new
from subagent import SubagentResult
from tools.executor import ExecutorSession, ToolExecutor
from tools.registry import ToolRegistry
from tests.isolation_helpers import make_temp_agent_paths


class SessionAgnosticDeliverableReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.paths = make_temp_agent_paths(self, prefix="ordinary-review-target-")
        self.project_id = "demo-project"
        self.project_root = self.paths.workspace / self.project_id
        self.project_root.mkdir()
        (self.project_root / "PROJECT.md").write_text("# Demo project\n", encoding="utf-8")
        self.registry = ToolRegistry.load(self.paths)
        self.source_session = create_new(self.paths, conversation_id="ordinary-review-source")
        self.executor = self._executor(self.source_session)

    def _executor(self, session, *, project_id: str = "", project_root: str = ""):
        return ToolExecutor(
            registry=self.registry,
            session=ExecutorSession(
                session_dir=session.session_dir,
                allowed_evolved=set(),
                active_shell="project" if project_id else "grow",
                project_id=project_id,
                project_root=project_root,
                turn_mode="agent",
                ordinary_work_intent="execute",
            ),
            confirm_fn=lambda *_: "y",
        )

    @staticmethod
    def _review_result() -> SubagentResult:
        return SubagentResult(
            kind="review",
            summary="项目结构可交付。\nREVIEW_VERDICT: pass",
            paths_cited=["workspace/demo-project/PROJECT.md"],
            tool_rounds=1,
            truncated=False,
            task="检查项目结构",
            verdict="pass",
        )

    def test_schema_accepts_optional_project_id(self) -> None:
        params = build_builtin_tool_definition("deliverable_review")["function"]["parameters"]
        self.assertIn("project_id", params["properties"])
        self.assertEqual(params["required"], ["task"])

    def test_ordinary_session_reviews_explicit_workspace_project_without_rebinding_source(self) -> None:
        original_meta = self.source_session.meta.to_dict()
        with patch(
            "subagent.SubagentRunner.run_deliverable_review",
            return_value=self._review_result(),
        ) as review:
            result = self.executor.run(
                "deliverable_review",
                {"task": "检查项目结构", "project_id": self.project_id},
            )

        self.assertTrue(result.ok, result.error)
        review_session = review.call_args.kwargs["session"]
        self.assertEqual(review_session.meta.project_id, self.project_id)
        self.assertEqual(review_session.meta.project_root, f"workspace/{self.project_id}")
        self.assertEqual(self.source_session.meta.project_id, "")
        self.assertEqual(self.source_session.meta.project_root, "")
        self.assertEqual(self.source_session.meta.to_dict(), original_meta)
        persisted = Session.load(self.paths, self.source_session.conversation_id)
        self.assertEqual(persisted.meta.project_id, "")
        self.assertEqual(persisted.meta.project_root, "")

    def test_ordinary_session_requires_existing_safe_project_id(self) -> None:
        cases = (
            ({"task": "review"}, "project_id"),
            ({"task": "review", "project_id": "../outside"}, "project id"),
            ({"task": "review", "project_id": "not-a-project"}, "project"),
        )
        for arguments, expected_fragment in cases:
            with self.subTest(project_id=arguments.get("project_id")):
                with patch("subagent.SubagentRunner.run_deliverable_review") as review:
                    result = self.executor.run("deliverable_review", arguments)
                self.assertFalse(result.ok)
                self.assertIn(expected_fragment, result.error.message.casefold())
                review.assert_not_called()

    def test_files_scope_paths_must_stay_under_explicit_project(self) -> None:
        result = self.executor.run(
            "deliverable_review",
            {
                "task": "review a file",
                "project_id": self.project_id,
                "scope": "files",
                "paths": ["workspace/another-project/README.md"],
            },
        )
        self.assertFalse(result.ok)
        self.assertIn("under project_root", result.error.message)

    def test_files_scope_accepts_paths_relative_to_explicit_project_root(self) -> None:
        with patch(
            "subagent.SubagentRunner.run_deliverable_review",
            return_value=self._review_result(),
        ) as review:
            result = self.executor.run(
                "deliverable_review",
                {
                    "task": "review project document",
                    "project_id": self.project_id,
                    "scope": "files",
                    "paths": ["PROJECT.md", f"{self.project_id}/PROJECT.md"],
                },
            )
        self.assertTrue(result.ok, result.error)
        self.assertEqual(review.call_args.kwargs["paths"], ["PROJECT.md", f"{self.project_id}/PROJECT.md"])

    def test_files_scope_rejects_symlink_escape_from_selected_project(self) -> None:
        outside = self.paths.workspace / "another-project"
        outside.mkdir()
        (outside / "PROJECT.md").write_text("# Other project\n", encoding="utf-8")
        link = self.project_root / "linked-project"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            if sys.platform != "win32":
                self.skipTest("directory symlinks are unavailable")
            import subprocess

            created = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(link), str(outside)],
                capture_output=True,
                text=True,
            )
            if created.returncode != 0:
                self.skipTest("directory junctions are unavailable")

        def remove_link() -> None:
            if link.is_symlink():
                link.unlink(missing_ok=True)
            elif hasattr(link, "is_junction") and link.is_junction():
                link.rmdir()

        self.addCleanup(remove_link)
        with patch(
            "subagent.SubagentRunner.run_deliverable_review",
            return_value=self._review_result(),
        ) as review:
            result = self.executor.run(
                "deliverable_review",
                {
                    "task": "review linked project",
                    "project_id": self.project_id,
                    "scope": "files",
                    "paths": [f"workspace/{self.project_id}/linked-project/PROJECT.md"],
                },
            )
        self.assertFalse(result.ok)
        self.assertIn("under project_root", result.error.message)
        review.assert_not_called()

    def test_bound_session_defaults_to_current_project_and_rejects_other_target(self) -> None:
        self.source_session.meta.project_id = self.project_id
        self.source_session.meta.project_root = f"workspace/{self.project_id}"
        self.source_session.meta.active_shell = "project"
        self.source_session.save()
        executor = self._executor(
            self.source_session,
            project_id=self.project_id,
            project_root=f"workspace/{self.project_id}",
        )

        with patch(
            "subagent.SubagentRunner.run_deliverable_review",
            return_value=self._review_result(),
        ) as review:
            result = executor.run("deliverable_review", {"task": "检查项目结构"})
        self.assertTrue(result.ok, result.error)
        self.assertEqual(review.call_args.kwargs["session"].meta.project_id, self.project_id)

        with patch("subagent.SubagentRunner.run_deliverable_review") as review:
            mismatch = executor.run(
                "deliverable_review",
                {"task": "review", "project_id": "another-project"},
            )
        self.assertFalse(mismatch.ok)
        self.assertIn("current project", mismatch.error.message.casefold())
        review.assert_not_called()


if __name__ == "__main__":
    unittest.main()
