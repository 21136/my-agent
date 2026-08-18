"""T-5832: tier content gates and change-scope exceptions."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from project_manifest import bootstrap_manifest, lint_project_content
from project_mode import TaskStats, compute_execution_stage


class ProjectContentLinterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name) / "demo"
        self.root.mkdir()
        contents = {
            "PROJECT.md": "# demo\n",
            "SCOPE.md": "# scope\nREQ-001\nAC-001\n",
            "DESIGN.md": "# design\nUX-001\n```mermaid\nflowchart TD\n  UC-001 --> UX-001\n```\n",
            "TECH-DESIGN.md": "# tech\nTD-001\n",
            "TASKS.md": "# tasks\n- [ ] T-001\n  design: UX-001\n",
            "VERIFY.md": "# verify\nV-001 covers UX-001\n",
            "RELEASE.md": "# release\nREL-001\n",
        }
        for name, text in contents.items():
            (self.root / name).write_text(text, encoding="utf-8")
        self.addCleanup(self.temp_dir.cleanup)

    def test_normal_requires_independent_sequence_gate(self) -> None:
        result = lint_project_content(self.root, tier="normal")
        self.assertFalse(result["ok"])
        self.assertIn("G2", result["missing"])
        self.assertTrue(result["checks"]["G1_non_sequence"])

    def test_small_scope_skips_diagram_gate(self) -> None:
        result = lint_project_content(self.root, tier="normal", change_scope="small")
        self.assertTrue(result["ok"])
        self.assertFalse(result["hard_gate"])

    def test_manifest_lifecycle_and_stage_missing_are_visible(self) -> None:
        manifest = bootstrap_manifest(self.root, "demo")
        self.assertEqual(manifest["project"]["content_origin"], "scaffold")
        self.assertEqual(manifest["change_scope"], "normal")
        self.assertEqual(
            {item["completeness"] for item in manifest["artifacts"]},
            {"skeleton"},
        )
        result = compute_execution_stage(
            project_id="demo",
            plan_status="confirmed",
            task_stats=TaskStats(done=1, total=2),
            manifest=manifest,
            project_root=self.root,
        )
        self.assertEqual(result["stage"], "design")
        self.assertEqual(result["reason"], "content_incomplete")
        self.assertIn("G2", result["missing"])


if __name__ == "__main__":
    unittest.main()
