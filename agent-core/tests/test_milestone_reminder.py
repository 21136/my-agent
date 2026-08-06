"""Phase 47 · milestone suggestion evaluation (IT-476 · T-4714/4715)."""

from __future__ import annotations

import secrets
import unittest
from unittest.mock import patch

from plan_agent import drop_plan_agent, get_plan_agent
from project_mode import (
    TASKS_ARCHIVE_NAME,
    add_task_to_tasks_md,
    archive_and_remove_task_line,
    archive_done_count_for_phase,
    create_project,
    evaluate_milestone_after_archive,
    normalize_project_id,
    phase_key_for_title,
    phase_open_count_visible,
    project_dir,
    toggle_task_line,
)

from tests.isolation_helpers import make_temp_agent_paths


class MilestoneHelperTests(unittest.TestCase):
    def test_phase_open_count_visible_exact_not_substring(self) -> None:
        lines = [
            "## Phase 1",
            "- [ ] T-001 open task with enough text",
            "## Phase 10",
            "- [ ] T-010 another open task with text",
        ]
        self.assertEqual(phase_open_count_visible(lines, "Phase 1"), 1)
        self.assertEqual(phase_open_count_visible(lines, "Phase 10"), 1)
        self.assertEqual(phase_open_count_visible(lines, "Phase"), 0)

    def test_phase_open_count_visible_skips_closed_section(self) -> None:
        lines = [
            "## Phase 1",
            "- [ ] Keep open task with enough text",
            "## 已关闭",
            "- [ ] Hidden open task with enough text",
        ]
        self.assertEqual(phase_open_count_visible(lines, "Phase 1"), 1)

    def test_archive_done_count_for_phase_exact(self) -> None:
        archive = self._archive_text(
            [
                "- T-001 done item with enough text · closed:done · phase:Phase 1",
                "- T-002 wontfix item with enough text · closed:wontfix · phase:Phase 1",
                "- T-003 done item with enough text · closed:done · phase:Phase 2",
            ]
        )
        self.assertEqual(archive_done_count_for_phase(archive, "Phase 1"), 1)
        self.assertEqual(archive_done_count_for_phase(archive, "Phase"), 0)

    def test_archive_done_count_missing_reason_defaults_done(self) -> None:
        archive = self._archive_text(
            [
                "- T-001 legacy item with enough text · phase:Phase 1",
            ]
        )
        self.assertEqual(archive_done_count_for_phase(archive, "Phase 1"), 1)

    def test_phase_key_prefers_header_index(self) -> None:
        lines = ["## Phase 1", "## Phase 2", "## Phase 3"]
        self.assertEqual(phase_key_for_title(lines, "Phase 3"), "phase:3")

    def _archive_text(self, bullets: list[str]) -> "Path":
        from pathlib import Path

        path = Path(self._tmp) / TASKS_ARCHIVE_NAME
        path.write_text("\n".join(bullets) + "\n", encoding="utf-8")
        return path

    def setUp(self) -> None:
        import tempfile
        from pathlib import Path

        self._tmp = Path(tempfile.mkdtemp(prefix="ms-"))


class MilestoneEvaluateTests(unittest.TestCase):
    """IT-476 · evaluate_milestone_after_archive (T-4714)."""

    def setUp(self) -> None:
        self.paths = make_temp_agent_paths(self)
        self.pid = normalize_project_id(f"ms-{secrets.token_hex(3)}")
        create_project(self.paths, self.pid)
        self.root = project_dir(self.paths, self.pid)
        self.tasks = self.root / "TASKS.md"
        self.archive = self.root / TASKS_ARCHIVE_NAME

    def _write(self, body: str) -> None:
        self.tasks.write_text(body.rstrip() + "\n", encoding="utf-8")

    def _eval(self, phase: str, **kwargs: object) -> dict:
        return evaluate_milestone_after_archive(
            tasks_path=self.tasks,
            archive_path=self.archive,
            phase=phase,
            **kwargs,
        )

    # IT-476-1: archive first of two open tasks → no remind
    def test_it476_first_archive_no_remind(self) -> None:
        self._write(
            "## Phase A\n"
            "- [ ] T-001 first task with enough text\n"
            "- [ ] T-002 second task with enough text\n"
        )
        toggle_task_line(self.paths, self.pid, 1, True)
        ev = self._eval("Phase A")
        self.assertEqual(ev["open_after"], 1)
        self.assertGreater(ev["archive_done"], 0)
        self.assertFalse(ev["m1"])
        self.assertFalse(ev["should_remind"])

    # IT-476-2: archive last open task → M1 + should_remind
    def test_it476_last_archive_triggers_m1(self) -> None:
        self._write(
            "## Phase A\n"
            "- [ ] T-001 first task with enough text\n"
            "- [ ] T-002 second task with enough text\n"
        )
        toggle_task_line(self.paths, self.pid, 1, True)
        toggle_task_line(self.paths, self.pid, 1, True)
        ev = self._eval("Phase A")
        self.assertEqual(ev["open_after"], 0)
        self.assertGreater(ev["archive_done"], 0)
        self.assertTrue(ev["m1"])
        self.assertTrue(ev["should_remind_m1"])
        self.assertTrue(ev["should_remind"])

    # IT-476-3: post-archive snapshot fields
    def test_it476_open_after_zero_and_archive_done_positive(self) -> None:
        self._write("## Phase A\n- [ ] T-001 only task with enough text\n")
        toggle_task_line(self.paths, self.pid, 1, True)
        ev = self._eval("Phase A")
        self.assertEqual(ev["open_after"], 0)
        self.assertGreater(ev["archive_done"], 0)

    # IT-476-4: reminded phase_key suppresses repeat
    def test_it476_reminded_phase_key_no_repeat(self) -> None:
        self._write(
            "## Phase A\n"
            "- [ ] T-001 first task with enough text\n"
            "- [ ] T-002 second task with enough text\n"
            "## Phase B\n"
            "- [ ] T-003 third task with enough text\n"
        )
        toggle_task_line(self.paths, self.pid, 1, True)
        toggle_task_line(self.paths, self.pid, 1, True)
        phase_key = phase_key_for_title(self.tasks.read_text(encoding="utf-8").splitlines(), "Phase A")
        ev = self._eval("Phase A", reminded_phase_keys=frozenset({phase_key}))
        self.assertTrue(ev["m1"])
        self.assertFalse(ev["m2"])
        self.assertFalse(ev["should_remind_m1"])
        self.assertFalse(ev["should_remind"])

    # IT-476-5: review pass clears reminded
    def test_it476_review_pass_clears_reminded(self) -> None:
        self._write(
            "## Phase A\n"
            "- [ ] T-001 first task with enough text\n"
            "- [ ] T-002 second task with enough text\n"
            "## Phase B\n"
            "- [ ] T-003 third task with enough text\n"
        )
        drop_plan_agent(self.pid)
        agent = get_plan_agent(self.paths, self.pid)
        agent.report_progress(1, "T-001 first task with enough text done")
        state = agent.report_progress(1, "T-002 second task with enough text done")
        self.assertTrue(
            any(s.get("kind") == "milestone_review" for s in state.get("suggestions", []))
        )
        self.assertIn("phase:1", agent._milestone_reminded_phase_keys)
        agent.clear_milestone_reminded_on_review("pass")
        self.assertEqual(len(agent._milestone_reminded_phase_keys), 0)
        add_task_to_tasks_md(
            self.paths,
            self.pid,
            "Phase A",
            "T-004 follow-up task with enough text",
        )
        lines = self.tasks.read_text(encoding="utf-8").splitlines()
        line_new = next(i for i, ln in enumerate(lines) if "T-004" in ln)
        state2 = agent.report_progress(line_new, "T-004 follow-up task with enough text done")
        kinds = [str(s.get("kind")) for s in state2.get("suggestions", [])]
        self.assertIn("milestone_review", kinds)

    # IT-476-6: empty phase (no tasks, no archive) → no M1
    def test_it476_empty_phase_no_trigger(self) -> None:
        self._write("## Phase A\n- [ ] T-001 real task with enough text\n")
        ev = self._eval("Phase Empty")
        self.assertEqual(ev["archive_done"], 0)
        self.assertFalse(ev["m1"])
        self.assertFalse(ev["should_remind"])

    def test_does_not_use_phase_open_and_done_counts_done_n(self) -> None:
        """Regression: M1 must not depend on legacy [x] done_n in TASKS."""
        self._write("## Phase A\n- [ ] T-001 only task with enough text\n")
        result = archive_and_remove_task_line(self.paths, self.pid, 1, reason="done")
        ev = self._eval(result["phase"])
        self.assertEqual(ev["open_after"], 0)
        self.assertGreater(ev["archive_done"], 0)
        self.assertTrue(ev["m1"])


class ReportProgressMilestoneTests(unittest.TestCase):
    """IT-476 · report_progress hook (T-4715)."""

    def setUp(self) -> None:
        self.paths = make_temp_agent_paths(self)
        self.pid = normalize_project_id(f"rp-{secrets.token_hex(3)}")
        create_project(self.paths, self.pid)
        drop_plan_agent(self.pid)
        self.agent = get_plan_agent(self.paths, self.pid)
        self.root = project_dir(self.paths, self.pid)
        self.tasks = self.root / "TASKS.md"

    def _write(self, body: str) -> None:
        self.tasks.write_text(body.rstrip() + "\n", encoding="utf-8")

    def _suggestion_kinds(self, state: dict) -> list[str]:
        return [str(s.get("kind")) for s in state.get("suggestions", [])]

    # IT-476-1
    def test_report_progress_first_archive_no_milestone_suggestion(self) -> None:
        self._write(
            "## Phase A\n"
            "- [ ] T-001 first task with enough text\n"
            "- [ ] T-002 second task with enough text\n"
        )
        state = self.agent.report_progress(1, "T-001 first task with enough text done")
        self.assertNotIn("milestone_review", self._suggestion_kinds(state))

    # IT-476-2
    @patch("subagent.SubagentRunner.run", create=True)
    def test_report_progress_last_archive_emits_milestone_review(
        self, mock_review_run
    ) -> None:
        self._write(
            "## Phase A\n"
            "- [ ] T-001 first task with enough text\n"
            "- [ ] T-002 second task with enough text\n"
        )
        self.agent.report_progress(1, "T-001 first task with enough text done")
        state = self.agent.report_progress(1, "T-002 second task with enough text done")
        self.assertIn("milestone_review", self._suggestion_kinds(state))
        milestone = next(
            s for s in state["suggestions"] if s.get("kind") == "milestone_review"
        )
        self.assertIn("git_commit", milestone.get("body", ""))
        self.assertTrue(self.agent._last_partner_notices)
        mock_review_run.assert_not_called()

    def test_milestone_review_body_ritual_profile(self) -> None:
        self._write(
            "## Phase A\n"
            "- [ ] T-001 first task with enough text\n"
            "- [ ] T-002 second task with enough text\n"
        )
        self.agent.report_progress(1, "T-001 first task with enough text done")
        state = self.agent.report_progress(
            1,
            "T-002 second task with enough text done",
            delivery_profile="ritual",
        )
        milestone = next(
            s for s in state["suggestions"] if s.get("kind") == "milestone_review"
        )
        self.assertIn("ritual", milestone.get("body", ""))
        self.assertIn("deliverable_review", milestone.get("body", ""))

    def test_milestone_suggestion_persisted_in_state_json(self) -> None:
        self._write("## Phase A\n- [ ] T-001 only task with enough text\n")
        self.agent.report_progress(1, "T-001 only task with enough text done")
        state_path = self.root / ".plan-agent" / "state.json"
        self.assertTrue(state_path.is_file())
        import json

        data = json.loads(state_path.read_text(encoding="utf-8"))
        reminders = data.get("milestone_review_reminders") or {}
        self.assertIn("phase:1", reminders.get("reminded_phase_keys", []))
        self.assertTrue(reminders.get("active_suggestions"))


class MilestoneReminderStateTests(unittest.TestCase):
    """IT-477 · plan state milestone_review_reminders (T-4716)."""

    def setUp(self) -> None:
        self.paths = make_temp_agent_paths(self)
        self.pid = normalize_project_id(f"mr-{secrets.token_hex(3)}")
        create_project(self.paths, self.pid)
        drop_plan_agent(self.pid)
        self.agent = get_plan_agent(self.paths, self.pid)
        self.root = project_dir(self.paths, self.pid)
        self.tasks = self.root / "TASKS.md"

    def _write(self, body: str) -> None:
        self.tasks.write_text(body.rstrip() + "\n", encoding="utf-8")

    def _archive_phase_a(self) -> dict:
        self._write(
            "## Phase A\n"
            "- [ ] T-001 first task with enough text\n"
            "- [ ] T-002 second task with enough text\n"
            "## Phase B\n"
            "- [ ] T-003 third task with enough text\n"
        )
        self.agent.report_progress(1, "T-001 first task with enough text done")
        return self.agent.report_progress(1, "T-002 second task with enough text done")

    def _suggestion_kinds(self, state: dict) -> list[str]:
        return [str(s.get("kind")) for s in state.get("suggestions", [])]

    def test_it477_dismiss_blocks_future_reminders(self) -> None:
        state = self._archive_phase_a()
        milestone = next(
            s for s in state["suggestions"] if s.get("kind") == "milestone_review"
        )
        self.agent.ignore_suggestion(milestone["id"])
        self.assertIn("phase:1", self.agent._milestone_dismissed_phase_keys)
        add_task_to_tasks_md(
            self.paths,
            self.pid,
            "Phase A",
            "T-004 follow-up task with enough text",
        )
        lines = self.tasks.read_text(encoding="utf-8").splitlines()
        line_new = next(i for i, ln in enumerate(lines) if "T-004" in ln)
        state2 = self.agent.report_progress(
            line_new, "T-004 follow-up task with enough text done"
        )
        self.assertNotIn("milestone_review", self._suggestion_kinds(state2))

    def test_phase_key_stable_when_phase_title_renamed(self) -> None:
        self._archive_phase_a()
        reminded_before = set(self.agent._milestone_reminded_phase_keys)
        self.assertIn("phase:1", reminded_before)
        text = self.tasks.read_text(encoding="utf-8")
        self.tasks.write_text(
            text.replace("## Phase A", "## Phase Alpha", 1),
            encoding="utf-8",
        )
        add_task_to_tasks_md(
            self.paths,
            self.pid,
            "Phase Alpha",
            "T-005 renamed phase task with enough text",
        )
        lines = self.tasks.read_text(encoding="utf-8").splitlines()
        line_new = next(i for i, ln in enumerate(lines) if "T-005" in ln)
        self.agent.report_progress(
            line_new, "T-005 renamed phase task with enough text done"
        )
        self.assertEqual(self.agent._milestone_reminded_phase_keys, reminded_before)
        ev = evaluate_milestone_after_archive(
            tasks_path=self.tasks,
            archive_path=self.root / TASKS_ARCHIVE_NAME,
            phase="Phase Alpha",
            reminded_phase_keys=self.agent._milestone_reminded_phase_keys,
            dismissed_phase_keys=self.agent._milestone_dismissed_phase_keys,
        )
        self.assertTrue(ev["m1"])
        self.assertFalse(ev["should_remind"])
        self.assertEqual(
            phase_key_for_title(lines, "Phase Alpha"),
            "phase:1",
        )

    @patch("subagent.SubagentRunner.run_deliverable_review")
    def test_deliverable_review_pass_clears_reminded_via_executor(
        self, mock_run_review
    ) -> None:
        from session import create_new
        from subagent import SubagentResult
        from tools.executor import ToolExecutor
        from tools.registry import ToolRegistry

        self._archive_phase_a()
        self.assertIn("phase:1", self.agent._milestone_reminded_phase_keys)
        mock_run_review.return_value = SubagentResult(
            kind="review",
            summary="Looks good.\nREVIEW_VERDICT: pass",
            paths_cited=[],
            tool_rounds=1,
            truncated=False,
            task="review milestone delivery",
            verdict="pass",
        )

        session = create_new(self.paths)
        session.meta.project_id = self.pid
        session.meta.project_root = f"workspace/{self.pid}"
        session.meta.project_plan_status = "confirmed"
        session.meta.active_shell = "project"
        session.save()

        ToolRegistry.load(self.paths)
        executor = ToolExecutor.create(
            paths=self.paths,
            session_dir=session.session_dir,
            allowed_evolved=set(),
            confirm_fn=lambda _p, _a: "y",
        )
        executor.session.active_shell = "project"
        executor.session.project_root = f"workspace/{self.pid}"
        executor.session.project_id = self.pid
        executor.session.project_plan_status = "confirmed"
        executor.begin_turn()

        result = executor.run(
            "deliverable_review",
            {"task": "review milestone delivery"},
        )
        self.assertTrue(result.ok)
        self.assertEqual(len(self.agent._milestone_reminded_phase_keys), 0)


class MilestoneOverlayTests(unittest.TestCase):
    """IT-476 / T-4717 · overlay milestone_review_suggested."""

    def setUp(self) -> None:
        self.paths = make_temp_agent_paths(self)
        self.pid = normalize_project_id(f"ov-{secrets.token_hex(3)}")
        create_project(self.paths, self.pid)
        drop_plan_agent(self.pid)
        self.agent = get_plan_agent(self.paths, self.pid)
        self.root = project_dir(self.paths, self.pid)
        self.tasks = self.root / "TASKS.md"

    def _write(self, body: str) -> None:
        self.tasks.write_text(body.rstrip() + "\n", encoding="utf-8")

    def test_format_project_overlay_includes_milestone_key(self) -> None:
        from project_mode import format_project_overlay

        overlay = format_project_overlay(
            project_root=f"workspace/{self.pid}",
            project_id=self.pid,
            plan_status="confirmed",
            milestone_review_suggested="phase:2",
        )
        self.assertIn("milestone_review_suggested: phase:2", overlay)

    def test_format_project_overlay_omits_when_absent(self) -> None:
        from project_mode import format_project_overlay

        overlay = format_project_overlay(
            project_root=f"workspace/{self.pid}",
            project_id=self.pid,
            plan_status="confirmed",
        )
        self.assertNotIn("milestone_review_suggested:", overlay)

    def test_read_overlay_key_from_saved_state(self) -> None:
        from project_mode import read_milestone_review_overlay_key

        self._write(
            "## Phase A\n"
            "- [ ] T-001 first task with enough text\n"
            "- [ ] T-002 second task with enough text\n"
        )
        self.agent.report_progress(1, "T-001 first task with enough text done")
        self.agent.report_progress(1, "T-002 second task with enough text done")
        self.assertEqual(self.agent.milestone_review_overlay_key(), "phase:1")
        self.assertEqual(
            read_milestone_review_overlay_key(self.paths, self.pid),
            "phase:1",
        )

    def test_overlay_key_cleared_after_review_pass(self) -> None:
        from project_mode import read_milestone_review_overlay_key

        self._write("## Phase A\n- [ ] T-001 only task with enough text\n")
        self.agent.report_progress(1, "T-001 only task with enough text done")
        self.assertEqual(self.agent.milestone_review_overlay_key(), "phase:1")
        self.agent.clear_milestone_reminded_on_review("pass")
        self.assertIsNone(self.agent.milestone_review_overlay_key())
        self.assertIsNone(read_milestone_review_overlay_key(self.paths, self.pid))

    def test_build_system_prompt_injects_overlay_key(self) -> None:
        from loader import build_system_prompt
        from session import create_new

        self._write("## Phase A\n- [ ] T-001 only task with enough text\n")
        self.agent.report_progress(1, "T-001 only task with enough text done")
        session = create_new(self.paths)
        session.meta.active_shell = "project"
        session.meta.project_id = self.pid
        session.meta.project_root = f"workspace/{self.pid}"
        session.meta.project_plan_status = "confirmed"
        loaded = build_system_prompt(session, paths=self.paths)
        self.assertIn("milestone_review_suggested: phase:1", loaded.prompt)


if __name__ == "__main__":
    unittest.main()
