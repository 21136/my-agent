"""IT-180 · PLAN-ARCH M1: open-queue injection slice excludes done/closed/archive."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from project_mode import (
    build_tasks_injection_slice,
    format_project_overlay,
    format_tasks_open_slice_numbered,
    is_tasks_archive_filename,
    read_tasks_text_for_injection,
    select_open_task_indices_for_injection,
)


SAMPLE = """# demo

## Phase 1
- [x] done A
- [x] done B
- [ ] T-001 open in P1

## Phase 2
- [ ] T-002 open in P2
- [x] done C

## 已关闭
- [ ] should never inject
- [x] old done

## Phase 3
- [ ] T-003 later open
"""


class PlanArchInjectionTests(unittest.TestCase):
    def test_slice_omits_done_and_closed_section(self) -> None:
        slice_text = build_tasks_injection_slice(SAMPLE)
        self.assertIn("T-001", slice_text)
        self.assertIn("T-002", slice_text)
        self.assertNotIn("done A", slice_text)
        self.assertNotIn("should never inject", slice_text)
        self.assertNotIn("- [x]", slice_text)
        self.assertIn("已完成已省略", slice_text)
        self.assertIn("TASKS.archive.md", slice_text)

    def test_active_phase_preferred_under_cap(self) -> None:
        # Many opens; cap=1 should keep active (P1) item
        idxs = select_open_task_indices_for_injection(SAMPLE, open_cap=1)
        self.assertEqual(len(idxs), 1)
        line = SAMPLE.splitlines()[idxs[0]]
        self.assertIn("T-001", line)

    def test_overlay_has_open_queue_without_closed(self) -> None:
        slice_text = build_tasks_injection_slice(SAMPLE)
        overlay = format_project_overlay(
            project_root="workspace/demo",
            project_id="demo",
            plan_status="confirmed",
            next_open_task="- [ ] T-001 open in P1",
            armed_task_id="T-001",
            open_tasks_slice=slice_text,
        )
        self.assertIn("open_queue:", overlay)
        self.assertIn("T-001", overlay)
        self.assertNotIn("should never inject", overlay)
        self.assertIn("plan_inject:", overlay)

    def test_numbered_slice_preserves_original_line_index(self) -> None:
        numbered = format_tasks_open_slice_numbered(SAMPLE)
        self.assertIn("T-001", numbered)
        self.assertNotIn("should never inject", numbered)
        # original index of T-001 line
        lines = SAMPLE.splitlines()
        idx = next(i for i, ln in enumerate(lines) if "T-001" in ln)
        self.assertIn(f"{idx}|", numbered)

    def test_archive_file_never_read_for_injection(self) -> None:
        self.assertTrue(is_tasks_archive_filename("TASKS.archive.md"))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "TASKS.archive.md"
            archive.write_text("- [ ] leaked from archive\n", encoding="utf-8")
            self.assertEqual(read_tasks_text_for_injection(archive), "")
            tasks = root / "TASKS.md"
            tasks.write_text("- [ ] real open\n", encoding="utf-8")
            self.assertIn("real open", read_tasks_text_for_injection(tasks))


if __name__ == "__main__":
    unittest.main()
