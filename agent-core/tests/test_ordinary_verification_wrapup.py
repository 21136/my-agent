# -*- coding: utf-8 -*-
"""Ordinary-mode verification is optional wrap-up, not a write/continue wall."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
_ROOT = _AGENT_CORE.parent
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from project_manifest import bootstrap_manifest
from project_mode import (
    TaskStats,
    compute_execution_stage,
    format_project_overlay,
    project_mode_block_reason,
)

_WRITE_APP = {
    "tool_name": "write_text",
    "arguments": {"path": "workspace/demo/src/app.py", "content": "x"},
}


def _write_block_reason(*, runaway_enabled: bool, workflow_stage: str = "verification") -> str | None:
    return project_mode_block_reason(
        active_shell="project",
        project_root="workspace/demo",
        plan_status="confirmed",
        workflow_stage=workflow_stage,
        runaway_enabled=runaway_enabled,
        tool_name="run_evolved",
        arguments=_WRITE_APP,
    )


class OrdinaryVerificationStageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name) / "demo"
        self.root.mkdir()
        for name, text in {
            "PROJECT.md": "# demo\n",
            "SCOPE.md": "# scope\nREQ-001\nAC-001\n",
            "DESIGN.md": "# design\nUX-001\n",
            "TECH-DESIGN.md": "# tech\nTD-001\n",
            "TASKS.md": "# tasks\nT-001\n",
            "VERIFY.md": "# verify\nV-001\n",
            "RELEASE.md": "# release\nREL-001\n",
        }.items():
            (self.root / name).write_text(text, encoding="utf-8")
        self.manifest = bootstrap_manifest(self.root, "demo")
        self.addCleanup(self.temp_dir.cleanup)

    def test_verification_pending_is_ready_not_blocked(self) -> None:
        result = compute_execution_stage(
            project_id="demo",
            plan_status="confirmed",
            task_stats=TaskStats(done=1, total=1),
            manifest=self.manifest,
            project_root=self.root,
        )
        self.assertEqual(result["stage"], "verification")
        self.assertEqual(result["reason"], "verification_pending")
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["blockers"], [])
        self.assertIn("review", result["warnings"])

    def test_review_fail_still_wraps_up_not_hard_block(self) -> None:
        result = compute_execution_stage(
            project_id="demo",
            plan_status="confirmed",
            task_stats=TaskStats(done=1, total=1),
            manifest=self.manifest,
            project_root=self.root,
            review_verdict="fail",
            review_blockers_count=2,
        )
        self.assertEqual(result["stage"], "verification")
        self.assertEqual(result["reason"], "verification_pending")
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["blockers"], [])
        self.assertIn("review_blockers", result["warnings"])

    def test_passed_review_still_reaches_release(self) -> None:
        result = compute_execution_stage(
            project_id="demo",
            plan_status="confirmed",
            task_stats=TaskStats(done=1, total=1),
            manifest=self.manifest,
            project_root=self.root,
            review_verdict="pass",
        )
        self.assertEqual(result["stage"], "release")
        self.assertEqual(result["reason"], "verification_passed")
        self.assertEqual(result["status"], "ready")


class OrdinaryVerificationWriteGateTests(unittest.TestCase):
    def test_ordinary_verification_allows_code_writes(self) -> None:
        self.assertIsNone(_write_block_reason(runaway_enabled=False))

    def test_ordinary_release_allows_code_writes(self) -> None:
        self.assertIsNone(_write_block_reason(runaway_enabled=False, workflow_stage="release"))

    def test_runaway_verification_still_blocks_business_write(self) -> None:
        reason = _write_block_reason(runaway_enabled=True)
        self.assertIsNotNone(reason)
        self.assertIn("implementation", reason or "")

    def test_runaway_verification_still_allows_run_command(self) -> None:
        reason = project_mode_block_reason(
            active_shell="project",
            project_root="workspace/demo",
            plan_status="confirmed",
            workflow_stage="verification",
            runaway_enabled=True,
            tool_name="run_evolved",
            arguments={
                "tool_name": "run_command",
                "arguments": {"command": "python verify.py", "working_dir": "workspace/demo"},
            },
        )
        self.assertIsNone(reason)


class OrdinaryVerificationOverlayTests(unittest.TestCase):
    def test_ordinary_overlay_says_optional_wrapup(self) -> None:
        overlay = format_project_overlay(
            project_root="workspace/demo",
            project_id="demo",
            plan_status="confirmed",
            workflow_stage="verification",
            runaway_enabled=False,
        )
        self.assertIn("可选收尾", overlay)
        self.assertIn("可写项目内代码", overlay)
        self.assertNotIn("禁止主 Agent 写业务代码", overlay)
        self.assertNotIn("当前无法继续", overlay)

    def test_runaway_overlay_still_forbids_business_writes(self) -> None:
        overlay = format_project_overlay(
            project_root="workspace/demo",
            project_id="demo",
            plan_status="confirmed",
            workflow_stage="verification",
            runaway_enabled=True,
            runaway_checkpoint="",
        )
        self.assertIn("禁止主 Agent 写业务代码", overlay)
        self.assertNotIn("可选收尾", overlay)


class OrdinaryVerificationDesktopContractTests(unittest.TestCase):
    def test_focus_card_treats_pending_verify_as_optional(self) -> None:
        panel = (_ROOT / "desktop" / "src" / "shells" / "unified" / "project-panel.ts").read_text(
            encoding="utf-8"
        )
        self.assertIn("function isOrdinaryVerificationWrapUp", panel)
        self.assertIn('executionStageReason === "verification_pending"', panel)
        self.assertIn("可以先收尾验收", panel)
        self.assertIn("跑验收是可选收尾", panel)
        self.assertIn("也可以继续改代码", panel)
        self.assertIn('actionLabel: "跑验收"', panel)
        self.assertIn('data-action="run-verify">跑验收', panel)
        self.assertNotRegex(
            panel,
            r'executionStageReason === "verification_pending"[\s\S]{0,200}当前无法继续',
        )
        blocked_title = (
            'state.runawayEnabled ? "需要你的处理" : "当前无法继续"'
        )
        self.assertIn(blocked_title, panel)
        wrap_idx = panel.index("function isOrdinaryVerificationWrapUp")
        blocked_idx = panel.index(blocked_title)
        self.assertLess(wrap_idx, blocked_idx)


if __name__ == "__main__":
    unittest.main()
