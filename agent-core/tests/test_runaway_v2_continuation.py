"""IT-6107 — runaway v2 continuation is derived from current disk state."""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from runaway_v2.checklist import Checklist, ChecklistItem, build_checklist
from runaway_v2.continuation import (
    observe_runaway_turn,
    pending_runaway_work,
    repair_stale_v2_plan_status,
    reset_continuation_guard,
    should_continue_runaway,
)
from session import create_new
from tests.isolation_helpers import temporary_agent_paths


class RunawayV2ContinuationTests(unittest.TestCase):
    def _session(self, paths, pid: str = "demo"):
        session = create_new(paths, conversation_id="_runaway_v2_continuation_")
        session.meta.project_id = pid
        session.meta.project_root = f"workspace/{pid}"
        session.meta.active_shell = "project"
        session.meta.project_runaway_enabled = True
        session.meta.project_plan_status = "confirmed"
        return session

    def _write_docs(self, root: Path, tasks: str) -> None:
        root.mkdir(parents=True, exist_ok=True)
        (root / "PROJECT.md").write_text(
            "REQ-001\nAC-001\n\n## 验收标准\n\n- 命令：`python verify.py` 期望退出码：0\n",
            encoding="utf-8",
        )
        (root / "DESIGN.md").write_text("UX-001\nTD-001\n", encoding="utf-8")
        (root / "TASKS.md").write_text(tasks, encoding="utf-8")
        (root / "VERIFY.md").write_text("V-001 T-001 pass\nAC-001\n", encoding="utf-8")
        (root / "ENV.md").write_text(
            "quality:\n  commands:\n    - id: smoke\n      cmd: [\"echo\", \"ok\"]\n",
            encoding="utf-8",
        )

    def test_prepare_has_pending_work(self) -> None:
        with temporary_agent_paths() as paths:
            root = paths.workspace / "demo"
            root.mkdir(parents=True, exist_ok=True)
            (root / "PROJECT.md").write_text("REQ-001\nAC-001\n", encoding="utf-8")
            session = self._session(paths)
            session.meta.project_plan_status = "draft"

            pending = pending_runaway_work(paths, "demo", session)

            self.assertIsNotNone(pending)
            assert pending is not None
            self.assertEqual(pending.phase, "prepare")
            self.assertEqual(pending.focus_item_id, "PREPARE")
            self.assertTrue(should_continue_runaway(paths, "demo", session))

    def test_implement_exposes_active_task(self) -> None:
        with temporary_agent_paths() as paths:
            self._write_docs(paths.workspace / "demo", "- [ ] T-003 implement API\n")
            session = self._session(paths)

            pending = pending_runaway_work(paths, "demo", session)

            self.assertIsNotNone(pending)
            assert pending is not None
            self.assertEqual(pending.phase, "implement")
            self.assertEqual(pending.active_task_id, "T-003")

    def test_verify_continues_independent_of_qa_intent(self) -> None:
        with temporary_agent_paths() as paths:
            self._write_docs(paths.workspace / "demo", "- [x] T-001 implemented\n")
            session = self._session(paths)
            checklist = Checklist(
                version=1,
                project_id="demo",
                generated_at="",
                source_fingerprint="",
                items=[
                    ChecklistItem(
                        id="MX-5",
                        kind="matrix",
                        title="环境质量命令",
                        stable_key="matrix:MX-5",
                        acceptance={"type": "matrix_rule", "rule_id": "MX-5"},
                    )
                ],
            )

            pending = pending_runaway_work(paths, "demo", session, checklist=checklist)

            self.assertIsNotNone(pending)
            assert pending is not None
            self.assertEqual(pending.phase, "verify")
            self.assertEqual(pending.focus_item_id, "MX-5")
            self.assertTrue(
                should_continue_runaway(
                    paths, "demo", session, finish_reason="qa", checklist=checklist
                )
            )

    @patch.dict(os.environ, {"MY_AGENT_RUNAWAY_V2_DIRECTED_AFTER": "2"}, clear=False)
    def test_directed_failure_escalates_to_human(self) -> None:
        with temporary_agent_paths() as paths:
            self._write_docs(paths.workspace / "demo", "- [x] T-001 implemented\n")
            session = self._session(paths)
            checklist = Checklist(
                1,
                "demo",
                "",
                "",
                items=[
                    ChecklistItem(
                        id="MX-5",
                        kind="matrix",
                        title="环境质量命令",
                        stable_key="matrix:MX-5",
                        status="failed",
                        attempts=2,
                        directed_used=True,
                    )
                ],
            )

            self.assertIsNone(pending_runaway_work(paths, "demo", session, checklist=checklist))
            self.assertFalse(
                should_continue_runaway(paths, "demo", session, checklist=checklist)
            )

    def test_release_wait_has_no_pending_work(self) -> None:
        with temporary_agent_paths() as paths:
            self._write_docs(paths.workspace / "demo", "- [x] T-001 implemented\n")
            session = self._session(paths)
            checklist = build_checklist(paths, "demo")
            for item in checklist.items:
                item.status = "passed"

            self.assertIsNone(pending_runaway_work(paths, "demo", session, checklist=checklist))

    @patch.dict(os.environ, {"MY_AGENT_RUNAWAY_V2": "1"}, clear=False)
    def test_stale_plan_dirty_release_wait_is_repaired_before_continuation(self) -> None:
        with temporary_agent_paths() as paths:
            self._write_docs(paths.workspace / "demo", "- [x] T-001 implemented\n")
            session = self._session(paths)
            checklist = build_checklist(paths, "demo")
            for item in checklist.items:
                item.status = "passed"
            from runaway_v2.checklist import save_checklist

            save_checklist(paths, checklist)
            session.meta.project_plan_status = "plan_dirty"
            session.meta.project_runaway_checkpoint = "release_wait"
            session.meta.project_runaway_v2_phase = "release_wait"

            self.assertTrue(repair_stale_v2_plan_status(paths, session))
            self.assertEqual(session.meta.project_plan_status, "confirmed")
            self.assertIsNone(pending_runaway_work(paths, "demo", session))
            self.assertFalse(should_continue_runaway(paths, "demo", session))

    def test_fatal_finish_reason_stops_cross_turn_only(self) -> None:
        with temporary_agent_paths() as paths:
            self._write_docs(paths.workspace / "demo", "- [ ] T-003 implement API\n")
            session = self._session(paths)

            self.assertFalse(
                should_continue_runaway(paths, "demo", session, finish_reason="cancelled")
            )
            self.assertTrue(
                should_continue_runaway(paths, "demo", session, finish_reason="completed")
            )

    def test_depth_limit_is_independent_of_pending_state(self) -> None:
        with temporary_agent_paths() as paths:
            self._write_docs(paths.workspace / "demo", "- [ ] T-003 implement API\n")
            session = self._session(paths)

            self.assertFalse(
                should_continue_runaway(
                    paths, "demo", session, in_turn_depth=12, max_in_turn_depth=12
                )
            )

    @patch.dict(
        os.environ,
        {
            "MY_AGENT_RUNAWAY_V2_NO_PROGRESS_TURNS": "2",
            "MY_AGENT_RUNAWAY_V2_MAX_AUTO_TURNS": "20",
        },
        clear=False,
    )
    def test_repeated_unchanged_state_pauses_instead_of_looping(self) -> None:
        with temporary_agent_paths() as paths:
            self._write_docs(paths.workspace / "demo", "- [ ] T-003 implement API\n")
            session = self._session(paths)

            self.assertTrue(
                observe_runaway_turn(paths, "demo", session, automatic=False)
            )
            self.assertTrue(
                observe_runaway_turn(paths, "demo", session, automatic=True)
            )
            self.assertEqual(session.meta.project_runaway_v2_no_progress_turns, 1)
            self.assertFalse(
                observe_runaway_turn(paths, "demo", session, automatic=True)
            )
            self.assertEqual(
                session.meta.project_runaway_paused_reason,
                "连续 2 个自动回合没有检测到项目状态变化",
            )
            self.assertEqual(session.meta.project_runaway_checkpoint, "paused")
            self.assertFalse(
                should_continue_runaway(paths, "demo", session, finish_reason="completed")
            )

    @patch.dict(
        os.environ,
        {
            "MY_AGENT_RUNAWAY_V2_NO_PROGRESS_TURNS": "20",
            "MY_AGENT_RUNAWAY_V2_MAX_AUTO_TURNS": "1",
        },
        clear=False,
    )
    def test_automatic_turn_budget_pauses_even_when_state_changes(self) -> None:
        with temporary_agent_paths() as paths:
            self._write_docs(paths.workspace / "demo", "- [ ] T-003 implement API\n")
            session = self._session(paths)

            self.assertTrue(
                observe_runaway_turn(paths, "demo", session, automatic=False)
            )
            (paths.workspace / "demo" / "app.py").write_text("print('changed')\n", encoding="utf-8")
            self.assertFalse(
                observe_runaway_turn(paths, "demo", session, automatic=True)
            )
            self.assertEqual(session.meta.project_runaway_v2_auto_turns, 1)
            self.assertIn("自动回合预算已用尽", session.meta.project_runaway_paused_reason)

    def test_resume_guard_clears_persisted_counters(self) -> None:
        with temporary_agent_paths() as paths:
            session = self._session(paths)
            session.meta.project_runaway_v2_last_state_fingerprint = "old"
            session.meta.project_runaway_v2_no_progress_turns = 4
            session.meta.project_runaway_v2_auto_turns = 7

            self.assertTrue(reset_continuation_guard(session))
            self.assertEqual(session.meta.project_runaway_v2_last_state_fingerprint, "")
            self.assertEqual(session.meta.project_runaway_v2_no_progress_turns, 0)
            self.assertEqual(session.meta.project_runaway_v2_auto_turns, 0)

    @patch.dict(os.environ, {"MY_AGENT_RUNAWAY_V2_NO_PROGRESS_TURNS": "3"}, clear=False)
    def test_server_read_only_check_does_not_double_count_controller_observation(self) -> None:
        with temporary_agent_paths() as paths:
            self._write_docs(paths.workspace / "demo", "- [ ] T-003 implement API\n")
            session = self._session(paths)
            self.assertTrue(
                observe_runaway_turn(paths, "demo", session, automatic=False)
            )
            self.assertTrue(
                observe_runaway_turn(paths, "demo", session, automatic=True)
            )

            self.assertTrue(
                should_continue_runaway(
                    paths,
                    "demo",
                    session,
                    finish_reason="completed",
                    observe=False,
                )
            )
            self.assertEqual(session.meta.project_runaway_v2_no_progress_turns, 1)
            self.assertEqual(session.meta.project_runaway_v2_auto_turns, 1)


if __name__ == "__main__":
    unittest.main()
