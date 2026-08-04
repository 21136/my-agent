"""IT-182 / IT-183 · PLAN-ARCH M6: file_patch gated proposals."""

from __future__ import annotations

import json
import secrets
import unittest
from unittest.mock import MagicMock

from plan_agent import drop_plan_agent, get_plan_agent
from plan_patch import apply_plan_patch, apply_replacements, build_patch_preview
from project_mode import ProjectModeError, create_project, normalize_project_id, project_dir

from tests.isolation_helpers import make_temp_agent_paths


class PlanArchPatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.paths = make_temp_agent_paths(self)
        self.pid = normalize_project_id(f"ppatch-{secrets.token_hex(3)}")
        create_project(self.paths, self.pid)
        drop_plan_agent(self.pid)
        self.agent = get_plan_agent(self.paths, self.pid)
        self.root = project_dir(self.paths, self.pid)
        self.tasks = self.root / "TASKS.md"
        self.map = self.root / "MAP.md"

    def tearDown(self) -> None:
        drop_plan_agent(self.pid)

    def test_it182_apply_patch_writes_after_accept(self) -> None:
        self.map.write_text("# demo\n\n## Phase 6 修复记录\n\nnote\n", encoding="utf-8")
        before = self.map.read_text(encoding="utf-8")
        reps = [
            {
                "old": "## Phase 6 修复记录",
                "new": "## 修复记录（原 Phase 6）",
            }
        ]
        preview = build_patch_preview(
            self.paths, self.pid, relpath="MAP.md", replacements=reps
        )
        self.assertIn("修复记录", preview["diff"])
        self.assertEqual(self.map.read_text(encoding="utf-8"), before)

        sug = self.agent._suggestion(
            kind="file_patch",
            title="改 MAP.md（待采纳）",
            body="rename section",
            key="map-1",
            risk="gate",
            action="apply_patch",
            payload={
                "path": "MAP.md",
                "base_hash": preview["base_hash"],
                "replacements": reps,
                "diff": preview["diff"],
            },
        )
        self.agent.park_gated_suggestion(sug)
        self.assertEqual(self.map.read_text(encoding="utf-8"), before)

        result = self.agent.accept_suggestion(sug["id"])
        self.assertTrue(result.get("ok"))
        text = self.map.read_text(encoding="utf-8")
        self.assertIn("## 修复记录（原 Phase 6）", text)
        self.assertNotIn("## Phase 6 修复记录", text)

    def test_it182_stale_base_hash_rejects(self) -> None:
        self.map.write_text("# a\n", encoding="utf-8")
        with self.assertRaises(ProjectModeError):
            apply_plan_patch(
                self.paths,
                self.pid,
                relpath="MAP.md",
                replacements=[{"old": "# a", "new": "# b"}],
                base_hash="deadbeef",
            )
        self.assertEqual(self.map.read_text(encoding="utf-8"), "# a\n")

    def test_it183_legacy_move_line_not_parked(self) -> None:
        self.tasks.write_text(
            "## Phase 1\n- [ ] Open task with enough text here\n",
            encoding="utf-8",
        )
        before = self.tasks.read_text(encoding="utf-8")
        fake_resp = MagicMock()
        fake_resp.content = json.dumps(
            {
                "operations": [
                    {
                        "kind": "move",
                        "line": 0,
                        "phase": "Phase 6",
                        "reason": "hallucinated",
                    }
                ]
            },
            ensure_ascii=False,
        )
        fake_llm = MagicMock()
        fake_llm.chat.return_value = fake_resp
        fake_llm._plan_model = "test"
        self.agent._llm = fake_llm

        summary = self.agent.reason_about_intent("phase6 在 map 不合理")
        self.assertIn("拒绝", summary)
        self.assertEqual(self.tasks.read_text(encoding="utf-8"), before)
        self.assertEqual(len(self.agent._pending_gated), 0)
        state = self.agent.build_state()
        moves = [
            s
            for s in (state.get("suggestions") or [])
            if isinstance(s, dict) and s.get("action") == "move_task"
        ]
        self.assertEqual(moves, [])

    def test_llm_map_patch_proposal(self) -> None:
        self.map.write_text("# m\n\n## Phase 6 修复记录\n", encoding="utf-8")
        before = self.map.read_text(encoding="utf-8")
        fake_resp = MagicMock()
        fake_resp.content = json.dumps(
            {
                "operations": [
                    {
                        "kind": "patch",
                        "path": "MAP.md",
                        "replacements": [
                            {
                                "old": "## Phase 6 修复记录",
                                "new": "## 修复记录",
                            }
                        ],
                        "reason": "命名不像执行 Phase",
                    }
                ]
            },
            ensure_ascii=False,
        )
        fake_llm = MagicMock()
        fake_llm.chat.return_value = fake_resp
        fake_llm._plan_model = "test"
        self.agent._llm = fake_llm

        summary = self.agent.reason_about_intent("map 里 phase6 不合理")
        self.assertIn("提案", summary)
        self.assertEqual(self.map.read_text(encoding="utf-8"), before)
        state = self.agent.build_state()
        patches = [
            s
            for s in (state.get("suggestions") or [])
            if isinstance(s, dict) and s.get("action") == "apply_patch"
        ]
        self.assertTrue(patches)
        self.assertIn("diff", patches[0].get("payload") or {})
        self.agent.accept_suggestion(patches[0]["id"])
        self.assertIn("## 修复记录", self.map.read_text(encoding="utf-8"))
        self.assertNotIn("Phase 6", self.map.read_text(encoding="utf-8"))

    def test_replacement_must_be_unique(self) -> None:
        with self.assertRaises(ProjectModeError):
            apply_replacements("aa aa", [{"old": "aa", "new": "b"}])


if __name__ == "__main__":
    unittest.main()
