"""Regression tests for the real-LLM runaway experience harness."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from runaway_v2.checklist import build_checklist, save_checklist
from session import create_new
from tests.isolation_helpers import temporary_agent_paths
import runaway_experience as experience


class RunawayExperienceTests(unittest.TestCase):
    def _session(self, paths):
        session = create_new(paths, conversation_id="_runaway_experience_test_")
        session.meta.project_id = "demo"
        session.meta.project_root = "workspace/demo"
        session.meta.project_plan_status = "confirmed"
        session.meta.project_runaway_enabled = True
        session.meta.project_runaway_checkpoint = "verifying"
        return session

    @patch.dict(os.environ, {"MY_AGENT_RUNAWAY_V2": "1"}, clear=False)
    def test_v2_verify_does_not_fake_verifying_milestone(self) -> None:
        with temporary_agent_paths() as paths:
            root = paths.workspace / "demo"
            root.mkdir(parents=True, exist_ok=True)
            (root / "PROJECT.md").write_text(
                "REQ-001\nAC-001\n\n## 验收标准\n\n- 命令：`python verify.py` 期望退出码：0\n",
                encoding="utf-8",
            )
            (root / "DESIGN.md").write_text("UX-001\nTD-001\n", encoding="utf-8")
            (root / "TASKS.md").write_text("- [x] T-001 done\n", encoding="utf-8")
            (root / "VERIFY.md").write_text("V-001 T-001 pass\nAC-001\n", encoding="utf-8")
            (root / "ENV.md").write_text(
                "quality:\n  commands:\n    - id: smoke\n      cmd: [\"echo\", \"ok\"]\n",
                encoding="utf-8",
            )
            checklist = build_checklist(paths, "demo")
            self.assertTrue(checklist.items)
            save_checklist(paths, checklist)
            session = self._session(paths)

            with patch.object(experience, "UNTIL", "verifying"):
                self.assertFalse(experience._until_milestone_reached(session, paths, "demo"))

            checklist.items[0].status = "passed"
            save_checklist(paths, checklist)
            with patch.object(experience, "UNTIL", "verifying"):
                self.assertTrue(experience._until_milestone_reached(session, paths, "demo"))

    @patch.dict(os.environ, {"MY_AGENT_RUNAWAY_V2": "1"}, clear=False)
    def test_v2_directed_block_stops_experience(self) -> None:
        with temporary_agent_paths() as paths:
            root = paths.workspace / "demo"
            root.mkdir(parents=True, exist_ok=True)
            (root / "PROJECT.md").write_text(
                "REQ-001\nAC-001\n\n## 验收标准\n\n- 命令：`python verify.py` 期望退出码：0\n",
                encoding="utf-8",
            )
            (root / "DESIGN.md").write_text("UX-001\nTD-001\n", encoding="utf-8")
            (root / "TASKS.md").write_text("- [x] T-001 done\n", encoding="utf-8")
            (root / "VERIFY.md").write_text("V-001 T-001 pass\nAC-001\n", encoding="utf-8")
            (root / "ENV.md").write_text(
                "quality:\n  commands:\n    - id: smoke\n      cmd: [\"echo\", \"ok\"]\n",
                encoding="utf-8",
            )
            checklist = build_checklist(paths, "demo")
            self.assertTrue(checklist.items)
            item = checklist.items[0]
            item.status = "failed"
            item.attempts = 2
            item.directed_used = True
            save_checklist(paths, checklist)
            session = self._session(paths)

            self.assertTrue(experience._v2_human_block(session, paths, "demo"))


if __name__ == "__main__":
    unittest.main()
