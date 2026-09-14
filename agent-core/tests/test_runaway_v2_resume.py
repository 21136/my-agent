"""Tests for runaway v2 resume bootstrap."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from runaway_v2.checklist import build_checklist, first_open_item
from runaway_v2.resume import bootstrap_on_resume, resume_user_line
from tests.isolation_helpers import temporary_agent_paths


class RunawayV2ResumeTests(unittest.TestCase):
    @patch.dict(os.environ, {"MY_AGENT_RUNAWAY_V2": "1"}, clear=False)
    def test_resume_user_line_includes_focus_and_forbids_harness_wait(self) -> None:
        with temporary_agent_paths() as paths:
            root = paths.workspace / "demo"
            root.mkdir(parents=True, exist_ok=True)
            (root / "PROJECT.md").write_text("# Demo\nREQ-001\nAC-001\n", encoding="utf-8")
            (root / "DESIGN.md").write_text("UX-001\nTD-001\n", encoding="utf-8")
            (root / "TASKS.md").write_text("- [ ] T-001 task\n", encoding="utf-8")
            (root / "VERIFY.md").write_text("V-001\n", encoding="utf-8")
            checklist = build_checklist(paths, "demo")
            item = first_open_item(checklist)
            assert item is not None
            item.status = "failed"
            item.last_failure = {"tail": "pytest 工作目录无效", "command": "run_command"}
            line = resume_user_line(paths, "demo", checklist)
            self.assertIn(item.id, line)
            self.assertIn("不要要求用户等待", line)
            self.assertIn("pytest", line)

    @patch.dict(os.environ, {"MY_AGENT_RUNAWAY_V2": "1"}, clear=False)
    def test_bootstrap_hint_tells_agent_to_act(self) -> None:
        with temporary_agent_paths() as paths:
            root = paths.workspace / "demo"
            root.mkdir(parents=True, exist_ok=True)
            (root / "PROJECT.md").write_text("# Demo\nREQ-001\nAC-001\n", encoding="utf-8")
            (root / "DESIGN.md").write_text("UX-001\nTD-001\n", encoding="utf-8")
            (root / "TASKS.md").write_text("- [ ] T-001 task\n", encoding="utf-8")
            (root / "VERIFY.md").write_text("V-001\n", encoding="utf-8")
            checklist = build_checklist(paths, "demo")
            item = first_open_item(checklist)
            assert item is not None
            item.status = "failed"
            hint, _changed = bootstrap_on_resume(paths, "demo", checklist)
            self.assertIn("继续狂奔", hint)
            self.assertIn("不要", hint)


if __name__ == "__main__":
    unittest.main()
