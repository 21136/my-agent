"""PLAN-ARCH M3 · archive on done/drop + closed-section migrate."""

from __future__ import annotations

import secrets
import unittest

from project_mode import (
    TASKS_ARCHIVE_NAME,
    archive_and_remove_task_line,
    create_project,
    drop_task_line,
    migrate_closed_sections_to_archive,
    normalize_project_id,
    project_dir,
    read_task_stats,
    toggle_task_line,
)

from tests.isolation_helpers import make_temp_agent_paths


class PlanArchArchiveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.paths = make_temp_agent_paths(self)
        self.pid = normalize_project_id(f"ar-{secrets.token_hex(3)}")
        create_project(self.paths, self.pid)
        self.root = project_dir(self.paths, self.pid)
        self.tasks = self.root / "TASKS.md"

    def _write(self, body: str) -> None:
        self.tasks.write_text(body.rstrip() + "\n", encoding="utf-8")

    def test_toggle_done_archives_and_removes(self) -> None:
        self._write(
            "## Phase 1\n"
            "- [ ] T-001 do the thing with enough text\n"
            "- [ ] T-002 still open here\n"
        )
        result = toggle_task_line(self.paths, self.pid, 1, True)
        self.assertTrue(result.get("done"))
        text = self.tasks.read_text(encoding="utf-8")
        self.assertNotIn("T-001", text)
        self.assertIn("T-002", text)
        archive = (self.root / TASKS_ARCHIVE_NAME).read_text(encoding="utf-8")
        self.assertIn("T-001", archive)
        self.assertIn("closed:done", archive)
        stats = read_task_stats(self.tasks)
        self.assertEqual(stats.open_count, 1)
        self.assertEqual(stats.done, 1)

    def test_drop_archives_wontfix(self) -> None:
        self._write("## Phase 1\n- [ ] Drop me please item text\n")
        drop_task_line(self.paths, self.pid, 1)
        self.assertNotIn("Drop me", self.tasks.read_text(encoding="utf-8"))
        archive = (self.root / TASKS_ARCHIVE_NAME).read_text(encoding="utf-8")
        self.assertIn("closed:wontfix", archive)

    def test_migrate_closed_section(self) -> None:
        self._write(
            "## Phase 1\n"
            "- [ ] Keep open task with enough text\n"
            "## 已关闭\n"
            "- [x] Old done item with enough text\n"
            "- [ ] Abandoned open with enough text\n"
        )
        n = migrate_closed_sections_to_archive(self.paths, self.pid)
        self.assertEqual(n, 2)
        text = self.tasks.read_text(encoding="utf-8")
        self.assertNotIn("已关闭", text)
        self.assertIn("Keep open", text)
        archive = (self.root / TASKS_ARCHIVE_NAME).read_text(encoding="utf-8")
        self.assertIn("Old done", archive)
        self.assertIn("Abandoned open", archive)

    def test_close_reason_validation(self) -> None:
        self._write("## Phase 1\n- [ ] X with enough text\n")
        with self.assertRaises(Exception):
            archive_and_remove_task_line(
                self.paths, self.pid, 1, reason="nope", source="test"
            )


if __name__ == "__main__":
    unittest.main()
