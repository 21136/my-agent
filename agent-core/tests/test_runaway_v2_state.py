"""Tests for runaway v2 project.state fields (T-6105)."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from project_api import project_state_payload
from runaway_v2.checklist import Checklist, ChecklistItem, build_checklist, save_checklist
from runaway_v2.state import build_v2_state_fields
from session import create_new
from tests.isolation_helpers import temporary_agent_paths


class RunawayV2StateTests(unittest.TestCase):
    def _write_project(self, root: Path) -> None:
        root.mkdir(parents=True, exist_ok=True)
        (root / "PROJECT.md").write_text(
            "# Demo\n\nREQ-001\nAC-001\n\n## 验收标准\n\n- 命令：`python verify.py` 期望退出码：0\n",
            encoding="utf-8",
        )
        (root / "DESIGN.md").write_text("UX-001\nTD-001\n", encoding="utf-8")
        (root / "TASKS.md").write_text(
            "- [x] T-001 done\n- [ ] 后续任务待规划\n",
            encoding="utf-8",
        )
        (root / "VERIFY.md").write_text("- V-001 T-001 pass\nAC-001\n", encoding="utf-8")

    @mock.patch.dict(os.environ, {"MY_AGENT_RUNAWAY_V2": "1"}, clear=False)
    def test_project_state_includes_v2_fields(self) -> None:
        with temporary_agent_paths() as paths:
            pid = "demo"
            self._write_project(paths.workspace / pid)
            session = create_new(paths, conversation_id="_runaway_v2_state_")
            session.meta.project_id = pid
            session.meta.project_root = f"workspace/{pid}"
            session.meta.active_shell = "project"
            session.meta.project_plan_status = "confirmed"
            session.meta.project_runaway_enabled = True
            session.meta.project_runaway_acceptance_passed = True
            checklist = build_checklist(paths, pid)
            mx5 = next(item for item in checklist.items if item.id == "MX-5")
            mx5.status = "failed"
            mx5.attempts = 2
            mx5.directed_used = True
            mx5.last_failure = {"command": "lint MX-5", "tail": "缺 quality.commands"}
            save_checklist(paths, checklist)

            payload = project_state_payload(session, paths)
            self.assertEqual(payload.get("runaway_version"), 2)
            self.assertIn("runaway_user_line", payload)
            self.assertTrue(payload.get("runaway_blocked"))
            self.assertFalse(payload.get("runaway_acceptance_passed"))
            checklist_payload = payload.get("runaway_checklist") or {}
            self.assertGreaterEqual(checklist_payload.get("total", 0), 1)
            self.assertIsNotNone(payload.get("runaway_block"))

    def test_state_does_not_replace_active_implementation_with_release_copy(self) -> None:
        with temporary_agent_paths() as paths:
            pid = "demo"
            self._write_project(paths.workspace / pid)
            (paths.workspace / pid / "TASKS.md").write_text(
                "- [ ] T-001 active task\n",
                encoding="utf-8",
            )
            checklist = Checklist(
                1,
                pid,
                "",
                "",
                items=[
                    ChecklistItem(
                        id="AC-PROJECT",
                        kind="acceptance",
                        title="项目验收命令",
                        status="passed",
                    )
                ],
            )

            fields = build_v2_state_fields(
                paths,
                project_id=pid,
                plan_status="confirmed",
                checklist=checklist,
            )

            self.assertEqual(fields["runaway_phase"], "implement")
            self.assertEqual(fields["runaway_user_line"], "正在实现 T-001")

    @mock.patch.dict(os.environ, {"MY_AGENT_RUNAWAY_V2": "1"}, clear=False)
    def test_project_state_preserves_v2_pause_reason(self) -> None:
        with temporary_agent_paths() as paths:
            pid = "demo"
            self._write_project(paths.workspace / pid)
            session = create_new(paths, conversation_id="_runaway_v2_state_paused_")
            session.meta.project_id = pid
            session.meta.project_root = f"workspace/{pid}"
            session.meta.active_shell = "project"
            session.meta.project_plan_status = "confirmed"
            session.meta.project_runaway_enabled = True
            session.meta.project_runaway_checkpoint = "paused"
            session.meta.project_runaway_paused_reason = "连续 2 个自动回合没有检测到项目状态变化"

            payload = project_state_payload(session, paths)

            self.assertTrue(payload.get("runaway_blocked"))
            self.assertEqual(
                payload.get("runaway_paused_reason"),
                "连续 2 个自动回合没有检测到项目状态变化",
            )
            self.assertEqual(payload.get("runaway_checkpoint"), "paused")


if __name__ == "__main__":
    unittest.main()
