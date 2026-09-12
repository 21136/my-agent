"""Tests for runaway v2 checklist (IT-6101 / IT-6105 / IT-6106)."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from runaway_v2.acceptance import run_acceptance
from runaway_v2.checklist import (
    Checklist,
    ChecklistItem,
    build_checklist,
    compute_source_fingerprint,
    load_checklist,
    load_or_build_checklist,
    merge_checklist,
    save_checklist,
)
from runaway_v2.phase import derive_phase
from runaway_v2.plan import build_turn_plan
from runaway_v2.state import build_v2_state_fields
from project_manifest import build_manifest, save_manifest, project_manifest_path
from project_release import save_release_acceptance
from tests.isolation_helpers import temporary_agent_paths


class RunawayV2ChecklistTests(unittest.TestCase):
    def _write_core(self, root: Path, *, with_quality: bool = True) -> None:
        (root / "PROJECT.md").write_text(
            "REQ-001\nAC-001\n\n## 验收标准\n\n- 命令：`python verify.py` 期望退出码：0\n",
            encoding="utf-8",
        )
        (root / "DESIGN.md").write_text("# Design\n\nUX-001\nTD-001\n", encoding="utf-8")
        (root / "TASKS.md").write_text("- [ ] T-001 open task\n", encoding="utf-8")
        (root / "VERIFY.md").write_text("- V-001 T-001 pass\nAC-001\n", encoding="utf-8")
        if with_quality:
            (root / "ENV.md").write_text(
                "quality:\n  commands:\n    - id: smoke\n      cmd: [\"echo\", \"ok\"]\n",
                encoding="utf-8",
            )

    def test_it_6101_missing_env_quality_creates_mx5(self) -> None:
        with temporary_agent_paths() as paths:
            pid = "demo"
            root = paths.workspace / pid
            root.mkdir(parents=True, exist_ok=True)
            self._write_core(root, with_quality=False)
            checklist = build_checklist(paths, pid)
            ids = {item.id for item in checklist.items}
            self.assertIn("MX-5", ids)
            mx5 = next(item for item in checklist.items if item.id == "MX-5")
            hook = run_acceptance(paths, pid, mx5)
            self.assertFalse(hook.ok)

            (root / "ENV.md").write_text(
                "quality:\n  commands:\n    - id: smoke\n      cmd: [\"echo\", \"ok\"]\n",
                encoding="utf-8",
            )
            rebuilt = build_checklist(paths, pid)
            mx5_ids = [item.id for item in rebuilt.items if item.id.startswith("MX-5")]
            self.assertEqual(mx5_ids, [])
            hook2 = run_acceptance(paths, pid, mx5)
            self.assertTrue(hook2.ok)

    def test_it_6105_merge_preserves_attempts_on_fingerprint_change(self) -> None:
        with temporary_agent_paths() as paths:
            pid = "demo"
            root = paths.workspace / pid
            root.mkdir(parents=True, exist_ok=True)
            self._write_core(root, with_quality=False)
            first = build_checklist(paths, pid)
            mx5 = next(item for item in first.items if item.id == "MX-5")
            mx5.attempts = 2
            mx5.directed_used = True
            mx5.status = "failed"
            save_checklist(paths, first)

            (root / "TASKS.md").write_text("- [ ] T-001 open\n- [ ] T-002 also open\n", encoding="utf-8")
            merged = load_or_build_checklist(paths, pid)
            kept = next(item for item in merged.items if item.id == "MX-5")
            self.assertEqual(kept.attempts, 2)
            self.assertTrue(kept.directed_used)

    def test_it_6106_open_task_impl_phase_even_when_mx_red(self) -> None:
        with temporary_agent_paths() as paths:
            pid = "demo"
            root = paths.workspace / pid
            root.mkdir(parents=True, exist_ok=True)
            self._write_core(root, with_quality=False)
            checklist = build_checklist(paths, pid)
            phase = derive_phase(paths, pid, plan_status="confirmed", checklist=checklist)
            self.assertEqual(phase.phase, "implement")
            self.assertEqual(phase.active_task_id, "T-001")

    def test_empty_project_does_not_skip_prepare_for_release_wait(self) -> None:
        with temporary_agent_paths() as paths:
            pid = "demo"
            root = paths.workspace / pid
            root.mkdir(parents=True, exist_ok=True)

            phase = derive_phase(
                paths,
                pid,
                plan_status="confirmed",
                checklist=build_checklist(paths, pid),
            )

            self.assertEqual(phase.phase, "prepare")
            self.assertIn("PROJECT.md", phase.blockers)

    def test_complete_documents_need_confirmed_plan_before_release_wait(self) -> None:
        with temporary_agent_paths() as paths:
            pid = "demo"
            root = paths.workspace / pid
            root.mkdir(parents=True, exist_ok=True)
            self._write_core(root)
            (root / "TASKS.md").write_text("- [x] T-001 completed\n", encoding="utf-8")
            checklist = build_checklist(paths, pid)
            for item in checklist.items:
                item.status = "passed"

            draft_phase = derive_phase(
                paths,
                pid,
                plan_status="draft",
                checklist=checklist,
            )
            confirmed_phase = derive_phase(
                paths,
                pid,
                plan_status="confirmed",
                checklist=checklist,
            )

            self.assertEqual(draft_phase.phase, "prepare")
            self.assertEqual(draft_phase.blockers, ("plan_status",))
            self.assertEqual(confirmed_phase.phase, "release_wait")

    def test_prepare_ready_accepts_completed_formal_queue(self) -> None:
        with temporary_agent_paths() as paths:
            pid = "demo"
            root = paths.workspace / pid
            root.mkdir(parents=True, exist_ok=True)
            self._write_core(root)
            (root / "TASKS.md").write_text("- [x] T-001 completed\n", encoding="utf-8")

            item = ChecklistItem(
                id="PREPARE",
                kind="matrix",
                title="四件套就绪",
                acceptance={"type": "prepare_ready"},
            )
            hook = run_acceptance(paths, pid, item)

            self.assertTrue(hook.ok)
            self.assertEqual(hook.exit_code, 0)
            self.assertEqual(hook.command, "documentation_ready_for_design")

    def test_blocked_checklist_item_requires_human_handling(self) -> None:
        with temporary_agent_paths() as paths:
            pid = "demo"
            root = paths.workspace / pid
            root.mkdir(parents=True, exist_ok=True)
            self._write_core(root)
            (root / "TASKS.md").write_text("- [x] T-001 completed\n", encoding="utf-8")
            checklist = Checklist(
                version=1,
                project_id=pid,
                generated_at="",
                source_fingerprint="",
                items=[
                    ChecklistItem(
                        id="MX-5",
                        kind="matrix",
                        title="环境质量命令",
                        status="blocked",
                    )
                ],
            )

            phase = derive_phase(paths, pid, plan_status="confirmed", checklist=checklist)

            self.assertEqual(phase.phase, "human")
            self.assertIn("MX-5", phase.human_reason)

    def test_passed_command_acceptance_survives_reload(self) -> None:
        with temporary_agent_paths() as paths:
            pid = "demo"
            root = paths.workspace / pid
            root.mkdir(parents=True, exist_ok=True)
            self._write_core(root)
            first = build_checklist(paths, pid)
            acceptance = next(item for item in first.items if item.id == "AC-PROJECT")
            acceptance.status = "passed"
            save_checklist(paths, first)

            loaded = load_or_build_checklist(paths, pid)

            self.assertEqual(
                next(item for item in loaded.items if item.id == "AC-PROJECT").status,
                "passed",
            )

    def test_changed_source_requeues_passed_command_acceptance(self) -> None:
        with temporary_agent_paths() as paths:
            pid = "demo"
            root = paths.workspace / pid
            root.mkdir(parents=True, exist_ok=True)
            self._write_core(root)
            first = build_checklist(paths, pid)
            acceptance = next(item for item in first.items if item.id == "AC-PROJECT")
            acceptance.status = "passed"
            acceptance.attempts = 1
            save_checklist(paths, first)

            (root / "PROJECT.md").write_text(
                "REQ-001\nAC-001\n\n## 验收标准\n\n"
                "- 命令：`python verify_new.py` 期望退出码：0\n",
                encoding="utf-8",
            )
            loaded = load_or_build_checklist(paths, pid)

            updated = next(item for item in loaded.items if item.id == "AC-PROJECT")
            self.assertEqual(updated.status, "pending")
            self.assertEqual(updated.attempts, 1)

    def test_archived_done_tasks_keep_project_out_of_prepare(self) -> None:
        with temporary_agent_paths() as paths:
            pid = "demo"
            root = paths.workspace / pid
            root.mkdir(parents=True, exist_ok=True)
            self._write_core(root)
            (root / "TASKS.md").write_text(
                "# Demo tasks\n\n## 已关闭\n",
                encoding="utf-8",
            )
            (root / "TASKS.archive.md").write_text(
                "- T-001 completed · closed:done · 2026-09-07T00:00:00Z\n",
                encoding="utf-8",
            )
            checklist = Checklist(1, pid, "", "", items=[])

            phase = derive_phase(paths, pid, plan_status="confirmed", checklist=checklist)

            self.assertEqual(phase.phase, "release_wait")

    def test_corrupt_checklist_is_ignored_instead_of_crashing(self) -> None:
        with temporary_agent_paths() as paths:
            pid = "demo"
            root = paths.workspace / pid
            root.mkdir(parents=True, exist_ok=True)
            checklist_file = root / ".agent" / "runaway-checklist.json"
            checklist_file.parent.mkdir(parents=True, exist_ok=True)
            checklist_file.write_bytes(b"\xff\xfe not utf-8")

            self.assertIsNone(load_checklist(paths, pid))

    def test_release_acceptance_is_loaded_against_current_manifest_revision(self) -> None:
        with temporary_agent_paths() as paths:
            pid = "demo"
            root = paths.workspace / pid
            root.mkdir(parents=True, exist_ok=True)
            self._write_core(root)
            (root / "TASKS.md").write_text("- [x] T-001 completed\n", encoding="utf-8")
            checklist = build_checklist(paths, pid)
            for item in checklist.items:
                item.status = "passed"
            manifest = build_manifest(root, pid)
            save_manifest(project_manifest_path(paths, pid), manifest)
            save_release_acceptance(
                root,
                pid,
                release_revision="r0",
                checklist={"human_acceptance": True},
            )

            phase = derive_phase(paths, pid, plan_status="confirmed", checklist=checklist)

            self.assertEqual(phase.phase, "release_wait")
            self.assertTrue(phase.release_accepted)
            plan = build_turn_plan(
                paths=paths,
                project_id=pid,
                phase=phase,
                checklist=checklist,
                user_text="继续",
                intent="execute",
            )
            self.assertEqual(plan.user_line, "项目已完成")
            self.assertEqual(
                build_v2_state_fields(
                    paths,
                    project_id=pid,
                    plan_status="confirmed",
                    checklist=checklist,
                )["runaway_user_line"],
                "项目已完成",
            )

            manifest["artifacts"] = [
                {
                    **artifact,
                    "status": "stale",
                }
                if artifact.get("path") == "RELEASE.md"
                else artifact
                for artifact in manifest["artifacts"]
            ]
            save_manifest(project_manifest_path(paths, pid), manifest)
            stale_phase = derive_phase(
                paths,
                pid,
                plan_status="confirmed",
                checklist=checklist,
            )

            self.assertFalse(stale_phase.release_accepted)

    def test_closed_task_section_does_not_satisfy_documentation_gate(self) -> None:
        with temporary_agent_paths() as paths:
            pid = "demo"
            root = paths.workspace / pid
            root.mkdir(parents=True, exist_ok=True)
            self._write_core(root)
            (root / "TASKS.md").write_text(
                "## 已关闭\n- [x] T-001 old task\n",
                encoding="utf-8",
            )
            checklist = Checklist(1, pid, "", "", items=[])

            phase = derive_phase(paths, pid, plan_status="confirmed", checklist=checklist)

            self.assertEqual(phase.phase, "prepare")
            self.assertIn("TASKS.md", phase.blockers)

    def test_corrupt_task_archive_does_not_crash_phase_derivation(self) -> None:
        with temporary_agent_paths() as paths:
            pid = "demo"
            root = paths.workspace / pid
            root.mkdir(parents=True, exist_ok=True)
            self._write_core(root)
            (root / "TASKS.archive.md").write_bytes(b"\xff\xfe invalid archive")
            checklist = Checklist(1, pid, "", "", items=[])

            phase = derive_phase(paths, pid, plan_status="confirmed", checklist=checklist)

            self.assertEqual(phase.phase, "implement")


if __name__ == "__main__":
    unittest.main()
