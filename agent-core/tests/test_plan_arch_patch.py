"""IT-182 / IT-183 · PLAN-ARCH M6: file_patch gated proposals."""

from __future__ import annotations

import json
import secrets
import unittest
from unittest.mock import MagicMock

from plan_agent import drop_plan_agent, get_plan_agent
from plan_patch import apply_plan_patch, apply_replacements, build_patch_preview
from project_mode import ProjectModeError, create_project, normalize_project_id, project_dir, snapshot_plan_fingerprints, sync_plan_dirty_if_structure_changed

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

    def test_it_aff_01_accept_notice_no_diff(self) -> None:
        """IT-AFF-01: adopted partner_notices are one line, no diff hunks."""
        self.tasks.write_text(
            "## Phase 1\n- [ ] old task\n",
            encoding="utf-8",
        )
        preview = build_patch_preview(
            self.paths,
            self.pid,
            relpath="TASKS.md",
            replacements=[{"old": "old task", "new": "new task"}],
        )
        sug = self.agent._suggestion(
            kind="file_patch",
            title="改 TASKS.md（待采纳）",
            body="rename task",
            key="tasks-aff",
            risk="gate",
            action="apply_patch",
            payload={
                "path": "TASKS.md",
                "base_hash": preview["base_hash"],
                "replacements": [{"old": "old task", "new": "new task"}],
                "diff": preview["diff"],
            },
        )
        self.agent.park_gated_suggestion(sug)
        self.agent.accept_suggestion(sug["id"])
        state = self.agent.build_state()
        notices = state.get("partner_notices") or []
        self.assertTrue(notices)
        joined = "\n".join(notices)
        self.assertIn("已采纳写入", joined)
        self.assertNotIn("@@", joined)
        self.assertNotIn("\n-", joined)

    def test_adopt_clears_plan_dirty(self) -> None:
        """After human adopt, session plan_dirty should clear (Phase 40)."""
        from project_api import dispatch_project_message
        from session import create_new

        session = create_new(self.paths, conversation_id=f"aff-{secrets.token_hex(3)}")
        session.meta.project_id = self.pid
        session.meta.project_root = f"workspace/{self.pid}"
        session.meta.active_shell = "project"
        session.meta.project_plan_status = "confirmed"
        snapshot_plan_fingerprints(session, self.paths, self.pid)
        session.save()

        self.tasks.write_text("## Phase 1\n- [ ] old task\n", encoding="utf-8")
        preview = build_patch_preview(
            self.paths,
            self.pid,
            relpath="TASKS.md",
            replacements=[{"old": "old task", "new": "new task"}],
        )
        sug = self.agent._suggestion(
            kind="file_patch",
            title="改 TASKS.md（待采纳）",
            body="rename",
            key="dirty-aff",
            risk="gate",
            action="apply_patch",
            payload={
                "path": "TASKS.md",
                "base_hash": preview["base_hash"],
                "replacements": [{"old": "old task", "new": "new task"}],
                "diff": preview["diff"],
            },
        )
        self.agent.park_gated_suggestion(sug)
        sync_plan_dirty_if_structure_changed(session, self.paths)
        self.assertEqual(session.meta.project_plan_status, "plan_dirty")

        dispatch_project_message(
            session,
            self.paths,
            {
                "type": "project.plan.accept_suggestion",
                "suggestion_id": sug["id"],
            },
        )
        self.assertEqual(session.meta.project_plan_status, "confirmed")
        self.assertFalse(self.agent.check_plan_dirty())

    def test_maybe_clear_stale_plan_dirty_on_load(self) -> None:
        from project_api import maybe_clear_stale_plan_dirty
        from session import create_new

        session = create_new(self.paths, conversation_id=f"stale-{secrets.token_hex(3)}")
        session.meta.project_id = self.pid
        session.meta.project_root = f"workspace/{self.pid}"
        session.meta.active_shell = "project"
        session.meta.project_plan_status = "confirmed"
        session.meta.project_plan_confirmed_at = "2026-08-04T00:00:00Z"
        snapshot_plan_fingerprints(session, self.paths, self.pid)
        session.save()

        self.tasks.write_text(
            "## Phase 1\n- [ ] task\n\n## Phase 8 — new phase\n",
            encoding="utf-8",
        )
        sync_plan_dirty_if_structure_changed(session, self.paths)
        self.assertEqual(session.meta.project_plan_status, "plan_dirty")
        self.assertEqual(len(self.agent._pending_gated), 0)

        cleared = maybe_clear_stale_plan_dirty(session, self.paths, self.agent)
        self.assertTrue(cleared)
        self.assertEqual(session.meta.project_plan_status, "confirmed")
        self.assertFalse(self.agent.check_plan_dirty())

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

    def test_it4810_same_path_patches_merge_to_one_card(self) -> None:
        """BUG-026: two patch ops on MAP.md → one suggestion; single accept applies both."""
        self.map.write_text(
            "# m\n\n## Section A\n\n## Section B\n",
            encoding="utf-8",
        )
        before = self.map.read_text(encoding="utf-8")
        fake_resp = MagicMock()
        fake_resp.content = json.dumps(
            {
                "operations": [
                    {
                        "kind": "patch",
                        "path": "MAP.md",
                        "replacements": [{"old": "## Section A", "new": "## Part A"}],
                        "reason": "rename A",
                    },
                    {
                        "kind": "patch",
                        "path": "MAP.md",
                        "replacements": [{"old": "## Section B", "new": "## Part B"}],
                        "reason": "rename B",
                    },
                ]
            },
            ensure_ascii=False,
        )
        fake_llm = MagicMock()
        fake_llm.chat.return_value = fake_resp
        fake_llm._plan_model = "test"
        self.agent._llm = fake_llm

        summary = self.agent.reason_about_intent("map 两处标题都要改")
        self.assertIn("提案", summary)
        self.assertEqual(self.map.read_text(encoding="utf-8"), before)

        state = self.agent.build_state()
        patches = [
            s
            for s in (state.get("suggestions") or [])
            if isinstance(s, dict) and s.get("action") == "apply_patch"
        ]
        self.assertEqual(len(patches), 1)
        payload = patches[0].get("payload") or {}
        self.assertEqual(payload.get("path"), "MAP.md")
        reps = payload.get("replacements")
        self.assertIsInstance(reps, list)
        self.assertEqual(len(reps), 2)

        result = self.agent.accept_suggestion(patches[0]["id"])
        self.assertTrue(result.get("ok"))
        text = self.map.read_text(encoding="utf-8")
        self.assertIn("## Part A", text)
        self.assertIn("## Part B", text)
        self.assertNotIn("## Section A", text)
        self.assertNotIn("## Section B", text)

    def test_it4813_multi_file_patches_all_accept(self) -> None:
        """S-481 proxy: TASKS + MAP×2 + PROJECT×2 → 3 cards, sequential accept OK."""
        self.tasks.write_text("## P\n- [ ] old task\n", encoding="utf-8")
        self.map.write_text("# m\n\n## A\n\n## B\n", encoding="utf-8")
        (self.root / "PROJECT.md").write_text("# p\n\n## X\n", encoding="utf-8")
        fake_resp = MagicMock()
        fake_resp.content = json.dumps(
            {
                "operations": [
                    {
                        "kind": "patch",
                        "path": "TASKS.md",
                        "replacements": [{"old": "old task", "new": "new task"}],
                    },
                    {
                        "kind": "patch",
                        "path": "MAP.md",
                        "replacements": [{"old": "## A", "new": "## A1"}],
                    },
                    {
                        "kind": "patch",
                        "path": "MAP.md",
                        "replacements": [{"old": "## B", "new": "## B1"}],
                    },
                    {
                        "kind": "patch",
                        "path": "PROJECT.md",
                        "replacements": [{"old": "## X", "new": "## X1"}],
                    },
                    {
                        "kind": "patch",
                        "path": "PROJECT.md",
                        "replacements": [{"old": "## X1", "new": "## X2"}],
                    },
                ]
            },
            ensure_ascii=False,
        )
        fake_llm = MagicMock()
        fake_llm.chat.return_value = fake_resp
        fake_llm._plan_model = "test"
        self.agent._llm = fake_llm

        self.agent.reason_about_intent("sync all plan files")
        state = self.agent.build_state()
        patches = [
            s
            for s in (state.get("suggestions") or [])
            if isinstance(s, dict) and s.get("action") == "apply_patch"
        ]
        self.assertEqual(len(patches), 3)
        paths_seen = {p.get("payload", {}).get("path") for p in patches}
        self.assertEqual(paths_seen, {"TASKS.md", "MAP.md", "PROJECT.md"})

        for sug in patches:
            result = self.agent.accept_suggestion(sug["id"])
            self.assertTrue(result.get("ok"), msg=result.get("summary"))

        self.assertIn("new task", self.tasks.read_text(encoding="utf-8"))
        map_text = self.map.read_text(encoding="utf-8")
        self.assertIn("## A1", map_text)
        self.assertIn("## B1", map_text)
        self.assertIn("## X2", (self.root / "PROJECT.md").read_text(encoding="utf-8"))

    def test_it4812_rebase_pending_same_path_after_accept(self) -> None:
        """BUG-026 A2: adopt first card rebases second card base_hash; sequential accept OK."""
        self.map.write_text(
            "# m\n\n## Section A\n\n## Section B\n",
            encoding="utf-8",
        )
        preview_a = build_patch_preview(
            self.paths,
            self.pid,
            relpath="MAP.md",
            replacements=[{"old": "## Section A", "new": "## Part A"}],
        )
        preview_b = build_patch_preview(
            self.paths,
            self.pid,
            relpath="MAP.md",
            replacements=[{"old": "## Section B", "new": "## Part B"}],
        )
        self.assertEqual(preview_a["base_hash"], preview_b["base_hash"])

        sug_a = self.agent._suggestion(
            kind="file_patch",
            title="改 MAP.md A（待采纳）",
            body="rename A",
            key="map-a",
            risk="gate",
            action="apply_patch",
            payload={
                "path": "MAP.md",
                "base_hash": preview_a["base_hash"],
                "replacements": [{"old": "## Section A", "new": "## Part A"}],
                "diff": preview_a["diff"],
            },
        )
        sug_b = self.agent._suggestion(
            kind="file_patch",
            title="改 MAP.md B（待采纳）",
            body="rename B",
            key="map-b",
            risk="gate",
            action="apply_patch",
            payload={
                "path": "MAP.md",
                "base_hash": preview_b["base_hash"],
                "replacements": [{"old": "## Section B", "new": "## Part B"}],
                "diff": preview_b["diff"],
            },
        )
        self.agent.park_gated_suggestion(sug_a)
        self.agent.park_gated_suggestion(sug_b)

        result = self.agent.accept_suggestion(sug_a["id"])
        self.assertTrue(result.get("ok"))
        self.assertIn(sug_b["id"], self.agent._pending_gated)

        rebased_payload = self.agent._pending_gated[sug_b["id"]].get("payload") or {}
        current_preview = build_patch_preview(
            self.paths,
            self.pid,
            relpath="MAP.md",
            replacements=[{"old": "## Section B", "new": "## Part B"}],
        )
        self.assertEqual(rebased_payload.get("base_hash"), current_preview["base_hash"])
        self.assertNotEqual(rebased_payload.get("base_hash"), preview_b["base_hash"])

        result_b = self.agent.accept_suggestion(sug_b["id"])
        self.assertTrue(result_b.get("ok"))
        text = self.map.read_text(encoding="utf-8")
        self.assertIn("## Part A", text)
        self.assertIn("## Part B", text)

    def test_it4812_rebase_withdraws_stale_patch(self) -> None:
        """BUG-026 A2: after adopt, conflicting pending patch is auto-withdrawn."""
        self.map.write_text("# m\n\n## Section A\n\n## Section B\n", encoding="utf-8")
        preview_a = build_patch_preview(
            self.paths,
            self.pid,
            relpath="MAP.md",
            replacements=[{"old": "## Section A", "new": "## Part A"}],
        )
        preview_conflict = build_patch_preview(
            self.paths,
            self.pid,
            relpath="MAP.md",
            replacements=[{"old": "## Section A", "new": "## Gone"}],
        )
        sug_a = self.agent._suggestion(
            kind="file_patch",
            title="改 MAP.md A",
            body="rename A",
            key="map-a2",
            risk="gate",
            action="apply_patch",
            payload={
                "path": "MAP.md",
                "base_hash": preview_a["base_hash"],
                "replacements": [{"old": "## Section A", "new": "## Part A"}],
                "diff": preview_a["diff"],
            },
        )
        sug_conflict = self.agent._suggestion(
            kind="file_patch",
            title="改 MAP.md conflict",
            body="also rename A",
            key="map-c",
            risk="gate",
            action="apply_patch",
            payload={
                "path": "MAP.md",
                "base_hash": preview_conflict["base_hash"],
                "replacements": [{"old": "## Section A", "new": "## Gone"}],
                "diff": preview_conflict["diff"],
            },
        )
        self.agent.park_gated_suggestion(sug_a)
        self.agent.park_gated_suggestion(sug_conflict)

        result = self.agent.accept_suggestion(sug_a["id"])
        self.assertTrue(result.get("ok"))
        self.assertNotIn(sug_conflict["id"], self.agent._pending_gated)
        self.assertIn("已撤回无效提案", result.get("summary") or "")


if __name__ == "__main__":
    unittest.main()
