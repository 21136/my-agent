"""Git review dedicated execute budget."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from agent import (
    _DEFAULT_GIT_REVIEW_SEGMENT_MAX,
    _DEFAULT_GIT_REVIEW_TOTAL_MAX,
    _DEFAULT_PROJECT_EXECUTE_SEGMENT_MAX,
    auto_continue_enabled,
    parent_execute_segment_max,
    parent_execute_total_max,
)


class GitReviewBudgetTests(unittest.TestCase):
    def tearDown(self) -> None:
        for key in (
            "GIT_REVIEW_SEGMENT_MAX",
            "GIT_REVIEW_TOTAL_MAX",
            "GIT_REVIEW_AUTO_CONTINUE",
            "PARENT_EXECUTE_SEGMENT_MAX",
        ):
            os.environ.pop(key, None)

    def test_git_review_segment_overrides_project_cap(self) -> None:
        self.assertGreater(
            parent_execute_segment_max(active_shell="project", git_review_turn=True),
            _DEFAULT_PROJECT_EXECUTE_SEGMENT_MAX,
        )
        self.assertEqual(
            parent_execute_segment_max(active_shell="project", git_review_turn=True),
            _DEFAULT_GIT_REVIEW_SEGMENT_MAX,
        )

    def test_git_review_total_higher_than_default(self) -> None:
        self.assertGreaterEqual(
            parent_execute_total_max(git_review_turn=True),
            _DEFAULT_GIT_REVIEW_TOTAL_MAX,
        )

    def test_git_review_auto_continue_on_desktop_project(self) -> None:
        self.assertTrue(
            auto_continue_enabled(
                active_shell="project",
                harness="desktop",
                git_review_turn=True,
            )
        )
        os.environ["GIT_REVIEW_AUTO_CONTINUE"] = "0"
        self.assertFalse(
            auto_continue_enabled(
                active_shell="project",
                harness="desktop",
                git_review_turn=True,
            )
        )

    def test_normal_project_still_pauses_segments(self) -> None:
        self.assertFalse(
            auto_continue_enabled(
                active_shell="project",
                harness="desktop",
                git_review_turn=False,
            )
        )


if __name__ == "__main__":
    unittest.main()
