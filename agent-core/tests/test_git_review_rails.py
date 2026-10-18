"""Git review completion rails."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from git_review_rails import (
    paths_from_name_status,
    paths_from_status_lines,
    pending_git_review_nudge,
    update_git_review_state,
    user_wants_git_review,
)
from tools.schema import tool_ok


def _make_session(**kwargs) -> SimpleNamespace:
    defaults = dict(
        git_review_turn=True,
        git_review_pending_paths=[],
        git_review_completed_paths=[],
        git_review_spill_paths={},
        git_review_nudge_count=0,
        git_review_must_continue=False,
        git_review_listed=False,
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


class GitReviewRailsTests(unittest.TestCase):
    def test_review_intent(self) -> None:
        self.assertTrue(user_wants_git_review("我刚刚调整了一下代码，你现在去看看呢"))
        self.assertFalse(user_wants_git_review("Python 的 list 和 tuple 区别？"))

    def test_status_lines_skip_artifacts_and_handle_rename(self) -> None:
        paths = paths_from_status_lines(
            [
                " M agent-core/foo.py",
                "R  old/name.py -> agent-core/new_name.py",
                "?? artifacts/tmp/x",
            ]
        )
        self.assertEqual(paths, ["agent-core/foo.py", "agent-core/new_name.py"])

    def test_name_status_parse(self) -> None:
        diff = "M\tagent-core/a.py\nA\tagent-core/b.py"
        self.assertEqual(
            paths_from_name_status(diff),
            ["agent-core/a.py", "agent-core/b.py"],
        )

    def test_pending_nudge_respects_max_cap(self) -> None:
        session = _make_session(git_review_pending_paths=["a.py", "b.py"])
        nudge = pending_git_review_nudge(session)
        self.assertIsNotNone(nudge)
        assert nudge is not None
        self.assertIn("尚有 2 个文件", nudge)

        session.git_review_nudge_count = 1
        self.assertIsNone(pending_git_review_nudge(session))

    def test_spilled_scoped_git_diff_waits_for_spill_or_source_read(self) -> None:
        session = _make_session(
            git_review_pending_paths=["agent-core/agent.py", "b.py"],
            git_review_must_continue=False,
            git_review_listed=True,
        )
        spilled_result = tool_ok(
            "run_evolved",
            {"preview": '{"ok": true}', "spilled": True},
            truncated=True,
            output_path="data/sessions/s1/tool_outputs/abc.txt",
        )
        update_git_review_state(
            session,
            "run_evolved",
            {
                "tool_name": "git_diff",
                "arguments": {"paths": ["agent-core/agent.py"]},
            },
            spilled_result,
        )
        # Still pending until the spill file (or source file) is actually read in full
        self.assertIn("agent-core/agent.py", session.git_review_pending_paths)
        self.assertTrue(session.git_review_must_continue)

        # Partial read of spill file (truncated_lines=True) keeps it pending
        update_git_review_state(
            session,
            "read_file",
            {"path": "data/sessions/s1/tool_outputs/abc.txt"},
            tool_ok(
                "read_file",
                {"content": "part 1", "truncated_lines": True, "hint": "offset=501"},
                truncated=True,
            ),
        )
        self.assertIn("agent-core/agent.py", session.git_review_pending_paths)

        # Final read of spill file (truncated_lines=False) marks it reviewed
        update_git_review_state(
            session,
            "read_file",
            {"path": "data/sessions/s1/tool_outputs/abc.txt", "offset": 501},
            tool_ok(
                "read_file",
                {"content": "part 2", "truncated_lines": False},
                truncated=False,
            ),
        )
        self.assertEqual(session.git_review_pending_paths, ["b.py"])

    def test_read_file_marks_pending_path_reviewed(self) -> None:
        session = _make_session(
            git_review_pending_paths=["agent-core/git_review_rails.py"],
            git_review_must_continue=True,
        )
        update_git_review_state(
            session,
            "read_file",
            {"path": "agent-core/git_review_rails.py"},
            tool_ok("read_file", {"content": "x"}),
        )
        self.assertEqual(session.git_review_pending_paths, [])
        self.assertFalse(session.git_review_must_continue)


if __name__ == "__main__":
    unittest.main()
