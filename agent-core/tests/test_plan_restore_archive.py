"""Plan restore from TASKS.archive.md + plan_tools project path resolution."""

from __future__ import annotations

import json
import secrets
import unittest
from unittest.mock import MagicMock

from plan_agent import drop_plan_agent, get_plan_agent, looks_like_restore_request
from plan_tools import execute_plan_tool
from project_mode import (
    TASKS_ARCHIVE_NAME,
    archive_and_remove_task_line,
    create_project,
    normalize_project_id,
    project_dir,
    read_task_stats,
    restore_archived_tasks,
)
from tests.isolation_helpers import make_temp_agent_paths


class PlanRestoreArchiveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.paths = make_temp_agent_paths(self)
        self.pid = normalize_project_id(f"restore-{secrets.token_hex(3)}")
        create_project(self.paths, self.pid)
        drop_plan_agent(self.pid)
        self.agent = get_plan_agent(self.paths, self.pid)
        self.root = project_dir(self.paths, self.pid)
        self.tasks = self.root / "TASKS.md"
        self.archive = self.root / TASKS_ARCHIVE_NAME

    def tearDown(self) -> None:
        drop_plan_agent(self.pid)

    def _write_tasks(self, text: str) -> None:
        self.tasks.write_text(text, encoding="utf-8")

    def test_restore_archived_tasks_roundtrip(self) -> None:
        self._write_tasks(
            "## Phase 7 — demo\n"
            "- [ ] T-020 first task\n"
            "- [ ] T-021 second task\n"
        )
        archive_and_remove_task_line(
            self.paths, self.pid, 1, reason="done", source="toggle"
        )
        archive_and_remove_task_line(
            self.paths, self.pid, 1, reason="done", source="toggle"
        )
        self.assertNotIn("T-020", self.tasks.read_text(encoding="utf-8"))
        self.assertIn("T-020", self.archive.read_text(encoding="utf-8"))

        result = restore_archived_tasks(
            self.paths,
            self.pid,
            task_ids=["T-020", "T-021"],
        )
        self.assertEqual(len(result["restored"]), 2)
        tasks_text = self.tasks.read_text(encoding="utf-8")
        self.assertIn("- [ ] T-020 first task", tasks_text)
        self.assertIn("- [ ] T-021 second task", tasks_text)
        self.assertNotIn("T-020", self.archive.read_text(encoding="utf-8"))
        stats = read_task_stats(self.tasks)
        self.assertEqual(stats.open_count, 2)

    def test_plan_tools_read_bare_tasks_md(self) -> None:
        self._write_tasks("## Phase 1\n- [ ] hello\n")
        tr = execute_plan_tool(
            self.paths,
            self.pid,
            "read_file",
            {"path": "TASKS.md"},
        )
        self.assertTrue(tr.ok, tr.error.message if tr.error else tr)
        self.assertIn("hello", (tr.data or {}).get("content", ""))

    def test_fallback_restore_proposal(self) -> None:
        self._write_tasks("## Phase 7 — demo\n")
        self.archive.write_text(
            "- T-020 menu api · closed:done · 2026-08-04T00:00:00Z · "
            "phase:Phase 7 — demo · src:toggle\n",
            encoding="utf-8",
        )
        self.assertTrue(looks_like_restore_request("帮我把 Phase 7 任务恢复"))
        out = self.agent._handle_restore_request("恢复 Phase 7 任务")
        self.assertIsNotNone(out)
        assert out is not None
        self.assertIn("待审阅", out)
        self.assertGreaterEqual(len(self.agent._pending_gated), 1)

    def test_llm_restore_operation_parks_suggestion(self) -> None:
        self._write_tasks("## Phase 7 — demo\n")
        self.archive.write_text(
            "- T-025 vue page · closed:done · 2026-08-04T00:00:00Z · "
            "phase:Phase 7 — demo · src:toggle\n",
            encoding="utf-8",
        )
        mock_llm = MagicMock()
        mock_llm.chat.return_value = MagicMock(
            content=json.dumps(
                {
                    "reply": "可以恢复",
                    "operations": [
                        {
                            "kind": "restore",
                            "task_ids": ["T-025"],
                            "reason": "误勾选归档",
                        }
                    ],
                },
                ensure_ascii=False,
            )
        )
        mock_llm._plan_model = "deepseek-v4-flash"
        self.agent._llm = mock_llm
        out = self.agent.reason_about_intent("恢复 T-025")
        self.assertIn("待审阅", out)
        sid = next(iter(self.agent._pending_gated))
        result = self.agent.accept_suggestion(sid)
        self.assertTrue(result.get("ok"))
        self.assertIn("T-025", self.tasks.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
