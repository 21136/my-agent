"""IT-5817: backend-authoritative execution stage calculation."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from project_manifest import bootstrap_manifest, propagate_stale
from project_mode import TaskStats, compute_execution_stage


class ProjectStageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name) / "demo"
        self.root.mkdir()
        contents = {
            "PROJECT.md": "# demo\n",
            "SCOPE.md": "# scope\nREQ-001\nAC-001\n",
            "DESIGN.md": "# design\nUX-001\n",
            "TECH-DESIGN.md": "# tech\nTD-001\n",
            "TASKS.md": "# tasks\nT-001\n",
            "VERIFY.md": "# verify\nV-001\n",
            "RELEASE.md": "# release\nREL-001\n",
        }
        for name, text in contents.items():
            (self.root / name).write_text(text, encoding="utf-8")
        self.manifest = bootstrap_manifest(self.root, "demo")
        self.addCleanup(self.temp_dir.cleanup)

    def test_it5817_authoritative_stage_progression(self) -> None:
        self.assertEqual(
            compute_execution_stage(
                project_id="demo",
                plan_status="draft",
                task_stats=TaskStats(done=0, total=1),
                manifest=self.manifest,
            )["stage"],
            "requirements",
        )
        self.assertEqual(
            compute_execution_stage(
                project_id="demo",
                plan_status="confirmed",
                task_stats=TaskStats(done=0, total=1),
                manifest=self.manifest,
            )["stage"],
            "design",
        )
        self.assertEqual(
            compute_execution_stage(
                project_id="demo",
                plan_status="confirmed",
                task_stats=TaskStats(done=1, total=2),
                manifest=self.manifest,
            )["stage"],
            "implementation",
        )
        self.assertEqual(
            compute_execution_stage(
                project_id="demo",
                plan_status="confirmed",
                task_stats=TaskStats(done=1, total=1),
                manifest=self.manifest,
            )["stage"],
            "verification",
        )
        self.assertEqual(
            compute_execution_stage(
                project_id="demo",
                plan_status="confirmed",
                task_stats=TaskStats(done=1, total=1),
                manifest=self.manifest,
                review_verdict="pass",
            )["stage"],
            "release",
        )

    def test_it5817_l2_stale_forces_requirements(self) -> None:
        propagate_stale(self.manifest, "SCOPE.md", level="L2")
        result = compute_execution_stage(
            project_id="demo",
            plan_status="confirmed",
            task_stats=TaskStats(done=1, total=1),
            manifest=self.manifest,
            review_verdict="pass",
        )
        self.assertEqual(result["stage"], "requirements")
        self.assertEqual(result["reason"], "l2_stale")
        self.assertIn("SCOPE.md", result["blockers"])


if __name__ == "__main__":
    unittest.main()
